"""Phone numbers, icons, the index, configuration, messages and the documented limits.

Small modules, but each one is where a particular kind of mistake would otherwise reach the
API: a truncated spreadsheet cell sent as a recipient, a PNG named ``.jpg``, an index whose
file was truncated by a crash, a boolean environment variable that somebody spelled ``maybe``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from whatsapp_cloud import phone
from whatsapp_cloud.config import DEFAULT_GRAPH_VERSION, Config, from_env
from whatsapp_cloud.errors import (
    ApiError,
    ConfigurationError,
    InvalidIconError,
    InvalidPhoneNumberError,
    LimitExceededError,
)
from whatsapp_cloud.index import JsonFileIndex, MemoryIndex, NoIndex, normalise_subject
from whatsapp_cloud.limits import DOCUMENTED_LIMITS, MAX_PARTICIPANTS, capacity_warning
from whatsapp_cloud.media import inspect, problems, validate
from whatsapp_cloud.messages import MAX_TEXT_CHARS, MessagesClient
from whatsapp_cloud.transport import DryRunTransport

ICONS = Path(__file__).resolve().parent.parent / "fixtures" / "icons"


# --- phone numbers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("+55 (62) 98459-0000", "5562984590000"),
        ("5562984590000", "5562984590000"),
        ("55-62-98459-0000", "5562984590000"),
        ("  +55 62 98459 0000  ", "5562984590000"),
        ("005562984590000", "5562984590000"),
        ("+1 (555) 000-0000", "15550000000"),
    ],
)
def test_a_number_becomes_digits_in_international_order(written: str, expected: str) -> None:
    assert phone.normalise(written) == expected


@pytest.mark.parametrize(
    "written",
    ["", "   ", "ramal 4471", "000", "12345", "5562984590000000000", "----"],
)
def test_what_is_not_a_number_is_refused_rather_than_mangled(written: str) -> None:
    # A wrong recipient is worse than a failed send, and every one of these survives a plain
    # strip-the-punctuation as a short run of digits.
    with pytest.raises(InvalidPhoneNumberError):
        phone.normalise(written)


def test_a_non_string_is_refused_clearly() -> None:
    with pytest.raises(InvalidPhoneNumberError, match="Expected a string"):
        phone.normalise(5562984590000)  # type: ignore[arg-type]


def test_normalising_a_list_keeps_order_and_drops_repeats() -> None:
    # Order carries meaning — the client first, then the team — so a set would lose it.
    assert phone.normalise_all(["+55 62 90000-0001", "005562900000002", "5562900000001"]) == [
        "5562900000001",
        "5562900000002",
    ]


def test_the_non_raising_form_is_for_filtering_a_column() -> None:
    assert phone.looks_like_phone("+55 62 98459-0000") is True
    assert phone.looks_like_phone("see attached") is False


# --- icons ----------------------------------------------------------------------------------


def test_a_valid_icon_passes_and_reports_what_it_is() -> None:
    facts = validate(ICONS / "valid-square.jpg")
    assert facts.image_format == "JPEG"
    assert facts.is_square is True
    assert facts.width >= 192


def test_a_png_named_jpg_is_refused_because_the_bytes_are_read() -> None:
    # Meta's own sample request uploads a .png in violation of the image/jpeg rule stated on
    # the same page, so neither the extension nor the example can be trusted.
    with pytest.raises(InvalidIconError, match="PNG"):
        validate(ICONS / "actually-a-png.jpg")


def test_a_rectangle_is_refused_and_says_its_dimensions() -> None:
    with pytest.raises(InvalidIconError, match="512x288"):
        validate(ICONS / "not-square.jpg")


def test_one_pixel_below_the_minimum_is_refused() -> None:
    with pytest.raises(InvalidIconError, match="192"):
        validate(ICONS / "too-small.jpg")


def test_a_missing_file_is_refused_before_anything_is_opened() -> None:
    with pytest.raises(InvalidIconError, match="not found"):
        validate(ICONS / "does-not-exist.jpg")


def test_a_file_that_is_not_an_image_is_refused(tmp_path: Path) -> None:
    fake = tmp_path / "icon.jpg"
    fake.write_text("this is not an image", encoding="utf-8")
    with pytest.raises(InvalidIconError, match="Not an image"):
        validate(fake)


def test_every_problem_is_reported_at_once_not_one_per_round_trip() -> None:
    found = problems(ICONS / "not-square.jpg")
    assert any("not square" in p for p in found)
    assert problems(ICONS / "valid-square.jpg") == []


def test_inspect_reports_facts_without_judging_them() -> None:
    facts = inspect(ICONS / "not-square.jpg")
    assert (facts.width, facts.height) == (512, 288)


# --- the index ------------------------------------------------------------------------------


def test_the_null_index_always_misses() -> None:
    index = NoIndex()
    index.remember("Subject", "a@g.us")
    assert index.lookup("Subject") is None


def test_the_subject_key_ignores_case_and_spacing() -> None:
    assert normalise_subject("  Example  Ltd — matter 4471 ") == normalise_subject(
        "example ltd — matter 4471"
    )


def test_the_memory_index_remembers_and_forgets() -> None:
    index = MemoryIndex()
    index.remember("Example Ltd", "a@g.us")
    assert index.lookup("example  ltd") == "a@g.us"
    index.forget("Example Ltd")
    assert index.lookup("Example Ltd") is None


def test_the_file_index_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "index.json"
    JsonFileIndex(path).remember("Example Ltd", "a@g.us")
    assert JsonFileIndex(path).lookup("Example Ltd") == "a@g.us"


def test_a_truncated_index_file_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    # A crash mid-write is exactly the situation that would otherwise create duplicates, so
    # a corrupt file has to degrade to "scan" rather than to "stop".
    path = tmp_path / "index.json"
    path.write_text('{"example ltd": "a@g', encoding="utf-8")
    assert JsonFileIndex(path).lookup("Example Ltd") is None


def test_the_index_file_is_written_whole(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "index.json"
    index = JsonFileIndex(path)
    index.remember("One", "1@g.us")
    index.remember("Two", "2@g.us")
    assert json.loads(path.read_text(encoding="utf-8")) == {"one": "1@g.us", "two": "2@g.us"}


# --- configuration ---------------------------------------------------------------------------


def test_the_dry_run_is_the_default() -> None:
    # The one default that matters: a library that talks to a messaging platform gets one
    # chance to send the wrong thing to a real person.
    assert Config(phone_number_id="1", token="t").dry_run is True
    assert from_env({"WA_PHONE_NUMBER_ID": "1", "WA_TOKEN": "t"}).dry_run is True


def test_sending_for_real_has_to_be_typed() -> None:
    assert (
        from_env({"WA_PHONE_NUMBER_ID": "1", "WA_TOKEN": "t", "WA_DRY_RUN": "false"}).dry_run
        is False
    )


def test_a_boolean_nobody_recognises_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ConfigurationError, match="boolean"):
        from_env({"WA_PHONE_NUMBER_ID": "1", "WA_TOKEN": "t", "WA_DRY_RUN": "maybe"})


def test_missing_credentials_are_named() -> None:
    with pytest.raises(ConfigurationError, match="WA_TOKEN"):
        from_env({"WA_PHONE_NUMBER_ID": "1"})


def test_the_package_can_be_inspected_without_credentials() -> None:
    config = from_env({}, require_credentials=False)
    assert config.dry_run is True
    assert config.graph_version == DEFAULT_GRAPH_VERSION


def test_the_graph_version_is_configuration_not_a_constant_in_the_code() -> None:
    assert from_env(
        {"WA_PHONE_NUMBER_ID": "1", "WA_TOKEN": "t", "WA_GRAPH_VERSION": "v27.0"}
    ).base_url.endswith("v27.0")


def test_a_version_that_is_not_version_shaped_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="v23.0"):
        Config(phone_number_id="1", token="t", graph_version="23")


def test_secrets_never_appear_in_a_repr_or_a_log_line() -> None:
    config = Config(phone_number_id="1", token="super-secret-token", app_secret="also-secret")
    assert "super-secret-token" not in repr(config)
    printed = json.dumps(config.redacted())
    assert "super-secret-token" not in printed
    assert "also-secret" not in printed


# --- messages ---------------------------------------------------------------------------------


def messages() -> tuple[MessagesClient, DryRunTransport]:
    transport = DryRunTransport()
    return MessagesClient("000000000000000", transport), transport


def test_a_group_template_is_addressed_to_the_group() -> None:
    client, transport = messages()
    client.template_to_group("a@g.us", "matter_opened_v1", variables=["4471"])
    body = transport.writes[0].json
    assert body["recipient_type"] == "group"
    assert body["to"] == "a@g.us"
    assert body["template"]["components"][0]["parameters"][0]["text"] == "4471"


def test_an_individual_template_normalises_the_number() -> None:
    client, transport = messages()
    client.template_to_person("+55 (62) 98459-0000", "invite_v1")
    assert transport.writes[0].json["to"] == "5562984590000"


def test_a_template_with_no_variables_carries_no_components() -> None:
    client, transport = messages()
    client.template_to_group("a@g.us", "notice_v1")
    assert "components" not in transport.writes[0].json["template"]


def test_an_empty_message_is_refused() -> None:
    client, _ = messages()
    with pytest.raises(LimitExceededError):
        client.text_to_group("a@g.us", "   ")


def test_an_oversized_message_is_refused_rather_than_truncated() -> None:
    client, _ = messages()
    with pytest.raises(LimitExceededError, match=str(MAX_TEXT_CHARS)):
        client.text_to_group("a@g.us", "x" * (MAX_TEXT_CHARS + 1))


def test_one_group_message_is_billed_once_per_recipient() -> None:
    # Meta's own worked example: a template to a group of five is charged five times.
    assert MessagesClient.billable_sends(5) == 5
    assert MessagesClient.billable_sends(0) == 0
    with pytest.raises(LimitExceededError):
        MessagesClient.billable_sends(-1)


# --- the limits themselves -----------------------------------------------------------------------


def test_every_documented_limit_names_where_it_came_from() -> None:
    for limit in DOCUMENTED_LIMITS:
        assert len(limit.source) > 20, limit.name


def test_the_undocumented_reading_of_the_cap_is_marked_as_such() -> None:
    # The claim that seven guests fit appears in no Meta page. If it is ever encoded as a
    # constant, this test should be the thing that objects.
    participants = next(
        limit for limit in DOCUMENTED_LIMITS if limit.name.startswith("participants")
    )
    assert participants.value == MAX_PARTICIPANTS
    assert "not documented" in participants.source


def test_the_capacity_warning_fires_only_at_the_cap() -> None:
    assert capacity_warning(MAX_PARTICIPANTS - 1) is None
    assert capacity_warning(MAX_PARTICIPANTS) is not None


def test_an_api_error_keeps_metas_own_code_and_message() -> None:
    error = ApiError(
        400, '{"error": {"message": "bad subject", "code": 100, "error_subcode": 2494}}', "groups"
    )
    assert error.code == 100
    assert error.subcode == 2494
    assert error.payload["error"]["message"] == "bad subject"


def test_an_unparseable_error_body_does_not_break_the_error() -> None:
    error = ApiError(502, "<html>bad gateway</html>", "groups")
    assert error.payload == {}
    assert error.code is None
    assert error.is_transient is True
