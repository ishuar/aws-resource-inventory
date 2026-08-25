"""
Logging seam: configuration belongs to ``configure_logging`` alone.

Pinned here:
- ``get_logger()`` never configures. Every scanning module calls it at
  import time (``logger = get_logger()``), so an auto-configure there
  locks in handler choices before the CLI has parsed a single flag —
  the flaw that made #61's ``--output -`` stdout pollution possible.
- ``configure_logging`` applies every argument on every call (last call
  wins). A configure that ignores its own arguments is the same bug
  class in a different coat.
"""

import logging
from typing import Any

import pytest
from rich.logging import RichHandler

import aws_resource_inventory.lib.logging as logging_module
from aws_resource_inventory.lib.logging import configure_logging, get_logger


@pytest.fixture()
def fresh_logging_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Reset the module singleton and the underlying stdlib logger, and
    restore both afterwards — the singleton is process-wide state shared
    with every other test in the run."""
    monkeypatch.setattr(logging_module, "_aws_logger", None)
    stdlib_logger = logging.getLogger("aws-inventory")
    saved_handlers = stdlib_logger.handlers[:]
    saved_level = stdlib_logger.level
    stdlib_logger.handlers.clear()
    stdlib_logger.setLevel(logging.NOTSET)
    yield
    stdlib_logger.handlers[:] = saved_handlers
    stdlib_logger.setLevel(saved_level)


def test_get_logger_does_not_configure(fresh_logging_state: Any) -> None:
    """A module-import call to get_logger() must leave configuration open.

    Installing handlers here would decide console placement before the
    CLI runs; configure_logging is the only thing allowed to decide.
    """
    get_logger()

    assert logging.getLogger("aws-inventory").handlers == []


def test_configure_logging_applies_settings_after_import_time_get_logger(
    fresh_logging_state: Any,
) -> None:
    """The CLI's configure call is authoritative even though modules
    already fetched the logger at import time."""
    get_logger()

    logger = configure_logging(debug=False, log_file=None, log_to_stderr=True)

    rich_handlers = [
        handler
        for handler in logger.logger.handlers
        if isinstance(handler, RichHandler)
    ]
    assert rich_handlers, "configure_logging must install the console handler"
    assert all(handler.console.stderr for handler in rich_handlers)


def test_configure_logging_is_last_call_wins(fresh_logging_state: Any) -> None:
    """Every configure call applies its arguments — none may be ignored
    because an earlier call already ran."""
    configure_logging(debug=True, log_file=None)
    logger = configure_logging(debug=False, log_file=None)

    assert logger.logger.level == logging.INFO
    assert not logger.is_debug_enabled()
