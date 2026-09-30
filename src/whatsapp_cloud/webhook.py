"""Receiving group events, without depending on a web framework.

Subscribing to webhooks is not optional for the Groups API: without a public HTTPS endpoint
that answers the verification challenge, none of the group events are delivered at all.

Everything here is a pure function over bytes and dictionaries. No framework, no server, no
global state. :mod:`whatsapp_cloud.server` wraps them in a standard-library server for anyone
who wants one.

Three things about this payload are easy to get wrong, and all three are quiet failures.

**The four group fields share one envelope.** ``group_lifecycle_update``,
``group_participants_update``, ``group_settings_update`` and ``group_status_update`` all
arrive as ``entry[].changes[].value.groups[]``, and the thing that says what actually
happened is ``groups[].type``, not ``changes[].field``. Dispatching on the field alone
collapses distinct events into one bucket.

**One webhook can carry many statuses.** Delivery and read receipts do not arrive on those
four fields; they come in a ``statuses`` array and are aggregated — one payload may hold many
participants' statuses for one message, or many messages' statuses for one participant.
Treating a delivery as one-status-per-webhook under-counts; treating it as one recipient
per payload over-counts. This module returns every status separately and gives each one a
deduplication key.

**Deliveries repeat.** Meta retries a webhook it did not get a 2xx for, immediately and then
with decreasing frequency for up to seven days, so the same event can arrive several times
over a week. Anything that counts, bills or notifies has to deduplicate, which is what
:attr:`StatusUpdate.dedupe_key` is for.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]

#: The four fields a Groups integration subscribes to. They share an envelope, so they are
#: which subscription delivered the event rather than what the event is.
GROUP_FIELDS: frozenset[str] = frozenset(
    {
        "group_lifecycle_update",
        "group_participants_update",
        "group_settings_update",
        "group_status_update",
    }
)

SIGNATURE_HEADER = "X-Hub-Signature-256"
_SIGNATURE_PREFIX = "sha256="


# --------------------------------------------------------------------------------------
# Authenticity
# --------------------------------------------------------------------------------------


def sign(body: bytes, app_secret: str) -> str:
    """The header value Meta would send for this body. Used by the tests and the demo."""
    digest = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{_SIGNATURE_PREFIX}{digest}"


def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    """Checks ``X-Hub-Signature-256`` against the body.

    Two implementation notes, both of which are this package's choices rather than something
    Meta asks for.

    The hash is computed over the bytes exactly as received, before any parsing. Meta's page
    says the signature covers "the JSON payload", which is true and not precise enough: a
    framework that parses the body and re-serialises it produces different bytes, the
    signature stops matching, and the bug looks like a wrong secret.

    The comparison is constant-time. An endpoint that leaks how much of a signature was
    correct can be walked a byte at a time.

    Only SHA-256 is supported. There is no SHA-1 fallback, because accepting a weaker
    signature as a courtesy makes the weaker one the one an attacker uses.
    """
    if not app_secret:
        # Nothing can be authenticated without a secret, so nothing is accepted. The
        # alternative — treating a missing secret as "skip the check" — turns a public URL
        # into an open event injector the first time a deployment forgets a variable.
        return False
    if not header or not header.startswith(_SIGNATURE_PREFIX):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len(_SIGNATURE_PREFIX) :])


def verify_challenge(params: Mapping[str, str], verify_token: str) -> str | None:
    """The one-time handshake, returning the challenge to echo or ``None`` to refuse.

    The challenge is treated as an opaque string and echoed back unchanged. Meta's WhatsApp
    page describes it as a random string and its Graph page as an integer; coercing it to
    either would break the handshake against the other.
    """
    if not verify_token:
        return None
    if params.get("hub.mode") != "subscribe":
        return None
    if not hmac.compare_digest(params.get("hub.verify_token", ""), verify_token):
        return None
    challenge = params.get("hub.challenge", "")
    return challenge or None


# --------------------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupEvent:
    """One entry of ``value.groups[]``: one thing that happened to one group."""

    #: Which subscription delivered it. Context, not identity.
    field_name: str
    #: What happened. This is the discriminator.
    type: str
    group_id: str
    timestamp: str
    phone_number_id: str = ""
    display_phone_number: str = ""
    raw: JsonDict = field(default_factory=dict, repr=False)

    @property
    def is_participant_change(self) -> bool:
        """Somebody joined or left.

        The event worth storing: it carries the moment a person entered the group, and
        nothing the API exposes can reconstruct that afterwards.
        """
        return self.field_name == "group_participants_update"

    @property
    def is_suspension(self) -> bool:
        """The group was suspended by moderation."""
        return self.field_name == "group_status_update"

    @property
    def dedupe_key(self) -> tuple[str, str, str, str]:
        """Stable across redeliveries of the same event, for up to seven days of retries."""
        return (self.field_name, self.type, self.group_id, self.timestamp)


@dataclass(frozen=True)
class StatusUpdate:
    """One delivery or read receipt, pulled out of an aggregated ``statuses`` array."""

    status: str
    message_id: str
    recipient_id: str
    timestamp: str
    group_id: str = ""
    raw: JsonDict = field(default_factory=dict, repr=False)

    @property
    def dedupe_key(self) -> tuple[str, str, str]:
        """``(message, recipient, status)``.

        The key that stops a read being counted twice. One payload can carry the same
        message's status for several participants, or several messages' statuses for one
        participant, and the same payload can arrive again days later.
        """
        return (self.message_id, self.recipient_id, self.status)


@dataclass
class ParsedPayload:
    """What one delivery contained."""

    events: list[GroupEvent] = field(default_factory=list)
    statuses: list[StatusUpdate] = field(default_factory=list)
    #: Fields that arrived and are not group fields. Not an error: one app subscription can
    #: also carry message and template events, and treating those as failures logs noise
    #: forever.
    ignored_fields: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.events) + len(self.statuses)


def parse_events(payload: JsonDict) -> ParsedPayload:
    """Walks ``entry[].changes[]`` and returns everything it carried.

    Tolerant on purpose: a delivery whose envelope is not the expected shape yields nothing
    rather than raising, because a caller's next move is the same either way.
    """
    parsed = ParsedPayload()
    for entry in _list(payload.get("entry")):
        for change in _list(entry.get("changes")):
            name = change.get("field")
            if not isinstance(name, str):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            if name not in GROUP_FIELDS:
                parsed.ignored_fields.append(name)
                continue
            metadata = value.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            for group in _list(value.get("groups")):
                parsed.events.append(
                    GroupEvent(
                        field_name=name,
                        type=_str(group.get("type")),
                        group_id=_str(group.get("group_id")) or _str(group.get("id")),
                        timestamp=_str(group.get("timestamp")),
                        phone_number_id=_str(metadata.get("phone_number_id")),
                        display_phone_number=_str(metadata.get("display_phone_number")),
                        raw=group,
                    )
                )
            parsed.statuses.extend(_statuses(value))
    return parsed


def _statuses(value: JsonDict) -> list[StatusUpdate]:
    """Every status in an aggregated array, one object each.

    Never "the status of this webhook": the array is the unit Meta aggregates on, and code
    that reads only its first element silently loses the rest.
    """
    out: list[StatusUpdate] = []
    for status in _list(value.get("statuses")):
        recipient = _str(status.get("recipient_id")) or _str(status.get("wa_id"))
        out.append(
            StatusUpdate(
                status=_str(status.get("status")),
                message_id=_str(status.get("id")),
                recipient_id=recipient,
                timestamp=_str(status.get("timestamp")),
                group_id=_str(status.get("group_id")),
                raw=status,
            )
        )
    return out


def _list(value: Any) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _str(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


# --------------------------------------------------------------------------------------
# One delivery, start to finish
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WebhookResponse:
    """What to answer, and why. The caller turns this into its framework's response."""

    status: int
    body: str
    reason: str

    @property
    def accepted(self) -> bool:
        return self.status == 200


def handle_delivery(
    body: bytes,
    signature_header: str | None,
    app_secret: str,
    *,
    on_event: Callable[[GroupEvent], None] | None = None,
    on_status: Callable[[StatusUpdate], None] | None = None,
) -> WebhookResponse:
    """Verifies, parses and dispatches one POST.

    The status codes are the design, and one of them is commonly got wrong:

    * **403** — the signature did not verify. Nothing is parsed.
    * **400** — authentic, and not something this endpoint can accept. Meta's own guidance
      for the WhatsApp endpoint is that an invalid request gets a 400-level status, and
      answering 200 to make the retries stop is a community workaround that reports a
      failure as a success.
    * **500** — authentic and understood, and the handler raised. Left to Meta's retries,
      which is what they are for: a handler that failed on a database blip should see the
      event again.
    * **200** — received and handled. Reserved for exactly that.
    """
    if not verify_signature(body, signature_header, app_secret):
        return WebhookResponse(403, "invalid signature", "signature did not verify")

    try:
        payload = json.loads(body or b"{}")
    except ValueError as error:
        return WebhookResponse(400, "malformed payload", f"payload was not JSON: {error}")
    if not isinstance(payload, dict):
        return WebhookResponse(400, "malformed payload", "payload was not an object")

    parsed = parse_events(payload)

    try:
        if on_event is not None:
            for event in parsed.events:
                on_event(event)
        if on_status is not None:
            for status in parsed.statuses:
                on_status(status)
    except Exception as error:  # noqa: BLE001 - deliberate, see the docstring
        return WebhookResponse(500, "handler failed", f"handler raised: {error!r}")

    return WebhookResponse(
        200,
        json.dumps(
            {
                "events": len(parsed.events),
                "statuses": len(parsed.statuses),
                "ignored": len(parsed.ignored_fields),
            }
        ),
        f"handled {parsed.count} item(s)",
    )
