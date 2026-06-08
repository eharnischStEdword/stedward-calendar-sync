"""Shared pytest fixtures/config for the calendar sync test suite."""
import os

# Prevent the background sync-init thread (app.py) from starting during tests.
# Honored by the guard at app import time; keeps test runs deterministic and offline.
os.environ.setdefault('DISABLE_BACKGROUND_INIT', 'true')
