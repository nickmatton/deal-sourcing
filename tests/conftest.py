"""Test configuration — initializes logging once for the test suite."""

from src.common.logging import configure_logging

configure_logging(log_level="DEBUG", json_output=False)
