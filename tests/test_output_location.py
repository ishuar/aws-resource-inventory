"""
Output-location seam: where a scan writes its JSON document by default.

The document is the same data the cache holds — an account's ids, ARNs,
names, and the account number in every ARN — but durable rather than
expiring after ten minutes. ADR-0008 moved the cache off a predictable
path in world-writable /tmp; the same reasoning applies here.

Pinned here:
- the default lands under ``$XDG_DATA_HOME`` / ``~/.local/share``,
  never a shared temp directory;
- a path the user names with ``--output`` is returned untouched, and
  its permissions are left alone — we harden only the directory we own.
"""

import os
import tempfile
from pathlib import Path

import pytest

from aws_resource_inventory.cli import _generate_output_filename
from aws_resource_inventory.lib.outputs import (
    ensure_output_directory,
    write_document,
)
from aws_resource_inventory.lib.paths import default_output_dir

REGION = "eu-central-1"


class TestDefaultOutputDir:
    def test_respects_xdg_data_home(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        assert default_output_dir() == tmp_path / "xdg" / "aws-resource-inventory"

    def test_falls_back_to_the_home_data_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert (
            default_output_dir()
            == Path.home() / ".local" / "share" / "aws-resource-inventory"
        )

    def test_is_not_in_the_shared_temp_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A predictable path under a world-writable /tmp exposes the
        # account inventory to every local user, and the document is
        # durable — unlike the cache, no TTL retires it.
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        shared_tmp = Path(tempfile.gettempdir()).resolve()
        assert shared_tmp not in default_output_dir().resolve().parents

    def test_carries_the_installed_package_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        assert "aws_scanner" not in str(default_output_dir())


class TestGeneratedOutputPath:
    def test_default_path_lands_in_the_per_user_data_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))

        generated = _generate_output_filename(None, None, None, [REGION], ["s3"])

        assert generated.parent == default_output_dir()
        assert generated.name.endswith(".json")

    def test_default_path_is_never_under_the_shared_temp_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        shared_tmp = Path(tempfile.gettempdir()).resolve()

        generated = _generate_output_filename(None, None, None, [REGION], ["s3"])

        assert shared_tmp not in generated.resolve().parents

    def test_an_explicit_path_is_returned_untouched(self, tmp_path: Path) -> None:
        chosen = tmp_path / "somewhere" / "mine.json"
        assert _generate_output_filename(chosen, None, None, [REGION], ["s3"]) == chosen


class TestOutputDirectoryPermissions:
    def test_our_own_directory_is_created_owner_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        os.umask(0o022)

        ensure_output_directory(default_output_dir() / "scan.json")

        assert default_output_dir().stat().st_mode & 0o777 == 0o700

    def test_our_own_directory_is_tightened_if_it_already_exists_wide(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # mkdir applies its mode only when it creates the directory.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        our_dir = default_output_dir()
        our_dir.mkdir(parents=True)
        our_dir.chmod(0o777)

        ensure_output_directory(our_dir / "scan.json")

        assert our_dir.stat().st_mode & 0o777 == 0o700

    def test_a_user_named_directory_keeps_its_own_permissions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # --output names the path; its permissions are the user's call,
        # not ours to tighten behind their back.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        theirs = tmp_path / "theirs"
        theirs.mkdir()
        theirs.chmod(0o755)

        ensure_output_directory(theirs / "scan.json")

        assert theirs.stat().st_mode & 0o777 == 0o755


class TestDocumentPermissions:
    """A document we place is owner-only; one the user placed is theirs.

    ADR-0008 made cache entries 0600 rather than leaning on the parent
    directory alone — "defence in depth, and it costs one chmod". The
    document carries the same account inventory and is durable, so the
    same reasoning applies to it.
    """

    def test_a_document_at_the_default_path_is_owner_only(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        os.umask(0o022)
        document = default_output_dir() / "scan.json"
        ensure_output_directory(document)

        write_document(document, '{"schema_version": 1}')

        assert document.stat().st_mode & 0o777 == 0o600

    def test_a_document_at_a_user_named_path_keeps_the_usual_mode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # --output names the path; forcing 0600 on a file the user asked
        # for somewhere specific would be surprising, not secure.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        os.umask(0o022)
        theirs = tmp_path / "theirs"
        theirs.mkdir()
        document = theirs / "scan.json"

        write_document(document, '{"schema_version": 1}')

        assert document.stat().st_mode & 0o777 == 0o644
