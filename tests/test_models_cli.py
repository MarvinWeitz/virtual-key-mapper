import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from virtual_key_mapper.adapters import Credential
from virtual_key_mapper.cli import main
from virtual_key_mapper.profiles import Profile, ProfileStore
from virtual_key_mapper.transport import GatewayError

LISTED = {"object": "list", "data": [
    {"id": "claude-sonnet", "object": "model"}, {"id": "gpt-codex"}, {"id": "claude-sonnet"},
    {"id": "bad\x1b[31m"}, {"id": ""}, {"id": 7}, "row",
]}


class ModelsCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        ProfileStore(self.home).add(Profile("consulting", "https://gateway.example", {"team_id": "t"}))
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def call(self, *args, listed=LISTED):
        response = {"side_effect": listed} if isinstance(listed, Exception) else {"return_value": listed}
        with contextlib.redirect_stdout(self.stdout), contextlib.redirect_stderr(self.stderr), \
                patch("virtual_key_mapper.cli.NativeKeyring"), \
                patch("virtual_key_mapper.cli.auth_for") as auth, \
                patch("virtual_key_mapper.models.JsonTransport.request", **response) as request:
            auth.return_value.credential.return_value = Credential("secret")
            return main(["--home", str(self.home), "models", "list", *args]), request

    def test_lists_the_aliases_once_each_in_the_gateways_order(self):
        code, request = self.call()
        self.assertEqual(code, 0)
        self.assertEqual(self.stdout.getvalue().splitlines(), ["claude-sonnet", "gpt-codex"])
        self.assertIn("2 model(s)", self.stderr.getvalue())
        self.assertEqual(request.call_args.args, ("GET", "https://gateway.example/v1/models"))
        self.assertEqual(request.call_args.kwargs["headers"], {"Authorization": "Bearer secret"})

    def test_json_prints_one_object_per_model(self):
        code, _ = self.call("--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.stdout.getvalue()), [{"id": "claude-sonnet"}, {"id": "gpt-codex"}])

    def test_a_failed_or_malformed_list_is_an_error(self):
        for listed in (GatewayError("Gateway request failed with HTTP 401.", status=401), {"data": "none"}):
            with self.subTest(listed=listed):
                code, _ = self.call(listed=listed)
                self.assertEqual(code, 1)
                self.assertIn("Error:", self.stderr.getvalue())
        self.assertEqual(self.stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
