"""Serves the built UI: the files, the SPA fallback, and the document's own policy.

The document's CSP lives here rather than with the API because whoever serves a document owns its
policy — the API stopped serving one when it became API-only.
"""

from __future__ import annotations

import http.server
import logging
import mimetypes
import socketserver
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit

logger = logging.getLogger("avid.ui.server")

#: Where the page learns which backend to call. A file rather than an inline script, so the policy
#: below can stay `script-src 'self'`.
BASE_SCRIPT = "/__avid/api-base.js"
_BASE_TAG = f'<script src="{BASE_SCRIPT}"></script>'

#: Fingerprinted assets are fetched once per build; the document must never be cached, because the
#: address injected into it changes with the backend this launch picked.
ASSET_CACHE = "public, max-age=31536000, immutable"
DOCUMENT_CACHE = "no-store"

#: mimetypes misses these on some platforms, and a wrong type means the browser refuses the file.
_EXACT_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".webmanifest": "application/manifest+json",
    ".json": "application/json",
    ".map": "application/json",
}


def document_policy(backend_origin: str) -> str:
    """The policy for the document this server hands out.

    The backend is listed twice — as http(s) and as ws(s) — because CSP matches those schemes
    separately, and the terminal panel is a WebSocket. Getting this wrong disables the panel with no
    other symptom.
    """
    reach = ""
    if backend_origin:
        socket_origin = backend_origin.replace("https://", "wss://").replace("http://", "ws://")
        reach = f" {backend_origin} {socket_origin}"
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        # Radix injects scroll-lock styles at runtime.
        "style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data:{reach}; "
        "font-src 'self'; "
        f"connect-src 'self'{reach}; "
        "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


def inject_base(html: str) -> str:
    """Puts the backend address in front of the app's own scripts, which are deferred and so run later."""
    marker = "<head>"
    at = html.lower().find(marker)
    if at < 0:
        return _BASE_TAG + html
    end = at + len(marker)
    return html[:end] + _BASE_TAG + html[end:]


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "avid-ui"
    # Keep-alive: every response below carries a Content-Length, so the connection can be reused.
    protocol_version = "HTTP/1.1"

    @property
    def _ui(self) -> UiServer:
        """The base class types `server` as a plain BaseServer; every handler here is made by UiServer."""
        return cast(UiServer, self.server)

    def do_GET(self) -> None:  # noqa: N802 - the base class names the methods
        self._serve(with_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve(with_body=False)

    def _serve(self, *, with_body: bool) -> None:
        path = unquote(urlsplit(self.path).path)
        if path == BASE_SCRIPT:
            payload = self._ui.base_script.encode("utf-8")
            self._respond(
                payload,
                "text/javascript",
                DOCUMENT_CACHE,
                with_body=with_body,
            )
            return

        target = self._resolve(path)
        if target is None:
            self._respond(b"", "text/plain", DOCUMENT_CACHE, status=404, with_body=with_body)
            return

        payload = target.read_bytes()
        if target.name == "index.html":
            payload = inject_base(payload.decode("utf-8")).encode("utf-8")
        self._respond(
            payload,
            _content_type(target),
            ASSET_CACHE if path.startswith("/assets/") else DOCUMENT_CACHE,
            with_body=with_body,
        )

    def _resolve(self, path: str) -> Path | None:
        """Maps a URL path to a file: a real file wins, an extension-less miss is the SPA shell."""
        root: Path = self._ui.root
        relative = path.lstrip("/")
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            return None  # traversal: neither a page nor an asset, and resolving first is what proves it
        if candidate.is_file():
            return candidate
        if "." in relative.rsplit("/", 1)[-1]:
            # A missing asset must not be answered with HTML: the browser would parse it as script.
            return None
        index = root / "index.html"
        return index if index.is_file() else None

    def _respond(
        self,
        payload: bytes,
        content_type: str,
        cache: str,
        *,
        status: int = 200,
        with_body: bool = True,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", self._ui.policy)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if with_body and payload:
            self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("%s - %s", self.address_string(), format % args)


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    exact = _EXACT_TYPES.get(suffix)
    if exact is not None:
        return exact
    guessed, _encoding = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


class UiServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """The static host for one launch: a fixed root, a fixed backend, a fixed policy."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], root: Path, backend_origin: str) -> None:
        self.root = root.resolve()
        self.backend_origin = backend_origin
        self.base_script = f'globalThis.__AVID_API_BASE__ = "{backend_origin}";\n'
        self.policy = document_policy(backend_origin)
        super().__init__(address, _Handler)


def serve(
    root: Path, port: int, backend_origin: str
) -> tuple[UiServer, Callable[[], None]]:
    """Starts serving on a thread; the returned callable stops it and is the caller's to invoke."""
    server = UiServer(("127.0.0.1", port), root, backend_origin)
    thread = threading.Thread(target=server.serve_forever, name="avid-ui-static", daemon=True)
    thread.start()

    def stop() -> None:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return server, stop


__all__ = ["ASSET_CACHE", "BASE_SCRIPT", "DOCUMENT_CACHE", "UiServer", "document_policy", "inject_base", "serve"]
