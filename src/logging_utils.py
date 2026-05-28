"""
Shared logging helpers for file-backed module loggers.

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


class RetentionFileHandler(logging.FileHandler):
    """File handler that keeps only the last 24 hours of log lines."""

    retention_period = timedelta(hours=24)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._next_prune_at = datetime.now()

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        if datetime.now() >= self._next_prune_at:
            self._prune_expired_entries()
            self._next_prune_at = datetime.now() + self.retention_period

    def _prune_expired_entries(self) -> None:
        log_path = Path(self.baseFilename)
        if not log_path.exists():
            return

        cutoff = datetime.now() - self.retention_period
        temp_path = log_path.with_name(f"{log_path.name}.retention")

        with (
            log_path.open("r", encoding=self.encoding or "utf-8") as source,
            temp_path.open("w", encoding=self.encoding or "utf-8") as destination,
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
    logger.setLevel(logging.DEBUG)

    log_path = str(log_file)
    if not any(
        isinstance(handler, logging.FileHandler)
        and getattr(handler, "baseFilename", "") == log_path
        for handler in logger.handlers
    ):
        handler = RetentionFileHandler(log_file, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    logger.propagate = False
    return logger
