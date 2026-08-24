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
    "Tags": [{"Key": "Name", "Value": "shared-data"}],
    "SizeInBytes": {"Value": 6144},
    "NumberOfMountTargets": 2,
}
# AWS surfaces the Name tag as the Name field, so a file system tagged
# with its own id arrives with Name == FileSystemId. That is an id copy,
# not a name.
FS_NAME_REPEATS_ID = {
    "FileSystemId": "fs-00112233445566778",
    "FileSystemArn": (
        "arn:aws:elasticfilesystem:eu-central-1:123456789012"
        ":file-system/fs-00112233445566778"
    ),
    "Name": "fs-00112233445566778",
    "Tags": [{"Key": "Name", "Value": "fs-00112233445566778"}],
    "SizeInBytes": {"Value": 0},
    "NumberOfMountTargets": 0,
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
        # The name comes from Tags, not the Name field, so the shared
        # reader applies its id-repeat guard here like everywhere else.
        assert found["Tags"] == [{"Key": "Name", "Value": "shared-data"}]

    def test_empty_region_returns_all_keys_with_empty_lists(
        self, aws_session: Any
    ) -> None:
        result = scan_efs(aws_session, REGION)
        assert result == {"file_systems": []}


class TestProcessEfsOutput:
    def test_file_systems_flatten_with_pinned_vocabulary(self) -> None:
        flattened: list[Resource] = []

        process_efs_output(
            {"file_systems": [FS_NAMED, FS_UNNAMED, FS_NAME_REPEATS_ID]},
            REGION,
            flattened,
            IDENTITY,
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
                # No Name tag: null, never a copy of the id.
                "region": REGION,
                "resource_name": None,
                "resource_type": "efs:file-system",
                "resource_id": "fs-0fedcba9876543210",
                "resource_arn": FS_UNNAMED["FileSystemArn"],
            },
            {
                # A Name tag that merely repeats the id is not a name.
                "region": REGION,
                "resource_name": None,
                "resource_type": "efs:file-system",
                "resource_id": "fs-00112233445566778",
                "resource_arn": FS_NAME_REPEATS_ID["FileSystemArn"],
            },
        ]

    def test_empty_scan_appends_nothing(self) -> None:
        flattened: list[Resource] = []
        process_efs_output({"file_systems": []}, REGION, flattened, IDENTITY)
        assert flattened == []
