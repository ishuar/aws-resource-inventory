"""
CLI seam: the exit code states how much of the scan actually ran.

ADR-0010's contract: 0 = complete, 3 = partial (some scan units errored,
envelope written), 1 = no usable inventory (every region wholly failed —
the envelope is still written first, as evidence of the failure). 2 is
never used: click owns it for usage errors.

Driven through the real typer command with AWS faked at three seams
(session, credentials, fan-out) — a green run here proves the CLI's
control flow, not the orchestrator's error collection, which
tests/test_scan_orchestration.py owns.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import aws_resource_inventory.cli as cli_module
from aws_resource_inventory.lib.envelope import ScanError
from aws_resource_inventory.lib.records import CallerIdentity

IDENTITY = CallerIdentity(account="111122223333", partition="aws")
REGIONS = ["eu-central-1", "eu-west-1"]


@pytest.fixture()
def fake_credentials(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    # get_session runs before credential validation and reads the real AWS
    # config; the suite must pass with none, so it is faked too.
    monkeypatch.setattr(cli_module, "get_session", lambda profile: object())
    monkeypatch.setattr(
        cli_module,
        "validate_aws_credentials",
        lambda session, profile: (True, "Credentials valid", IDENTITY),
    )
    return monkeypatch


def run_scan_with(
    monkeypatch: pytest.MonkeyPatch,
    scan_result: tuple[dict[str, Any], list[ScanError]],
    output_file: Path,
) -> Any:
    monkeypatch.setattr(cli_module, "perform_scan", lambda *args, **kwargs: scan_result)
    return CliRunner().invoke(
        cli_module.app,
        [
            "scan",
            "--regions",
            ",".join(REGIONS),
            "--service",
            "s3",
            "--no-cache",
            "--output",
            str(output_file),
        ],
    )


def written_envelope(tmp_path: Path) -> dict[str, Any]:
    written = list(tmp_path.glob("*.json"))
    assert written, "the scan wrote no envelope at all"
    envelope: dict[str, Any] = json.loads(written[0].read_text())
    return envelope


def test_complete_scan_exits_zero(
    fake_credentials: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = run_scan_with(fake_credentials, ({}, []), tmp_path / "scan.json")

    assert result.exit_code == 0, result.output
    assert written_envelope(tmp_path)["scan"]["errors"] == []


def test_partial_scan_exits_three_and_the_envelope_names_the_failure(
    fake_credentials: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    errors = [ScanError(region="eu-west-1", service="ec2", message="AccessDenied")]
    result = run_scan_with(fake_credentials, ({}, errors), tmp_path / "scan.json")

    assert result.exit_code == 3, result.output
    assert written_envelope(tmp_path)["scan"]["errors"] == [
        {"region": "eu-west-1", "service": "ec2", "message": "AccessDenied"}
    ]


def test_all_regions_failed_exits_one_but_still_writes_the_envelope(
    fake_credentials: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Total failure is still evidence: the envelope records what was
    # attempted and why nothing came back — then the exit code says
    # "no usable inventory".
    errors = [
        ScanError(region=region, service=None, message="timed out")
        for region in REGIONS
    ]
    result = run_scan_with(fake_credentials, ({}, errors), tmp_path / "scan.json")

    assert result.exit_code == 1, result.output
    envelope = written_envelope(tmp_path)
    assert envelope["summary"]["total"] == 0
    assert [e["region"] for e in envelope["scan"]["errors"]] == REGIONS


def test_one_region_wholly_failed_is_partial_not_total(
    fake_credentials: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Exit 1 is reserved for "every region failed"; one dead region with
    # the other alive is a partial inventory.
    errors = [ScanError(region="eu-west-1", service=None, message="timed out")]
    result = run_scan_with(fake_credentials, ({}, errors), tmp_path / "scan.json")

    assert result.exit_code == 3, result.output
