import os
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from isolation import isolate_tool_configuration
from virtual_key_mapper.adapters import Credential
from virtual_key_mapper.errors import CredentialError
from virtual_key_mapper.labels import Choice, LabelDefinition
from virtual_key_mapper.profiles import Profile, ProfileStore
from virtual_key_mapper.repositories import Checkout, RepositoryStore
from virtual_key_mapper.service import Service

LABELS = (
    LabelDefinition("customer", required=True, values=(Choice("acme", "Acme"), Choice("globex")), strict=True),
    LabelDefinition("project", sources=("explicit", "file")),
)


class ServiceTests(unittest.TestCase):
    """The operations the command line and the local server share, called without the command line."""

    def setUp(self):
        isolate_tool_configuration(self)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.store = ProfileStore(self.home)
        self.store.add(Profile("consulting", "https://gateway.example", {"team_id": "t"}, labels=LABELS))
        self.work = self.home / "work"
        self.work.mkdir()
        self.checkout = Checkout(self.work, "github.com/company/repo", None)
        environment = {name: value for name, value in os.environ.items() if not name.startswith(("VKM_LABEL", "VKM_LAUNCH"))}
        for patcher in (
            patch("virtual_key_mapper.resolve.Path.home", return_value=self.home),
            patch.dict(os.environ, environment, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.auth = MagicMock()
        self.auth.credential.return_value = Credential("secret", 1234.0, "dev@example.com", "Sign in again soon.")
        self.messages: list[str] = []
        self.service = Service(
            self.store, notify=self.messages.append, keyring=None, auth_factory=lambda profile, store, keyring: self.auth,
        )

    def test_the_token_document_carries_the_credential_and_passes_its_notice_on(self):
        self.assertEqual(self.service.token_document(), {
            "access_token": "secret", "expires_at": 1234.0, "profile": "consulting", "user": "dev@example.com",
        })
        self.assertEqual(self.messages, ["Sign in again soon."])

    def test_explicit_values_are_checked_against_the_definitions(self):
        self.assertEqual(self.service.explicit({"customer": "acme", "project": ""}, "explicit"), {"customer": "acme", "project": ""})
        for values, source, message in (
            ({"customer": "initech"}, "explicit", "is not one of the values"),
            ({"team": "x"}, "explicit", "No label 'team' is defined"),
            ({"project": "shop"}, "local", "cannot be set with a personal binding"),
            ({"Customer": "acme"}, "explicit", ""),
        ):
            with self.subTest(values=values), self.assertRaisesRegex(CredentialError, message):
                self.service.explicit(values, source)

    def test_definitions_and_values_documents(self):
        self.assertEqual([item["key"] for item in self.service.definitions()], ["customer", "project"])
        self.assertEqual(self.service.values_document("customer"), {
            "key": "customer", "source": "values",
            "values": [{"id": "acme", "name": "Acme", "description": None}, {"id": "globex", "name": None, "description": None}],
        })

    def test_removing_a_profile_signs_out_and_leaves_no_default(self):
        self.store.add(Profile("second", "https://other.example", {"team_id": "t"}))
        self.assertTrue(self.service.remove_profile())
        self.auth.forget.assert_called_once()
        self.assertEqual(self.store.list(), (None, [self.store.get("second")]))
        self.assertEqual(len(self.messages), 1)
        self.assertIn("no longer get keys", self.messages[0])
        self.assertFalse(Service(self.store, "second", notify=self.messages.append, auth_factory=lambda *_: self.auth).remove_profile())
        self.assertEqual(self.store.list(), (None, []))

    def test_a_profile_is_not_removed_when_signing_out_fails(self):
        self.auth.forget.side_effect = CredentialError("Cannot read the OS credential vault; unlock it and retry.")
        with self.assertRaisesRegex(CredentialError, "credential vault"):
            self.service.remove_profile()
        self.assertEqual(self.store.get().name, "consulting")

    def test_updating_a_profile_is_checked_and_reloads_it(self):
        self.assertEqual(self.service.labels.keys, ("customer", "project"))
        profile = self.service.update_profile(lambda profile: replace(profile, labels=LABELS[:1]))
        self.assertEqual(([item.key for item in profile.labels], self.service.labels.keys), (["customer"], ("customer",)))
        broken = (LabelDefinition("customer"), LabelDefinition("customer"))
        with self.assertRaises(CredentialError):
            self.service.update_profile(lambda profile: replace(profile, labels=broken))
        self.assertEqual(len(self.store.get().labels), 1)

    def test_the_repository_document_reports_missing_labels_and_then_the_headers(self):
        document = self.service.repository(self.work, self.checkout)
        self.assertEqual((document["complete"], document["missing"], document["headers"]), (False, ["customer"], {}))
        binding = self.service.bind(self.work, self.checkout, {"customer": "acme"})
        self.assertEqual((binding.shared, binding.target, binding.labels), (False, "github.com/company/repo", {"customer": "acme"}))
        document = self.service.repository(self.work, self.checkout)
        self.assertTrue(document["complete"])
        self.assertTrue(document["headers"])

    def test_a_shared_binding_writes_the_label_file_and_unbind_removes_a_personal_one(self):
        binding = self.service.bind(self.work, self.checkout, {"customer": "globex", "project": "shop"}, shared=True)
        self.assertEqual(Path(binding.target), self.work / ".vkm")
        self.assertIn('customer = "globex"', (self.work / ".vkm").read_text(encoding="utf-8"))
        self.service.bind(self.work, self.checkout, {"customer": "acme"})
        self.assertEqual(self.service.unbind(self.work, self.checkout), "github.com/company/repo")
        self.assertEqual(RepositoryStore(self.store).get("https://gateway.example", "github.com/company/repo"), {})

    def test_personal_bindings_need_a_checkout_with_an_origin(self):
        with self.assertRaisesRegex(CredentialError, "Personal bindings need a Git checkout"):
            self.service.bind(self.work, None, {"customer": "acme"})


if __name__ == "__main__":
    unittest.main()
