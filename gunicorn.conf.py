# © 2024-2026 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

import os

# Basic worker configuration
workers = 1  # Start with just 1 worker for Render
worker_class = 'sync'  # Use sync workers instead of threaded
timeout = 3600  # Increased to 3600 seconds (1 hour) for extended migration operations
graceful_timeout = 30  # Add this line
keepalive = 2

# Bind to the port Render provides
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"

# Logging
loglevel = 'info'
accesslog = '-'
errorlog = '-'

# Don't preload - let each worker load the app
preload_app = False

# Process naming
proc_name = 'calendar-sync'

# Worker lifecycle settings
max_requests = 0  # Don't restart workers after N requests
max_requests_jitter = 0
worker_connections = 1000

print(f"Gunicorn binding to {bind}")


def post_worker_init(worker):
    """Start the 23-minute sync scheduler inside the worker, once the app is loaded.

    Only a worker serves requests and only a worker should sync. A thread
    started at import time can land in the master (before the fork) and never
    reach the worker, or keep syncing from the master beside the worker's own.
    This hook runs in the worker only, after gunicorn has imported app:app.
    """
    import sys
    try:
        calsync = sys.modules.get('app')
        if calsync is None:
            import app as calsync
        calsync.ensure_components_initialized()
        calsync.start_scheduler_if_idle("gunicorn worker start")
    except Exception as e:
        print(f"post_worker_init: scheduler not started: {e}", file=sys.stderr)

