"""
Rendering seams pinned here:

Configuration panel — the Workers row describes the parallelism the scan
will actually use, not the --max-workers/--service-workers caps verbatim:
- worker counts are bounded by the actual work (regions to scan, services
  requested), with the flag caps as upper limits — mirroring the pools in
  aws_resource_inventory/orchestrator.py (regions) and aws_resource_inventory/lib/scan.py (services);
- tag mode and --all-services fan out per region only (Resource Groups
  API), so the services multiplier must not appear there;
- grammar stays correct for single region/service.

Results table — display only, the serialized envelope is pinned elsewhere:
- columns are Region · Type · Name · ID, in that order; no ARN column
  (reconstructible from account + region + type + id, and the JSON file
  still carries it);
- the scan's account id heads the table;
- a null resource_name renders as the id (the record itself stays null);
- an 80-column terminal still gets a legible table.
"""

from typing import Any

from rich.console import Console

import aws_resource_inventory.cli as cli_module
from aws_resource_inventory.lib.outputs import create_aws_resources_table
from aws_resource_inventory.lib.records import CallerIdentity, Resource


def render_config_panel(monkeypatch: Any, **overrides: Any) -> str:
    """Render the configuration panel and return its plain text."""
    params: dict[str, Any] = {
        "all_services": False,
        "tag_key": None,
        "tag_value": None,
        "services": ["efs"],
        "region_list": [
            "eu-north-1",
            "eu-west-1",
            "eu-west-2",
            "eu-west-3",
            "eu-central-1",
            "us-east-1",
            "us-east-2",
            "us-west-1",
            "us-west-2",
        ],
        "max_workers": 8,
        "service_workers": 4,
        "use_cache": True,
        "refresh": False,
        "refresh_interval": 10,
        "aws_profile": "test-profile",
        "debug": False,
    }
    params.update(overrides)

    capture_console = Console(width=200, force_terminal=False)
    monkeypatch.setattr(cli_module, "console", capture_console)
    with capture_console.capture() as capture:
        cli_module._display_configuration_panel(**params)
    return capture.get()


class TestWorkersRow:
    def test_single_service_shows_one_service_worker_not_the_cap(
        self, monkeypatch: Any
    ) -> None:
        # 9 regions capped at 8 workers; 1 service needs 1 worker, not 4.
        text = render_config_panel(monkeypatch, services=["efs"])
        assert "8 regions × 1 service" in text
        assert "4 services" not in text

    def test_counts_follow_the_actual_work_below_the_caps(
        self, monkeypatch: Any
    ) -> None:
        text = render_config_panel(
            monkeypatch,
            services=["ec2", "s3", "efs"],
            region_list=["eu-central-1", "us-east-1"],
        )
        assert "2 regions × 3 services" in text

    def test_caps_still_bound_large_work(self, monkeypatch: Any) -> None:
        regions = [f"region-{i}" for i in range(12)]
        services = ["ec2", "s3", "ecs", "efs", "elb", "vpc", "rds", "autoscaling"]
        text = render_config_panel(monkeypatch, services=services, region_list=regions)
        assert "8 regions × 4 services" in text

    def test_singular_grammar_for_one_region_and_one_service(
        self, monkeypatch: Any
    ) -> None:
        text = render_config_panel(
            monkeypatch, services=["efs"], region_list=["eu-central-1"]
        )
        assert "1 region × 1 service" in text
        assert "1 regions" not in text
        assert "1 services" not in text

    def test_tag_mode_has_no_services_multiplier(self, monkeypatch: Any) -> None:
        # Resource Groups API fans out per region only.
        text = render_config_panel(
            monkeypatch,
            tag_key="managed_by",
            tag_value="terraform",
            region_list=["eu-central-1", "us-east-1"],
        )
        assert "2 regions" in text
        assert "service" not in text.split("Tag Filter")[0].split("Workers")[1]

    def test_all_services_mode_has_no_services_multiplier(
        self, monkeypatch: Any
    ) -> None:
        text = render_config_panel(
            monkeypatch,
            all_services=True,
            tag_key="managed_by",
            region_list=["eu-central-1"],
        )
        assert "1 region" in text
        assert "×" not in text


IDENTITY = CallerIdentity(account="111122223333", partition="aws")


def make_resource(
    resource_id: str = "i-0abc123def456789a",
    resource_name: str | None = None,
) -> Resource:
    return Resource(
        region="eu-central-1",
        resource_type="ec2:instance",
        resource_id=resource_id,
        resource_arn=(f"arn:aws:ec2:eu-central-1:111122223333:instance/{resource_id}"),
        arn_source="constructed",
        resource_name=resource_name,
    )


def render_table(resources: list[Resource], width: int = 200) -> str:
    """Render the results table and return its plain text."""
    capture_console = Console(width=width, force_terminal=False)
    with capture_console.capture() as capture:
        capture_console.print(
            create_aws_resources_table(resources, debug=False, identity=IDENTITY)
        )
    return capture.get()


class TestResultsTable:
    def test_columns_are_region_type_name_id_in_order(self) -> None:
        text = render_table([make_resource(resource_name="web-server-prod-01")])
        header = next(line for line in text.splitlines() if "Region" in line)
        columns = ["Region", "Type", "Name", "ID"]
        positions = [header.index(column) for column in columns]
        assert positions == sorted(positions)

    def test_arn_column_is_gone(self) -> None:
        text = render_table([make_resource()])
        header = next(line for line in text.splitlines() if "Region" in line)
        assert "ARN" not in header
        assert "arn:aws:" not in text

    def test_account_header_appears_above_the_columns(self) -> None:
        text = render_table([make_resource()])
        assert "111122223333" in text
        assert text.index("111122223333") < text.index("Region")

    def test_null_name_cell_falls_back_to_the_id(self) -> None:
        text = render_table([make_resource(resource_name=None)])
        row = next(line for line in text.splitlines() if "i-0abc123def456789a" in line)
        # Name and ID cells both show the id — display fallback only.
        assert row.count("i-0abc123def456789a") == 2

    def test_real_name_is_shown_and_id_stays_in_its_own_column(self) -> None:
        text = render_table([make_resource(resource_name="web-server-prod-01")])
        row = next(line for line in text.splitlines() if "web-server-prod-01" in line)
        assert row.count("i-0abc123def456789a") == 1

    def test_narrow_terminal_render_stays_legible(self) -> None:
        text = render_table(
            [make_resource(resource_name="web-server-prod-01")], width=80
        )
        assert all(len(line) <= 80 for line in text.splitlines())
        assert "111122223333" in text
        for column in ["Region", "Type", "Name", "ID"]:
            assert column in text
