"""
CLI seam: `scan --output -` gives stdout to the JSON document alone.

Pinned here, at the full-command level (typer CliRunner over moto):
- stdout parses as one JSON envelope — no banner, panels, table, or any
  other rich decoration may leak into the pipe (`... --output - | jq`),
  and that holds with --debug too: diagnostics move to stderr in stdout
  mode, they are not discarded;
- nothing is written to disk in stdout mode — not even a generated
  output filename;
- the exit code stays 0 for a successful scan.
"""

import json
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

import aws_resource_inventory.cli as cli_module
import aws_resource_inventory.lib.outputs as outputs_module
import aws_resource_inventory.orchestrator as orchestrator_module
from aws_resource_inventory.cli import app
from aws_resource_inventory.lib.logging import get_output_console

runner = CliRunner()


@pytest.fixture(autouse=True)
def _restore_console_loudness() -> Any:
    """stdout mode quiets module-level consoles; un-quiet them so later
    tests in the same process render normally."""
    yield
    for decorative_console in (
        cli_module.console,
        orchestrator_module.console,
        outputs_module.console,
        get_output_console(),
    ):
        decorative_console.quiet = False


@pytest.fixture()
def hermetic_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """A throwaway AWS profile in tmp config files, so the CLI's profile
    resolution never reads the developer's real ~/.aws (SSO profiles
    there would try to refresh tokens and break under moto)."""
    config_file = tmp_path / "aws_config"
    config_file.write_text("[profile scanner-test]\nregion = us-east-1\n")
    credentials_file = tmp_path / "aws_credentials"
    credentials_file.write_text(
        "[scanner-test]\naws_access_key_id = testing\naws_secret_access_key = testing\n"
    )
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials_file))
    return "scanner-test"


@mock_aws
def test_output_dash_prints_only_the_json_envelope(
    monkeypatch: pytest.MonkeyPatch, hermetic_profile: str
) -> None:
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="stdout-bucket")

    def _no_disk_path(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("stdout mode must never compute a disk output path")

    monkeypatch.setattr(cli_module, "_generate_output_filename", _no_disk_path)

    result = runner.invoke(
        app,
        [
            "scan",
            "--regions",
            "us-east-1",
            "--service",
            "s3",
            "--profile",
            hermetic_profile,
            "--output",
            "-",
            "--no-cache",
        ],
    )

    assert result.exit_code == 0, result.output
    # json.loads over the *entire* stream is the purity assertion: any
    # rich decoration before or after the document would fail the parse.
    envelope = json.loads(result.stdout)
    assert envelope["schema_version"] == 1
    assert envelope["scan"]["source"] == "services"
    assert {r["type"] for r in envelope["resources"]} == {"s3:bucket"}
    assert {r["id"] for r in envelope["resources"]} == {"stdout-bucket"}


@mock_aws
def test_debug_diagnostics_go_to_stderr_not_into_the_pipe(
    monkeypatch: pytest.MonkeyPatch, hermetic_profile: str
) -> None:
    """--debug must not break `... --output - | jq`.

    Logging is configured before the scan starts, so pointing its console
    at stdout puts the debug banner in the pipe ahead of the document.
    In stdout mode the log console belongs on stderr — moved, not
    removed: a user piping to jq still needs to see what went wrong.
    """
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="stdout-bucket")

    result = runner.invoke(
        app,
        [
            "scan",
            "--regions",
            "us-east-1",
            "--service",
            "s3",
            "--profile",
            hermetic_profile,
            "--output",
            "-",
            "--no-cache",
            "--debug",
        ],
    )

    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["schema_version"] == 1


def test_stdout_mode_moves_the_log_console_to_stderr_and_keeps_it() -> None:
    """The handler is redirected, never dropped.

    Removing it would silence real diagnostics — a scan that fails on
    expired credentials must still say so on stderr.
    """
    from rich.logging import RichHandler

    from aws_resource_inventory.lib.logging import configure_logging

    logger = configure_logging(debug=False, log_file=None, log_to_stderr=True)

    rich_handlers = [
        handler
        for handler in logger.logger.handlers
        if isinstance(handler, RichHandler)
    ]
    assert rich_handlers, "stdout mode must keep a console handler, not remove it"
    assert all(handler.console.stderr for handler in rich_handlers)
