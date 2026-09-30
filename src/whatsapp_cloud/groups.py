"""The Groups API.

Eight endpoints, and one shape that follows from what is missing among them: there is no
endpoint that adds a participant. Entry to a group is always by invite link, which means a
provisioning flow is create → set the icon → fetch the link → send the link, and there is no
way to put someone in a group without their action. Every design decision below follows from
that, including why the guest list is validated before the group is created rather than after.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import media, phone
from .errors import ApiError, LimitExceededError
from .index import GroupIndex, NoIndex
from .limits import (
    DEFAULT_PAGE_SIZE,
    MAX_DESCRIPTION_CHARS,
    MAX_PAGE_SIZE,
    MAX_PARTICIPANTS,
    MAX_SUBJECT_CHARS,
    MIN_PAGE_SIZE,
    capacity_warning,
)
from .models import CreatedGroup, Group, GroupInfo, InviteLink, Participant
from .transport import DRY_RUN_GROUP_ID, JsonDict, Request, Transport

#: Requested by default from the info endpoint. The API returns only what is asked for.
DEFAULT_INFO_FIELDS: tuple[str, ...] = (
    "subject",
    "description",
    "participants",
    "join_approval_mode",
    "suspended",
    "creation_timestamp",
    "total_participant_count",
)

#: Required in the body of four Groups writes: create, update settings, invite-link reset and
#: remove participants. Easy to leave out, because these endpoints are not under /messages and
#: the field reads like a messaging concern — and the request is then refused by schema
#: validation before eligibility is even considered, which makes the failure look like a
#: permissions problem. Sent on exactly the four Meta documents it for, and on no others.
MESSAGING_PRODUCT = "whatsapp"

APPROVAL_REQUIRED = "approval_required"

#: What Meta applies when ``join_approval_mode`` is omitted.
AUTO_APPROVE = "auto_approve"


class GroupsClient:
    """Group lifecycle: create, describe, invite, inspect, remove."""

    def __init__(
        self,
        phone_number_id: str,
        transport: Transport,
        *,
        index: GroupIndex | None = None,
    ) -> None:
        self.phone_number_id = phone_number_id
        self.transport = transport
        # `index or NoIndex()` would be wrong: an empty MemoryIndex is falsy, so a caller
        # who passed one would silently get no index at all until it had something in it.
        self.index: GroupIndex = NoIndex() if index is None else index

    # -- creating ----------------------------------------------------------------------

    def create(
        self,
        subject: str,
        *,
        description: str | None = None,
        require_approval: bool = True,
    ) -> str:
        """``POST /<PHONE_NUMBER_ID>/groups``. Returns the new group's id.

        ``require_approval`` defaults to True. **Meta's default is the opposite**: omit
        ``join_approval_mode`` and the group is ``auto_approve``, so anyone holding the
        invite link is in. This client inverts that deliberately, because for a group that
        carries a client's business an unapproved join is a confidentiality incident, and
        opening the door should be something somebody typed rather than something they
        inherited. Pass ``require_approval=False`` to get Meta's behaviour.

        Approval only works if somebody reads the queue: see :meth:`join_requests`.
        """
        _check_subject(subject)
        _check_description(description)

        body: JsonDict = {
            "messaging_product": MESSAGING_PRODUCT,
            "subject": subject,
            "join_approval_mode": APPROVAL_REQUIRED if require_approval else AUTO_APPROVE,
        }
        if description:
            body["description"] = description

        response = self.transport.send(Request("POST", f"{self.phone_number_id}/groups", json=body))
        group_id = _extract_id(response)
        self.index.remember(subject, group_id)
        return group_id

    def create_idempotent(
        self,
        subject: str,
        *,
        description: str | None = None,
        require_approval: bool = True,
    ) -> CreatedGroup:
        """Creates the group, or returns the one that already has this subject.

        A provisioning run gets re-run: a retry after a timeout, a double click, a queue
        redelivering a message. None of those may produce a second group, because the second
        group is the one nobody is watching and the client is in the wrong one.

        The known id is confirmed against the API before it is returned. An index can be
        stale — a group can be deleted or suspended between runs — and returning a stale id
        would be a worse failure than the scan this avoids.
        """
        known = self.index.lookup(subject)
        if known and self._still_exists(known):
            return CreatedGroup(id=known, subject=subject, created=False)
        if known:
            self.index.forget(subject)

        existing = self.find_by_subject(subject)
        if existing:
            self.index.remember(subject, existing)
            return CreatedGroup(id=existing, subject=subject, created=False)

        group_id = self.create(subject, description=description, require_approval=require_approval)
        return CreatedGroup(
            id=group_id,
            subject=subject,
            created=True,
            dry_run=group_id == DRY_RUN_GROUP_ID,
        )

    def _still_exists(self, group_id: str) -> bool:
        if group_id == DRY_RUN_GROUP_ID:
            return True
        try:
            info = self.info(group_id, fields=("subject", "suspended"))
        except ApiError:
            return False
        return not info.suspended

    # -- describing --------------------------------------------------------------------

    def update(
        self,
        group_id: str,
        *,
        subject: str | None = None,
        description: str | None = None,
        icon: str | Path | None = None,
    ) -> JsonDict:
        """``POST /<GROUP_ID>``: subject, description and the icon, together or apart.

        The icon travels as multipart under ``profile_picture_file`` and is validated first,
        because the alternative is a group that exists without its image and a retry that
        has to know the group was already created.
        """
        if subject is None and description is None and icon is None:
            raise LimitExceededError("Nothing to update: pass a subject, a description or an icon.")
        _check_subject(subject, allow_none=True)
        _check_description(description)

        fields: JsonDict = {"messaging_product": MESSAGING_PRODUCT}
        if subject is not None:
            fields["subject"] = subject
        if description is not None:
            fields["description"] = description

        if icon is None:
            return self.transport.send(Request("POST", group_id, json=fields))

        icon_path = media.validate(icon).path
        with icon_path.open("rb") as handle:
            return self.transport.send(
                Request(
                    "POST",
                    group_id,
                    data=fields or None,
                    files={"profile_picture_file": (icon_path.name, handle, "image/jpeg")},
                )
            )

    def set_icon(self, group_id: str, icon: str | Path) -> JsonDict:
        """Shorthand for updating only the picture."""
        return self.update(group_id, icon=icon)

    def set_description(self, group_id: str, description: str) -> JsonDict:
        """Shorthand for updating only the description.

        Worth its own name because of what it costs. A message to a group is billed once per
        participant who receives it; the description is free, needs no template approval, and
        is visible to anyone who joins later. A notice that should always be true belongs
        here, not in a message.
        """
        return self.update(group_id, description=description)

    # -- the invite link ---------------------------------------------------------------

    def invite_link(self, group_id: str) -> InviteLink:
        """``GET /<GROUP_ID>/invite_link``.

        The only way into a group. Treat the result as a credential.
        """
        response = self.transport.send(Request("GET", f"{group_id}/invite_link"))
        link = InviteLink.parse(response, group_id)
        if not link.url:
            raise ApiError(200, f"response carried no invite link: {response}", "invite_link")
        return link

    def reset_invite_link(self, group_id: str) -> InviteLink:
        """``POST /<GROUP_ID>/invite_link``: invalidates every previous link and issues one.

        Meta calls this resetting the link, and the word matters: nothing is revoked from
        anyone already in the group, and everyone still outside it now needs the new link.
        Use it when onboarding finishes and when a link has been somewhere it should not. A
        link that was forwarded around keeps working until it is reset.
        """
        response = self.transport.send(
            Request(
                "POST",
                f"{group_id}/invite_link",
                json={"messaging_product": MESSAGING_PRODUCT},
            )
        )
        return InviteLink.parse(response, group_id)

    #: The older name for :meth:`reset_invite_link`, kept because "revoke" is what people
    #: search for even though it is not what the endpoint does.
    revoke_invite_link = reset_invite_link

    # -- reading -----------------------------------------------------------------------

    def info(self, group_id: str, fields: tuple[str, ...] = DEFAULT_INFO_FIELDS) -> GroupInfo:
        """``GET /<GROUP_ID>?fields=…``. The API returns only the fields asked for."""
        response = self.transport.send(
            Request("GET", group_id, params={"fields": ",".join(fields)})
        )
        response.setdefault("id", group_id)
        return GroupInfo.parse(response)

    def list_groups(
        self, *, page_size: int = DEFAULT_PAGE_SIZE, limit: int | None = None
    ) -> Iterator[Group]:
        """``GET /<BUSINESS_PHONE_NUMBER_ID>/groups``, following the cursor.

        A generator, so a caller looking for one group stops as soon as it is found instead
        of paging through everything the number owns.
        """
        if not MIN_PAGE_SIZE <= page_size <= MAX_PAGE_SIZE:
            raise LimitExceededError(
                f"page_size must be between {MIN_PAGE_SIZE} and {MAX_PAGE_SIZE}; got {page_size}."
            )
        params: JsonDict = {"limit": page_size}
        seen = 0
        while True:
            response = self.transport.send(
                Request("GET", f"{self.phone_number_id}/groups", params=dict(params))
            )
            page = _page(response)
            if not page:
                return
            for payload in page:
                yield Group.parse(payload)
                seen += 1
                if limit is not None and seen >= limit:
                    return
            after = _cursor(response)
            if not after:
                return
            params["after"] = after

    def find_by_subject(self, subject: str) -> str | None:
        """The first active group whose subject matches exactly.

        Exact, not fuzzy. A subject is generated from a template and is stable; matching
        loosely would eventually return a different client's group, which is the one mistake
        this whole module exists to prevent.
        """
        wanted = subject.strip()
        for group in self.list_groups():
            if group.subject.strip() == wanted:
                return group.id or None
        return None

    # -- join requests -------------------------------------------------------------------

    def join_requests(self, group_id: str) -> list[Participant]:
        """``GET /<GROUP_ID>/join_requests``: who is waiting to be let in.

        The other half of ``require_approval``. Turning approval on without ever reading this
        queue produces a group nobody can join: the invite link works, the request is made,
        and it sits there. The two belong together, which is why they are in the same class.
        """
        response = self.transport.send(Request("GET", f"{group_id}/join_requests"))
        pending = response.get("data")
        if not isinstance(pending, list):
            return []
        return [Participant.parse(item) for item in pending if isinstance(item, dict | str)]

    def approve_join_requests(self, group_id: str, numbers: list[str]) -> JsonDict:
        """``POST /<GROUP_ID>/join_requests``: let these numbers in.

        Approving is the moment a person gains access to a client's matter, so this client
        never does it implicitly. There is deliberately no "approve everything waiting"
        convenience here: a caller who wants that can pass the list from
        :meth:`join_requests`, and will have had to look at it.
        """
        digits = _participants(numbers, "approve")
        return self.transport.send(
            Request("POST", f"{group_id}/join_requests", json={"participants": digits})
        )

    def reject_join_requests(self, group_id: str, numbers: list[str]) -> JsonDict:
        """``DELETE /<GROUP_ID>/join_requests``: refuse these numbers."""
        digits = _participants(numbers, "reject")
        return self.transport.send(
            Request("DELETE", f"{group_id}/join_requests", json={"participants": digits})
        )

    # -- ending a group --------------------------------------------------------------------

    def delete(self, group_id: str, *, subject: str | None = None) -> JsonDict:
        """``DELETE /<GROUP_ID>``.

        Pass the ``subject`` when you know it so the index forgets the group too. An index
        still pointing at a deleted group is the one case where idempotent creation would
        return an id that no longer exists — which it guards against by confirming, but
        forgetting here saves the wasted call.
        """
        response = self.transport.send(Request("DELETE", group_id))
        if subject is not None:
            self.index.forget(subject)
        return response

    # -- participants ------------------------------------------------------------------

    def remove_participants(self, group_id: str, numbers: list[str]) -> JsonDict:
        """``DELETE /<GROUP_ID>/participants``.

        The only control over who is in a group that the API offers. There is no endpoint to
        add someone and none to promote an administrator, so composition is managed by
        deciding who receives the invite link and by removing whoever should not be there.
        """
        digits = _participants(numbers, "remove")
        return self.transport.send(
            Request(
                "DELETE",
                f"{group_id}/participants",
                # An array of OBJECTS keyed "user", not an array of strings. A flat list of
                # numbers is the shape everything else in this API uses, and it is refused
                # here.
                json={
                    "messaging_product": MESSAGING_PRODUCT,
                    "participants": [{"user": number} for number in digits],
                },
            )
        )

    @staticmethod
    def check_guest_list(guests: list[str]) -> list[str]:
        """Refuses a guest list that cannot fit, and returns the normalised one that can.

        Called before the group is created, on purpose: discovering the problem on the last
        invitation means somebody — quite possibly the client — is outside the group for
        their own matter, and there is no endpoint that adds them.

        The cap enforced is the one Meta publishes, eight. Meta does not say whether the
        business number occupies one of those places, so a list of exactly eight is accepted
        and :func:`~whatsapp_cloud.limits.capacity_warning` has something to say about it.
        Refusing at seven would mean this library asserting a limit its vendor does not.
        """
        digits = phone.normalise_all(guests)
        if len(digits) > MAX_PARTICIPANTS:
            raise LimitExceededError(
                f"{len(digits)} guests for a group documented to hold {MAX_PARTICIPANTS} "
                "participants. Shorten the list or split the group before creating anything."
            )
        return digits

    @staticmethod
    def guest_list_warning(guests: list[str]) -> str | None:
        """The caution that goes with a guest list at the documented maximum, or ``None``."""
        return capacity_warning(len(phone.normalise_all(guests)))


# --------------------------------------------------------------------------------------


def _participants(numbers: list[str], action: str) -> list[str]:
    if not numbers:
        raise LimitExceededError(f"Pass at least one participant to {action}.")
    digits = phone.normalise_all(numbers)
    if len(digits) > MAX_PARTICIPANTS:
        raise LimitExceededError(
            f"{len(digits)} participants in one call to {action}; a group holds "
            f"{MAX_PARTICIPANTS}, so a longer list cannot be describing one group."
        )
    return digits


def _check_subject(subject: str | None, *, allow_none: bool = False) -> None:
    """Measured after trimming, because Meta trims before measuring.

    A subject padded to 130 characters with spaces is accepted by the API and would be
    refused here otherwise — a client stricter than the server for no reason.
    """
    if subject is None:
        if allow_none:
            return
        raise LimitExceededError("A group needs a subject.")
    trimmed = subject.strip()
    if not trimmed:
        raise LimitExceededError("A group's subject cannot be blank.")
    if len(trimmed) > MAX_SUBJECT_CHARS:
        raise LimitExceededError(
            f"The subject is {len(trimmed)} characters once trimmed; the limit is "
            f"{MAX_SUBJECT_CHARS}."
        )


def _check_description(description: str | None) -> None:
    if description is not None and len(description) > MAX_DESCRIPTION_CHARS:
        raise LimitExceededError(
            f"The description is {len(description)} characters; the limit is "
            f"{MAX_DESCRIPTION_CHARS}."
        )


def _page(response: JsonDict) -> list[JsonDict]:
    """The list endpoint nests its page under ``data.groups``.

    A flat ``data`` list is accepted too. Meta's Graph endpoints use both shapes, the
    difference is invisible until it is a KeyError in production, and tolerating the other
    one costs three lines.
    """
    data: Any = response.get("data")
    if isinstance(data, dict):
        data = data.get("groups")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _extract_id(response: JsonDict) -> str:
    """Pulls the new group's id out of a response whose shape Meta does not document.

    The create endpoint's request body and its webhook are both documented; its response body
    is not, checked on 30 September 2026. So this parses defensively for the two shapes the
    rest of the page uses and raises loudly with the raw body if neither matches, rather than
    aborting a provisioning run *after* the group exists — which is the expensive moment to
    fail.
    """
    direct = response.get("id")
    if isinstance(direct, str) and direct:
        return direct
    for candidate in _page(response) or _as_list(response.get("groups")):
        nested = candidate.get("id")
        if isinstance(nested, str) and nested:
            return nested
    raise ApiError(
        200,
        f"the create response carried no group id, and Meta documents no response body for "
        f"this endpoint. Raw body: {response}",
        "groups",
    )


def _as_list(value: Any) -> list[JsonDict]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _cursor(response: JsonDict) -> str | None:
    paging = response.get("paging")
    if not isinstance(paging, dict):
        return None
    cursors = paging.get("cursors")
    if not isinstance(cursors, dict):
        return None
    after: Any = cursors.get("after")
    return after if isinstance(after, str) and after else None
