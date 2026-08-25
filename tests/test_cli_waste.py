"""
CLI seam: the `waste` command — scan, judge, report, exit honestly.

Driven through the real typer command with AWS faked at the seams
(session, credentials, fan-out, and the tag fetch): a green run here
proves the command's control flow — provider wiring, the findings
document on disk and on stdout, the exit-code contract shared with scan
(ADR-0010) — not the rules themselves, which tests/test_waste_rules.py
owns.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import aws_resource_inventory.cli as cli_module
import aws_resource_inventory.lib.outputs as outputs_module
import aws_resource_inventory.orchestrator as orchestrator_module
from aws_resource_inventory.lib.envelope import ScanError
from aws_resource_inventory.lib.logging import get_output_console
from aws_resource_inventory.lib.records import CallerIdentity

runner = CliRunner()

IDENTITY = CallerIdentity(account="111122223333", partition="aws")
REGION = "eu-central-1"
VOLUME_ARN = f"arn:aws:ec2:{REGION}:111122223333:volume/vol-1"


def ec2_region_data(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "instances": [],
        "volumes": [],
        "security_groups": [],
        "amis": [],
        "snapshots": [],
        "addresses": [],
    }
    base.update(sections)
    return {"ec2": base}


AVAILABLE_VOLUME = {"VolumeId": "vol-1", "State": "available", "Size": 8}


@pytest.fixture(autouse=True)
def _restore_console_loudness() -> Any:
    yield
    for decorative_console in (
        cli_module.console,
        orchestrator_module.console,
        outputs_module.console,
        get_output_console(),
    ):
        decorative_console.quiet = False


@pytest.fixture()
def fake_aws(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setattr(cli_module, "get_session", lambda profile: object())
    monkeypatch.setattr(
        cli_module,
        "validate_aws_credentials",
        lambda session, profile: (True, "Credentials valid", IDENTITY),
    )
    return monkeypatch


def run_waste(
    monkeypatch: pytest.MonkeyPatch,
    scan_result: tuple[dict[str, Any], list[ScanError]],
    *args: str,
) -> Any:
    monkeypatch.setattr(cli_module, "perform_scan", lambda *a, **k: scan_result)
    return runner.invoke(
        cli_module.app,
        ["waste", "--regions", REGION, "--no-cache", *args],
    )


def test_findings_document_is_written_and_exit_is_zero(
    fake_aws: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "findings.json"
    result = run_waste(
        fake_aws,
        ({REGION: ec2_region_data(volumes=[AVAILABLE_VOLUME])}, []),
        "--output",
        str(out),
    )

    assert result.exit_code == 0, result.output
    document = json.loads(out.read_text())
    assert document["schema_version"] == 1
    assert document["scan"]["errors"] == []
    assert document["summary"]["by_confidence"]["certain"] == 1
    (record,) = document["findings"]
    assert record["rule"] == "ebs-unattached"
    assert record["arn"] == VOLUME_ARN
    # The judgment inputs are recorded for reproducibility.
    assert document["waste"]["thresholds"]["stopped_days"] == 90


def test_stdout_mode_emits_only_the_document(
    fake_aws: pytest.MonkeyPatch,
) -> None:
    result = run_waste(
        fake_aws,
        ({REGION: ec2_region_data(volumes=[AVAILABLE_VOLUME])}, []),
        "--output",
        "-",
    )

    assert result.exit_code == 0, result.output
    # json.loads over the entire stream is the purity assertion: any
    # decorative line before or after the document breaks the pipe.
    document = json.loads(result.stdout)
    assert [f["rule"] for f in document["findings"]] == ["ebs-unattached"]


def test_partial_scan_exits_three_and_marks_the_document(
    fake_aws: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = tmp_path / "findings.json"
    errors = [ScanError(region=REGION, service="rds", message="AccessDenied")]
    result = run_waste(
        fake_aws,
        ({REGION: ec2_region_data(volumes=[AVAILABLE_VOLUME])}, errors),
        "--output",
        str(out),
    )

    assert result.exit_code == 3, result.output
    document = json.loads(out.read_text())
    assert document["scan"]["errors"] == [
        {"region": REGION, "service": "rds", "message": "AccessDenied"}
    ]
    # The ec2 rule still judged the data that did arrive.
    assert document["summary"]["by_rule"]["ebs-unattached"] == 1


def test_trust_tags_without_managed_tag_is_refused(
    fake_aws: pytest.MonkeyPatch,
) -> None:
    result = run_waste(fake_aws, ({}, []), "--trust-tags")
    assert result.exit_code == 1
    assert "--managed-tag" in result.output


def test_managed_tag_runs_the_drift_provider(
    fake_aws: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The volume is inventoried but not in the tagged set -> drift.
    fake_aws.setattr(
        cli_module,
        "get_all_tagged_resources_across_services",
        lambda session, region, key, value: {},
    )
    out = tmp_path / "findings.json"
    result = run_waste(
        fake_aws,
        (
            {
                REGION: ec2_region_data(
                    volumes=[{"VolumeId": "vol-1", "State": "in-use"}]
                )
            },
            [],
        ),
        "--managed-tag",
        "managed_by=terraform",
        "--output",
        str(out),
    )

    assert result.exit_code == 0, result.output
    document = json.loads(out.read_text())
    (record,) = document["findings"]
    assert record["rule"] == "tag-drift"
    assert record["confidence"] == "review"
    assert document["waste"]["managed_tag"] == "managed_by=terraform"
    assert document["summary"]["by_rule"]["tag-drift"] == 1


def test_failed_tag_fetch_skips_drift_for_the_region_and_exits_three(
    fake_aws: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A failed tagged-set fetch must not read as "everything drifted":
    # the drift provider is skipped there and the failure is recorded.
    def boom(session: Any, region: str, key: Any, value: Any) -> dict[str, Any]:
        raise RuntimeError("throttled")

    fake_aws.setattr(cli_module, "get_all_tagged_resources_across_services", boom)
    out = tmp_path / "findings.json"
    result = run_waste(
        fake_aws,
        (
            {
                REGION: ec2_region_data(
                    volumes=[{"VolumeId": "vol-1", "State": "in-use"}]
                )
            },
            [],
        ),
        "--managed-tag",
        "managed_by",
        "--output",
        str(out),
    )

    assert result.exit_code == 3, result.output
    document = json.loads(out.read_text())
    assert document["findings"] == []
    assert document["scan"]["errors"] == [
        {"region": REGION, "service": "tagging", "message": "throttled"}
    ]
