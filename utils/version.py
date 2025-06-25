"""
Version management utilities
"""
import os
import subprocess
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def get_version_info():
    """Get version info automatically"""
    now = datetime.utcnow()
    version_info = {
        "version": now.strftime("%Y.%m.%d"),
        "build_number": 1,
        "commit_hash": "unknown",
        "build_date": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "environment": "unknown"
    }
    
    try:
        # Try to get git info
        result = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            version_info["commit_hash"] = result.stdout.strip()
        
        # Get commit count for build number
        result = subprocess.run(['git', 'rev-list', '--count', 'HEAD'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            count = int(result.stdout.strip())
            version_info["build_number"] = count
            version_info["version"] = f"{now.strftime('%Y.%m.%d')}.{count}"
        
        # Get branch name
        result = subprocess.run(['git', 'branch', '--show-current'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            branch = result.stdout.strip()
            version_info["branch"] = branch
            version_info["environment"] = "production" if branch == "main" else "development"
    
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
        # Git not available
        timestamp = int(now.timestamp())
        version_info["version"] = f"{now.strftime('%Y.%m.%d')}.{timestamp}"
        version_info["build_number"] = timestamp
        logger.info("Git not available, using date-based version")
    
    # Add deployment info
    if "RENDER" in os.environ:
        version_info["deployment_platform"] = "Render.com"
        version_info["service_name"] = os.environ.get("RENDER_SERVICE_NAME", "calendar-sync")
        version_info["environment"] = "production"
    else:
        version_info["deployment_platform"] = "Local Development"
        version_info["environment"] = "local"
    
    # Add display strings
    version_info["version_string"] = f"v{version_info['version']}"
    version_info["build_string"] = f"Build #{version_info['build_number']}"
    version_info["full_version"] = f"Calendar Sync v{version_info['version']} (Build #{version_info['build_number']})"
    
    if version_info["commit_hash"] != "unknown":
        version_info["full_version"] += f" [{version_info['commit_hash']}]"
    
    return version_info
