# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

import os

# Basic worker configuration
workers = 1  # Start with just 1 worker for Render
worker_class = 'sync'  # Use sync workers instead of threaded
timeout = 120
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

print(f"Gunicorn binding to {bind}")
