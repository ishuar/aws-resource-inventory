"""
AWS Resource Inventory Logging Module
----------------------------------

Unified logging system for AWS Resource Inventory with all features:
- Rich console output with proper formatting
- File logging for debug sessions with caller information
- Console/progress separation to prevent Live display conflicts
- Debug mode switching
- AWS library noise suppression
- Correct caller context (file:function:line)
- Performance timing capabilities
- AWS API call tracking
- Enhanced boto3 request/response logging
"""

import logging
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler
from rich.traceback import install

# Default debug log directory (easily configurable)
DEFAULT_DEBUG_LOG_DIR = Path.cwd() / ".debug_logs"


class SimpleTimer:
    """Simple context manager for timing operations."""

    def __init__(self, logger: logging.Logger, operation: str):
        self.logger = logger
        self.operation = operation
        self.start_time: float | None = None

    def __enter__(self) -> "SimpleTimer":
        self.start_time = time.perf_counter()
        self.logger.debug("Starting: %s", self.operation, stacklevel=3)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self.start_time is not None:
            duration = time.perf_counter() - self.start_time
            if exc_type is None:
                self.logger.debug(
                    "Completed: %s (%.3fs)", self.operation, duration, stacklevel=3
                )
            else:
                self.logger.error(
                    "Failed: %s (%.3fs) - %s",
                    self.operation,
                    duration,
                    exc_val,
                    stacklevel=3,
                )


class AWSLogger:
    """
    AWS Scanner Logger - Unified logging system.

    Features:
    - Rich console output
    - File logging with caller information
    - Live display isolation
    - Debug mode switching
    - Library suppression
    - AWS API call tracking
    """

    def __init__(self, name: str = "aws-inventory"):
        self.name = name
        self.logger = logging.getLogger(name)
        self._debug_mode = False
        self._verbose_mode = False
        self._log_file: Path | None = None
        self._progress_console: Console | None = None

    def configure(
        self,
        debug: bool = False,
        log_file: Path | None = None,
        verbose: bool = False,
        log_to_stderr: bool = False,
    ) -> None:
        """One-method setup for all logging needs.

        ``log_to_stderr`` is for ``--output -``, where stdout carries the
        JSON document and nothing else. Diagnostics move to stderr rather
        than being dropped: a scan that fails still has to say why.
        """
        # Every call applies every argument — last call wins. A guard
        # that skipped "already configured" is what let --output - keep
        # logging to stdout (#61, ADR-0007).
        self._debug_mode = debug
        self._verbose_mode = verbose
        self._log_file = log_file

        # Clear any existing handlers
        self.logger.handlers.clear()

        # Set log level
        self.logger.setLevel(logging.DEBUG if debug else logging.INFO)

        # Install rich traceback for better error display
        if debug:
            install(show_locals=True)

        # Regular logs go to stdout so they cannot interleave with the
        # Live progress display, which owns stderr. Under --output -
        # stdout is reserved for the JSON document and the progress
        # display is silenced, so stderr is free and logs move there.
        console = Console(
            stderr=log_to_stderr,
            force_terminal=True,
            legacy_windows=False,
            width=None,
        )

        console_handler = RichHandler(
            console=console,
            show_time=True,
            show_path=debug,
            markup=False,
            rich_tracebacks=True,
            tracebacks_show_locals=debug,
            keywords=[],
            omit_repeated_times=False,
        )

        console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(console_handler)

        # File handler for debug mode
        if debug and log_file:
            self._setup_file_logging(log_file)

        # Configure AWS library logging based on verbose mode
        if verbose and debug:
            self._enable_detailed_aws_logging()
        else:
            self._suppress_noisy_loggers()

        # Enable boto3 request/response logging in debug mode
        if debug:
            self._enable_boto3_logging()

        if debug:
            self.logger.debug("Simplified logging configured")
            self.logger.debug("Debug mode: %s", debug)
            self.logger.debug("Verbose mode: %s", verbose)
            if log_file:
                self.logger.debug("Log file: %s", log_file)

    def _setup_file_logging(self, log_file: Path) -> None:
        """Setup file logging with detailed format."""
        try:
            # Ensure log directory exists
            log_file.parent.mkdir(parents=True, exist_ok=True)

            file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
            file_handler.setLevel(logging.DEBUG)

            # Detailed format for file logging - shows complete file paths and caller info
            file_formatter = logging.Formatter(
                "%(asctime)s.%(msecs)03d | %(name)s | %(levelname)-8s | %(pathname)s:%(funcName)s:%(lineno)d | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            file_handler.setFormatter(file_formatter)
            self.logger.addHandler(file_handler)

            # Log session start
            self.logger.debug("=" * 80)
            self.logger.debug(
                "AWS Resource Inventory Debug Session Started (Simplified Logger)"
            )
            self.logger.debug("=" * 80)

        except OSError as e:
            self.logger.warning("Could not setup file logging: %s", e)

    def _suppress_noisy_loggers(self) -> None:
        """Suppress noisy third-party library loggers."""
        noisy_loggers = [
            "boto3",
            "botocore",
            "urllib3",
            "requests",
            "s3transfer",
            "botocore.credentials",
            "botocore.httpsession",
            "botocore.parsers",
            "botocore.endpoint",
        ]

        for logger_name in noisy_loggers:
            logging.getLogger(logger_name).setLevel(logging.WARNING)

    def _enable_detailed_aws_logging(self) -> None:
        """Enable comprehensive AWS API call tracing with detailed request/response logging."""
        # Configure all AWS-related loggers for detailed tracing
        aws_loggers = {
            "boto3": logging.DEBUG,
            "boto3.session": logging.DEBUG,
            "boto3.resources": logging.DEBUG,
            "botocore": logging.DEBUG,
            "botocore.client": logging.DEBUG,
            "botocore.endpoint": logging.DEBUG,
            "botocore.httpsession": logging.DEBUG,
            "botocore.parsers": logging.DEBUG,
            "botocore.response": logging.DEBUG,
            "botocore.awsrequest": logging.DEBUG,
            "botocore.credentials": logging.INFO,  # Slightly less verbose for security
            "urllib3.connectionpool": logging.DEBUG,
            "urllib3.util.retry": logging.DEBUG,
            "requests.packages.urllib3": logging.DEBUG,
            "s3transfer": logging.DEBUG,
        }

        for logger_name, level in aws_loggers.items():
            aws_logger = logging.getLogger(logger_name)
            aws_logger.setLevel(level)
            # Add our handlers to AWS loggers so they write to our log file
            for handler in self.logger.handlers:
                if handler not in aws_logger.handlers:
                    aws_logger.addHandler(handler)
            # Ensure AWS logs are captured by our handlers
            aws_logger.propagate = False  # Don't propagate to avoid duplicate logs

        # Enable HTTP wire logging for full request/response details
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except ImportError:
            pass

        self.logger.debug(
            "Verbose AWS API tracing enabled - all boto3/botocore calls will be logged"
        )
        self.logger.debug(
            "HTTP request/response details, API parameters, and response data will be captured"
        )

    def _enable_boto3_logging(self) -> None:
        """Enable detailed boto3/botocore logging for AWS API call tracking."""
        # Enable boto3 wire logging for API call details
        boto3_logger = logging.getLogger("boto3.resources")
        boto3_logger.setLevel(logging.DEBUG)

        # Enable botocore event logging
        botocore_logger = logging.getLogger("botocore.endpoint")
        botocore_logger.setLevel(logging.DEBUG)

        self.logger.debug("Enhanced AWS API call logging enabled")

    def get_progress_console(self) -> Console:
        """Get isolated console for Live progress displays (uses stderr)."""
        if self._progress_console is None:
            self._progress_console = Console(
                stderr=True,  # Use stderr for progress - isolated from logs
                force_terminal=True,
                legacy_windows=False,
            )
        return self._progress_console

    def disable_console_output(self, log_file_path: Path | None = None) -> None:
        """Disable console logging during Live displays with user info."""
        if log_file_path and self._debug_mode:
            self.logger.info(
                "Debug events during progress display will be written to: %s",
                log_file_path,
            )
            self.logger.info(
                "Console debug output temporarily disabled during Live progress display"
            )

        for handler in self.logger.handlers:
            if isinstance(handler, RichHandler):
                handler.setLevel(logging.CRITICAL)  # Effectively disable

    def enable_console_output(self, log_file_path: Path | None = None) -> None:
        """Re-enable console logging after Live displays."""
        for handler in self.logger.handlers:
            if isinstance(handler, RichHandler):
                handler.setLevel(logging.DEBUG if self._debug_mode else logging.INFO)

        if log_file_path and self._debug_mode:
            self.logger.info("Console debug output restored")
            self.logger.info(
                "For debug events during progress display, see: %s", log_file_path
            )

    @contextmanager
    def timer(self, operation: str) -> Any:
        """Context manager for timing operations with proper caller context."""
        timer = SimpleTimer(self.logger, operation)
        with timer:
            yield timer

    def is_debug_enabled(self) -> bool:
        """Check if debug mode is enabled."""
        return self._debug_mode

    def is_verbose_enabled(self) -> bool:
        """Check if verbose mode is enabled."""
        return self._verbose_mode

    # Logging methods that preserve caller information
    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log debug message with preserved caller context."""
        kwargs.setdefault("stacklevel", 2)
        self.logger.debug(message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log info message with preserved caller context."""
        kwargs.setdefault("stacklevel", 2)
        self.logger.info(message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log warning message with preserved caller context."""
        kwargs.setdefault("stacklevel", 2)
        self.logger.warning(message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log error message with preserved caller context."""
        kwargs.setdefault("stacklevel", 2)
        self.logger.error(message, *args, **kwargs)

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log critical message with preserved caller context."""
        kwargs.setdefault("stacklevel", 2)
        self.logger.critical(message, *args, **kwargs)

    # Specialized logging methods for AWS operations
    def log_aws_operation(
        self, service: str, operation: str, region: str, **kwargs: Any
    ) -> None:
        """Log AWS service operations with enhanced detail for boto3 API calls."""
        extra_info = ", ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        context = f" ({extra_info})" if extra_info else ""

        # Enhanced logging for AWS API operations
        if operation.startswith("boto3_"):
            # This is a direct AWS API call
            api_call = operation.replace("boto3_", "")
            self.logger.debug(
                "AWS API Call: %s.%s() in %s%s",
                service,
                api_call,
                region,
                context,
                stacklevel=2,
            )
        else:
            # This is an internal scanner operation
            self.logger.debug(
                "AWS %s operation '%s' in %s%s",
                service,
                operation,
                region,
                context,
                stacklevel=2,
            )

    def log_scan_progress(
        self, service: str, region: str, resource_count: int, duration: float
    ) -> None:
        """Log scan progress and performance metrics."""
        self.logger.debug(
            "Scan complete: %s in %s - %d resources (%.2fs)",
            service,
            region,
            resource_count,
            duration,
            stacklevel=2,
        )

    def log_cache_operation(
        self, operation: str, key: str, hit: bool | None = None, **kwargs: Any
    ) -> None:
        """Log cache operations with enhanced context."""
        if hit is not None:
            status = "HIT" if hit else "MISS"
            extra = (
                f" ({kwargs.get('resource_count', 'unknown')} resources)"
                if hit and "resource_count" in kwargs
                else ""
            )
            self.logger.debug("Cache %s for %s%s", status, key, extra, stacklevel=2)
        else:
            extra_info = (
                ", ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
            )
            context = f" ({extra_info})" if extra_info else ""
            self.logger.debug(
                "Cache %s: %s%s", operation.upper(), key, context, stacklevel=2
            )

    def log_error_context(
        self, error: Exception, context: dict[str, Any] | None = None
    ) -> None:
        """Log error with contextual information."""
        error_msg = f"❌ Error: {type(error).__name__}: {error}"
        if context:
            context_str = ", ".join(f"{k}={v}" for k, v in context.items())
            error_msg += f" (Context: {context_str})"
        self.logger.error(error_msg, stacklevel=2)


# Global logger instance
_aws_logger: AWSLogger | None = None


def configure_logging(
    debug: bool = False,
    log_file: Path | None = None,
    verbose: bool = False,
    log_to_stderr: bool = False,
) -> AWSLogger:
    """
    Configure and return the AWS scanner logging system.

    Args:
        debug: Enable debug mode with verbose logging
        log_file: Optional file path for debug log output
        verbose: Enable verbose AWS API call tracing (requires debug=True)
        log_to_stderr: Send console logs to stderr instead of stdout
            (--output -, where stdout belongs to the JSON document)

    Returns:
        Configured AWSLogger instance
    """
    global _aws_logger

    if _aws_logger is None:
        _aws_logger = AWSLogger("aws-inventory")

    _aws_logger.configure(
        debug=debug, log_file=log_file, verbose=verbose, log_to_stderr=log_to_stderr
    )
    return _aws_logger


def get_logger(name: str = "aws-inventory") -> AWSLogger:
    """
    Get logger instance - unified interface for all AWS scanner logging.

    Never configures: modules fetch the logger at import time, and any
    configuration here would lock in handler choices before the CLI has
    parsed a flag. configure_logging() is the only configurator.

    Args:
        name: Logger name (for backward compatibility, ignored)

    Returns:
        AWSLogger instance
    """
    global _aws_logger

    if _aws_logger is None:
        _aws_logger = AWSLogger(name)

    return _aws_logger


def get_output_console() -> Console:
    """
    Get the progress console for Live displays.
    """
    logger = get_logger()
    return logger.get_progress_console()


def create_debug_log_file(log_file: Path | None) -> Path:
    """
    Create a debug log file path with timestamp.

    Args:
        log_file: Optional custom log file path. If provided and is a file path,
                 uses that exact path. If provided and is a directory, creates
                 timestamped file in that directory. If None, uses default directory.

    Returns:
        Path to debug log file
    """
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    if log_file:
        log_path = Path(log_file)
        # If custom path is a file (has extension), use it directly
        if log_path.suffix:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            return log_path
        else:
            # If custom path is a directory, create timestamped file in it
            log_path.mkdir(parents=True, exist_ok=True)
            return log_path / f"aws_scanner_debug_{timestamp}.log"
    else:
        # Use default directory with timestamped file
        DEFAULT_DEBUG_LOG_DIR.mkdir(exist_ok=True)
        return DEFAULT_DEBUG_LOG_DIR / f"aws_scanner_debug_{timestamp}.log"
