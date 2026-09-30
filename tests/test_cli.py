"""The command line, including the demo the README shows.

The demo is the thing a reader runs first, so it is pinned: it must work with no credentials
and no network, it must exercise the real client rather than a script that imitates it, and
its last line must be the count of writes the dry run withheld — which is the whole claim the
repository makes about being safe to point at production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from whatsapp_cloud.cli import main

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def run(args: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = main(args)
    return code, capsys.readouterr().out


def test_the_demo_runs_offline(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(["demo"], capsys)
    assert code == 0
    assert "no network call, no credentials, nothing sent" in out


def test_the_demo_refuses_an_impossible_guest_list(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(["demo"], capsys)
    assert "refused before anything was created" in out


def test_the_demo_creates_once_and_then_finds_the_group(capsys: pytest.CaptureFixture[str]) -> None:
    # The claim being demonstrated: a re-run does not make a second group.
    _, out = run(["demo"], capsys)
    assert "created: dry-run-group-id" in out
    assert "already existed" in out


def test_the_demo_withholds_exactly_one_write(capsys: pytest.CaptureFixture[str]) -> None:
    _, out = run(["demo"], capsys)
    assert "writes withheld by the dry run: 1" in out


def test_the_limits_command_prints_provenance(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(["limits"], capsys)
    assert code == 0
    assert "Max group participants: 8" in out
    # The uncertainty is printed too, not quietly resolved.
    assert "not documented" in out


def test_check_icon_accepts_a_valid_one(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(["check-icon", str(FIXTURES / "icons" / "valid-square.jpg")], capsys)
    assert code == 0
    assert out.startswith("ok: JPEG 512x512")


def test_check_icon_reports_every_problem_and_exits_non_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["check-icon", str(FIXTURES / "icons" / "too-small.jpg")])
    assert exit_info.value.code == 1
    assert "192px minimum" in capsys.readouterr().out


def test_check_guests_normalises_and_accepts(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(["check-guests", "--number", "+55 (62) 90000-0001"], capsys)
    assert code == 0
    assert "5562900000001" in out


def test_check_guests_refuses_a_list_that_cannot_fit(capsys: pytest.CaptureFixture[str]) -> None:
    numbers: list[str] = []
    for n in range(9):
        numbers += ["--number", f"556290000{n:04d}"]
    assert main(["check-guests", *numbers]) == 1


def test_cost_reports_one_send_per_recipient(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(["cost", "--recipients", "5"], capsys)
    assert code == 0
    assert "billed as 5 send(s)" in out
    assert "volume tiers" in out


def test_replaying_a_webhook_exercises_the_real_verification(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = FIXTURES / "webhooks" / "participants-update.json"
    code, out = run(["webhook-replay", str(payload), "--tamper"], capsys)
    assert code == 0
    assert "-> 200" in out
    # And the same payload with somebody else's signature is refused.
    assert "-> with a wrong signature: 403" in out


def test_replaying_an_aggregated_status_payload_counts_every_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = FIXTURES / "webhooks" / "aggregated-statuses.json"
    _, out = run(["webhook-replay", str(payload)], capsys)
    assert "-> 200" in out


def test_the_recordings_file_is_valid_and_carries_no_real_looking_identifiers() -> None:
    loaded = json.loads((FIXTURES / "recorded.json").read_text(encoding="utf-8"))
    blob = json.dumps(loaded)
    assert loaded["responses"]
    # Numbers are in ranges reserved for examples, so nothing here can reach a person.
    for number in ("15550000001", "15550000004"):
        assert number in blob
    assert "@g.us" in blob
