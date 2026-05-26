"""
Shared logging helpers for file-backed module loggers. 

Functions:
- get_file_logger: Get a logger that writes DEBUG+ output to a specified file.
"""

from __future__ import annotations

import logging
from pathlib import Path


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
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    logger.propagate = False
    return logger