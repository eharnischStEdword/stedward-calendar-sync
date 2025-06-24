import os
import multiprocessing

# Worker configuration
workers = int(os.environ.get('GUNICORN_WORKERS', 2))
worker_class = 'gthread'
threads = int(os.environ.get('GUNICORN_THREADS', 4))

# Timeout configuration - critical for Render stability
timeout = int(os.environ.get('GUNICORN_TIMEOUT', 120))  # 120 seconds vs default 30
graceful_timeout = int(os.environ.get('GUNICORN_GRACEFUL_TIMEOUT', 60))  # Time for graceful shutdown
keepalive = 2

# Bind configuration
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Performance settings
preload_app = True  # Load app before forking workers
max_requests = 1000  # Restart workers after handling this many requests
max_requests_jitter = 100  # Add randomness to avoid all workers restarting at once

# Logging
loglevel = 'info'
accesslog = '-'  # Log to stdout
errorlog = '-'   # Log to stderr

# Process naming
proc_name = 'stedward-calendar-sync'
