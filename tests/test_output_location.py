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
from collections.abc import Iterator
from pathlib import Path

import pytest

from aws_resource_inventory.cli import _generate_output_filename
from aws_resource_inventory.lib.cache import default_cache_dir
from aws_resource_inventory.lib.outputs import (
    ensure_output_directory,
    write_document,
)
from aws_resource_inventory.lib.paths import default_output_dir, user_dir

REGION = "eu-central-1"


@pytest.fixture(autouse=True)
def _fixed_umask() -> Iterator[None]:
    """Pin umask for the permission assertions, then put it back.

    ``os.umask`` is process-global with no getter that does not also
    set, so a test that leaves it changed silently rewrites the file
    modes every later test observes.
    """
    original = os.umask(0o022)
    yield
    os.umask(original)


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

    def test_a_relative_xdg_value_is_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The XDG spec: a value that is not an absolute path is invalid
        # and must be ignored. Honouring one would put the account
        # inventory under whatever directory the scan was run from.
        monkeypatch.setenv("XDG_DATA_HOME", "relative-data")
        monkeypatch.setenv("XDG_CACHE_HOME", "relative-cache")

        assert default_output_dir().is_absolute()
        assert default_cache_dir().is_absolute()
        assert user_dir("XDG_DATA_HOME", Path.home()).is_absolute()

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

        ensure_output_directory(default_output_dir() / "scan.json", ours=True)

        assert default_output_dir().stat().st_mode & 0o777 == 0o700

    def test_our_own_directory_is_tightened_if_it_already_exists_wide(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # mkdir applies its mode only when it creates the directory.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        our_dir = default_output_dir()
        our_dir.mkdir(parents=True)
        our_dir.chmod(0o777)

        ensure_output_directory(our_dir / "scan.json", ours=True)

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

        ensure_output_directory(theirs / "scan.json", ours=False)

        assert theirs.stat().st_mode & 0o777 == 0o755

    def test_a_user_named_directory_is_created_with_the_usual_mode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Creating it does not make it ours. A directory the user named
        # gets the mode any other tool would give it, not 0700.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        theirs = tmp_path / "theirs-new"

        ensure_output_directory(theirs / "scan.json", ours=False)

        assert theirs.stat().st_mode & 0o777 == 0o755

    def test_ownership_follows_the_caller_not_the_path_spelling(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The caller knows whether it chose the path; comparing strings
        # does not. This spelling names our own directory, so a path
        # comparison would call it ours and silently retighten a
        # directory the user asked for by name.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        our_dir = default_output_dir()
        our_dir.mkdir(parents=True)
        our_dir.chmod(0o755)
        (tmp_path / "xdg" / "sibling").mkdir()
        alias = tmp_path / "xdg" / "sibling" / ".." / "aws-resource-inventory"

        ensure_output_directory(alias / "scan.json", ours=False)

        assert our_dir.stat().st_mode & 0o777 == 0o755

    def test_a_chmod_failure_does_not_lose_a_finished_scan(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The scan has already run by the time this is called, and the
        # document itself is written 0600 either way. Failing to tighten
        # the directory is worth saying out loud, not worth discarding
        # the results over.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        our_dir = default_output_dir()
        our_dir.mkdir(parents=True)

        def refuse(self: Path, mode: int) -> None:
            raise PermissionError("read-only file system")

        monkeypatch.setattr(Path, "chmod", refuse)

        ensure_output_directory(our_dir / "scan.json", ours=True)


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
        document = default_output_dir() / "scan.json"
        ensure_output_directory(document, ours=True)

        write_document(document, '{"schema_version": 1}', ours=True)

        assert document.stat().st_mode & 0o777 == 0o600

    def test_a_document_at_a_user_named_path_keeps_the_usual_mode(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # --output names the path; forcing 0600 on a file the user asked
        # for somewhere specific would be surprising, not secure.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        theirs = tmp_path / "theirs"
        theirs.mkdir()
        document = theirs / "scan.json"

        write_document(document, '{"schema_version": 1}', ours=False)

        assert document.stat().st_mode & 0o777 == 0o644
