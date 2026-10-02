"""Sending, to a group or to one person.

Two facts about this endpoint change how a system around it should be designed, so they are
written here rather than in a wiki nobody opens:

**A group message is billed once per recipient.** Meta charges each time a billable message
is delivered to someone in the group, at the same rates as one-to-one traffic: its own worked
example is one template to a five-person group, charged five times. Group utility messages
are also excluded from volume tiers, so the per-message rate is the rate. A notice that
should always be true is therefore both cheaper and more useful in the group's description,
which is not a message at all: no send, no template approval, and visible to whoever joins
tomorrow.

**A business cannot open a conversation with free text.** Outside a service window the first
message has to be an approved template. Free text sent as the first message of a
provisioning run is refused, and the group is created and then silent — which looks like a
bug in the provisioning and is not.

Neither fact is enforced here, because this layer should not guess a caller's billing
arrangement or window state. They are named in the method that they bear on.
"""

from __future__ import annotations

from typing import Any

from . import phone
from .errors import LimitExceededError
from .models import SendResult
from .transport import JsonDict, Request, Transport

#: The template language used when a caller does not name one. It must match the language
#: the template was registered under in the WhatsApp Manager, or Meta rejects the send — so
#: there is no default that is right everywhere, and this one is merely neutral. Pass
#: ``language=`` explicitly for anything but English.
DEFAULT_LANGUAGE = "en_US"

#: The maximum a WhatsApp text message body can carry. Checked locally because a truncated
#: legal notice is worse than a refused one.
MAX_TEXT_CHARS = 4096


class MessagesClient:
    """``POST /<PHONE_NUMBER_ID>/messages``, in the shapes the Groups API supports."""

    def __init__(self, phone_number_id: str, transport: Transport) -> None:
        self.phone_number_id = phone_number_id
        self.transport = transport

    def _send(self, body: JsonDict, recipient: str) -> SendResult:
        response = self.transport.send(
            Request("POST", f"{self.phone_number_id}/messages", json=body)
        )
        return SendResult.parse(response, recipient)

    # -- templates ---------------------------------------------------------------------

    def template_to_group(
        self,
        group_id: str,
        template_name: str,
        *,
        variables: list[str] | None = None,
        language: str = DEFAULT_LANGUAGE,
    ) -> SendResult:
        """A template to a group. Billed once per recipient.

        Use templates created specifically for group sends. Meta does not report performance
        metrics for a template used in a group, so reusing a one-to-one template means its
        numbers quietly stop describing the one-to-one traffic you were reading them for.
        """
        return self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "group",
                "to": group_id,
                "type": "template",
                "template": _template(template_name, variables, language),
            },
            recipient=group_id,
        )

    def template_to_person(
        self,
        number: str,
        template_name: str,
        *,
        variables: list[str] | None = None,
        language: str = DEFAULT_LANGUAGE,
    ) -> SendResult:
        """A template to one person. This is how an invite link reaches someone.

        There is no endpoint that adds a participant to a group, so the whole of onboarding
        is this call carrying a link.
        """
        digits = phone.normalise(number)
        return self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": digits,
                "type": "template",
                "template": _template(template_name, variables, language),
            },
            recipient=digits,
        )

    # -- free text ---------------------------------------------------------------------

    def text_to_group(self, group_id: str, text: str, *, preview_url: bool = True) -> SendResult:
        """Free text to a group. Only works inside an open service window.

        Fine as a reply to a participant, and wrong as the first message of a provisioning
        run: with no window open the send is refused and the group is born silent.
        """
        _check_text(text)
        return self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "group",
                "to": group_id,
                "type": "text",
                "text": {"body": text, "preview_url": preview_url},
            },
            recipient=group_id,
        )

    def text_to_person(self, number: str, text: str, *, preview_url: bool = True) -> SendResult:
        """Free text to one person. Same window rule."""
        _check_text(text)
        digits = phone.normalise(number)
        return self._send(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": digits,
                "type": "text",
                "text": {"body": text, "preview_url": preview_url},
            },
            recipient=digits,
        )

    # -- cost --------------------------------------------------------------------------

    @staticmethod
    def billable_sends(recipient_count: int) -> int:
        """How many sends one group message is billed as.

        One per recipient, which is Meta's own worked example: a template to a group of five
        is charged five times. Exposed as a function because "one message" is the unit people
        reason in and "five messages" is the unit that reaches the invoice.

        No rate is applied and none is shipped. Rates are per market and per category, Meta
        publishes them as downloadable cards that change, and a number hardcoded in a library
        goes wrong silently.
        """
        if recipient_count < 0:
            raise LimitExceededError("recipient_count cannot be negative.")
        return recipient_count


def _check_text(text: str) -> None:
    if not text.strip():
        raise LimitExceededError("A text message cannot be empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise LimitExceededError(
            f"The message is {len(text)} characters; the limit is {MAX_TEXT_CHARS}. "
            "Shorten it deliberately rather than letting it be cut."
        )


def _template(name: str, variables: list[str] | None, language: str) -> dict[str, Any]:
    template: dict[str, Any] = {"name": name, "language": {"code": language}}
    if variables:
        template["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": value} for value in variables],
            }
        ]
    return template
