"""The local proxy (`vkm serve --proxy`): forwards model requests to the gateway with a fresh key and the labels.

A tool that knows nothing about vkm sets its base URL to ``http://127.0.0.1:PORT/<api>`` (one prefix per wire
protocol, such as ``openai-chat``) and the proxy key as its API key. Labels come from ``X-Vkm-Label`` headers
(``KEY=VALUE``, comma-separated), then from the folder in ``X-Vkm-Directory`` or the folder the proxy started in.
Nothing is forwarded while a required label has no value.
"""

import hmac
import json
import secrets
import threading
import time
from http.client import HTTPException, HTTPResponse
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .adapters import Api, Credential
from .errors import CredentialError
from .integrations.common import attribution_headers
from .profiles import ProfileStore, atomic_write_text
from .repositories import find_checkout
from .service import Service
from .transport import NoRedirects

KEY_FILE = "proxy.key"
PROXY_PORT = 47474
LABEL_HEADER = "x-vkm-label"
DIRECTORY_HEADER = "x-vkm-directory"
PREFIXES = {api.value: api for api in Api}
MAX_BODY = 64 * 1024 * 1024
TIMEOUT_SECONDS = 600
_CHUNK = 65536
# Headers that belong to one connection, or that the proxy sets itself.
_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade", "host", "content-length", "authorization", "cookie",
})


class ProxyError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def proxy_key(store: ProfileStore) -> str:
    """The proxy's API key: made once and kept in the configuration folder, readable only by the user."""
    path = store.home / KEY_FILE
    try:
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    except OSError:
        pass
    key = "vkm-" + secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, key + "\n", permissions=0o600)
    return key


def request_labels(values: list[str]) -> dict[str, str]:
    """``X-Vkm-Label`` headers: ``KEY=VALUE`` items, comma-separated or repeated; ``KEY=`` is "no value"."""
    labels: dict[str, str] = {}
    for item in (part.strip() for value in values for part in value.split(",")):
        if not item:
            continue
        key, separator, value = item.partition("=")
        if not separator:
            raise ProxyError(400, f"Use {LABEL_HEADER}: KEY=VALUE, not {json.dumps(item[:80])}.")
        if key.strip() in labels:
            raise ProxyError(400, f"Label '{key.strip()}' is given more than once.")
        labels[key.strip()] = value.strip()
    return labels


class Proxy:
    """Resolves the labels of each request and forwards it with the profile's current credential."""

    def __init__(
        self, store: ProfileStore, directory: Path, key: str, *, profile: str | None = None,
        labels: Mapping[str, str] | None = None, services: Callable[..., Service] = Service,
        opener=None, clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.directory = directory
        self.key = key
        self.profile = profile
        self.labels = dict(labels or {})
        self.services = services
        self.opener = opener or build_opener(NoRedirects())
        self.clock = clock
        self._credential: Credential | None = None
        self._renew_at = 0.0
        self._lock = threading.Lock()

    def authorized(self, headers) -> bool:
        """The client sends the proxy key as its API key, in ``Authorization: Bearer`` or ``x-api-key``."""
        scheme, _, bearer = (headers.get("Authorization") or "").partition(" ")
        for given in (bearer.strip() if scheme.lower() == "bearer" else "", (headers.get("x-api-key") or "").strip()):
            if given and hmac.compare_digest(given.encode(), self.key.encode()):
                return True
        return False

    def credential(self, service: Service) -> Credential:
        """The profile's credential, reused until a minute before it expires (and at most four minutes)."""
        with self._lock:
            now = self.clock()
            cached = self._credential
            if cached is None or now >= self._renew_at:
                cached = service.credential()
                expiry = cached.expires_at if cached.expires_at is not None else now + 240
                self._renew_at = min(expiry - 60, now + 240)
                self._credential = cached
            return cached

    def prepare(self, method: str, path: str, query: str, headers, body: bytes | None) -> Request:
        """The gateway request for one client request; raises ProxyError or CredentialError when it must not go out."""
        prefix, _, rest = path.lstrip("/").partition("/")
        api = PREFIXES[prefix]
        messages: list[str] = []
        service = self.services(self.store, self.profile, notify=messages.append)
        directory = self._directory(headers.get(DIRECTORY_HEADER))
        explicit = {**self.labels, **request_labels(headers.get_all(LABEL_HEADER) or [])}
        checked = service.explicit(explicit, "explicit")
        attribution = service.resolve(directory, find_checkout(directory), checked)
        integration = service.integration()
        label_headers = attribution_headers(integration, attribution)  # Raises while required labels are missing.
        token = self.credential(service).secret
        drop = {name.lower() for name in service.labels.header_names()} | _HOP
        forwarded = {
            name: value for name, value in headers.items()
            if name.lower() not in drop and not name.lower().startswith("x-vkm-") and not name.lower().endswith("api-key")
        }
        forwarded.update(label_headers)
        forwarded["Authorization"] = "Bearer " + token
        forwarded["x-api-key"] = token
        url = service.gateway.endpoint(api).rstrip("/") + ("/" + rest if rest else "") + (f"?{query}" if query else "")
        return Request(url, data=body, headers=forwarded, method=method)

    def _directory(self, given: str | None) -> Path:
        if not given:
            return self.directory
        try:
            path = Path(given).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            raise ProxyError(400, f"Cannot resolve {DIRECTORY_HEADER}.") from None
        if not path.is_dir():
            raise ProxyError(400, f"{DIRECTORY_HEADER} is not a folder: {path}")
        return path

    def handle(self, handler, method: str) -> None:
        """Answer one request on ``handler`` (a BaseHTTPRequestHandler), streaming the gateway's response."""
        try:
            if handler.headers.get("Origin") is not None:
                raise ProxyError(403, "The proxy does not answer websites.")
            if not self.authorized(handler.headers):
                raise ProxyError(401, f"Send the proxy key from {KEY_FILE} as the API key.")
            path, _, query = handler.path.partition("?")
            body = self._body(handler) if method not in ("GET", "HEAD") else None
            request = self.prepare(method, path, query, handler.headers, body)
        except ProxyError as error:
            _error(handler, error.status, str(error))
            return
        except CredentialError as error:
            _error(handler, 400, str(error))
            return
        try:
            response = self.opener.open(request, timeout=TIMEOUT_SECONDS)
        except HTTPError as error:
            if 300 <= error.code < 400:
                error.close()
                _error(handler, 502, "The gateway redirected; vkm does not follow redirects.")
                return
            response = error  # An error status from the gateway: pass its answer on.
        except (URLError, OSError, HTTPException):
            _error(handler, 502, "Cannot reach the gateway; check connectivity and trusted TLS certificates.")
            return
        with response:
            _relay(handler, response, method)

    @staticmethod
    def _body(handler) -> bytes:
        try:
            length = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            raise ProxyError(400, "Invalid Content-Length.") from None
        if handler.headers.get("Transfer-Encoding"):
            raise ProxyError(411, "Send the request body with a Content-Length.")
        if not 0 <= length <= MAX_BODY:
            raise ProxyError(413, "The request body is too large.")
        return handler.rfile.read(length)


def _relay(handler, response, method: str) -> None:
    """Pass status, headers and body on as they arrive, so streamed answers are not held back."""
    handler.close_connection = True
    handler.send_response(response.status if hasattr(response, "status") else response.code)
    for name, value in response.headers.items():
        if name.lower() not in _HOP or name.lower() == "content-length":
            handler.send_header(name, value)
    handler.send_header("Connection", "close")
    handler.end_headers()
    if method == "HEAD":
        return
    try:
        # read1 hands on what has arrived without waiting for a full chunk; an error answer has only read.
        read = response.read1 if isinstance(response, HTTPResponse) else response.read
        while chunk := read(_CHUNK):
            handler.wfile.write(chunk)
            handler.wfile.flush()
    except (OSError, HTTPException):
        pass  # The client or the gateway closed the connection; nothing more can be sent.


def _error(handler, status: int, message: str) -> None:
    """An error both OpenAI and Anthropic clients show: ``{"error": {"message", "type"}}``."""
    body = json.dumps({"type": "error", "error": {"type": "vkm_error", "message": message}}).encode()
    handler.close_connection = True
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)
