import pytest
import os
from pathlib import Path
import logging
from app.utils.logger import setup_logger


def test_logger_creation():
    """Test that logger is created successfully"""
    logger = setup_logger(name="test_logger", log_file="data/test.log")
    
    assert logger is not None
    assert logger.name == "test_logger"
    assert logger.level == logging.INFO


def test_logger_has_handlers():
    """Test that logger has both file and console handlers"""
    logger = setup_logger(name="test_handlers", log_file="data/test_handlers.log")
    
    # Should have 2 handlers: file and console
    assert len(logger.handlers) >= 2


def test_logger_writes_to_file():
    """Test that logger writes to file"""
    log_file = "data/test_write.log"
    
    # Remove existing log file
    if os.path.exists(log_file):
        os.remove(log_file)
    
    logger = setup_logger(name="test_write", log_file=log_file)
    logger.info("Test message")
    
    # Check file exists and contains message
    assert os.path.exists(log_file)
    
    with open(log_file, 'r') as f:
        content = f.read()
        assert "Test message" in content


def test_logger_creates_directory():
    """Test that logger creates data directory if not exists"""
    test_path = "data/test_dir/test.log"
    
    logger = setup_logger(name="test_dir", log_file=test_path)
    
    assert Path(test_path).parent.exists()


def test_logger_singleton():
    """Test that same logger name returns same instance"""
    logger1 = setup_logger(name="singleton_test", log_file="data/singleton.log")
    logger2 = setup_logger(name="singleton_test", log_file="data/singleton.log")
    
    # Should be the same logger instance
    assert logger1 is logger2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
