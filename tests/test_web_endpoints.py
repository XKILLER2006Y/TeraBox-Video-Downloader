"""
tests/test_web_endpoints.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for FastAPI endpoint coroutines: /ping, /health, /dash, /api/stats.
"""

import pytest
import asyncio

try:
    from main import ping, health, dash, api_stats
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def test_ping_endpoint():
    if not HAS_FASTAPI:
        pytest.skip("FastAPI not installed in host environment (tested in container)")
    res = asyncio.run(ping())
    assert res == "pong"


def test_health_endpoint():
    if not HAS_FASTAPI:
        pytest.skip("FastAPI not installed in host environment (tested in container)")
    res = asyncio.run(health())
    assert res.status_code in (200, 503)


def test_dash_unauthorized():
    if not HAS_FASTAPI:
        pytest.skip("FastAPI not installed in host environment (tested in container)")
    res = asyncio.run(dash(t="invalid"))
    assert res.status_code in (403, 404)


def test_api_stats_forbidden():
    if not HAS_FASTAPI:
        pytest.skip("FastAPI not installed in host environment (tested in container)")
    res = asyncio.run(api_stats(t="invalid"))
    assert res.status_code == 403
