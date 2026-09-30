"""The shapes this package hands back.

The Graph API returns loosely-shaped JSON that changes between versions. Parsing it into
frozen dataclasses at the boundary means one place to fix when a field moves, and callers
that break at import time rather than three layers away on a missing key.

Every model keeps its ``raw`` payload. A typed model that discards what it did not
understand is a model that hides the field you turn out to need, and the Groups API is new
enough that this happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

JsonDict = dict[str, Any]


def _text(payload: JsonDict, *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


@dataclass(frozen=True)
class Group:
    """A group as the list endpoint describes it."""

    id: str
    subject: str
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, payload: JsonDict) -> Group:
        return cls(id=_text(payload, "id"), subject=_text(payload, "subject"), raw=payload)


@dataclass(frozen=True)
class Participant:
    """Someone in the group. The business number is one of these."""

    phone_number: str
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, payload: JsonDict | str) -> Participant:
        if isinstance(payload, str):
            return cls(phone_number=payload, raw={"phone_number": payload})
        return cls(phone_number=_text(payload, "phone_number", "wa_id", "user", "id"), raw=payload)


@dataclass(frozen=True)
class GroupInfo:
    """The full metadata of one group."""

    id: str
    subject: str
    description: str
    participants: tuple[Participant, ...]
    join_approval_mode: str
    suspended: bool
    total_participant_count: int | None
    created_at: datetime | None
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, payload: JsonDict) -> GroupInfo:
        raw_participants = payload.get("participants")
        if isinstance(raw_participants, dict):
            # Some edges wrap a list in {"data": [...]}; both shapes appear in the wild.
            raw_participants = raw_participants.get("data", [])
        if not isinstance(raw_participants, list):
            raw_participants = []

        return cls(
            id=_text(payload, "id"),
            subject=_text(payload, "subject"),
            description=_text(payload, "description"),
            participants=tuple(Participant.parse(p) for p in raw_participants),
            join_approval_mode=_text(payload, "join_approval_mode"),
            suspended=bool(payload.get("suspended", False)),
            total_participant_count=_optional_int(payload.get("total_participant_count")),
            created_at=_timestamp(payload.get("creation_timestamp")),
            raw=payload,
        )

    @property
    def participant_count(self) -> int:
        """How many people are in the group besides the business.

        ``total_participant_count`` is used as given: Meta defines it as the count
        "excluding your business", so subtracting one for the business number would be
        wrong twice over — once by double-counting the exclusion, and once by assuming an
        answer to a question Meta leaves open (see ``limits.PARTICIPANT_CAP_IS_AMBIGUOUS``).

        It is preferred over ``len(participants)`` because the list can be omitted from a
        ``fields`` selection while the scalar is still returned.
        """
        total = self.total_participant_count
        if total is not None:
            return total
        return len(self.participants)

    @property
    def approval_required(self) -> bool:
        return self.join_approval_mode == "approval_required"


@dataclass(frozen=True)
class InviteLink:
    """A group's invite link, and the only way anyone joins.

    There is no endpoint that adds a participant, so this string is the whole onboarding
    path. Treat it as a credential: whoever holds it can ask to join.
    """

    url: str
    group_id: str

    @classmethod
    def parse(cls, payload: JsonDict, group_id: str) -> InviteLink:
        return cls(url=_text(payload, "invite_link", "link", "url"), group_id=group_id)


@dataclass(frozen=True)
class SendResult:
    """What came back from a send."""

    message_id: str
    recipient: str
    dry_run: bool
    raw: JsonDict = field(default_factory=dict, repr=False)

    @classmethod
    def parse(cls, payload: JsonDict, recipient: str) -> SendResult:
        messages = payload.get("messages")
        message_id = ""
        if isinstance(messages, list) and messages and isinstance(messages[0], dict):
            message_id = _text(messages[0], "id")
        return cls(
            message_id=message_id or _text(payload, "id"),
            recipient=recipient,
            dry_run=bool(payload.get("dry_run", False)),
            raw=payload,
        )


@dataclass(frozen=True)
class CreatedGroup:
    """The outcome of an idempotent create, with the fact that matters to the caller."""

    id: str
    subject: str
    #: False when a group with this subject already existed. A provisioning run that cannot
    #: tell the difference cannot report honestly on what it did.
    created: bool
    dry_run: bool = False


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _timestamp(value: object) -> datetime | None:
    """Graph timestamps are Unix seconds, sometimes as a string."""
    seconds = _optional_int(value)
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None
