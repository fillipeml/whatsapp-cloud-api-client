"""A webhook endpoint, using only the standard library.

:mod:`whatsapp_cloud.webhook` has no server in it, because the hard parts of a webhook are
functions over bytes and are easier to test that way. This module is the thin part: a
``BaseHTTPRequestHandler`` that reads the raw body, calls those functions and writes the
status they chose.

It is a working endpoint and a worked example. Put the same three calls behind FastAPI,
Flask or a serverless function and the behaviour is identical — with one condition that is
easy to get wrong on every framework: **the signature covers the raw bytes.** A framework
that hands you a parsed body and re-serialises it produces different bytes, and the signature
stops verifying for reasons that look like a wrong secret.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .webhook import SIGNATURE_HEADER, GroupEvent, handle_delivery, verify_challenge

#: Meta will not deliver a body larger than this, so anything bigger is not from Meta. The
#: cap is here so an unauthenticated POST cannot make the process read an arbitrary amount
#: into memory before the signature is even checked.
MAX_BODY_BYTES = 1024 * 1024


def make_handler(
    app_secret: str,
    verify_token: str,
    *,
    on_event: Callable[[GroupEvent], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Builds a handler class bound to one set of credentials.

    A factory rather than a class with globals, so a test can stand up two endpoints with
    different secrets in the same process and prove they do not accept each other's traffic.
    """
    emit = log if log is not None else _default_log

    class WebhookHandler(BaseHTTPRequestHandler):
        server_version = "whatsapp-cloud-api-client"

        def _reply(self, status: int, body: str, content_type: str = "text/plain") -> None:
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802 - the base class names it
            """The one-time verification handshake."""
            query = parse_qs(urlparse(self.path).query)
            params = {key: values[0] for key, values in query.items() if values}
            challenge = verify_challenge(params, verify_token)
            if challenge is None:
                emit("webhook verification refused")
                self._reply(403, "verification refused")
                return
            self._reply(200, challenge)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._reply(413, "body too large")
                return
            body = self.rfile.read(length) if length else b""

            result = handle_delivery(
                body,
                self.headers.get(SIGNATURE_HEADER),
                app_secret,
                on_event=on_event,
            )
            if not result.accepted or "handled" not in result.body:
                emit(f"webhook: {result.reason}")
            content_type = "application/json" if result.body.startswith("{") else "text/plain"
            self._reply(result.status, result.body, content_type)

        def log_message(self, fmt: str, *args: object) -> None:
            """Silenced: the host already logs the request, and this would double it."""
            return

    return WebhookHandler


def serve(
    app_secret: str,
    verify_token: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    on_event: Callable[[GroupEvent], None] | None = None,
) -> None:
    """Runs the endpoint until interrupted.

    Binds to localhost by default. Meta needs a public HTTPS URL, so a real deployment puts
    this behind a reverse proxy or a tunnel rather than exposing it directly — and a default
    of ``0.0.0.0`` would quietly invite somebody to skip that step.
    """
    handler = make_handler(app_secret, verify_token, on_event=on_event)
    httpd = HTTPServer((host, port), handler)
    _default_log(f"listening on http://{host}:{port} (POST for events, GET for the handshake)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def log_event(event: GroupEvent) -> None:
    """A default handler: one JSON line per event.

    Enough to be useful on day one, and the obvious place to replace with a write to
    wherever the application keeps state. ``group_participants_update`` is the one worth
    storing: it carries the moment somebody joined, and nothing in the API can reconstruct
    that afterwards.
    """
    print(
        json.dumps(
            {"field": event.field_name, "group_id": event.group_id, "value": event.value},
            ensure_ascii=False,
        ),
        flush=True,
    )


def _default_log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
