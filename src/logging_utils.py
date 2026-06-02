"""
Shared logging helpers for file-backed module loggers.

Constants:
- LOG_TIMESTAMP_FORMATS: Tuple of timestamp formats to try when parsing log lines for retention pruning
- LOGGER_LEVEL: The logging level for the file logger

Functions:
- get_file_logger: Get a logger that writes DEBUG+ output to a specified file.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path


LOG_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%d %H:%M:%S",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "src" / "config" / "config.cfg"


def _load_config_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if not _CONFIG_PATH.exists():
        return values

    for raw_line in _CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()

    return values


_CONFIG = _load_config_file()


# Logger Levels:
# DEBUG: Detailed information, typically of interest only when diagnosing problems
# INFO: Useful information to log under normal circumstances
# WARNING: An indication that something unexpected happened, or indicative of some problem in the near future. The program continues to run
# ERROR: A serious issue has caused the program to fail
# CRITICAL: Not really used here, but a even more serious issue than an error
def _resolve_logger_level() -> int:
    level_text = _CONFIG.get("LOG_LEVEL", "INFO").strip()
    if not level_text:
        return logging.INFO
    if level_text.isdigit():
        return int(level_text)
    return getattr(logging, level_text.upper(), logging.INFO)


LOGGER_LEVEL = _resolve_logger_level()


class RetentionFileHandler(logging.FileHandler):
    """File handler that keeps only the last 24 hours of log lines."""

    retention_period = timedelta(hours=24)
    _pruned_files: set[str] = set()

    def __init__(self, filename, mode="a", encoding=None, delay=False, errors=None):
        """
        Initialize the handler and prune expired log entries from the file.

        Parameters:
            filename (str | Path): The log file path.
            mode (str): The file mode to use when opening the log file (default: "a").
            encoding (str | None): The encoding to use when writing to the log file (default: None, which means the system default).
            delay (bool): If True, the file will not be opened until the first log message is emitted (default: False).
            errors (str | None): The error handling scheme to use when encoding the log messages (default: None, which means "strict").
        """
        log_path = Path(filename).resolve()
        normalized_path = str(log_path)
        if normalized_path not in self._pruned_files:
            self._prune_expired_entries(log_path, encoding)
            self._pruned_files.add(normalized_path)

        super().__init__(
            filename, mode=mode, encoding=encoding, delay=delay, errors=errors
        )

    def _prune_expired_entries(self, log_path: Path, encoding: str | None) -> None:
        """
        Remove log lines from the file that are older than the retention period.

        Parameters:
            log_path (Path): The path to the log file to prune.
            encoding (str | None): The encoding to use when reading and writing the log file (default: None, which means the system default).
        """
        if not log_path.exists():
            return

        cutoff = datetime.now() - self.retention_period
        temp_path = log_path.with_name(f"{log_path.name}.retention")

        with (
            log_path.open("r", encoding=encoding or "utf-8") as source,
            temp_path.open("w", encoding=encoding or "utf-8") as destination,
        ):
            keeping_lines = False
            wrote_anything = False
            for line in source:
                if not keeping_lines and self._is_expired_line(
                    line.rstrip("\r\n"), cutoff
                ):
                    continue

                keeping_lines = True
                destination.write(line)
                wrote_anything = True

        if not wrote_anything:
            temp_path.unlink(missing_ok=True)
            return

        temp_path.replace(log_path)

    def _is_expired_line(self, line: str, cutoff: datetime) -> bool:
        """
        Determine if a log line is expired based on its timestamp.

        Parameters:
            line (str): The log line to check.
            cutoff (datetime): The cutoff date for determining expiration.

        Returns:
            bool: True if the line is expired, False otherwise.
        """
        if not line:
            return False

        timestamp_text = line.split(" ", 2)[:2]
        if len(timestamp_text) < 2:
            return False

        timestamp = " ".join(timestamp_text)
        for fmt in LOG_TIMESTAMP_FORMATS:
            try:
                parsed = datetime.strptime(timestamp, fmt)
            except ValueError:
                continue
            return parsed < cutoff

        return False


def get_file_logger(name: str, log_file: Path) -> logging.Logger:
    """
    Return a module logger that writes DEBUG+ output to ``log_file``.

    Parameters:
        name (str): The name of the logger.
        log_file (Path): The file path to write log messages to.

    Returns:
        logging.Logger: A configured logger instance.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(LOGGER_LEVEL)

    log_path = str(log_file)
    if not any(
        isinstance(handler, logging.FileHandler)
        and getattr(handler, "baseFilename", "") == log_path
        for handler in logger.handlers
    ):
        handler = RetentionFileHandler(log_file, encoding="utf-8")
        handler.setLevel(LOGGER_LEVEL)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    logger.propagate = False
    return logger
