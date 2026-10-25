import contextlib
import io
import json
from dataclasses import replace
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
from virtual_key_mapper import __version__, cli
from virtual_key_mapper.adapters import Credential
from virtual_key_mapper.labels import Choice, LabelDefinition, OutputOverride
from virtual_key_mapper.profiles import Profile, ProfileStore
from virtual_key_mapper.repositories import Checkout
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

    def get(self, path, *, token=TOKEN, method="GET", body=None, **headers):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.server.url + path, data=data, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
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
            ({"method": "POST"}, 415),
            ({"method": "POST", "body": {}}, 405),
            ({"method": "PUT"}, 405),
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

    def post(self, path, body):
        return self.get(path, method="POST", body=body)

    def test_profiles_can_be_added_and_selected(self):
        code, body = self.post("/api/profiles", {
            "name": "second", "gateway": "https://other.example", "team_id": "t2",
            "definitions": {"labels": [{"key": "customer", "required": True}]},
        })
        self.assertEqual((code, body["result"]["name"], body["result"]["labels"][0]["key"]), (200, "second", "customer"))
        self.assertEqual(self.store.get("second").gateway, "https://other.example")
        self.assertEqual(self.post("/api/profiles/default", {"name": "second"})[1]["result"], {"default": "second"})
        self.assertEqual(self.store.list()[0], "second")
        for request, message in (
            ({"name": "second", "gateway": "https://other.example", "team_id": "t"}, "exists"),
            ({"name": "third"}, "'gateway' is required"),
            ({"name": "third", "gateway": 5}, "'gateway' must be a string"),
        ):
            with self.subTest(request=request):
                code, body = self.post("/api/profiles", request)
                self.assertEqual(code, 400)
                self.assertIn(message, body["error"])

    def test_profiles_can_be_changed_and_removed(self):
        self.store.update("consulting", lambda profile: replace(profile, outputs={"tags": OutputOverride(("${customer}",))}))
        code, body = self.post("/api/profiles/update", {
            "name": "consulting", "rules": "https://gateway.example/rules.json",
            "definitions": {"labels": [{"key": "customer", "values": [{"id": "acme"}], "strict": True}]},
        })
        self.assertEqual(code, 200, body)
        profile = self.store.get("consulting")
        self.assertEqual((profile.rules, [item.key for item in profile.labels]), ("https://gateway.example/rules.json", ["customer"]))
        self.assertTrue(profile.labels[0].strict)
        self.assertIn("tags", profile.outputs)
        self.assertEqual(self.post("/api/profiles/update", {"name": "consulting", "rules": None})[0], 200)
        self.assertIsNone(self.store.get("consulting").rules)
        for request, message in (
            ({"name": "consulting"}, "Nothing to change"),
            ({"name": "missing", "rules": None}, "Select a configured profile"),
            ({"name": "consulting", "definitions": {"labels": [{"key": "Bad"}]}}, ""),
        ):
            with self.subTest(request=request):
                code, body = self.post("/api/profiles/update", request)
                self.assertEqual(code, 400)
                self.assertIn(message, body["error"])
        code, body = self.post("/api/profiles/remove", {"name": "consulting"})
        self.assertEqual((code, body["result"]), (200, {"removed": "consulting", "was_default": True}))
        self.auth.forget.assert_called_once()
        self.assertEqual(self.store.list(), (None, []))
        self.assertEqual(self.post("/api/profiles/remove", {"name": "consulting"})[0], 400)

    def test_sign_in_and_out_go_through_the_profiles_sign_in(self):
        self.auth.login.return_value = Credential("secret", None, "dev@example.com")
        self.assertEqual(self.post("/api/login", {"timeout": 30})[1]["result"], {"profile": "consulting", "user": "dev@example.com"})
        self.assertEqual(self.auth.login.call_args.kwargs, {"no_browser": False, "timeout": 30})
        self.assertEqual(self.post("/api/login", {"timeout": 0})[0], 400)
        self.assertEqual(self.post("/api/logout", {})[1]["result"], {"profile": "consulting"})
        self.auth.logout.assert_called_once()

    def test_tools_are_set_up_and_removed_through_their_integration(self):
        self.assertIn("claude", self.get("/api/tools")[1]["result"])
        integration = MagicMock()
        integration.setup.return_value = ["Configured."]
        integration.remove.return_value = ["Removed."]
        with patch.dict("virtual_key_mapper.service.INTEGRATIONS", {"claude": integration}):
            code, body = self.post("/api/setup/claude", {"model": "sonnet", "replace": True})
            self.assertEqual((code, body["result"]), (200, {"notes": ["Configured."]}))
            context, model, replace = integration.setup.call_args.args
            self.assertEqual((context.token, model, replace), ("secret", "sonnet", True))
            self.assertEqual(self.post("/api/setup/claude/remove", {})[1]["result"], {"notes": ["Removed."]})
        code, body = self.post("/api/setup/nothing", {})
        self.assertEqual(code, 400)
        self.assertIn("No tool 'nothing'", body["error"])

    def test_labels_are_bound_and_unbound(self):
        checkout = Checkout(self.work, "github.com/company/repo", None)
        with patch("virtual_key_mapper.server.find_checkout", return_value=checkout):
            code, body = self.post("/api/repository/bind", {"labels": {"customer": "acme"}})
            self.assertEqual((code, body["result"]), (200, {"shared": False, "target": "github.com/company/repo", "labels": {"customer": "acme"}}))
            self.assertEqual(self.get("/api/repositories")[1]["result"][0]["labels"], {"customer": "acme"})
            self.assertTrue(self.get("/api/repository")[1]["result"]["complete"])
            code, body = self.post("/api/repository/bind", {"labels": {"project": "shop"}, "shared": True})
            self.assertEqual((code, Path(body["result"]["target"])), (200, self.work / ".vkm"))
            self.assertEqual(self.post("/api/repository/unbind", {"keys": ["customer"]})[1]["result"], {"repository": "github.com/company/repo"})
            self.assertEqual(self.get("/api/repositories")[1]["result"], [])
            for request, message in (
                ({"labels": {}}, "'labels' must be an object"),
                ({"labels": {"customer": 1}}, "'labels' must be an object"),
                ({"labels": {"customer": "initech", "team": "x"}}, "No label 'team'"),
                ({"keys": "customer"}, None),
            ):
                with self.subTest(request=request):
                    path = "/api/repository/unbind" if "keys" in request else "/api/repository/bind"
                    code, body = self.post(path, request)
                    self.assertEqual(code, 400)
                    if message:
                        self.assertIn(message, body["error"])

    def test_bodies_must_be_json_objects_of_a_limited_size(self):
        for data, status in ((b"[1]", 400), (b"{", 400), (b" " * 70000, 413)):
            with self.subTest(status=status, size=len(data)):
                request = urllib.request.Request(self.server.url + "/api/logout", data=data, method="POST")
                request.add_header("Authorization", f"Bearer {TOKEN}")
                request.add_header("Content-Type", "application/json")
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    DIRECT.open(request, timeout=10)
                self.assertEqual(caught.exception.code, status)
        self.auth.logout.assert_not_called()

    def test_the_page_is_served_without_the_token_but_only_to_its_own_host(self):
        for path, kind in (("/", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css")):
            with self.subTest(path=path), DIRECT.open(self.server.url + path, timeout=10) as response:
                self.assertEqual(response.headers["Content-Type"].split(";")[0], kind)
                self.assertIn("script-src 'self'", response.headers["Content-Security-Policy"])
                self.assertEqual(response.headers["X-Frame-Options"], "DENY")
                self.assertTrue(response.read())
        request = urllib.request.Request(self.server.url + "/", headers={"Host": "attacker.example"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            DIRECT.open(request, timeout=10)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(self.get("/index.html", token=None)[0], 401)

    def test_folders_lists_visible_subfolders_and_marks_git_checkouts(self):
        (self.work / "shop" / ".git").mkdir(parents=True)
        (self.work / "Billing").mkdir()
        (self.work / ".hidden").mkdir()
        (self.work / "notes.txt").write_text("x", encoding="utf-8")
        result = self.get("/api/folders")[1]["result"]
        self.assertEqual((result["path"], result["parent"]), (str(self.work), str(self.home)))
        self.assertEqual(result["folders"], [
            {"name": "Billing", "path": str(self.work / "Billing"), "git": False},
            {"name": "shop", "path": str(self.work / "shop"), "git": True},
        ])
        self.assertTrue(self.get(f"/api/folders?directory={quote(str(self.work / 'shop'))}")[1]["result"]["git"])

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


class CommandTests(unittest.TestCase):
    """The commands start the server with the right port, browser and proxy."""

    def start(self, *arguments):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        ProfileStore(Path(temp.name)).add(Profile("consulting", "https://gateway.example", {"team_id": "t"}))
        with patch.object(cli, "serve") as serve, contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["--home", temp.name, *arguments, "--directory", temp.name])
        return code, serve

    def test_ui_opens_the_browser_on_a_free_port(self):
        code, serve = self.start("ui")
        self.assertEqual(code, 0)
        self.assertEqual((serve.call_args.args[2], serve.call_args.kwargs), (0, {"browser": True, "proxy": None}))
        self.assertFalse(self.start("ui", "--no-browser")[1].call_args.kwargs["browser"])

    def test_serve_takes_labels_only_with_the_proxy(self):
        code, serve = self.start("serve")
        self.assertEqual((code, serve.call_args.kwargs), (0, {"browser": None, "proxy": None}))
        code, serve = self.start("serve", "--proxy", "--profile", "consulting")
        self.assertEqual(code, 0)
        self.assertEqual(serve.call_args.args[2], cli.PROXY_PORT)
        self.assertIsNotNone(serve.call_args.kwargs["proxy"])
        code, serve = self.start("serve", "--label", "customer=acme")
        self.assertEqual(code, 1)
        serve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
