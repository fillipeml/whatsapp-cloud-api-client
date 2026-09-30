"""The limits this client enforces, and where each one comes from.

Every constant here is checked *before* a request leaves the process. The alternative is to
let Meta reject the call and find out afterwards, which is fine for a script and bad for
provisioning: a group created without its icon, or a client left outside the group for their
own matter, is a support ticket rather than a stack trace.

Each value is quoted from Meta's Groups reference, and the one thing Meta does **not** say is
marked as such. That distinction matters more than it looks: the widely repeated claim that
"the business occupies one of the eight places" appears in no Meta page, and this module
refuses to encode it. See :data:`PARTICIPANT_CAP_IS_AMBIGUOUS`.

Checked against Meta's documentation on 30 September 2026. Check it again before relying on
any of these; ``wa-groups limits`` prints what the installed build actually enforces.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Meta's wording: "Max group participants: 8".
MAX_PARTICIPANTS = 8

#: Meta's wording: "Max groups you can create: 10,000" per business number.
MAX_GROUPS_PER_NUMBER = 10_000

#: Meta's wording: "Max Cloud API businesses per group: 1".
MAX_BUSINESSES_PER_GROUP = 1

MAX_SUBJECT_CHARS = 128
MAX_DESCRIPTION_CHARS = 2048

#: Pagination on the list endpoint: minimum 1, default 25, maximum 1024.
MIN_PAGE_SIZE = 1
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 1024

#: Group icon contract, sent as ``profile_picture_file``: image/jpeg only, square, at least
#: 192x192, at most 5 MB. Meta's own sample request uploads a ``.png``, contradicting the
#: mime-type rule stated on the same page — which is why this client reads the format from
#: the file's bytes rather than trusting either the extension or the example.
ICON_MIME_TYPE = "image/jpeg"
ICON_FORMAT = "JPEG"
ICON_MIN_SIDE_PX = 192
ICON_MAX_BYTES = 5 * 1024 * 1024

#: E.164 caps a phone number at 15 digits. The lower bound is deliberately permissive: it is
#: here to catch an extension or a truncated spreadsheet cell, not to rule out a country.
MIN_PHONE_DIGITS = 8
MAX_PHONE_DIGITS = 15

#: Whether the business phone number counts toward :data:`MAX_PARTICIPANTS` is **not
#: documented**, and Meta's own reference points both ways: the creating business "is always
#: added to the group as the creator and admin", while ``total_participant_count`` is defined
#: as the count "excluding your business".
#:
#: Every vendor page asserts the inclusive reading — that seven guests fit — and none cites a
#: source; they appear to be paraphrasing each other. So this client enforces the number Meta
#: publishes, eight, and *warns* at eight rather than refusing, because refusing would mean
#: this library asserting something Meta does not.
PARTICIPANT_CAP_IS_AMBIGUOUS = True


def capacity_warning(guest_count: int) -> str | None:
    """A caution when a guest list relies on the undocumented reading of the cap.

    Returns ``None`` when the list is unambiguously fine. Eight guests is accepted, because
    eight is the documented maximum, and flagged, because there is no endpoint that adds a
    participant: if the business does occupy a place, the eighth person is left out and stays
    out until somebody notices.
    """
    if guest_count < MAX_PARTICIPANTS:
        return None
    return (
        f"{guest_count} guests is the documented maximum, but Meta does not say whether the "
        "business phone number occupies one of those places. If it does, the last invitation "
        "is refused, and there is no endpoint that adds a participant afterwards. Consider "
        "inviting one fewer, or confirm the behaviour on your own account first."
    )


@dataclass(frozen=True)
class Limit:
    """One limit, with the provenance a reader needs to check it."""

    name: str
    value: int | str
    unit: str
    source: str


#: Printed by ``wa-groups limits``, so the values a deployment enforces can be read off the
#: installed build rather than inferred from the documentation of some other version.
DOCUMENTED_LIMITS: tuple[Limit, ...] = (
    Limit(
        "participants per group",
        MAX_PARTICIPANTS,
        "participants",
        'Groups reference: "Max group participants: 8". Whether the business counts toward '
        "it is not documented, so this client assumes neither reading.",
    ),
    Limit(
        "groups per business number",
        MAX_GROUPS_PER_NUMBER,
        "groups",
        'Groups reference: "Max groups you can create: 10,000"',
    ),
    Limit(
        "Cloud API businesses per group",
        MAX_BUSINESSES_PER_GROUP,
        "businesses",
        'Groups reference: "Max Cloud API businesses per group: 1"',
    ),
    Limit("subject", MAX_SUBJECT_CHARS, "characters", "Groups reference; Meta trims whitespace"),
    Limit("description", MAX_DESCRIPTION_CHARS, "characters", "Groups reference, optional field"),
    Limit("page size", MAX_PAGE_SIZE, "groups per page", "Groups reference: min 1, default 25"),
    Limit("icon type", ICON_MIME_TYPE, "", "profile_picture_file contract: image/jpeg only"),
    Limit(
        "icon minimum side",
        ICON_MIN_SIDE_PX,
        "pixels",
        "profile_picture_file contract: square, 192x192 or larger",
    ),
    Limit("icon maximum size", ICON_MAX_BYTES, "bytes", "profile_picture_file contract: 5 MB"),
)
