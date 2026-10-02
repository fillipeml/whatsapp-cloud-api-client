"""The webhook: authenticity, the envelope, and the status code for each outcome.

The envelope tests are the ones that matter. All four group subscriptions deliver the same
shape, and the thing that says what happened is ``value.groups[].type`` rather than
``changes[].field`` — so a parser that dispatches on the field alone collapses four kinds of
event into one. The aggregation test is the other: one delivery can carry many participants'
statuses for one message, and reading only the first is how a read receipt gets under-counted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from whatsapp_cloud.webhook import (
    GROUP_FIELDS,
    handle_delivery,
    parse_events,
    sign,
    verify_challenge,
    verify_signature,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "webhooks"
SECRET = "an-app-secret"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# --- authenticity ---------------------------------------------------------------------------


def test_a_correct_signature_verifies() -> None:
    body = b'{"entry": []}'
    assert verify_signature(body, sign(body, SECRET), SECRET) is True


def test_a_signature_for_different_bytes_does_not_verify() -> None:
    # The whole point of signing the raw body: re-serialising changes the bytes.
    original = b'{"entry":[]}'
    reserialised = b'{"entry": []}'
    assert verify_signature(reserialised, sign(original, SECRET), SECRET) is False


def test_a_signature_from_a_different_secret_does_not_verify() -> None:
    body = b'{"entry": []}'
    assert verify_signature(body, sign(body, "somebody-elses-secret"), SECRET) is False


@pytest.mark.parametrize(
    "header",
    [None, "", "sha256=", "sha1=abc", "abc", "SHA256=abc"],
)
def test_a_malformed_header_does_not_verify(header: str | None) -> None:
    assert verify_signature(b"{}", header, SECRET) is False


def test_no_secret_means_nothing_is_accepted() -> None:
    # Not "skip the check". A deployment that forgets the variable must fail closed, or a
    # public URL becomes an open event injector.
    body = b"{}"
    assert verify_signature(body, sign(body, ""), "") is False


def test_there_is_no_sha1_fallback() -> None:
    import hashlib
    import hmac

    body = b'{"entry": []}'
    sha1 = "sha1=" + hmac.new(SECRET.encode(), body, hashlib.sha1).hexdigest()
    assert verify_signature(body, sha1, SECRET) is False


# --- the handshake --------------------------------------------------------------------------


def test_the_challenge_is_echoed_when_the_token_matches() -> None:
    params = {"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": "12345"}
    assert verify_challenge(params, "tok") == "12345"


def test_the_challenge_is_returned_unchanged_whatever_it_looks_like() -> None:
    # Meta's WhatsApp page calls it a random string and its Graph page an integer. Coercing
    # to either would break the handshake against the other.
    for challenge in ["12345", "abc-DEF_123", "0012"]:
        params = {"hub.mode": "subscribe", "hub.verify_token": "tok", "hub.challenge": challenge}
        assert verify_challenge(params, "tok") == challenge


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "unsubscribe", "hub.verify_token": "tok", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "subscribe", "hub.verify_token": "tok"},
        {},
    ],
)
def test_the_handshake_is_refused_otherwise(params: dict) -> None:
    assert verify_challenge(params, "tok") is None


def test_no_configured_token_refuses_the_handshake() -> None:
    assert (
        verify_challenge(
            {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}, ""
        )
        is None
    )


# --- the envelope ---------------------------------------------------------------------------


def test_every_group_field_is_subscribed_to() -> None:
    assert {
        "group_lifecycle_update",
        "group_participants_update",
        "group_settings_update",
        "group_status_update",
    } == GROUP_FIELDS


def test_events_come_from_value_groups_not_from_the_field() -> None:
    parsed = parse_events(load("participants-update.json"))
    assert len(parsed.events) == 2
    # One delivery, one field, two distinct things that happened. Dispatching on the field
    # alone would see one event.
    assert [e.type for e in parsed.events] == ["participant_joined", "participant_left"]
    assert {e.field_name for e in parsed.events} == {"group_participants_update"}


def test_an_event_carries_its_group_and_the_number_it_arrived_on() -> None:
    event = parse_events(load("lifecycle-update.json")).events[0]
    assert event.group_id == "120363000000000003@g.us"
    assert event.type == "group_created"
    assert event.phone_number_id == "000000000000000"
    assert event.timestamp == "1893456000"


def test_the_participant_change_is_flagged_for_what_it_is_worth() -> None:
    parsed = parse_events(load("participants-update.json"))
    assert all(e.is_participant_change for e in parsed.events)
    assert parse_events(load("status-update.json")).events[0].is_suspension is True


def test_an_unsubscribed_field_is_ignored_and_not_an_error() -> None:
    parsed = parse_events(load("other-field.json"))
    assert parsed.events == []
    assert parsed.ignored_fields == ["message_template_status_update"]


@pytest.mark.parametrize(
    "payload",
    [{}, {"entry": "nonsense"}, {"entry": [{"changes": "nonsense"}]}, {"entry": [{}]}],
)
def test_a_payload_of_the_wrong_shape_yields_nothing_rather_than_raising(payload: dict) -> None:
    assert parse_events(payload).count == 0


# --- aggregation ------------------------------------------------------------------------------


def test_one_delivery_can_carry_many_statuses() -> None:
    parsed = parse_events(load("aggregated-statuses.json"))
    assert len(parsed.statuses) == 6
    # Four recipients for one message and two for another, in one webhook. Reading only the
    # first status would count one read where there were three.
    reads = [s for s in parsed.statuses if s.status == "read"]
    assert len(reads) == 3


def test_every_status_has_a_key_that_survives_a_redelivery() -> None:
    # Meta retries for up to seven days, so the same payload arrives again. Anything that
    # counts or bills has to deduplicate on something stable.
    parsed = parse_events(load("aggregated-statuses.json"))
    keys = [s.dedupe_key for s in parsed.statuses]
    assert len(set(keys)) == len(keys)

    again = parse_events(load("aggregated-statuses.json"))
    assert {s.dedupe_key for s in again.statuses} == set(keys)


def test_a_status_names_its_message_and_its_recipient() -> None:
    status = parse_events(load("aggregated-statuses.json")).statuses[0]
    assert status.message_id == "wamid.EXAMPLEMESSAGE001"
    assert status.recipient_id == "15550000001"
    assert status.group_id == "120363000000000001@g.us"


# --- one delivery, end to end ------------------------------------------------------------------


def test_a_verified_delivery_is_handled_and_answered_200() -> None:
    body = raw("participants-update.json")
    seen: list[str] = []
    result = handle_delivery(
        body, sign(body, SECRET), SECRET, on_event=lambda e: seen.append(e.type)
    )
    assert result.status == 200
    assert seen == ["participant_joined", "participant_left"]
    assert json.loads(result.body)["events"] == 2


def test_an_unsigned_delivery_is_refused_and_never_parsed() -> None:
    body = raw("participants-update.json")
    seen: list[str] = []
    result = handle_delivery(
        body, "sha256=deadbeef", SECRET, on_event=lambda e: seen.append(e.type)
    )
    assert result.status == 403
    assert seen == []


def test_an_authentic_but_unreadable_body_is_answered_400() -> None:
    # Not 200. Answering 200 to stop the retries reports a failure as a success; Meta's own
    # guidance for this endpoint is a 400-level status for a request it cannot accept.
    body = b"{not json"
    result = handle_delivery(body, sign(body, SECRET), SECRET)
    assert result.status == 400
    assert "not JSON" in result.reason


def test_a_body_that_is_not_an_object_is_answered_400() -> None:
    body = b"[1, 2, 3]"
    result = handle_delivery(body, sign(body, SECRET), SECRET)
    assert result.status == 400


def test_a_handler_that_raises_is_answered_500_so_meta_retries() -> None:
    # The opposite of the malformed case, on purpose: this one might work next time.
    body = raw("participants-update.json")

    def explode(_event: object) -> None:
        raise RuntimeError("the database was having a moment")

    result = handle_delivery(body, sign(body, SECRET), SECRET, on_event=explode)
    assert result.status == 500
    assert "the database was having a moment" in result.reason


def test_statuses_reach_their_own_handler() -> None:
    body = raw("aggregated-statuses.json")
    seen: list[tuple[str, str, str]] = []
    result = handle_delivery(
        body, sign(body, SECRET), SECRET, on_status=lambda s: seen.append(s.dedupe_key)
    )
    assert result.status == 200
    assert len(seen) == 6


# --- the shipped default handler ------------------------------------------------------------


def test_the_default_handler_survives_every_shipped_event(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``log_event`` is what a reader wires up first, so it has to work on every event.

    It did not: it read an attribute ``GroupEvent`` does not have, so the endpoint answered
    500 to every group event and logged nothing. The suite had a test asserting that a handler
    which raises is answered 500 — and the handler that raised was the default one. This runs
    the real delivery path with the real handler, which is the gap that let it ship.
    """
    from whatsapp_cloud.server import log_event

    for name in sorted(p.name for p in FIXTURES.glob("*.json")):
        body = raw(name)
        result = handle_delivery(body, sign(body, SECRET), SECRET, on_event=log_event)
        assert result.status == 200, f"{name}: {result.reason}"

    for line in capsys.readouterr().out.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        assert set(row) == {"field", "group_id", "value"}
