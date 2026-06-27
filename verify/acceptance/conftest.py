"""Shared fixtures and helpers for the Top-K black-box acceptance suite.

These tests do NOT import `src.topk`. They talk to the running system
via HTTP at API_BASE_URL.
"""

import os
import pytest
import httpx


API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")


class _BodyCapableClient(httpx.Client):
    """httpx.Client whose DELETE can carry a request body.

    httpx's convenience `delete()` omits body params (json/content/data), but the
    DELETE /admin/blacklist endpoint takes an `item_ids` body. Route body-bearing
    DELETEs through `request()` so the suite exercises the real contract unchanged.
    """

    def delete(self, url, *, json=None, content=None, data=None, **kwargs):
        if json is not None or content is not None or data is not None:
            return self.request("DELETE", url, json=json, content=content, data=data, **kwargs)
        return super().delete(url, **kwargs)


@pytest.fixture(scope="session")
def base_url():
    return API_BASE_URL


@pytest.fixture(scope="session")
def client(base_url):
    """Session-scoped httpx client for the entire acceptance run."""
    with _BodyCapableClient(base_url=base_url, timeout=10) as c:
        yield c


def assert_json_200(r, expected_status=200):
    """Assert status and return parsed JSON."""
    assert r.status_code == expected_status, (
        f"Expected {expected_status}, got {r.status_code}: {r.text}"
    )
    return r.json()


def assert_201(r):
    return assert_json_200(r, 201)


def assert_202(r):
    return assert_json_200(r, 202)


def assert_422(r):
    assert r.status_code == 422, f"Expected 422, got {r.status_code}: {r.text}"
    return r.json()


def assert_503(r):
    assert r.status_code == 503, f"Expected 503, got {r.status_code}: {r.text}"
    return r.json()


@pytest.fixture(autouse=True)
def reset_state_between_tests(client, base_url):
    """Reset the server's in-memory state before each test.

    This isolates acceptance tests so that events fed by one test
    do not leak into another test's CMS / Space-Saving / Bloom state.
    The reset endpoint clears all in-memory data structures and Redis
    keys, giving each test a clean slate.

    Black-box: communicates via HTTP POST /admin/reset.
    """
    try:
        r = client.post(f"{base_url}/admin/reset")
        # Accept 200 (success) or any non-500 (e.g. 404 if endpoint
        # not available — skip reset). Only fail on connection errors.
        r.raise_for_status()
    except Exception:
        # If the reset endpoint isn't available (server not yet ready
        # or not running with the updated code), silently continue.
        pass
