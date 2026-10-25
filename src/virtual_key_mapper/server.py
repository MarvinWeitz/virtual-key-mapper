"""The local API (`vkm serve`): the CLI's JSON documents over HTTP on 127.0.0.1, for local apps and the browser UI.

Every request needs the random token of this start, which only the user can read from ``serve.json`` in the
configuration folder, and the server's own ``Host``; browsers on other sites are refused by ``Origin``.
Responses are ``{"result": ..., "messages": [...]}`` or ``{"error": ..., "messages": [...]}``.
"""

import hmac
import json
import os
import secrets
import signal
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .errors import CredentialError
from .profiles import ProfileStore, atomic_write_text
from .repositories import find_checkout
from .service import Service, profile_document

HOST = "127.0.0.1"
STATE_FILE = "serve.json"
_MAX_QUERY = 8192


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

    def handle(self, path: str, query: dict[str, list[str]], messages: list[str]):
        parts = [unquote(part) for part in path.strip("/").split("/")]
        if parts == ["api"]:
            return {"version": __version__, "directory": str(self.directory)}
        if parts == ["api", "profiles"]:
            active, profiles = self.store.list()
            documents = []
            for profile in profiles:
                try:
                    documents.append(profile_document(profile))
                except CredentialError as error:
                    messages.append(f"Profile '{profile.name}' cannot be used: {error}")
            return {"default": active, "profiles": documents}
        service = self.service(query, messages)
        if parts == ["api", "status"]:
            return service.status()
        if parts == ["api", "token"]:
            return service.token_document()
        if parts == ["api", "models"]:
            return [{"id": model} for model in service.models()]
        if parts == ["api", "labels"]:
            return service.definitions()
        if len(parts) == 4 and parts[:2] == ["api", "labels"] and parts[3] == "values":
            return service.values_document(parts[2])
        if parts == ["api", "repository"]:
            directory = self.folder(query)
            explicit = service.explicit(_labels(query), "explicit")
            return service.repository(directory, find_checkout(directory), explicit)
        raise ApiError(404, f"No such endpoint: {path}")

    def folder(self, query: dict[str, list[str]]) -> Path:
        directory = _one(query, "directory")
        try:
            path = Path(directory).expanduser().resolve() if directory else self.directory
        except (OSError, RuntimeError, ValueError):
            raise ApiError(400, "Cannot resolve the directory.") from None
        if not path.is_dir():
            raise ApiError(400, f"{path} is not a folder.")
        return path


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
        messages: list[str] = []
        try:
            self.check()
            target = urlsplit(self.path)
            if len(target.query) > _MAX_QUERY:
                raise ApiError(414, "The query is too long.")
            query = parse_qs(target.query, keep_blank_values=True, strict_parsing=False)
            result = self.server.api.handle(target.path, query, messages)
        except ApiError as error:
            self.reply(error.status, {"error": str(error), "messages": messages})
        except CredentialError as error:
            self.reply(400, {"error": str(error), "messages": messages})
        except Exception:  # noqa: BLE001 - a failing request must not stop the server or show internals.
            print("vkm serve: a request failed unexpectedly.", file=sys.stderr)
            self.reply(500, {"error": "The request failed unexpectedly; see the terminal running 'vkm serve'.", "messages": messages})
        else:
            self.reply(200, {"result": result, "messages": messages})

    def refuse(self) -> None:
        self.reply(405, {"error": "Only GET requests are accepted.", "messages": []})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = refuse

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


def serve(store: ProfileStore, directory: Path, port: int, notify: Callable[[str], None]) -> None:
    """Run until interrupted; the state file exists while the server runs."""
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
