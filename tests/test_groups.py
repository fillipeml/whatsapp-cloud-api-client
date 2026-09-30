"""The Groups client, checked against the request shapes Meta documents.

Three of these tests exist because the shape is not the one you would guess. Remove
participants takes objects and not strings. The list endpoint nests its page under
``data.groups`` and not under ``data``. And four writes carry ``messaging_product``, which is
easy to leave out of endpoints that are not under ``/messages`` — and which is rejected by
schema validation before eligibility is even considered, so the failure reads as a
permissions problem.
"""

from __future__ import annotations

import pytest

from whatsapp_cloud.errors import LimitExceededError
from whatsapp_cloud.groups import GroupsClient
from whatsapp_cloud.index import MemoryIndex
from whatsapp_cloud.limits import MAX_PARTICIPANTS, MAX_SUBJECT_CHARS
from whatsapp_cloud.transport import DRY_RUN_GROUP_ID, DryRunTransport, RecordedTransport, Request

PHONE_ID = "000000000000000"
GROUP = "120363000000000001@g.us"


def client(recordings: dict | None = None, index=None) -> tuple[GroupsClient, DryRunTransport]:
    reads = RecordedTransport(recordings or {})
    transport = DryRunTransport(reads=reads)
    return GroupsClient(PHONE_ID, transport, index=index), transport


def one_page(groups: list[dict]) -> dict:
    return {f"GET {PHONE_ID}/groups": [{"data": {"groups": groups}, "paging": {"cursors": {}}}]}


# --- what goes in the body ----------------------------------------------------------------


def test_create_sends_messaging_product() -> None:
    groups, transport = client()
    groups.create("Example Ltd — matter 4471")
    assert transport.writes[0].json["messaging_product"] == "whatsapp"


def test_create_defaults_to_requiring_approval() -> None:
    # Meta's own default is auto_approve. This client inverts it deliberately, so the
    # divergence is pinned here rather than left to be discovered.
    groups, transport = client()
    groups.create("Example Ltd — matter 4471")
    assert transport.writes[0].json["join_approval_mode"] == "approval_required"


def test_auto_approve_is_available_and_explicit() -> None:
    groups, transport = client()
    groups.create("Example Ltd — matter 4471", require_approval=False)
    assert transport.writes[0].json["join_approval_mode"] == "auto_approve"


def test_update_sends_messaging_product() -> None:
    groups, transport = client()
    groups.update(GROUP, subject="New subject")
    assert transport.writes[0].json["messaging_product"] == "whatsapp"


def test_invite_link_reset_sends_messaging_product() -> None:
    groups, transport = client()
    groups.reset_invite_link(GROUP)
    assert transport.writes[0].json["messaging_product"] == "whatsapp"


def test_remove_participants_sends_objects_keyed_user() -> None:
    # Not a list of strings. The rest of this API takes bare numbers, this endpoint does not.
    groups, transport = client()
    groups.remove_participants(GROUP, ["+55 (62) 90000-0001", "5562900000002"])
    body = transport.writes[0].json
    assert body["participants"] == [
        {"user": "5562900000001"},
        {"user": "5562900000002"},
    ]
    assert body["messaging_product"] == "whatsapp"


def test_remove_participants_refuses_an_empty_list() -> None:
    groups, _ = client()
    with pytest.raises(LimitExceededError):
        groups.remove_participants(GROUP, [])


def test_remove_participants_refuses_more_than_a_group_holds() -> None:
    groups, _ = client()
    with pytest.raises(LimitExceededError, match=str(MAX_PARTICIPANTS)):
        groups.remove_participants(GROUP, [f"5562900{n:06d}" for n in range(MAX_PARTICIPANTS + 1)])


# --- the limits, checked before anything is sent -------------------------------------------


def test_a_blank_subject_is_refused() -> None:
    groups, transport = client()
    with pytest.raises(LimitExceededError):
        groups.create("   ")
    assert transport.writes == []


def test_the_subject_is_measured_after_trimming() -> None:
    # Meta trims before measuring, so a client that measures first is stricter than the
    # server for no reason.
    groups, transport = client()
    groups.create(" " * 40 + "x" * MAX_SUBJECT_CHARS + " " * 40)
    assert transport.writes[0].json["subject"].strip() == "x" * MAX_SUBJECT_CHARS

    with pytest.raises(LimitExceededError, match=str(MAX_SUBJECT_CHARS)):
        groups.create("x" * (MAX_SUBJECT_CHARS + 1))


def test_an_oversized_description_is_refused() -> None:
    groups, _ = client()
    with pytest.raises(LimitExceededError, match="2048"):
        groups.create("Subject", description="d" * 2049)


def test_update_with_nothing_to_change_is_refused() -> None:
    groups, _ = client()
    with pytest.raises(LimitExceededError, match="Nothing to update"):
        groups.update(GROUP)


def test_guest_list_enforces_the_documented_cap_and_no_more() -> None:
    # Eight is what Meta publishes. Refusing at seven would mean asserting the undocumented
    # reading that the business occupies a place.
    eight = [f"5562900{n:06d}" for n in range(MAX_PARTICIPANTS)]
    assert len(GroupsClient.check_guest_list(eight)) == MAX_PARTICIPANTS

    with pytest.raises(LimitExceededError):
        GroupsClient.check_guest_list([*eight, "5562900999999"])


def test_a_guest_list_at_the_cap_is_accepted_with_a_warning() -> None:
    eight = [f"5562900{n:06d}" for n in range(MAX_PARTICIPANTS)]
    warning = GroupsClient.guest_list_warning(eight)
    assert warning is not None
    assert "does not say" in warning
    assert GroupsClient.guest_list_warning(eight[:-1]) is None


def test_guest_list_deduplicates_before_counting() -> None:
    # The same person written two ways is one guest, and counting them twice would refuse a
    # list that fits.
    guests = GroupsClient.check_guest_list(["+55 62 90000-0001", "005562900000001"])
    assert guests == ["5562900000001"]


# --- reading -------------------------------------------------------------------------------


def test_list_reads_the_page_from_data_groups() -> None:
    groups, _ = client(
        one_page([{"id": "a@g.us", "subject": "A"}, {"id": "b@g.us", "subject": "B"}])
    )
    assert [g.subject for g in groups.list_groups()] == ["A", "B"]


def test_list_also_tolerates_a_flat_data_list() -> None:
    # Graph uses both shapes across endpoints; the difference is invisible until production.
    recordings = {f"GET {PHONE_ID}/groups": [{"data": [{"id": "a@g.us", "subject": "A"}]}]}
    groups, _ = client(recordings)
    assert [g.id for g in groups.list_groups()] == ["a@g.us"]


def test_list_follows_the_cursor_then_stops() -> None:
    recordings = {
        f"GET {PHONE_ID}/groups": [
            {
                "data": {"groups": [{"id": "a@g.us", "subject": "A"}]},
                "paging": {"cursors": {"after": "NEXT"}},
            },
            {"data": {"groups": [{"id": "b@g.us", "subject": "B"}]}, "paging": {"cursors": {}}},
        ]
    }
    groups, _ = client(recordings)
    assert [g.id for g in groups.list_groups()] == ["a@g.us", "b@g.us"]


def test_list_stops_at_the_limit_without_reading_the_next_page() -> None:
    recordings = {
        f"GET {PHONE_ID}/groups": [
            {
                "data": {
                    "groups": [{"id": "a@g.us", "subject": "A"}, {"id": "b@g.us", "subject": "B"}]
                },
                "paging": {"cursors": {"after": "NEXT"}},
            }
        ]
    }
    groups, _ = client(recordings)
    assert [g.id for g in groups.list_groups(limit=1)] == ["a@g.us"]


def test_page_size_outside_the_documented_range_is_refused() -> None:
    groups, _ = client()
    with pytest.raises(LimitExceededError):
        next(groups.list_groups(page_size=0))
    with pytest.raises(LimitExceededError):
        next(groups.list_groups(page_size=2000))


def test_find_by_subject_matches_exactly() -> None:
    groups, _ = client(one_page([{"id": "a@g.us", "subject": "Example Ltd — matter 4471"}]))
    assert groups.find_by_subject("Example Ltd — matter 4471") == "a@g.us"

    groups, _ = client(one_page([{"id": "a@g.us", "subject": "example ltd — matter 4471"}]))
    # Loose matching would eventually return a different client's group. It stays exact.
    assert groups.find_by_subject("Example Ltd — matter 4471") is None


def test_info_parses_the_metadata() -> None:
    recordings = {
        f"GET {GROUP}": [
            {
                "subject": "Example Ltd — matter 4470",
                "description": "Standing notice",
                "participants": [{"wa_id": "15550000001"}, {"wa_id": "15550000002"}],
                "join_approval_mode": "approval_required",
                "suspended": False,
                "total_participant_count": 2,
            }
        ]
    }
    groups, _ = client(recordings)
    info = groups.info(GROUP)
    assert info.id == GROUP
    assert info.approval_required is True
    assert [p.phone_number for p in info.participants] == ["15550000001", "15550000002"]
    # total_participant_count already excludes the business; nothing is subtracted.
    assert info.participant_count == 2


def test_join_requests_are_read_as_people() -> None:
    recordings = {f"GET {GROUP}/join_requests": [{"data": [{"wa_id": "15550000004"}]}]}
    groups, _ = client(recordings)
    assert [p.phone_number for p in groups.join_requests(GROUP)] == ["15550000004"]


# --- idempotency ---------------------------------------------------------------------------


def test_create_idempotent_returns_the_existing_group() -> None:
    groups, transport = client(one_page([{"id": "a@g.us", "subject": "Example Ltd — matter 4471"}]))
    result = groups.create_idempotent("Example Ltd — matter 4471")
    assert result.created is False
    assert result.id == "a@g.us"
    # The important half: nothing was created.
    assert transport.writes == []


def test_create_idempotent_creates_when_nothing_matches() -> None:
    groups, transport = client(one_page([{"id": "a@g.us", "subject": "Somebody else"}]))
    result = groups.create_idempotent("Example Ltd — matter 4471")
    assert result.created is True
    assert len(transport.writes) == 1


def test_the_index_spares_the_scan_on_a_second_run() -> None:
    index = MemoryIndex()
    groups, transport = client(one_page([]), index=index)

    first = groups.create_idempotent("Example Ltd — matter 4471")
    assert first.created is True
    assert len(index) == 1

    second = groups.create_idempotent("Example Ltd — matter 4471")
    assert second.created is False
    assert second.id == first.id
    assert len(transport.writes) == 1


def test_an_empty_index_is_still_used() -> None:
    # `index or NoIndex()` would drop a MemoryIndex until it had something in it, because an
    # empty one is falsy. Pinned because the failure is silent and expensive: duplicate groups.
    index = MemoryIndex()
    groups, _ = client(one_page([]), index=index)
    groups.create("Example Ltd — matter 4471")
    assert index.lookup("Example Ltd — matter 4471") == DRY_RUN_GROUP_ID


def test_a_stale_index_entry_is_dropped_rather_than_returned() -> None:
    # A group can be deleted between runs. Handing back an id that no longer exists would be
    # worse than the scan the index avoids.
    index = MemoryIndex({"Example Ltd — matter 4471": "gone@g.us"})
    recordings = {
        "GET gone@g.us": [
            {"__error__": {"status": 404, "body": '{"error": {"message": "unknown", "code": 100}}'}}
        ],
        **one_page([{"id": "fresh@g.us", "subject": "Example Ltd — matter 4471"}]),
    }
    groups, _ = client(recordings, index=index)
    result = groups.create_idempotent("Example Ltd — matter 4471")
    assert result.id == "fresh@g.us"


def test_a_suspended_group_is_not_treated_as_existing() -> None:
    index = MemoryIndex({"Example Ltd — matter 4471": "suspended@g.us"})
    recordings = {
        "GET suspended@g.us": [{"subject": "Example Ltd — matter 4471", "suspended": True}],
        **one_page([]),
    }
    groups, transport = client(recordings, index=index)
    result = groups.create_idempotent("Example Ltd — matter 4471")
    assert result.created is True
    assert len(transport.writes) == 1


# --- the create response, whose shape Meta does not document --------------------------------


def test_create_reads_a_flat_id() -> None:
    groups = GroupsClient(PHONE_ID, _Fixed({"id": "flat@g.us"}))
    assert groups.create("Subject") == "flat@g.us"


def test_create_reads_a_nested_id() -> None:
    groups = GroupsClient(PHONE_ID, _Fixed({"data": {"groups": [{"id": "nested@g.us"}]}}))
    assert groups.create("Subject") == "nested@g.us"


def test_create_raises_with_the_raw_body_when_no_id_is_there() -> None:
    groups = GroupsClient(PHONE_ID, _Fixed({"unexpected": True}))
    with pytest.raises(Exception, match="no response body"):
        groups.create("Subject")


class _Fixed:
    """A transport that answers one thing, for the response-parsing tests."""

    def __init__(self, response: dict) -> None:
        self.response = response

    def send(self, request: Request) -> dict:
        return self.response
