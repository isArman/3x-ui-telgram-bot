"""Tests for the rotating logger."""

import logging
from logging.handlers import RotatingFileHandler

from app.utils.logger import setup_logger, _MAX_BYTES, _BACKUP_COUNT


def test_logger_uses_rotating_file_handler():
    logger = setup_logger(name="rot_test", log_file="data/rot.log")
    file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    assert file_handlers, "expected a file handler"
    rot = file_handlers[0]
    assert isinstance(rot, RotatingFileHandler)
    assert rot.maxBytes == _MAX_BYTES
    assert rot.backupCount == _BACKUP_COUNT


def test_logger_is_idempotent():
    a = setup_logger(name="rot_idem", log_file="data/rot2.log")
    b = setup_logger(name="rot_idem", log_file="data/rot2.log")
    assert a is b
    assert len(a.handlers) == 2
