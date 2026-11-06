"""
Token-cache and sign-in tests.

The persistent sync credential in /data/token_cache.json is shared by every
dashboard user and by the scheduler. It must survive a browser logout and a
rejected sign-in, the allow-list must fail closed when Graph /me does not
answer 200, the cache must be written atomically and never discarded on a
read failure, and POST /sync must accept a bodiless request.

Regression coverage for the 2026-11-06 review findings on /logout,
/auth/callback, /sync, /status, the canonical-host redirect, /bulletin-events,
/event-search and auth.py's token cache. Nothing here touches the network.
"""
import json
import os
import sys
from datetime import datetime, timedelta

# Make the app package importable and keep the background thread off.
os.environ.setdefault('DISABLE_BACKGROUND_INIT', 'true')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import requests

import app as app_module
import config
from app import app as flask_app
from auth import MicrosoftAuth
from utils import DateTimeUtils


OLD_TOKENS = {
    'access_token': 'OLD_ACCESS',
    'refresh_token': 'OLD_REFRESH',
    'expires_at': '2099-01-01T00:00:00-06:00',
}
NEW_TOKENS = {'access_token': 'NEW_ACCESS', 'refresh_token': 'NEW_REFRESH', 'expires_in': 3600}
ALLOWED_USER = 'allowed@stedward.org'


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else json.dumps(self._payload)

    def json(self):
        return self._payload


def _no_network(*args, **kwargs):
    raise AssertionError(f"network call attempted: {args[0] if args else kwargs}")


class _FakeReader:
    def find_calendar_id(self, name):
        return 'cal-id'


class _FakeEngine:
    """Stands in for SyncEngine on the routes under test."""
    def __init__(self, auth=None, in_progress=False):
        self.auth = auth
        self.sync_in_progress = in_progress
        self.reader = _FakeReader()

    def get_progress_percent(self):
        return 0

    def get_status(self):
        return {
            'scheduler_running': True,  # the placeholder the engine always reports
            'last_sync_result': {'success': False, 'message': 'Not synced yet'},
        }


class _FakeScheduler:
    def __init__(self, running):
        self._running = running

    def is_running(self):
        return self._running

    def get_scheduler_status(self):
        return {'running': self._running}


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / 'token_cache.json'
    path.write_text(json.dumps(OLD_TOKENS))
    return path


@pytest.fixture
def auth(token_file, monkeypatch):
    """A real MicrosoftAuth whose cache lives in tmp_path and holds OLD_TOKENS."""
    monkeypatch.delenv('ACCESS_TOKEN', raising=False)
    monkeypatch.delenv('REFRESH_TOKEN', raising=False)
    monkeypatch.setenv('TOKEN_EXPIRES_AT', OLD_TOKENS['expires_at'])
    manager = MicrosoftAuth()
    manager.token_file = str(token_file)
    manager._ensure_tokens_loaded()
    assert manager.env_refresh_token == 'OLD_REFRESH'
    return manager


@pytest.fixture
def client(auth, monkeypatch):
    flask_app.config['TESTING'] = True
    # The session cookie is marked Secure; the test client speaks plain http.
    monkeypatch.setitem(flask_app.config, 'SESSION_COOKIE_SECURE', False)
    monkeypatch.setattr(app_module, 'auth_manager', auth, raising=False)
    monkeypatch.setattr(requests, 'post', _no_network)
    monkeypatch.setattr(requests, 'get', _no_network)
    return flask_app.test_client()


def _sign_in(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def _session_authenticated(client):
    with client.session_transaction() as sess:
        return bool(sess.get('authenticated'))


def _run_callback(client, monkeypatch, me_response):
    """Exchange succeeds with NEW_TOKENS; Graph /me answers me_response."""
    monkeypatch.setattr(requests, 'post', lambda *a, **k: _FakeResponse(200, dict(NEW_TOKENS)))
    seen = {}

    def fake_get(url, headers=None, timeout=None, **kwargs):
        seen['url'] = url
        seen['headers'] = headers
        seen['timeout'] = timeout
        return me_response

    monkeypatch.setattr(requests, 'get', fake_get)
    with client.session_transaction() as sess:
        sess['oauth_state'] = 'state-123'
    return client.get('/auth/callback?code=abc&state=state-123'), seen


@pytest.mark.unit
class TestLogout:
    def test_anonymous_logout_leaves_token_file_alone(self, client, auth, token_file):
        resp = client.get('/logout')
        assert resp.status_code == 302
        assert token_file.exists()
        assert json.loads(token_file.read_text()) == OLD_TOKENS
        assert auth.env_refresh_token == 'OLD_REFRESH'

    def test_signed_in_logout_ends_session_only(self, client, auth, token_file):
        _sign_in(client)
        resp = client.get('/logout')
        assert resp.status_code == 302
        assert not _session_authenticated(client)
        assert json.loads(token_file.read_text()) == OLD_TOKENS
        assert auth.env_refresh_token == 'OLD_REFRESH'


@pytest.mark.unit
class TestAuthCallback:
    def test_non_allowed_user_is_denied_and_previous_token_kept(self, client, auth, token_file, monkeypatch):
        monkeypatch.setattr(config, 'ALLOWED_DASHBOARD_USERS', [ALLOWED_USER])
        resp, seen = _run_callback(client, monkeypatch, _FakeResponse(200, {'mail': 'Stranger@Example.com'}))
        assert resp.status_code == 403
        assert json.loads(token_file.read_text()) == OLD_TOKENS
        assert auth.env_refresh_token == 'OLD_REFRESH'
        assert auth.env_access_token == 'OLD_ACCESS'
        assert not _session_authenticated(client)
        # The check runs on the token just exchanged, not on the stored credential.
        assert seen['headers']['Authorization'] == 'Bearer NEW_ACCESS'
        assert seen['timeout'] == 30

    @pytest.mark.parametrize('status', [503, 429, 403])
    def test_non_200_from_me_is_a_denial(self, client, auth, token_file, monkeypatch, status):
        monkeypatch.setattr(config, 'ALLOWED_DASHBOARD_USERS', [ALLOWED_USER])
        resp, _ = _run_callback(client, monkeypatch, _FakeResponse(status, {}, text='throttled'))
        assert resp.status_code == 500
        assert json.loads(token_file.read_text()) == OLD_TOKENS
        assert auth.env_refresh_token == 'OLD_REFRESH'
        assert not _session_authenticated(client)

    def test_me_exception_is_a_denial(self, client, auth, token_file, monkeypatch):
        monkeypatch.setattr(config, 'ALLOWED_DASHBOARD_USERS', [ALLOWED_USER])
        monkeypatch.setattr(requests, 'post', lambda *a, **k: _FakeResponse(200, dict(NEW_TOKENS)))

        def boom(*a, **k):
            raise requests.exceptions.ConnectTimeout('graph down')

        monkeypatch.setattr(requests, 'get', boom)
        with client.session_transaction() as sess:
            sess['oauth_state'] = 'state-123'
        resp = client.get('/auth/callback?code=abc&state=state-123')
        assert resp.status_code == 500
        assert json.loads(token_file.read_text()) == OLD_TOKENS
        assert not _session_authenticated(client)

    def test_allowed_user_is_stored_after_the_check(self, client, auth, token_file, monkeypatch):
        monkeypatch.setattr(config, 'ALLOWED_DASHBOARD_USERS', [ALLOWED_USER])
        resp, _ = _run_callback(client, monkeypatch, _FakeResponse(200, {'mail': ALLOWED_USER.upper()}))
        assert resp.status_code == 302
        saved = json.loads(token_file.read_text())
        assert saved['access_token'] == 'NEW_ACCESS'
        assert saved['refresh_token'] == 'NEW_REFRESH'
        assert auth.env_refresh_token == 'NEW_REFRESH'
        assert _session_authenticated(client)
        assert not token_file.with_name(token_file.name + '.tmp').exists()

    def test_without_allow_list_any_tenant_user_is_stored(self, client, auth, token_file, monkeypatch):
        monkeypatch.setattr(config, 'ALLOWED_DASHBOARD_USERS', [])
        monkeypatch.setattr(requests, 'post', lambda *a, **k: _FakeResponse(200, dict(NEW_TOKENS)))
        # requests.get stays _no_network: no /me call may happen without a list.
        with client.session_transaction() as sess:
            sess['oauth_state'] = 'state-123'
        resp = client.get('/auth/callback?code=abc&state=state-123')
        assert resp.status_code == 302
        assert json.loads(token_file.read_text())['refresh_token'] == 'NEW_REFRESH'
        assert _session_authenticated(client)

    def test_failed_exchange_stores_nothing(self, client, auth, token_file, monkeypatch):
        monkeypatch.setattr(config, 'ALLOWED_DASHBOARD_USERS', [ALLOWED_USER])
        monkeypatch.setattr(requests, 'post', lambda *a, **k: _FakeResponse(400, {'error': 'invalid_grant'}))
        with client.session_transaction() as sess:
            sess['oauth_state'] = 'state-123'
        resp = client.get('/auth/callback?code=abc&state=state-123')
        assert resp.status_code == 400
        assert json.loads(token_file.read_text()) == OLD_TOKENS
        assert auth.env_refresh_token == 'OLD_REFRESH'
        assert not _session_authenticated(client)


@pytest.mark.unit
class TestTokenCache:
    def test_save_is_atomic(self, auth, token_file):
        auth._save_tokens_to_disk('A2', 'R2', datetime(2099, 1, 1))
        assert json.loads(token_file.read_text())['refresh_token'] == 'R2'
        assert not token_file.with_name(token_file.name + '.tmp').exists()

    def test_unparsable_file_keeps_memory_and_ignores_env_bootstrap(self, auth, token_file, monkeypatch):
        monkeypatch.setenv('REFRESH_TOKEN', 'STALE_BOOTSTRAP')
        token_file.write_text('{"access_token": "half-writ')
        auth._ensure_tokens_loaded()
        assert auth.env_refresh_token == 'OLD_REFRESH'
        assert auth.env_access_token == 'OLD_ACCESS'
        assert token_file.exists()

    def test_missing_file_keeps_memory(self, auth, token_file, monkeypatch):
        monkeypatch.setenv('REFRESH_TOKEN', 'STALE_BOOTSTRAP')
        token_file.unlink()
        auth._ensure_tokens_loaded()
        assert auth.env_refresh_token == 'OLD_REFRESH'

    def test_env_bootstrap_only_when_no_file_and_no_memory(self, tmp_path, monkeypatch):
        monkeypatch.setenv('ACCESS_TOKEN', 'BOOT_ACCESS')
        monkeypatch.setenv('REFRESH_TOKEN', 'BOOT_REFRESH')
        manager = MicrosoftAuth()
        manager.token_file = str(tmp_path / 'absent.json')
        manager.env_access_token = None
        manager.env_refresh_token = None
        manager._ensure_tokens_loaded()
        assert manager.env_refresh_token == 'BOOT_REFRESH'

    def test_clear_tokens_nulls_memory_and_deletes_file(self, auth, token_file):
        auth.clear_tokens()
        assert auth.env_refresh_token is None
        assert auth.env_access_token is None
        assert not token_file.exists()

    def test_invalid_grant_on_refresh_clears_the_credential(self, auth, token_file, monkeypatch):
        monkeypatch.setattr(requests, 'post', lambda *a, **k: _FakeResponse(400, text='{"error":"invalid_grant"}'))
        assert auth.refresh_access_token() is False
        assert auth.env_refresh_token is None
        assert not token_file.exists()

    def test_other_refresh_failures_keep_the_credential(self, auth, token_file, monkeypatch):
        monkeypatch.setattr(requests, 'post', lambda *a, **k: _FakeResponse(503, text='try later'))
        assert auth.refresh_access_token() is False
        assert auth.env_refresh_token == 'OLD_REFRESH'
        assert json.loads(token_file.read_text()) == OLD_TOKENS

    def test_successful_refresh_rewrites_file_in_place(self, auth, token_file, monkeypatch):
        monkeypatch.setattr(requests, 'post', lambda *a, **k: _FakeResponse(200, dict(NEW_TOKENS)))
        assert auth.refresh_access_token() is True
        assert auth.env_access_token == 'NEW_ACCESS'
        assert json.loads(token_file.read_text())['refresh_token'] == 'NEW_REFRESH'
        assert not token_file.with_name(token_file.name + '.tmp').exists()


@pytest.mark.unit
class TestSyncTrigger:
    @pytest.fixture(autouse=True)
    def engine(self, monkeypatch, auth):
        fake = _FakeEngine(auth=auth)
        monkeypatch.setattr(app_module, 'sync_engine', fake, raising=False)
        # Never let a real sync thread start.
        monkeypatch.setattr(app_module, '_run_sync_background', lambda dry_run=False: None)
        return fake

    def test_bodiless_post_is_accepted(self, client):
        _sign_in(client)
        resp = client.post('/sync')
        assert resp.status_code == 202
        assert resp.get_json()['dry_run'] is False

    def test_dry_run_without_dry_run_mode_is_refused(self, client, monkeypatch):
        monkeypatch.setattr(config, 'DRY_RUN_MODE', False)
        _sign_in(client)
        resp = client.post('/sync', json={'dry_run': True})
        assert resp.status_code == 400
        assert '/sync/preview' in resp.get_json()['error']

    def test_dry_run_with_dry_run_mode_is_accepted(self, client, monkeypatch):
        monkeypatch.setattr(config, 'DRY_RUN_MODE', True)
        _sign_in(client)
        resp = client.post('/sync', json={'dry_run': True})
        assert resp.status_code == 202

    def test_preview_refused_while_sync_runs(self, client, engine):
        engine.sync_in_progress = True
        _sign_in(client)
        resp = client.post('/sync/preview')
        assert resp.status_code == 409

    def test_clear_routes_refused_while_sync_runs(self, client, engine):
        engine.sync_in_progress = True
        _sign_in(client)
        target = config.get_sync_pairs()[0]['target']
        for path in ('/clear-target', '/clear-synced-only'):
            resp = client.post(path, json={'target': target})
            assert resp.status_code == 409, path
            assert resp.get_json()['success'] is False


@pytest.mark.unit
class TestStatusAndDebug:
    def test_status_reports_real_scheduler_state(self, client, auth, monkeypatch):
        monkeypatch.setattr(app_module, 'sync_engine', _FakeEngine(auth=auth), raising=False)
        monkeypatch.setattr(app_module, 'scheduler', _FakeScheduler(running=False), raising=False)
        _sign_in(client)
        resp = client.get('/status')
        assert resp.status_code == 200
        assert resp.get_json()['scheduler_running'] is False

    def test_debug_event_uses_get_headers(self, client, auth, monkeypatch):
        monkeypatch.setattr(app_module, 'sync_engine', _FakeEngine(auth=auth), raising=False)
        _sign_in(client)

        monkeypatch.setattr(auth, 'get_headers', lambda: None)
        resp = client.get('/debug/event/evt-1')
        assert resp.status_code == 401

        seen = {}

        def fake_get(url, headers=None, timeout=None, **kwargs):
            seen['headers'] = headers
            seen['timeout'] = timeout
            return _FakeResponse(200, {'subject': 'x', 'categories': [], 'showAs': 'busy',
                                       'start': {}, 'end': {}, 'isAllDay': False})

        monkeypatch.setattr(auth, 'get_headers', lambda: {'Authorization': 'Bearer T'})
        monkeypatch.setattr(requests, 'get', fake_get)
        resp = client.get('/debug/event/evt-1')
        assert resp.status_code != 500
        assert seen['headers']['Authorization'] == 'Bearer T'
        assert seen['timeout'] == 30


@pytest.mark.unit
class TestCanonicalHostRedirect:
    OLD = 'https://stedward-calendar-sync.onrender.com'

    def test_old_host_redirects_with_path_and_query(self, client, monkeypatch):
        monkeypatch.setattr(config, 'REDIRECT_URI', 'https://calsync.stedward.org/auth/callback')
        resp = client.get('/bulletin-events?week=current', base_url=self.OLD)
        assert resp.status_code == 301
        assert resp.headers['Location'] == 'https://calsync.stedward.org/bulletin-events?week=current'

    def test_health_probes_on_old_host_still_answer(self, client, monkeypatch):
        monkeypatch.setattr(config, 'REDIRECT_URI', 'https://calsync.stedward.org/auth/callback')
        assert client.get('/health', base_url=self.OLD).status_code == 200
        assert client.get('/health/detailed', base_url=self.OLD).status_code == 200

    def test_canonical_host_is_not_redirected(self, client, monkeypatch):
        monkeypatch.setattr(config, 'REDIRECT_URI', 'https://calsync.stedward.org/auth/callback')
        resp = client.get('/health', base_url='https://calsync.stedward.org')
        assert resp.status_code == 200

    @pytest.mark.parametrize('redirect_uri', ['', None, 'not a url', 'calsync.stedward.org/auth/callback'])
    def test_missing_or_unparsable_redirect_uri_changes_nothing(self, client, monkeypatch, redirect_uri):
        monkeypatch.setattr(config, 'REDIRECT_URI', redirect_uri)
        resp = client.get('/', base_url=self.OLD)
        assert resp.status_code != 301


def _upcoming_week():
    """Same Sunday..Saturday window /bulletin-events uses for week=upcoming."""
    today = DateTimeUtils.get_central_time().date()
    days_until_sunday = (6 - today.weekday()) % 7
    if days_until_sunday == 0:
        days_until_sunday = 7
    start = today + timedelta(days=days_until_sunday)
    return start, start + timedelta(days=6)


def _all_day(subject, first_day, last_day):
    """Graph shape for an all-day event: end is the midnight after its last day."""
    return {
        'id': subject,
        'subject': subject,
        'isAllDay': True,
        'start': {'dateTime': f'{first_day.isoformat()}T00:00:00.0000000', 'timeZone': 'UTC'},
        'end': {'dateTime': f'{(last_day + timedelta(days=1)).isoformat()}T00:00:00.0000000', 'timeZone': 'UTC'},
        'body': {'content': ''},
        'location': {'displayName': ''},
    }


@pytest.mark.unit
class TestBulletinEvents:
    @pytest.fixture(autouse=True)
    def engine(self, monkeypatch, auth):
        monkeypatch.setattr(auth, 'get_headers', lambda: {'Authorization': 'Bearer T'})
        monkeypatch.setattr(auth, 'refresh_access_token', lambda: False)
        fake = _FakeEngine(auth=auth)
        monkeypatch.setattr(app_module, 'sync_engine', fake, raising=False)
        return fake

    @pytest.fixture
    def rendered(self, monkeypatch):
        captured = {}

        def fake_render(name, **ctx):
            captured.update(ctx)
            return 'rendered'

        monkeypatch.setattr(app_module, 'render_template', fake_render)
        return captured

    @pytest.mark.parametrize('status', [503, 429, 401])
    def test_failed_read_is_an_error_not_a_blank_week(self, client, monkeypatch, status):
        monkeypatch.setattr(requests, 'get', lambda *a, **k: _FakeResponse(status, text='nope'))
        _sign_in(client)
        resp = client.get('/bulletin-events?week=upcoming')
        assert resp.status_code == 500
        assert 'error' in resp.get_json()

    def test_failed_next_page_is_an_error(self, client, monkeypatch):
        start, end = _upcoming_week()

        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            if 'next' in url:
                return _FakeResponse(503, text='nope')
            return _FakeResponse(200, {'value': [_all_day('Picture Day', start, start)],
                                       '@odata.nextLink': 'https://graph.microsoft.com/next'})

        monkeypatch.setattr(requests, 'get', fake_get)
        _sign_in(client)
        resp = client.get('/bulletin-events?week=upcoming')
        assert resp.status_code == 500

    def test_exception_during_read_is_an_error(self, client, monkeypatch):
        def boom(*a, **k):
            raise requests.exceptions.ReadTimeout('slow')

        monkeypatch.setattr(requests, 'get', boom)
        _sign_in(client)
        resp = client.get('/bulletin-events?week=upcoming')
        assert resp.status_code == 500

    def test_multi_day_all_day_event_appears_on_every_covered_day(self, client, monkeypatch, rendered):
        start, end = _upcoming_week()
        events = [
            # Thursday before the week through the week's Tuesday
            _all_day('Fall Break', start - timedelta(days=3), start + timedelta(days=2)),
            # Single-day all-day event on Wednesday
            _all_day('Picture Day', start + timedelta(days=3), start + timedelta(days=3)),
            # Runs past the end of the week
            _all_day('Retreat', end - timedelta(days=1), end + timedelta(days=4)),
        ]
        monkeypatch.setattr(requests, 'get', lambda *a, **k: _FakeResponse(200, {'value': events}))
        _sign_in(client)
        resp = client.get('/bulletin-events?week=upcoming')
        assert resp.status_code == 200

        days = rendered['days']
        assert [d['date'] for d in days] == [start + timedelta(days=i) for i in range(7)]
        by_day = {d['date']: [e['subject'] for e in d['events']] for d in days}
        assert [d for d in by_day if 'Fall Break' in by_day[d]] == [start + timedelta(days=i) for i in range(3)]
        assert [d for d in by_day if 'Picture Day' in by_day[d]] == [start + timedelta(days=3)]
        assert by_day[start + timedelta(days=3)].count('Picture Day') == 1
        assert [d for d in by_day if 'Retreat' in by_day[d]] == [end - timedelta(days=1), end]


@pytest.mark.unit
class TestEventSearch:
    @pytest.fixture(autouse=True)
    def engine(self, monkeypatch, auth):
        monkeypatch.setattr(auth, 'get_service_headers', lambda: {'Authorization': 'Bearer T'})
        fake = _FakeEngine(auth=auth)
        monkeypatch.setattr(app_module, 'sync_engine', fake, raising=False)
        return fake

    def _graph(self, monkeypatch, pages):
        """pages: list of calendarView pages; every page but the last gets a nextLink."""
        calls = []

        def fake_get(url, headers=None, params=None, timeout=None, **kwargs):
            calls.append(url)
            if url.endswith('/calendars'):
                return _FakeResponse(200, {'value': [{'name': config.TARGET_CALENDAR, 'id': 'pub'}]})
            if url.startswith('https://graph.microsoft.com/next/'):
                index = int(url.rsplit('/', 1)[1])
            else:
                index = 0
            page = {'value': pages[index]}
            if index + 1 < len(pages):
                page['@odata.nextLink'] = f'https://graph.microsoft.com/next/{index + 1}'
            return _FakeResponse(200, page)

        monkeypatch.setattr(requests, 'get', fake_get)
        return calls

    def test_follows_next_link(self, client, monkeypatch):
        day = DateTimeUtils.get_central_time().date() + timedelta(days=3)
        timed = {'subject': 'Youth Group', 'isAllDay': False,
                 'start': {'dateTime': f'{day.isoformat()}T20:00:00', 'timeZone': 'UTC'}}
        self._graph(monkeypatch, [[_all_day('Picture Day', day, day)], [timed]])
        _sign_in(client)
        resp = client.get('/event-search?range=week')
        assert resp.status_code == 200
        assert b'2 events found' in resp.data
        assert b'Youth Group' in resp.data

    def test_all_day_event_dated_from_start_string(self, client, monkeypatch):
        day = DateTimeUtils.get_central_time().date() + timedelta(days=3)
        self._graph(monkeypatch, [[_all_day('Holy Day', day, day)]])
        _sign_in(client)
        resp = client.get('/event-search?range=week')
        assert resp.status_code == 200
        assert day.strftime('%A, %B %d, %Y').encode() in resp.data
        assert (day - timedelta(days=1)).strftime('%A, %B %d, %Y').encode() not in resp.data
        assert b'All Day' in resp.data

    def test_search_term_and_subjects_are_escaped(self, client, monkeypatch):
        day = DateTimeUtils.get_central_time().date() + timedelta(days=3)
        self._graph(monkeypatch, [[_all_day('<b>Holy Day</b>', day, day)]])
        _sign_in(client)

        resp = client.get('/event-search?range=week&search=holy')
        assert resp.status_code == 200
        assert b'<b>Holy Day</b>' not in resp.data
        assert b'&lt;b&gt;Holy Day&lt;/b&gt;' in resp.data

        resp = client.get('/event-search?range=week&search=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E')
        assert resp.status_code == 200
        assert b'<img src=x' not in resp.data
        assert b'&lt;img src=x' in resp.data
