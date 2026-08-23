"""
CLI seam: a scan that finds nothing still writes the envelope.

The envelope is the tool's evidence that a scan ran (ADR-0005): who
scanned what, when, with which filters. A zero-resource scan has all of
that and must still produce a file — "no resources found" printed to a
terminal is not an artifact anyone can keep, diff, or hand to a
colleague.

Driven through the real typer command with AWS faked at two seams
(credentials and the region fan-out), so this pins the CLI's control
flow, not build_envelope's — which tests/test_envelope.py owns.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import aws_resource_inventory.cli as cli_module
from aws_resource_inventory.lib.records import CallerIdentity

IDENTITY = CallerIdentity(account="111122223333", partition="aws")


@pytest.fixture()
def empty_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valid credentials, a fan-out that finds nothing in any region."""
    # get_session runs before credential validation and reads the real
    # AWS config. The suite must pass with none (no profile, no
    # credentials), so the session is faked here too.
    monkeypatch.setattr(cli_module, "get_session", lambda profile: object())
    monkeypatch.setattr(
        cli_module,
        "validate_aws_credentials",
        lambda session, profile: (True, "Credentials valid", IDENTITY),
    )
    monkeypatch.setattr(
        cli_module,
        "perform_scan",
        lambda *args, **kwargs: {},
    )


def run_scan(output_file: Path, *extra: str) -> Any:
    return CliRunner().invoke(
        cli_module.app,
        [
            "scan",
            "--regions",
            "eu-central-1,eu-west-1",
            "--service",
            "s3",
            "--no-cache",
            "--output",
            str(output_file),
            *extra,
        ],
    )


def test_zero_resource_scan_writes_the_envelope(
    empty_scan: None, tmp_path: Path
) -> None:
    output_file = tmp_path / "scan.json"
    result = run_scan(output_file)

    assert result.exit_code == 0, result.output
    written = list(tmp_path.glob("*.json"))
    assert written, "a scan that found nothing wrote no file at all"

    envelope = json.loads(written[0].read_text())
    assert envelope["summary"]["total"] == 0
    assert envelope["resources"] == []


def test_zero_resource_envelope_still_names_every_scanned_region(
    empty_scan: None, tmp_path: Path
) -> None:
    output_file = tmp_path / "scan.json"
    run_scan(output_file)

    written = list(tmp_path.glob("*.json"))
    assert written, "a scan that found nothing wrote no file at all"
    envelope = json.loads(written[0].read_text())

    assert envelope["scan"]["regions"] == ["eu-central-1", "eu-west-1"]
    assert envelope["summary"]["by_region"] == {"eu-central-1": 0, "eu-west-1": 0}


def test_zero_resource_envelope_carries_the_scan_metadata(
    empty_scan: None, tmp_path: Path
) -> None:
    # The whole point of writing the file: it says what was looked for.
    output_file = tmp_path / "scan.json"
    run_scan(output_file)

    written = list(tmp_path.glob("*.json"))
    assert written, "a scan that found nothing wrote no file at all"
    scan = json.loads(written[0].read_text())["scan"]

    assert scan["account"] == "111122223333"
    assert scan["partition"] == "aws"
    assert scan["source"] == "services"
    assert scan["filters"]["services"] == ["s3"]
    assert scan["started_at"].endswith("Z")


def test_completion_line_counts_regions_scanned_not_regions_with_results(
    empty_scan: None, tmp_path: Path
) -> None:
    # Reachable only now that the zero-resource path runs through the
    # output flow: the count must be the regions asked for, not the
    # regions that happened to return something.
    result = run_scan(tmp_path / "scan.json")

    flat = " ".join(result.output.split())
    assert "Found 0 resources across 2 regions in" in flat, flat
    assert "across 0 region" not in flat, flat
