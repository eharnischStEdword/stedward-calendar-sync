"""
Auth-gate tests: sensitive endpoints must require authentication, while a small
set of public endpoints (health checks, login page, OAuth callback, static
assets) must stay reachable without a session.

Regression coverage for the 2026-06-08 finding that /metrics, /history,
/version, /dry-run/*, /restart-scheduler and /api/investigate answered with no
authentication.
"""
import os
import sys

# Make the app package importable and keep the background thread off.
os.environ.setdefault('DISABLE_BACKGROUND_INIT', 'true')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import app as app_module
from app import app as flask_app


class _FakeAuth:
    """Stand-in auth manager that reports an authenticated session."""
    def is_authenticated(self):
        return True


@pytest.fixture
def client(monkeypatch):
    flask_app.config['TESTING'] = True
    # Default to unauthenticated for every test unless it opts in.
    monkeypatch.setattr(app_module, 'auth_manager', None, raising=False)
    return flask_app.test_client()


# (path, http_method) pairs that must NOT be reachable without auth.
GATED = [
    ('/metrics', 'get'),
    ('/history', 'get'),
    ('/version', 'get'),
    ('/dry-run/enable', 'get'),
    ('/dry-run/disable', 'get'),
    ('/restart-scheduler', 'get'),
    ('/api/investigate', 'post'),
    ('/debug/calendars', 'get'),
    ('/cache/stats', 'get'),
]

# Paths that must stay public (no auth) for the app to function.
PUBLIC = ['/health', '/ready', '/']


@pytest.mark.unit
class TestAuthGate:
    @pytest.mark.parametrize('path,method', GATED)
    def test_sensitive_endpoint_requires_auth(self, client, path, method):
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, (
            f"{method.upper()} {path} returned {resp.status_code}, expected 401 "
            f"when unauthenticated"
        )

    @pytest.mark.parametrize('path', PUBLIC)
    def test_public_endpoint_stays_open(self, client, path):
        resp = client.get(path)
        assert resp.status_code != 401, (
            f"GET {path} returned 401 but must remain public"
        )

    def test_authenticated_request_passes_gate(self, client, monkeypatch):
        monkeypatch.setattr(app_module, 'auth_manager', _FakeAuth(), raising=False)
        resp = client.get('/metrics')
        # The gate must let an authenticated request through (it may then fail
        # for other reasons like an uninitialized sync engine, but not 401).
        assert resp.status_code != 401

    def test_google_verification_file_is_public(self, client):
        # Google Search Console site-ownership token must be reachable without auth.
        resp = client.get('/google496a12bccfaf6424.html')
        assert resp.status_code == 200
        assert b'google-site-verification: google496a12bccfaf6424.html' in resp.data
