"""The transport: retries, the dry run, and what a fixture can express.

The retry tests use ``respx`` to stand in for the network, so the real ``httpx`` client, the
real status handling and the real backoff arithmetic all run — with the sleep injected, so
the suite does not actually wait a minute to prove it would have.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from whatsapp_cloud.config import DEFAULT_GRAPH_VERSION, Config
from whatsapp_cloud.errors import ApiError, NotEligibleForGroupsError
from whatsapp_cloud.transport import (
    DRY_RUN_GROUP_ID,
    DryRunTransport,
    HttpTransport,
    RecordedTransport,
    Request,
)

PHONE_ID = "000000000000000"


def config(**overrides: object) -> Config:
    base = {
        "phone_number_id": PHONE_ID,
        "token": "a-token",
        "dry_run": False,
        "retry_base_seconds": 1.0,
        "retry_max_seconds": 8.0,
    }
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


class Clock:
    def __init__(self) -> None:
        self.slept: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.slept.append(seconds)


def url(path: str, version: str = DEFAULT_GRAPH_VERSION) -> str:
    return f"https://graph.facebook.com/{version}/{path}"


# --- the real transport ---------------------------------------------------------------------


@respx.mock
def test_a_successful_call_is_decoded() -> None:
    respx.post(url(f"{PHONE_ID}/groups")).mock(
        return_value=httpx.Response(200, json={"id": "a@g.us"})
    )
    transport = HttpTransport(config())
    assert transport.send(Request("POST", f"{PHONE_ID}/groups", json={})) == {"id": "a@g.us"}


@respx.mock
def test_the_token_travels_as_a_bearer_header() -> None:
    route = respx.get(url(f"{PHONE_ID}/groups")).mock(return_value=httpx.Response(200, json={}))
    HttpTransport(config()).send(Request("GET", f"{PHONE_ID}/groups"))
    assert route.calls.last.request.headers["Authorization"] == "Bearer a-token"


@respx.mock
def test_an_empty_body_is_an_empty_mapping_not_a_crash() -> None:
    respx.delete(url("a@g.us")).mock(return_value=httpx.Response(200))
    assert HttpTransport(config()).send(Request("DELETE", "a@g.us")) == {}


@respx.mock
def test_a_rate_limit_is_retried_then_succeeds() -> None:
    respx.get(url(f"{PHONE_ID}/groups")).mock(
        side_effect=[
            httpx.Response(429, text="slow down"),
            httpx.Response(200, json={"data": {"groups": []}}),
        ]
    )
    clock = Clock()
    transport = HttpTransport(config(), sleep=clock)
    assert transport.send(Request("GET", f"{PHONE_ID}/groups")) == {"data": {"groups": []}}
    assert clock.slept == [1.0]


@respx.mock
def test_a_server_error_is_retried() -> None:
    respx.get(url(f"{PHONE_ID}/groups")).mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json={})]
    )
    clock = Clock()
    HttpTransport(config(), sleep=clock).send(Request("GET", f"{PHONE_ID}/groups"))
    assert len(clock.slept) == 1


@respx.mock
def test_a_client_error_is_not_retried() -> None:
    # A 400 will be a 400 next time too. Retrying it spends rate limit to learn nothing.
    route = respx.post(url(f"{PHONE_ID}/groups")).mock(
        return_value=httpx.Response(400, text='{"error": {"message": "bad subject", "code": 100}}')
    )
    clock = Clock()
    with pytest.raises(ApiError) as caught:
        HttpTransport(config(), sleep=clock).send(Request("POST", f"{PHONE_ID}/groups", json={}))
    assert route.call_count == 1
    assert clock.slept == []
    assert caught.value.code == 100


@respx.mock
def test_the_backoff_doubles_and_stops_at_the_cap() -> None:
    respx.get(url(f"{PHONE_ID}/groups")).mock(return_value=httpx.Response(500))
    clock = Clock()
    with pytest.raises(ApiError):
        HttpTransport(config(retry_attempts=5, retry_max_seconds=4.0), sleep=clock).send(
            Request("GET", f"{PHONE_ID}/groups")
        )
    assert clock.slept == [1.0, 2.0, 4.0, 4.0]


@respx.mock
def test_meta_s_retry_after_wins_over_our_schedule() -> None:
    # Backing off less than the server asked for is how a rate limit becomes a ban.
    respx.get(url(f"{PHONE_ID}/groups")).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, json={}),
        ]
    )
    clock = Clock()
    HttpTransport(config(), sleep=clock).send(Request("GET", f"{PHONE_ID}/groups"))
    assert clock.slept == [7.0]


@respx.mock
def test_an_unreadable_retry_after_falls_back_to_our_schedule() -> None:
    respx.get(url(f"{PHONE_ID}/groups")).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "soon"}),
            httpx.Response(200, json={}),
        ]
    )
    clock = Clock()
    HttpTransport(config(), sleep=clock).send(Request("GET", f"{PHONE_ID}/groups"))
    assert clock.slept == [1.0]


@respx.mock
def test_an_ineligible_number_gets_its_own_error() -> None:
    respx.post(url(f"{PHONE_ID}/groups")).mock(
        return_value=httpx.Response(
            403,
            text='{"error": {"message": "This phone number is not eligible to access Groups APIs", "code": 131215}}',
        )
    )
    with pytest.raises(NotEligibleForGroupsError):
        HttpTransport(config(), sleep=Clock()).send(Request("POST", f"{PHONE_ID}/groups", json={}))


@respx.mock
def test_a_transient_failure_is_never_read_as_ineligibility() -> None:
    # Status first: a 500 whose body happens to mention groups is still just a bad minute.
    respx.post(url(f"{PHONE_ID}/groups")).mock(
        return_value=httpx.Response(500, text="not eligible groups api")
    )
    with pytest.raises(ApiError) as caught:
        HttpTransport(config(retry_attempts=1), sleep=Clock()).send(
            Request("POST", f"{PHONE_ID}/groups", json={})
        )
    assert not isinstance(caught.value, NotEligibleForGroupsError)


@respx.mock
def test_a_non_json_response_says_so_rather_than_raising_a_value_error() -> None:
    respx.get(url(f"{PHONE_ID}/groups")).mock(
        return_value=httpx.Response(200, text="<html>502</html>")
    )
    with pytest.raises(ApiError, match="not JSON"):
        HttpTransport(config()).send(Request("GET", f"{PHONE_ID}/groups"))


@respx.mock
def test_the_configured_graph_version_is_the_one_called() -> None:
    respx.get(url(f"{PHONE_ID}/groups", version="v99.0")).mock(
        return_value=httpx.Response(200, json={})
    )
    HttpTransport(config(graph_version="v99.0")).send(Request("GET", f"{PHONE_ID}/groups"))


# --- the dry run ------------------------------------------------------------------------------


def test_the_dry_run_records_a_write_and_sends_nothing() -> None:
    transport = DryRunTransport()
    response = transport.send(Request("POST", "a/groups", json={"subject": "x"}))
    assert response["dry_run"] is True
    assert response["id"] == DRY_RUN_GROUP_ID
    assert len(transport.writes) == 1
    assert transport.writes[0].json == {"subject": "x"}


def test_the_dry_run_id_is_obviously_not_real() -> None:
    # A dry run that returned something id-shaped would let a caller store it, and a stored
    # fake id is a bug that surfaces days later.
    assert not DRY_RUN_GROUP_ID.isdigit()
    assert "dry" in DRY_RUN_GROUP_ID


def test_the_dry_run_passes_reads_through() -> None:
    reads = RecordedTransport({"GET a/groups": [{"data": {"groups": [{"id": "x@g.us"}]}}]})
    transport = DryRunTransport(reads=reads)
    assert transport.send(Request("GET", "a/groups"))["data"]["groups"][0]["id"] == "x@g.us"
    assert transport.writes == []


def test_the_dry_run_answers_reads_emptily_when_it_has_nowhere_to_read_from() -> None:
    assert DryRunTransport().send(Request("GET", "a/groups")) == {"data": []}


def test_the_dry_run_describes_a_write_without_printing_its_values() -> None:
    # A log line naming the fields is useful; one naming a client's data is a leak.
    request = Request("POST", "a/groups", json={"subject": "Example Ltd — matter 4471"})
    described = request.describe()
    assert "subject" in described
    assert "Example Ltd" not in described


# --- recordings --------------------------------------------------------------------------------


def test_a_recording_queue_replays_in_order_then_repeats_the_last() -> None:
    transport = RecordedTransport({"GET a": [{"n": 1}, {"n": 2}]})
    assert transport.send(Request("GET", "a")) == {"n": 1}
    assert transport.send(Request("GET", "a")) == {"n": 2}
    assert transport.send(Request("GET", "a")) == {"n": 2}


def test_an_unrecorded_call_raises_and_names_what_it_has() -> None:
    transport = RecordedTransport({"GET a": [{}]})
    with pytest.raises(ApiError, match="no recorded response"):
        transport.send(Request("GET", "b"))


def test_a_recording_can_express_a_failure() -> None:
    transport = RecordedTransport({"GET a": [{"__error__": {"status": 404, "body": "gone"}}]})
    with pytest.raises(ApiError) as caught:
        transport.send(Request("GET", "a"))
    assert caught.value.status == 404
