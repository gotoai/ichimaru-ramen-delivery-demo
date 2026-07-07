"""Shared pytest config for agent-service tests.

Adds `--port` so the black-box client test (tests/test_api_client.py) can target a
running server on a given port:

    make test-serve PORT=9000
    python -m pytest tests/test_api_client.py --port 9000
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # import `agent` without install


def pytest_addoption(parser):
    parser.addoption(
        "--port", action="store", default=None, type=int,
        help="Port of the running agent.api server for the black-box test "
             "(default: config.API_PORT, i.e. 8000).",
    )


@pytest.fixture(scope="session")
def server_port(request) -> int:
    """The port to hit: --port if given, else the server's configured default (8000)."""
    port = request.config.getoption("--port")
    if port is None:
        from agent import config
        port = config.API_PORT
    return port
