"""Failures, named.

The rule throughout: fail loudly and early. A limit this client knows about is checked before
the request is built, so the traceback points at the guest list that was too long rather than
at a 400 from a server three hops away.

``ApiError`` keeps the status, the body and the endpoint because a caller retrying a
provisioning run needs to tell "the number is not allowed to do this" from "the gateway had a
bad minute", and those differ only in the payload.
"""

from __future__ import annotations

import json
from typing import Any

#: Seen on a live probe of an ineligible number (21 September 2026) as
#: "This phone number is not eligible to access Groups APIs".
#:
#: It appears **nowhere** in Meta's public error reference, which documents only generic
#: 400/401/500 for this endpoint — checked on both the current and the legacy paths on
#: 30 September 2026. So it is one recognised signal and not a contract: nothing in this
#: package depends on it, an unrecognised code falls through to a plain
#: :class:`ApiError` carrying Meta's own code and message, and the branch below also matches
#: on the message text in case the number changes without notice.
PROBED_NOT_ELIGIBLE_CODE = 131215

_NOT_ELIGIBLE_HINTS = ("not eligible", "groups api")


class WhatsAppError(RuntimeError):
    """Base of every error this package raises."""


class ConfigurationError(WhatsAppError):
    """A required setting is missing or unusable."""


class LimitExceededError(WhatsAppError):
    """A limit documented in :mod:`whatsapp_cloud.limits` would be violated.

    Raised before the request is sent. Nothing has reached Meta when you see this.
    """


class InvalidIconError(WhatsAppError):
    """The image does not satisfy the ``profile_picture_file`` contract."""


class InvalidPhoneNumberError(WhatsAppError):
    """The string does not look like a phone number this API would accept."""


class ApiError(WhatsAppError):
    """The Graph API answered with an error."""

    def __init__(self, status: int, body: str, endpoint: str) -> None:
        super().__init__(f"Graph API {status} on {endpoint}: {body}")
        self.status = status
        self.body = body
        self.endpoint = endpoint

    @property
    def payload(self) -> dict[str, Any]:
        """The error body parsed as JSON, or an empty mapping when it is not JSON."""
        try:
            parsed = json.loads(self.body)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @property
    def code(self) -> int | None:
        """Meta's numeric error code, when the body carries one."""
        error = self.payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), int):
            return error["code"]
        return None

    @property
    def subcode(self) -> int | None:
        error = self.payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("error_subcode"), int):
            return error["error_subcode"]
        return None

    @property
    def is_transient(self) -> bool:
        """Worth retrying: rate limiting, or the server's problem rather than ours."""
        return self.status == 429 or self.status >= 500


class NotEligibleForGroupsError(ApiError):
    """This phone number may not use the Groups API.

    Its own class because it is neither a bug nor a transient failure: the number has not
    been granted the Official Business Account status the Groups API requires, and retrying
    cannot change that. A provisioning run that meets this should stop and tell a person
    rather than queue the work for later.
    """


def classify(status: int, body: str, endpoint: str) -> ApiError:
    """Builds the most specific error the response supports.

    Status first, then the payload. Meta documents HTTP statuses for these endpoints and does
    not document the numeric codes, so branching the other way round would rest the whole
    error model on the part that is least certain.
    """
    error = ApiError(status, body, endpoint)
    if error.is_transient:
        # A 429 or a 5xx is about the moment, never about eligibility.
        return error
    if error.code == PROBED_NOT_ELIGIBLE_CODE:
        return NotEligibleForGroupsError(status, body, endpoint)
    lowered = body.lower()
    if all(hint in lowered for hint in _NOT_ELIGIBLE_HINTS):
        return NotEligibleForGroupsError(status, body, endpoint)
    # Everything else keeps Meta's own code and message rather than being reshaped into a
    # category this package invented.
    return error
