import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from isolation import isolate_tool_configuration
from virtual_key_mapper.adapters import Credential
from virtual_key_mapper.labels import LabelDefinition
from virtual_key_mapper.profiles import Profile, ProfileStore
from virtual_key_mapper.proxy import KEY_FILE, Proxy, proxy_key, request_labels
from virtual_key_mapper.server import Api, Server
from virtual_key_mapper.service import Service

LABELS = (LabelDefinition("customer", required=True), LabelDefinition("project"))
KEY = "vkm-test-key"
TAGS = "x-litellm-tags"


class Gateway(BaseHTTPRequestHandler):
    """A fake gateway: records each request and streams three events, or answers 429 at .../limited."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.requests.append((self.command, self.path, dict(self.headers), body))
        if self.path.endswith("/limited"):
            payload = b'{"error": {"message": "slow down"}}'
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for index in range(3):
            event = f"data: {index}\n\n".encode()
            self.wfile.write(f"{len(event):x}\r\n".encode() + event + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")

    do_GET = do_POST


class ProxyTests(unittest.TestCase):
    def setUp(self):
        isolate_tool_configuration(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.gateway = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        self.gateway.requests = []
        threading.Thread(target=self.gateway.serve_forever, daemon=True).start()
        self.addCleanup(self.gateway.server_close)
        self.addCleanup(self.gateway.shutdown)
        self.store = ProfileStore(self.home)
        self.store.add(Profile(
            "consulting", f"http://127.0.0.1:{self.gateway.server_address[1]}", {"team_id": "t"}, allow_http=True,
            labels=LABELS,
        ))
        self.work = self.home / "work"
        self.work.mkdir()
        (self.work / ".vkm").write_text('[labels]\ncustomer = "acme"\n', encoding="utf-8")
        environment = {name: value for name, value in os.environ.items() if not name.startswith(("VKM_LABEL", "VKM_LAUNCH"))}
        environment.update({"NO_PROXY": "*", "no_proxy": "*"})
        for patcher in (
            patch("virtual_key_mapper.resolve.Path.home", return_value=self.home),
            patch("virtual_key_mapper.proxy.find_checkout", return_value=None),
            patch.dict(os.environ, environment, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.auth = MagicMock()
        self.auth.credential.return_value = Credential("gateway-secret", None, "dev@example.com")
        self.auth.status.return_value = {"state": "ready"}

        def services(store, profile, notify):
            return Service(store, profile, notify=notify, keyring=None, auth_factory=lambda *_: self.auth)

        self.proxy = Proxy(self.store, self.work, KEY, services=services)
        self.server = Server(Api(self.store, self.work, services), "api-token", 0, self.proxy)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def send(self, path, *, method="POST", body=b'{"model": "m"}', key=KEY, **headers):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        self.addCleanup(connection.close)
        sent = {"Content-Type": "application/json", **{name.replace("_", "-"): value for name, value in headers.items()}}
        if key is not None:
            sent["Authorization"] = f"Bearer {key}"
        connection.request(method, path, body=body if method == "POST" else None, headers=sent)
        response = connection.getresponse()
        return response.status, dict(response.headers), response.read()

    def test_forwards_with_the_gateways_url_a_fresh_key_and_the_folders_labels(self):
        status, headers, body = self.send(
            "/openai-chat/chat/completions?stream=true", x_api_key="client-key", X_Litellm_Tags="customer:spoofed",
            Anthropic_Version="2023-06-01",
        )
        self.assertEqual((status, headers["Content-Type"]), (200, "text/event-stream"))
        self.assertEqual(body, b"data: 0\n\ndata: 1\n\ndata: 2\n\n")
        method, path, sent, received = self.gateway.requests[0]
        self.assertEqual((method, path, received), ("POST", "/v1/chat/completions?stream=true", b'{"model": "m"}'))
        sent = {name.lower(): value for name, value in sent.items()}
        self.assertEqual(sent["authorization"], "Bearer gateway-secret")
        self.assertEqual(sent["x-api-key"], "gateway-secret")
        self.assertEqual(sent[TAGS], "customer:acme")
        self.assertEqual(sent["anthropic-version"], "2023-06-01")
        self.assertNotIn(KEY, json.dumps(sent))

    def test_each_wire_protocol_has_its_own_prefix(self):
        self.assertEqual(self.send("/anthropic-messages/v1/messages")[0], 200)
        self.assertEqual(self.send("/openai-responses/responses")[0], 200)
        self.assertEqual([path for _, path, _, _ in self.gateway.requests], ["/v1/messages", "/v1/responses"])

    def test_request_headers_choose_labels_and_folder(self):
        other = self.home / "other"
        other.mkdir()
        self.send("/openai-chat/chat/completions", X_Vkm_Label="customer=globex, project=shop")
        self.send("/openai-chat/chat/completions", X_Vkm_Directory=str(other), X_Vkm_Label="customer=initech")
        tags = [{name.lower(): value for name, value in sent.items()}[TAGS] for _, _, sent, _ in self.gateway.requests]
        self.assertEqual(tags, ["customer:globex,project:shop", "customer:initech"])
        for _, _, sent, _ in self.gateway.requests:
            self.assertFalse(any(name.lower().startswith("x-vkm-") for name in sent))

    def test_nothing_is_forwarded_while_a_required_label_has_no_value(self):
        status, _, body = self.send("/openai-chat/chat/completions", X_Vkm_Label="customer=")
        self.assertEqual(status, 400)
        self.assertTrue(json.loads(body)["error"]["message"].startswith("Required labels have no value:"))
        status, _, body = self.send("/openai-chat/chat/completions", X_Vkm_Directory=str(self.home / "missing"))
        self.assertEqual(status, 400)
        self.assertEqual(self.gateway.requests, [])

    def test_only_local_clients_with_the_key_are_forwarded(self):
        for kwargs, status in (
            ({"key": None}, 401),
            ({"key": "wrong"}, 401),
            ({"Origin": "https://attacker.example"}, 403),
            ({"Host": "attacker.example"}, 403),
        ):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.send("/openai-chat/chat/completions", **kwargs)[0], status)
        self.assertEqual(self.send("/openai-chat/chat/completions", key=None, x_api_key=KEY)[0], 200)
        self.assertEqual(len(self.gateway.requests), 1)

    def test_gateway_errors_are_passed_on_and_the_credential_is_reused(self):
        self.proxy.profile = None
        status, _, body = self.send("/openai-chat/limited")
        self.assertEqual((status, json.loads(body)), (429, {"error": {"message": "slow down"}}))
        self.send("/openai-chat/chat/completions")
        self.assertEqual(self.auth.credential.call_count, 1)

    def test_the_api_still_answers_next_to_the_proxy(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        self.addCleanup(connection.close)
        connection.request("GET", "/api/status", headers={"Authorization": "Bearer api-token"})
        self.assertEqual(connection.getresponse().status, 200)

    def test_the_key_is_made_once_and_readable_only_by_the_user(self):
        key = proxy_key(self.store)
        self.assertTrue(key.startswith("vkm-"))
        self.assertEqual(proxy_key(self.store), key)
        if os.name != "nt":
            self.assertEqual((self.home / KEY_FILE).stat().st_mode & 0o777, 0o600)

    def test_label_headers_are_parsed_like_label_options(self):
        self.assertEqual(request_labels(["customer=acme, project=", "team=x"]), {"customer": "acme", "project": "", "team": "x"})
        for values in (["customer"], ["customer=a", "customer=b"]):
            with self.subTest(values=values), self.assertRaises(Exception):
                request_labels(values)


if __name__ == "__main__":
    unittest.main()
