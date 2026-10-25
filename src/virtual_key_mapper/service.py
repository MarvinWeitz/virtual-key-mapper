"""Operations on one profile that return data, shared by the command line and the local server."""

import dataclasses
import json
import sys
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Callable, Mapping

from .adapters import Api, Auth, Credential, Gateway, IntegrationContext
from .errors import CredentialError
from .labels import Attribution, Choice, LabelConfig, LabelDefinition, validate_key, validate_value
from .models import list_models
from .profiles import Profile, ProfileStore
from .integrations.claude import Claude
from .registry import auth_for, gateway_for
from .repositories import Checkout, RepositoryStore
from .resolve import failed_document, load_config, resolve_labels, show_document, write_label_file
from .storage import NativeKeyring

CLAUDE = Claude()

SOURCE_OPTIONS = {"explicit": "--label", "local": "a personal binding", "file": "a .vkm file"}


def notify_stderr(message: str) -> None:
    print(message, file=sys.stderr)


def undefined(config: LabelConfig, key: str) -> CredentialError:
    return CredentialError(f"No label '{key}' is defined; defined labels: {', '.join(config.keys) or 'none'}.")


def profile_document(profile: Profile) -> dict:
    """``vkm profile show --json``: the profile's settings and endpoints, never a credential."""
    gateway = gateway_for(profile)
    endpoints = {}
    for api in Api:
        try:
            endpoints[api.value] = gateway.endpoint(api)
        except CredentialError:
            pass
    return {
        "name": profile.name,
        "gateway": {"type": profile.gateway_type, "url": profile.gateway},
        "auth": {"type": profile.auth_type, **profile.auth},
        "rules": profile.rules,
        "endpoints": endpoints,
        # The profile's own definitions, not merged with the central rules (the dev container relay copies them).
        "labels": [],
        "outputs": {},
        **profile.definitions(),
    }


@dataclass(frozen=True)
class Binding:
    """What 'repo bind' saved: a personal binding of ``target`` (a repository) or a .vkm file at ``target``."""

    shared: bool
    target: str
    labels: dict[str, str]


class Service:
    """One profile's sign-in, labels and repositories; the profile and its adapters load on first use.

    ``notify`` receives nonsecret messages for the user, such as a sign-in that expires soon.
    """

    def __init__(
        self, store: ProfileStore, profile: str | None = None, *, notify: Callable[[str], None] = notify_stderr,
        keyring=NativeKeyring, auth_factory=auth_for,
    ) -> None:
        self.store = store
        self.profile_name = profile
        self.notify = notify
        self._keyring = keyring
        self._auth_factory = auth_factory

    @cached_property
    def profile(self) -> Profile:
        return self.store.get(self.profile_name)

    @cached_property
    def auth(self) -> Auth:
        return self._auth_factory(self.profile, self.store, self._keyring)  # Only kinds that store credentials open the vault.

    @cached_property
    def gateway(self) -> Gateway:
        return gateway_for(self.profile)

    def credential(self) -> Credential:
        credential = self.auth.credential()
        if credential.notice:
            self.notify(credential.notice)
        return credential

    def token(self) -> str:
        return self.credential().secret

    @cached_property
    def labels(self) -> LabelConfig:
        """The labels and outputs in effect; a credential is only requested for rules on the gateway's own origin."""
        return load_config(self.store, self.profile, self.gateway.outputs(), self.token)

    def resolve(
        self, directory: Path, checkout: Checkout | None, explicit: Mapping[str, str | None] | None = None,
        unknown_explicit: str = "error",
    ) -> Attribution:
        return resolve_labels(
            self.store, self.profile, self.labels, directory, checkout, explicit=explicit, unknown_explicit=unknown_explicit,
        )

    def integration(self, token: str = "", *, labels: bool = True) -> IntegrationContext:
        """``labels=False`` skips loading the rules where only the CLI command or the stored setup is needed."""
        return IntegrationContext(self.profile, self.gateway, token, self.store.home, labels=self.labels if labels else None)

    # Sign-in and models

    def status(self) -> dict:
        return self.auth.status()

    def token_document(self) -> dict:
        """``vkm token --json``: a current access token, renewed if needed."""
        credential = self.credential()
        return {
            "access_token": credential.secret,
            "expires_at": credential.expires_at,
            "profile": self.profile.name,
            "user": credential.user,
        }

    def models(self) -> list[str]:
        return list_models(self.gateway, self.token())

    # Labels

    def explicit(self, values: Mapping[str, str], source: str) -> dict[str, str]:
        """Label values a person gives, checked against the definitions; "" is "no value"."""
        config = self.labels
        checked: dict[str, str] = {}
        for key, value in values.items():
            validate_key(key)
            definition = config.get(key)
            if definition is None:
                raise undefined(config, key)
            if source not in definition.sources:
                raise CredentialError(
                    f"Label '{key}' cannot be set with {SOURCE_OPTIONS[source]}; its sources are: {', '.join(definition.sources)}."
                )
            if value:
                validate_value(value, key)
                if not definition.accepts(value):
                    raise CredentialError(
                        f"{json.dumps(value)} is not one of the values of '{key}' (see 'vkm labels values {key}')."
                    )
            checked[key] = value
        return checked

    def definitions(self) -> list[dict]:
        """``vkm labels list --json``: the labels in effect and where each is defined."""
        return [{**definition.to_dict(), "origin": definition.origin} for definition in self.labels.definitions]

    def offered(self, definition: LabelDefinition) -> tuple[list[Choice] | None, str | None]:
        """Values to choose from and where they come from: the definition's, the gateway's catalogue, or none."""
        if definition.values is not None:
            return list(definition.values), "values"
        catalogue = self.gateway.catalogue(definition.key, self.credential, self.labels)
        return (catalogue, "catalogue") if catalogue is not None else (None, None)

    def values_document(self, key: str) -> dict:
        """``vkm labels values KEY --json``."""
        key = validate_key(key)
        definition = self.labels.get(key)
        if definition is None:
            raise undefined(self.labels, key)
        choices, origin = self.offered(definition)
        return {
            "key": key, "source": origin,
            "values": None if choices is None else [dataclasses.asdict(choice) for choice in choices],
        }

    # Repositories

    def repository(
        self, directory: Path, checkout: Checkout | None, explicit: Mapping[str, str | None] | None = None,
    ) -> dict:
        """``vkm repo show --json``: once the configuration loads, a resolution error is reported in the document."""
        config = self.labels
        try:
            attribution = self.resolve(directory, checkout, explicit, "ignore")
        except CredentialError as error:
            return failed_document(config, error, checkout)
        return show_document(config, attribution, directory, checkout)

    def bind(
        self, directory: Path, checkout: Checkout | None, labels: Mapping[str, str], *, shared: bool = False,
        replace: bool = False,
    ) -> Binding:
        """Save checked label values as a personal binding, or with ``shared`` in the .vkm file at the checkout root."""
        if shared:
            path = write_label_file(checkout.root if checkout is not None else directory, labels)
            resolved = self.resolve(directory, checkout)
            for key in labels:
                label = resolved.get(key)
                if label is not None and label.source not in (None, "file"):
                    self.notify(f"Note: '{key}' still comes from {label.source} ({label.detail}) here.")
            binding = Binding(True, str(path), dict(labels))
        else:
            repository = checkout_origin(checkout)
            if not all(labels.values()):
                raise CredentialError("A personal binding needs values; remove one with 'repo unbind --label KEY'.")
            merged = RepositoryStore(self.store).bind(self.profile.gateway, repository, labels, replace=replace)
            binding = Binding(False, repository, merged)
        self.refresh_claude(directory, checkout)
        return binding

    def unbind(self, directory: Path, checkout: Checkout | None, keys: list[str] | None = None) -> str:
        """Remove the personal binding of the checkout's repository, or only ``keys``; return the repository."""
        repository = checkout_origin(checkout)
        keys = [validate_key(key) for key in keys] if keys else None
        RepositoryStore(self.store).unbind(self.profile.gateway, repository, keys)
        self.refresh_claude(directory, checkout)
        return repository

    def refresh_claude(self, directory: Path, checkout: Checkout | None) -> None:
        """Rewrite Claude local settings this tool wrote, so running sessions follow changed labels.

        Best effort after a change that is already saved: a failure is a warning (the guard still checks each prompt).
        """
        root = checkout.root if checkout is not None else None
        try:
            attribution = self.resolve(directory, checkout)
            integration = self.integration()
            for folder in dict.fromkeys(folder for folder in (root, directory) if folder is not None):
                path = folder / ".claude" / "settings.local.json"
                if CLAUDE.refresh_local(integration, path, attribution, root):
                    self.notify(f"Updated {path}; running Claude sessions use the new labels from their next prompt.")
        except CredentialError as error:
            self.notify(f"Warning: Claude local settings were not updated: {error}")


def checkout_origin(checkout: Checkout | None) -> str:
    if checkout is None or checkout.origin is None:
        raise CredentialError(
            "Personal bindings need a Git checkout with an HTTP/SSH origin remote. "
            "Use 'repo bind --shared' to label a folder with a .vkm file instead."
        )
    return checkout.origin
