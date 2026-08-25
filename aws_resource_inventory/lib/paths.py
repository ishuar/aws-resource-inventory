"""
Where this tool puts files on disk — the one home for that answer.

Both the cache and the scan document hold the same sensitive material:
an account's ids, ARNs, names, and the account number in every ARN.
Neither belongs at a predictable path in a world-writable directory
(ADR-0008), so both resolve through here: an XDG variable when the user
sets one, a single documented fallback otherwise — the same on every
platform — and the same directory name underneath either way.
"""

import os
from pathlib import Path

APP_DIR_NAME = "aws-resource-inventory"


def user_dir(xdg_variable: str, fallback_base: Path) -> Path:
    """One application directory, XDG variable first.

    The fallback is used on every platform rather than branching per OS
    (macOS's ``~/Library`` included), so there is one path to document.

    A relative ``xdg_variable`` is ignored, as the XDG Base Directory
    spec requires: honouring one would put an account's inventory under
    whatever directory the scan happened to run from, which is the
    exposure ADR-0008 exists to close.
    """
    configured = os.environ.get(xdg_variable)
    base = Path(configured) if configured else fallback_base
    if not base.is_absolute():
        base = fallback_base
    return base / APP_DIR_NAME


def default_output_dir() -> Path:
    """The per-user directory holding scan documents.

    Not a shared temp directory. The document carries the same account
    inventory the cache does, and it is durable — no TTL retires it —
    so a predictable world-readable path exposes it indefinitely.
    """
    return user_dir("XDG_DATA_HOME", Path.home() / ".local" / "share")
