"""The local API (`vkm serve`) and browser UI (`vkm ui`): the CLI's JSON documents over HTTP on 127.0.0.1.

The UI's static files need no token; the page reads it from the address ``vkm ui`` opens. Every API request needs
the random token of this start, which only the user can read from ``serve.json`` in the
configuration folder, and the server's own ``Host``; browsers on other sites are refused by ``Origin``.
Reads are GET; changes are POST with a JSON object body. Responses are ``{"result": ..., "messages": [...]}``
or ``{"error": ..., "messages": [...]}``.
"""

import dataclasses
import hmac
import importlib.resources
import json
import os
import secrets
import signal
import sys
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .errors import CredentialError
from .labels import definitions_from_dict
from .profiles import Profile, ProfileStore, atomic_write_text
from .repositories import find_checkout
from .registry import INTEGRATIONS
from .service import Service, add_profile, profile_document

HOST = "127.0.0.1"
STATE_FILE = "serve.json"
_MAX_QUERY = 8192
_MAX_BODY = 65536
_MAX_FOLDERS = 500
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css")}
_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Api:
    """The routes, independent of HTTP; ``directory`` is where the server was started, the default folder."""

    store: ProfileStore
    directory: Path
    services: Callable[..., Service] = Service

    def service(self, query: dict[str, list[str]], messages: list[str]) -> Service:
        return self.services(self.store, _one(query, "profile"), notify=messages.append)

    def handle(self, method: str, path: str, query: dict[str, list[str]], body: dict, messages: list[str]):
        parts = [unquote(part) for part in path.strip("/").split("/")]
        route = (method, *parts)
        if route == ("GET", "api"):
            return {"version": __version__, "directory": str(self.directory)}
        if route == ("GET", "api", "profiles"):
            active, profiles = self.store.list()
            documents = []
            for profile in profiles:
                try:
                    documents.append(profile_document(profile))
                except CredentialError as error:
                    messages.append(f"Profile '{profile.name}' cannot be used: {error}")
            return {"default": active, "profiles": documents}
        if route == ("POST", "api", "profiles"):
            profile = _profile(body)
            add_profile(self.store, profile, messages.append)
            return profile_document(profile)
        if route == ("POST", "api", "profiles", "default"):
            name = _field(body, "name", str, required=True)
            self.store.select(name)
            return {"default": name}
        if route == ("GET", "api", "tools"):
            return sorted(INTEGRATIONS)
        service = self.service(query, messages)
        if route == ("GET", "api", "status"):
            return service.status()
        if route == ("POST", "api", "login"):
            timeout = _field(body, "timeout", int)
            credential = service.login(timeout=180 if timeout is None else timeout)
            return {"profile": service.profile.name, "user": credential.user}
        if route == ("POST", "api", "logout"):
            service.logout()
            return {"profile": service.profile.name}
        if route == ("GET", "api", "token"):
            return service.token_document()
        if route == ("GET", "api", "models"):
            return [{"id": model} for model in service.models()]
        if len(parts) == 3 and route[:3] == ("POST", "api", "setup"):
            return {"notes": service.setup(parts[2], _field(body, "model", str), _field(body, "replace", bool) or False)}
        if len(parts) == 4 and route[:3] == ("POST", "api", "setup") and parts[3] == "remove":
            return {"notes": service.remove(parts[2])}
        if route == ("GET", "api", "labels"):
            return service.definitions()
        if len(parts) == 4 and route[:3] == ("GET", "api", "labels") and parts[3] == "values":
            return service.values_document(parts[2])
        if route == ("GET", "api", "repository"):
            directory = self.folder(_one(query, "directory"))
            explicit = service.explicit(_labels(query), "explicit")
            return service.repository(directory, find_checkout(directory), explicit)
        if route == ("GET", "api", "folders"):
            return self.folders(_one(query, "directory"))
        if route == ("GET", "api", "repositories"):
            return service.bindings()
        if route == ("POST", "api", "repository", "bind"):
            directory = self.folder(_field(body, "directory", str))
            shared = _field(body, "shared", bool) or False
            labels = _field(body, "labels", dict, required=True)
            if not labels or not all(isinstance(value, str) for value in labels.values()):
                raise ApiError(400, "'labels' must be an object of label values, such as {\"customer\": \"acme\"}.")
            checked = service.explicit(labels, "file" if shared else "local")
            binding = service.bind(
                directory, find_checkout(directory), checked, shared=shared, replace=_field(body, "replace", bool) or False,
            )
            return dataclasses.asdict(binding)
        if route == ("POST", "api", "repository", "unbind"):
            directory = self.folder(_field(body, "directory", str))
            keys = _field(body, "keys", list)
            if keys is not None and not all(isinstance(key, str) for key in keys):
                raise ApiError(400, "'keys' must be a list of label keys.")
            return {"repository": service.unbind(directory, find_checkout(directory), keys)}
        if any(other != method and (other, *parts) in _ROUTES for other in ("GET", "POST")):
            raise ApiError(405, f"{method} is not accepted for {path}.")
        raise ApiError(404, f"No such endpoint: {path}")

    def folders(self, directory: str | None) -> dict:
        """The subfolders of a folder for the UI's folder picker; hidden ones are left out."""
        path = self.folder(directory)
        folders = []
        try:
            entries = sorted(path.iterdir(), key=lambda entry: entry.name.lower())
        except OSError:
            raise ApiError(400, f"Cannot list {path}.") from None
        for entry in entries:
            if entry.name.startswith(".") or not _is_folder(entry):
                continue
            folders.append({"name": entry.name, "path": str(entry), "git": (entry / ".git").exists()})
            if len(folders) == _MAX_FOLDERS:
                break
        parent = path.parent if path.parent != path else None
        return {
            "path": str(path), "parent": str(parent) if parent else None, "home": str(Path.home()),
            "git": (path / ".git").exists(), "folders": folders,
        }

    def folder(self, directory: str | None) -> Path:
        try:
            path = Path(directory).expanduser().resolve() if directory else self.directory
        except (OSError, RuntimeError, ValueError):
            raise ApiError(400, "Cannot resolve the directory.") from None
        if not path.is_dir():
            raise ApiError(400, f"{path} is not a folder.")
        return path


_ROUTES = {
    ("GET", "api"), ("GET", "api", "profiles"), ("POST", "api", "profiles"), ("POST", "api", "profiles", "default"),
    ("GET", "api", "tools"), ("GET", "api", "status"), ("POST", "api", "login"), ("POST", "api", "logout"),
    ("GET", "api", "token"), ("GET", "api", "models"), ("GET", "api", "labels"), ("GET", "api", "repository"),
    ("GET", "api", "repositories"), ("GET", "api", "folders"), ("POST", "api", "repository", "bind"), ("POST", "api", "repository", "unbind"),
}


def _is_folder(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _field(body: dict, name: str, kind: type, *, required: bool = False):
    """One value of a JSON body, of the given type; None when absent."""
    value = body.get(name)
    if value is None:
        if required:
            raise ApiError(400, f"'{name}' is required.")
        return None
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ApiError(400, f"'{name}' must be {_KINDS[kind]}.")
    return value


_KINDS = {str: "a string", int: "a whole number", bool: "true or false", dict: "an object", list: "a list"}


def _profile(body: dict) -> Profile:
    """A new profile from the same settings as 'profile add'; ``definitions`` is its labels and outputs."""
    settings = {name: value for name in ("team_id", "host_profile") if (value := _field(body, name, str)) is not None}
    profile = Profile(
        _field(body, "name", str, required=True), _field(body, "gateway", str, required=True), settings,
        _field(body, "gateway_type", str) or "litellm", _field(body, "auth", str) or "litellm-sso",
        _field(body, "allow_insecure_localhost", bool) or False, _field(body, "rules", str),
    )
    definitions = _field(body, "definitions", dict)
    if definitions is not None:
        labels, outputs = definitions_from_dict(definitions, "definitions")
        profile = dataclasses.replace(profile, labels=labels, outputs=outputs)
    return profile


def _one(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name, [])
    if len(values) > 1:
        raise ApiError(400, f"Give '{name}' at most once.")
    return values[0] if values else None


def _labels(query: dict[str, list[str]]) -> dict[str, str]:
    """``label=KEY=VALUE`` parameters, as ``--label`` takes them; ``KEY=`` is "no value"."""
    values: dict[str, str] = {}
    for pair in query.get("label", []):
        key, separator, value = pair.partition("=")
        if not separator:
            raise ApiError(400, f"Use label=KEY=VALUE, not {json.dumps(pair[:80])}.")
        if key in values:
            raise ApiError(400, f"label {key} is given more than once.")
        values[key] = value
    return values


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, api: Api, token: str, port: int = 0) -> None:
        self.api = api
        self.token = token
        super().__init__((HOST, port), Handler)

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.port}"

    def origins(self) -> tuple[str, ...]:
        return (f"{HOST}:{self.port}", f"localhost:{self.port}")


class Handler(BaseHTTPRequestHandler):
    server: Server
    server_version = "vkm"
    sys_version = ""

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - the base class's name.
        pass  # Requests are not logged; the terminal shows only start, stop and errors.

    def do_GET(self) -> None:  # noqa: N802 - the base class's name.
        self.answer("GET")

    def do_POST(self) -> None:  # noqa: N802 - the base class's name.
        self.answer("POST")

    def refuse(self) -> None:
        self.reply(405, {"error": "Only GET and POST requests are accepted.", "messages": []})

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = refuse

    def answer(self, method: str) -> None:
        if method == "GET" and urlsplit(self.path).path in STATIC:
            self.static(urlsplit(self.path).path)
            return
        messages: list[str] = []
        try:
            self.check()
            target = urlsplit(self.path)
            if len(target.query) > _MAX_QUERY:
                raise ApiError(414, "The query is too long.")
            query = parse_qs(target.query, keep_blank_values=True, strict_parsing=False)
            body = self.body() if method == "POST" else {}
            result = self.server.api.handle(method, target.path, query, body, messages)
        except ApiError as error:
            self.reply(error.status, {"error": str(error), "messages": messages})
        except CredentialError as error:
            self.reply(400, {"error": str(error), "messages": messages})
        except Exception:  # noqa: BLE001 - a failing request must not stop the server or show internals.
            print("vkm serve: a request failed unexpectedly.", file=sys.stderr)
            self.reply(500, {"error": "The request failed unexpectedly; see the terminal running 'vkm serve'.", "messages": messages})
        else:
            self.reply(200, {"result": result, "messages": messages})

    def body(self) -> dict:
        """A JSON object; the content type also keeps browsers from sending it from a plain form."""
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            raise ApiError(415, "Send the body as Content-Type: application/json.")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "Invalid Content-Length.") from None
        if not 0 <= length <= _MAX_BODY:
            raise ApiError(413, "The body is too large.")
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, RecursionError):
            raise ApiError(400, "The body is not valid JSON.") from None
        if not isinstance(value, dict):
            raise ApiError(400, "The body must be a JSON object.")
        return value

    def static(self, path: str) -> None:
        """The UI's files: no secrets in them, but only for this server's own host name."""
        if self.headers.get("Host") not in self.server.origins():
            self.reply(403, {"error": "Requests must be addressed to 127.0.0.1 or localhost with this server's port.", "messages": []})
            return
        name, kind = STATIC[path]
        body = importlib.resources.files(__package__).joinpath("ui", name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{kind}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", _POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def check(self) -> None:
        """Refuse requests to another host name (DNS rebinding), from other sites, or without this start's token."""
        if self.headers.get("Host") not in self.server.origins():
            raise ApiError(403, "Requests must be addressed to 127.0.0.1 or localhost with this server's port.")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in tuple(f"http://{item}" for item in self.server.origins()):
            raise ApiError(403, "Requests from other sites are refused.")
        scheme, _, token = (self.headers.get("Authorization") or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), self.server.token.encode()):
            raise ApiError(401, f"Send 'Authorization: Bearer <token>' with the token from {STATE_FILE}.")

    def reply(self, status: int, value: dict) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


def state_path(store: ProfileStore) -> Path:
    return store.home / STATE_FILE


def write_state(store: ProfileStore, server: Server) -> Path:
    """Tell local clients where the server is: its URL and token, readable only by the user."""
    path = state_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"url": server.url, "port": server.port, "token": server.token, "pid": os.getpid(), "version": __version__}
    atomic_write_text(path, json.dumps(value, indent=2) + "\n", permissions=0o600)
    return path


def remove_state(store: ProfileStore, token: str) -> None:
    """Delete the state file unless a later start has replaced it."""
    path = state_path(store)
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("token") == token:
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def serve(
    store: ProfileStore, directory: Path, port: int, notify: Callable[[str], None], *, browser: bool | None = None,
) -> None:
    """Run until interrupted; the state file exists while the server runs.

    ``browser`` opens the UI (True) or prints its address (False); None serves only the API.
    """
    token = secrets.token_urlsafe(32)
    try:
        server = Server(Api(store, directory), token, port)
    except OSError as error:
        raise CredentialError(f"Cannot listen on {HOST}:{port}: {error.strerror or error}.") from None
    try:
        path = write_state(store, server)
    except OSError:
        server.server_close()
        raise CredentialError(f"Cannot write {state_path(store)}.") from None
    notify(f"vkm serves its API on {server.url} (labels default to {directory}).")
    notify(f"Local apps read the URL and token from {path}. Press Ctrl+C to stop.")
    if browser is not None:
        address = f"{server.url}/#token={token}"  # The fragment never reaches the server or its logs.
        if not browser or not webbrowser.open(address):
            notify(f"Open the Virtual Key Mapper UI in your browser: {address}")
    def stop(signum, frame) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)  # A terminated server also removes its state file.
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        notify("Stopped.")
    finally:
        server.server_close()
        remove_state(store, token)
