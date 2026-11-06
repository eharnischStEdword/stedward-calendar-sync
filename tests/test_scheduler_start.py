# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Tests for how the 23-minute scheduler gets started.

Under gunicorn, app.py is imported wherever gunicorn loads it, which can be
the master process before the fork. A thread started there never reaches the
worker, and if it keeps running in the master it syncs beside the worker's
own scheduler. So the automatic start lives in gunicorn.conf.py's
post_worker_init hook (worker only), the first request is the fallback, and
both go through start_scheduler_if_idle so a running or paused scheduler is
never started twice.
"""
import importlib.util
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

os.environ.setdefault('DISABLE_BACKGROUND_INIT', 'true')

import app as app_module  # noqa: E402


class _Scheduler:
    def __init__(self, running):
        self.running = running
        self.starts = 0

    def is_running(self):
        return self.running

    def start(self):
        self.starts += 1
        self.running = True


def _load_gunicorn_conf():
    path = os.path.join(os.path.dirname(__file__), '..', 'gunicorn.conf.py')
    spec = importlib.util.spec_from_file_location('gunicorn_conf', path)
    conf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conf)
    return conf


@pytest.mark.unit
class TestStartSchedulerIfIdle:
    def test_idle_scheduler_is_started_once(self, monkeypatch):
        sched = _Scheduler(running=False)
        monkeypatch.setattr(app_module, 'scheduler', sched)
        monkeypatch.setattr(app_module, 'scheduler_paused', False)
        assert app_module.start_scheduler_if_idle("test") is True
        assert app_module.start_scheduler_if_idle("test") is False
        assert sched.starts == 1

    def test_running_scheduler_is_left_alone(self, monkeypatch):
        sched = _Scheduler(running=True)
        monkeypatch.setattr(app_module, 'scheduler', sched)
        monkeypatch.setattr(app_module, 'scheduler_paused', False)
        assert app_module.start_scheduler_if_idle("test") is False
        assert sched.starts == 0

    def test_paused_scheduler_is_not_started(self, monkeypatch):
        sched = _Scheduler(running=False)
        monkeypatch.setattr(app_module, 'scheduler', sched)
        monkeypatch.setattr(app_module, 'scheduler_paused', True)
        assert app_module.start_scheduler_if_idle("test") is False
        assert sched.starts == 0

    def test_no_scheduler_object_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(app_module, 'scheduler', None)
        monkeypatch.setattr(app_module, 'scheduler_paused', False)
        assert app_module.start_scheduler_if_idle("test") is False


@pytest.mark.unit
class TestGunicornWorkerHook:
    def test_hook_starts_the_scheduler_in_the_worker(self, monkeypatch):
        conf = _load_gunicorn_conf()
        sched = _Scheduler(running=False)
        monkeypatch.setattr(app_module, 'scheduler', sched)
        monkeypatch.setattr(app_module, 'scheduler_paused', False)
        monkeypatch.setattr(app_module, 'initialize_components', lambda: None)
        conf.post_worker_init(worker=None)
        assert sched.starts == 1

    def test_hook_respects_a_paused_scheduler(self, monkeypatch):
        conf = _load_gunicorn_conf()
        sched = _Scheduler(running=False)
        monkeypatch.setattr(app_module, 'scheduler', sched)
        monkeypatch.setattr(app_module, 'scheduler_paused', True)
        monkeypatch.setattr(app_module, 'initialize_components', lambda: None)
        conf.post_worker_init(worker=None)
        assert sched.starts == 0

    def test_hook_never_raises_into_gunicorn(self, monkeypatch):
        conf = _load_gunicorn_conf()
        monkeypatch.setattr(app_module, 'ensure_components_initialized',
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        conf.post_worker_init(worker=None)  # must not raise

    def test_background_init_does_not_start_the_scheduler(self, monkeypatch):
        sched = _Scheduler(running=False)
        monkeypatch.setattr(app_module, 'scheduler', sched)
        monkeypatch.setattr(app_module, 'scheduler_paused', False)
        monkeypatch.setattr(app_module, 'initialize_components', lambda: None)
        app_module.initialize_calendar_sync_background()
        assert sched.starts == 0
