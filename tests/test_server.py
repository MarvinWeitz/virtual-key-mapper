import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from urllib.parse import quote
from pathlib import Path
from unittest.mock import MagicMock, patch

from isolation import isolate_tool_configuration
from virtual_key_mapper import __version__
from virtual_key_mapper.adapters import Credential
from virtual_key_mapper.labels import Choice, LabelDefinition
from virtual_key_mapper.profiles import Profile, ProfileStore
from virtual_key_mapper.server import STATE_FILE, Api, Server, remove_state, write_state
from virtual_key_mapper.service import Service

LABELS = (LabelDefinition("customer", required=True), LabelDefinition("project", values=(Choice("shop", "Web shop"),)))
TOKEN = "test-token"
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # Never through a configured proxy.


class ServerTests(unittest.TestCase):
    def setUp(self):
        isolate_tool_configuration(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.store = ProfileStore(self.home)
        self.store.add(Profile("consulting", "https://gateway.example", {"team_id": "t"}, labels=LABELS))
        self.work = self.home / "work"
        self.work.mkdir()
        environment = {name: value for name, value in os.environ.items() if not name.startswith(("VKM_LABEL", "VKM_LAUNCH"))}
        for patcher in (
            patch("virtual_key_mapper.resolve.Path.home", return_value=self.home),
            patch("virtual_key_mapper.server.find_checkout", return_value=None),
            patch.dict(os.environ, environment, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.auth = MagicMock()
        self.auth.credential.return_value = Credential("secret", 1234.0, "dev@example.com", "Sign in again soon.")
        self.auth.status.return_value = {"profile": "consulting", "state": "ready"}

        def services(store, profile, notify):
            return Service(store, profile, notify=notify, keyring=None, auth_factory=lambda *_: self.auth)

        self.server = Server(Api(self.store, self.work, services), TOKEN)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path, *, token=TOKEN, method="GET", **headers):
        request = urllib.request.Request(self.server.url + path, method=method)
        if token is not None:
            request.add_header("Authorization", f"Bearer {token}")
        for name, value in headers.items():
            request.add_header(name, value)
        try:
            with DIRECT.open(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_the_index_names_the_version_and_the_default_folder(self):
        self.assertEqual(self.get("/api"), (200, {"result": {"version": __version__, "directory": str(self.work)}, "messages": []}))

    def test_requests_without_the_token_from_another_host_or_site_are_refused(self):
        for kwargs, status in (
            ({"token": None}, 401),
            ({"token": "wrong"}, 401),
            ({"Host": f"attacker.example:{self.server.port}"}, 403),
            ({"Origin": "https://attacker.example"}, 403),
            ({"method": "POST"}, 405),
            ({"method": "OPTIONS"}, 405),
        ):
            with self.subTest(kwargs=kwargs):
                code, body = self.get("/api/status", **kwargs)
                self.assertEqual(code, status)
                self.assertIn("error", body)
        self.auth.status.assert_not_called()
        self.assertEqual(self.get("/api", Origin=f"http://localhost:{self.server.port}")[0], 200)

    def test_profiles_status_token_and_labels(self):
        code, body = self.get("/api/profiles")
        self.assertEqual((code, body["result"]["default"]), (200, "consulting"))
        self.assertEqual([profile["name"] for profile in body["result"]["profiles"]], ["consulting"])
        self.assertNotIn("secret", json.dumps(body))
        self.assertEqual(self.get("/api/status")[1]["result"], {"profile": "consulting", "state": "ready"})
        self.assertEqual(self.get("/api/token"), (200, {
            "result": {"access_token": "secret", "expires_at": 1234.0, "profile": "consulting", "user": "dev@example.com"},
            "messages": ["Sign in again soon."],
        }))
        self.assertEqual([label["key"] for label in self.get("/api/labels")[1]["result"]], ["customer", "project"])
        self.assertEqual(self.get("/api/labels/project/values")[1]["result"], {
            "key": "project", "source": "values", "values": [{"id": "shop", "name": "Web shop", "description": None}],
        })

    def test_the_repository_document_uses_the_start_folder_or_the_given_one_and_explicit_labels(self):
        code, body = self.get("/api/repository")
        self.assertEqual((code, body["result"]["complete"], body["result"]["missing"]), (200, False, ["customer"]))
        (self.work / ".vkm").write_text('[labels]\ncustomer = "acme"\n', encoding="utf-8")
        result = self.get("/api/repository")[1]["result"]
        self.assertTrue(result["complete"])
        self.assertTrue(result["headers"])
        other = self.work / "other"
        (other / "deeper").mkdir(parents=True)
        (other / ".vkm").write_text('[labels]\ncustomer = ""\n', encoding="utf-8")
        query = f"/api/repository?directory={quote(str(other / 'deeper'))}"
        self.assertFalse(self.get(query)[1]["result"]["complete"])
        result = self.get(query + "&label=customer=globex&label=project=shop")[1]["result"]
        self.assertEqual({label["key"]: label["value"] for label in result["labels"]}, {"customer": "globex", "project": "shop"})

    def test_bad_requests_are_reported_as_errors(self):
        for path, status, message in (
            ("/api/nothing", 404, "No such endpoint"),
            ("/api/repository?label=team=x", 400, "No label 'team' is defined"),
            ("/api/repository?label=customer", 400, "label=KEY=VALUE"),
            (f"/api/repository?directory={quote(str(self.work / 'missing'))}", 400, "is not a folder"),
            ("/api/status?profile=unknown", 400, "profile"),
            ("/api/status?profile=a&profile=b", 400, "at most once"),
        ):
            with self.subTest(path=path):
                code, body = self.get(path)
                self.assertEqual(code, status)
                self.assertIn(message, body["error"])

    def test_the_state_file_names_url_and_token_and_is_removed_only_by_its_server(self):
        path = write_state(self.store, self.server)
        self.assertEqual(path, self.home / STATE_FILE)
        state = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((state["url"], state["token"]), (self.server.url, TOKEN))
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        remove_state(self.store, "another start")
        self.assertTrue(path.exists())
        remove_state(self.store, TOKEN)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
