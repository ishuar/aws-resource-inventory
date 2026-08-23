"""
EFS scanner seam: services.efs_service against a fake AWS (moto).

Functional tests pin the result-key vocabulary and verify that a created
file system is found carrying the fields the future efs-empty waste rule
needs (SizeInBytes, NumberOfMountTargets). Processor tests pin the
flattened Resource vocabulary.
"""

from typing import Any

from aws_resource_inventory.lib.records import CallerIdentity, Resource
from aws_resource_inventory.services.efs_service import process_efs_output, scan_efs

REGION = "eu-central-1"
IDENTITY = CallerIdentity(account="123456789012", partition="aws")

FS_NAMED = {
    "FileSystemId": "fs-0123456789abcdef0",
    "FileSystemArn": (
        "arn:aws:elasticfilesystem:eu-central-1:123456789012"
        ":file-system/fs-0123456789abcdef0"
    ),
    "Name": "shared-data",
    "SizeInBytes": {"Value": 6144},
    "NumberOfMountTargets": 2,
}
FS_UNNAMED = {
    "FileSystemId": "fs-0fedcba9876543210",
    "FileSystemArn": (
        "arn:aws:elasticfilesystem:eu-central-1:123456789012"
        ":file-system/fs-0fedcba9876543210"
    ),
    "SizeInBytes": {"Value": 0},
    "NumberOfMountTargets": 0,
}


class TestScanEfs:
    def test_result_keys_and_created_file_system_are_found(
        self, aws_session: Any
    ) -> None:
        efs = aws_session.client("efs", region_name=REGION)
        fs_id = efs.create_file_system(
            CreationToken="token-1",
            Tags=[{"Key": "Name", "Value": "shared-data"}],
        )["FileSystemId"]

        result = scan_efs(aws_session, REGION)

        assert set(result) == {"file_systems"}
        file_systems = {fs["FileSystemId"]: fs for fs in result["file_systems"]}
        assert fs_id in file_systems
        found = file_systems[fs_id]
        # The future efs-empty waste rule needs these two fields.
        assert "SizeInBytes" in found
        assert "Value" in found["SizeInBytes"]
        assert "NumberOfMountTargets" in found
        assert found["NumberOfMountTargets"] == 0

    def test_empty_region_returns_all_keys_with_empty_lists(
        self, aws_session: Any
    ) -> None:
        result = scan_efs(aws_session, REGION)
        assert result == {"file_systems": []}


class TestProcessEfsOutput:
    def test_file_systems_flatten_with_pinned_vocabulary(self) -> None:
        flattened: list[Resource] = []

        process_efs_output(
            {"file_systems": [FS_NAMED, FS_UNNAMED]}, REGION, flattened, IDENTITY
        )

        assert [r.to_record() for r in flattened] == [
            {
                "region": REGION,
                "resource_name": "shared-data",
                "resource_type": "efs:file-system",
                "resource_id": "fs-0123456789abcdef0",
                "resource_arn": FS_NAMED["FileSystemArn"],
            },
            {
                # No Name field: fall back to the file system id.
                "region": REGION,
                "resource_name": "fs-0fedcba9876543210",
                "resource_type": "efs:file-system",
                "resource_id": "fs-0fedcba9876543210",
                "resource_arn": FS_UNNAMED["FileSystemArn"],
            },
        ]

    def test_empty_scan_appends_nothing(self) -> None:
        flattened: list[Resource] = []
        process_efs_output({"file_systems": []}, REGION, flattened, IDENTITY)
        assert flattened == []
