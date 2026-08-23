"""
Cache seam: aws_resource_inventory.lib.cache

The interface every scan path relies on: get_cache_key / cache_result /
get_cached_result. Planned refactors must keep keys stable (a changed key
format silently invalidates every user's warm cache) and keep the
store→retrieve round-trip lossless.
"""

import tempfile
import time
from datetime import timedelta
from pathlib import Path

import pytest

from aws_resource_inventory.lib.cache import (
    cache_result,
    default_cache_dir,
    get_cache_key,
    get_cached_result,
)

REGION = "eu-central-1"


class TestCacheKey:
    def test_key_is_stable_across_versions(self) -> None:
        # Known-good literals: md5("eu-central-1:ec2::") and
        # md5("eu-central-1:ec2:env:prod"). If this test fails the key
        # format changed and every existing cache entry is invalidated —
        # that must be a deliberate decision, not a refactor side effect.
        assert get_cache_key(REGION, "ec2") == "d02463c473244acc460d09c4b6c28e99"
        assert (
            get_cache_key(REGION, "ec2", "env", "prod")
            == "928e52ecd1ebf3bddfe695b8deaefe19"
        )

    def test_tags_participate_in_the_key(self) -> None:
        base = get_cache_key(REGION, "ec2")
        assert get_cache_key(REGION, "ec2", "env") != base
        assert get_cache_key(REGION, "ec2", None, "prod") != base
        assert get_cache_key(REGION, "ec2", "env", "prod") != get_cache_key(
            REGION, "ec2", "env"
        )

    def test_region_and_service_participate_in_the_key(self) -> None:
        assert get_cache_key(REGION, "ec2") != get_cache_key("us-east-1", "ec2")
        assert get_cache_key(REGION, "ec2") != get_cache_key(REGION, "s3")


class TestCacheRoundTrip:
    def test_miss_when_nothing_stored(self) -> None:
        assert get_cached_result(REGION, "ec2") is None

    def test_store_then_retrieve_is_lossless(self) -> None:
        payload = {"instances": [{"InstanceId": "i-123"}], "volumes": []}
        cache_result(REGION, "ec2", payload)
        assert get_cached_result(REGION, "ec2") == payload

    def test_entries_are_isolated_by_tags(self) -> None:
        cache_result(REGION, "ec2", {"instances": ["untagged"]})
        cache_result(REGION, "ec2", {"instances": ["tagged"]}, "env", "prod")
        assert get_cached_result(REGION, "ec2") == {"instances": ["untagged"]}
        assert get_cached_result(REGION, "ec2", "env", "prod") == {
            "instances": ["tagged"]
        }

    def test_expired_entry_is_a_miss(self, isolated_cache: Path) -> None:
        import os

        cache_result(REGION, "ec2", {"instances": []})
        # Age the cache file past the 10-minute TTL via its mtime,
        # which is what the TTL check reads.
        cache_file = next(isolated_cache.glob("*.pkl"))
        expired = time.time() - timedelta(minutes=11).total_seconds()
        os.utime(cache_file, (expired, expired))

        assert get_cached_result(REGION, "ec2") is None

    def test_corrupt_entry_is_a_miss_not_a_crash(self, isolated_cache: Path) -> None:
        cache_result(REGION, "ec2", {"instances": []})
        cache_file = next(isolated_cache.glob("*.pkl"))
        cache_file.write_bytes(b"not a pickle")

        assert get_cached_result(REGION, "ec2") is None


class TestCacheLocation:
    def test_cache_dir_is_not_in_the_shared_temp_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        # The cache holds pickled scan results — an account's resource
        # inventory. A predictable path under a world-writable /tmp lets
        # any local user read it, or pre-create the directory and feed
        # pickle.load a payload of their choosing.
        shared_tmp = Path(tempfile.gettempdir()).resolve()
        assert shared_tmp not in default_cache_dir().resolve().parents

    def test_cache_dir_respects_xdg_cache_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        assert default_cache_dir() == tmp_path / "xdg" / "aws-resource-inventory"

    def test_cache_dir_falls_back_to_the_home_cache_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert default_cache_dir() == Path.home() / ".cache" / "aws-resource-inventory"

    def test_cache_dir_carries_the_installed_package_name(self) -> None:
        # aws-scanner was retired before the first release (CLAUDE.md);
        # no runtime path may still spell it.
        assert "aws_scanner" not in str(default_cache_dir())

    def test_store_creates_the_whole_directory_tree(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # ~/.cache may not exist yet, unlike /tmp — so the store must
        # create parents, not just the leaf.
        import aws_resource_inventory.lib.cache as cache_module

        nested = tmp_path / "missing" / "parent" / "aws-resource-inventory"
        monkeypatch.setattr(cache_module, "CACHE_DIR", nested)

        cache_result(REGION, "ec2", {"instances": [{"InstanceId": "i-1"}]})

        assert nested.is_dir()
        assert get_cached_result(REGION, "ec2") == {
            "instances": [{"InstanceId": "i-1"}]
        }


class TestCachePermissions:
    """The cache is owner-only, not owner-only-if-we-happened-to-create-it.

    The threat ADR-0008 names is another local user reading pickled scan
    results. ``mkdir(mode=...)`` only applies the mode when it actually
    creates the directory, so a directory left behind at a wide mode —
    by an earlier version, or by whoever got there first — keeps it.
    """

    def test_store_creates_the_directory_owner_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import aws_resource_inventory.lib.cache as cache_module

        cache_dir = tmp_path / "aws-resource-inventory"
        monkeypatch.setattr(cache_module, "CACHE_DIR", cache_dir)

        cache_result(REGION, "ec2", {"instances": []})

        assert cache_dir.stat().st_mode & 0o777 == 0o700

    def test_store_tightens_a_pre_existing_wide_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import aws_resource_inventory.lib.cache as cache_module

        cache_dir = tmp_path / "aws-resource-inventory"
        cache_dir.mkdir()
        cache_dir.chmod(0o777)
        monkeypatch.setattr(cache_module, "CACHE_DIR", cache_dir)

        cache_result(REGION, "ec2", {"instances": []})

        assert cache_dir.stat().st_mode & 0o777 == 0o700

    def test_entries_are_readable_only_by_their_owner(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The directory is the outer guard, but a file written 0644 is
        # exposed the moment the directory guard is weakened or the
        # cache is copied. Defence in depth, and it costs one chmod.
        import aws_resource_inventory.lib.cache as cache_module

        cache_dir = tmp_path / "aws-resource-inventory"
        monkeypatch.setattr(cache_module, "CACHE_DIR", cache_dir)

        cache_result(REGION, "ec2", {"instances": []})

        entries = list(cache_dir.glob("*.pkl"))
        assert entries, "the store wrote no entry"
        assert all(entry.stat().st_mode & 0o777 == 0o600 for entry in entries)
