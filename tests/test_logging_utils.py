import logging

from src.logging_utils import get_file_logger


def test_get_file_logger_writes_to_log_file(tmp_path):
    """Logger should create the file and write the warning marker."""
    log_file = tmp_path / "logs" / "smoke.log"
    logger = get_file_logger("tests.logging_smoke", log_file)

    logger.warning("logger smoke test marker")
    for handler in logger.handlers:
        handler.flush()

    assert log_file.exists()
    assert "WARNING tests.logging_smoke: logger smoke test marker" in (
        log_file.read_text(encoding="utf-8")
    )


def test_get_file_logger_reuses_existing_file_handler(tmp_path):
    """Repeated setup should not add duplicate file handlers."""
    log_file = tmp_path / "logs" / "dedupe.log"
    logger = get_file_logger("tests.logging_dedupe", log_file)
    before = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.FileHandler)
        and handler.baseFilename == str(log_file)
    ]

    logger = get_file_logger("tests.logging_dedupe", log_file)
    after = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.FileHandler)
        and handler.baseFilename == str(log_file)
    ]

    assert len(after) == len(before) == 1
