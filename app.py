#!/usr/bin/env python3
"""
St. Edward Calendar Sync - Production Version with Central Time Support
"""
import os
import logging
import secrets
import traceback
import signal
import sys
import config
from datetime import datetime
from flask import Flask, render_template, jsonify, redirect, session, request

# Import timezone utilities
from utils.timezone import get_central_time, format_central_time, utc_to_central

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

# Global components - will be initialized after first successful load
sync_engine = None
scheduler = None
auth_manager = None

def initialize_components():
    """Initialize sync components safely"""
    global sync_engine, scheduler, auth_manager
    
    if auth_manager is None:
        try:
            from auth.microsoft_auth import MicrosoftAuth
            auth_manager = MicrosoftAuth()
            logger.info("✅ Auth manager initialized")
        except Exception as e:
            logger.error(f"Failed to initialize auth manager: {e}")
            return False
    
    if sync_engine is None:
        try:
            from sync.engine import SyncEngine
            sync_engine = SyncEngine(auth_manager)
            logger.info("✅ Sync engine initialized")
        except Exception as e:
            logger.error(f"Failed to initialize sync engine: {e}")
            # Continue without sync engine for now
    
    if scheduler is None:
        try:
            from sync.scheduler import SyncScheduler
            scheduler = SyncScheduler(sync_engine)
            scheduler.start()
            logger.info("✅ Scheduler started")
        except Exception as e:
            logger.error(f"Failed to initialize scheduler: {e}")
            # Continue without scheduler for now
    
    return True

@app.route('/health')
def health_check():
    """Health check for Render"""
    return jsonify({
        "status": "healthy",
        "timestamp": get_central_time().isoformat(),
        "service": "st-edward-calendar-sync",
        "timezone": "America/Chicago"
    }), 200

@app.route('/health/detailed')
def detailed_health():
    """Detailed health check"""
    checks = {}
    
    # Check authentication
    if auth_manager:
        checks['authenticated'] = auth_manager.is_authenticated()
    else:
        checks['authenticated'] = False
    
    # Check sync engine
    checks['sync_engine_loaded'] = sync_engine is not None
    
    # Check scheduler
    if scheduler:
        checks['scheduler_running'] = scheduler.is_running()
    else:
        checks['scheduler_running'] = False
    
    status = "healthy" if all(checks.values()) else "degraded"
    
    return jsonify({
        "status": status,
        "timestamp": get_central_time().isoformat(),
        "timezone": "America/Chicago",
        "checks": checks
    })

@app.route('/status')
def get_status():
    """Get current system status"""
    try:
        if not auth_manager:
            initialize_components()
        
        status = {
            "authenticated": auth_manager.is_authenticated() if auth_manager else False,
            "sync_in_progress": False,
            "last_sync_time": None,
            "last_sync_time_display": "Never",
            "last_sync_result": {"success": False, "message": "Not synced yet"},
            "scheduler_running": scheduler.is_running() if scheduler else False,
            "dry_run_mode": getattr(config, 'DRY_RUN_MODE', False),
            "circuit_breaker_state": "closed",
            "rate_limit_remaining": 20,
            "total_syncs": 0,
            "timezone": "America/Chicago",
            "current_time": get_central_time().isoformat()
        }
        
        if sync_engine:
            engine_status = sync_engine.get_status()
            status.update(engine_status)
            
            # Format the last sync time for display
            if engine_status.get('last_sync_time'):
                try:
                    last_sync_dt = datetime.fromisoformat(engine_status['last_sync_time'].replace('Z', '+00:00'))
                    status['last_sync_time_display'] = format_central_time(last_sync_dt)
                except:
                    status['last_sync_time_display'] = "Unknown"
        
        return jsonify(status)
        
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return jsonify({
            "error": str(e),
            "authenticated": False,
            "sync_in_progress": False
        }), 500

@app.route('/')
def index():
    """Main dashboard"""
    try:
        # Initialize components if needed
        initialize_components()
        
        if not auth_manager or not auth_manager.is_authenticated():
            # Show auth page
            state = secrets.token_urlsafe(16)
            session['oauth_state'] = state
            auth_url = auth_manager.get_auth_url(state) if auth_manager else "#"
            
            return f'''
            <!DOCTYPE html>
            <html>
            <head>
                <title>St. Edward Calendar Sync</title>
                <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>📅</text></svg>">
            </head>
            <body style="font-family: Arial; text-align: center; margin-top: 100px; background: #f5f5f5;">
                <div style="background: white; max-width: 500px; margin: 0 auto; padding: 40px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h1 style="color: #005921;">🗓️ St. Edward Calendar Sync</h1>
                    <p style="margin: 20px 0; color: #666;">Automated synchronization between internal and public calendars</p>
                    <a href="{auth_url}" style="background: #0078d4; color: white; padding: 15px 30px; text-decoration: none; border-radius: 5px; font-size: 18px; display: inline-block;">
                        Sign in with Microsoft
                    </a>
                    <p style="margin-top: 30px; font-size: 12px; color: #999;">
                        St. Edward Church & School • Nashville, TN
                    </p>
                </div>
            </body>
            </html>
            '''
        
        # User is authenticated - show dashboard
        last_sync_time = None
        last_sync_time_display = "Never"
        
        if sync_engine:
            status = sync_engine.get_status()
            last_sync_time_str = status.get('last_sync_time')
            
            # Convert ISO string back to datetime object and format for display
            if last_sync_time_str:
                try:
                    last_sync_dt = datetime.fromisoformat(last_sync_time_str.replace('Z', '+00:00'))
                    last_sync_time = last_sync_dt  # Keep original for template
                    last_sync_time_display = format_central_time(last_sync_dt)
                except:
                    last_sync_time = None
                    last_sync_time_display = "Never"
        
        return render_template('index.html', 
                             last_sync_time=last_sync_time,
                             last_sync_time_display=last_sync_time_display,
                             authenticated=True)
        
    except Exception as e:
        logger.error(f"Index route error: {e}")
        return f'''
        <html><body style="font-family: Arial; text-align: center; margin-top: 100px;">
            <h1>⚠️ Error</h1>
            <p>System error: {str(e)}</p>
            <p>The service is starting up. Please refresh in a moment.</p>
        </body></html>
        ''', 500

@app.route('/sync', methods=['POST'])
def trigger_sync():
    """Trigger manual sync"""
    try:
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated", "redirect": "/"}), 401
        
        result = sync_engine.sync_calendars()
        
        # Add formatted time to result
        if result.get('success') and sync_engine.last_sync_time:
            result['last_sync_time_display'] = format_central_time(sync_engine.last_sync_time)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Sync trigger error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/auth/callback')
def auth_callback():
    """OAuth callback"""
    try:
        if not auth_manager:
            initialize_components()
        
        code = request.args.get('code')
        state = request.args.get('state')
        
        if not code or not state or state != session.get('oauth_state'):
            return "Authentication failed: Invalid request", 400
        
        if auth_manager and auth_manager.exchange_code_for_token(code):
            return redirect('/')
        else:
            return "Authentication failed: Could not exchange token", 400
            
    except Exception as e:
        logger.error(f"Auth callback error: {e}")
        return f"Authentication error: {e}", 500

@app.route('/logout')
def logout():
    """Clear session and tokens"""
    try:
        session.clear()
        if auth_manager:
            auth_manager.clear_tokens()
        return redirect('/')
    except Exception as e:
        logger.error(f"Logout error: {e}")
        return redirect('/')

@app.route('/debug')
def debug_info():
    """Basic debug information"""
    try:
        debug_data = {
            "timestamp": get_central_time().isoformat(),
            "timezone": "America/Chicago",
            "current_time_display": format_central_time(get_central_time()),
            "environment_vars": {
                "CLIENT_ID": bool(os.environ.get('CLIENT_ID')),
                "CLIENT_SECRET": bool(os.environ.get('CLIENT_SECRET')),
                "TENANT_ID": bool(os.environ.get('TENANT_ID')),
                "ACCESS_TOKEN": bool(os.environ.get('ACCESS_TOKEN')),
                "REFRESH_TOKEN": bool(os.environ.get('REFRESH_TOKEN')),
            },
            "config_loaded": True,
            "components": {
                "auth_manager": auth_manager is not None,
                "sync_engine": sync_engine is not None,
                "scheduler": scheduler is not None,
                "scheduler_running": scheduler.is_running() if scheduler else False
            }
        }
        
        if auth_manager:
            debug_data["authenticated"] = auth_manager.is_authenticated()
        
        return jsonify(debug_data)
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/metrics')
def get_metrics():
    """Get system metrics"""
    try:
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        metrics = sync_engine.metrics.get_metrics_summary()
        
        # Add timezone info
        metrics['timezone'] = 'America/Chicago'
        metrics['report_time'] = format_central_time(get_central_time())
        
        return jsonify(metrics)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/history')
def get_history():
    """Get sync history"""
    try:
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        stats = sync_engine.history.get_statistics()
        
        # Format times in history
        if stats.get('last_sync'):
            try:
                dt = datetime.fromisoformat(stats['last_sync'])
                stats['last_sync_display'] = format_central_time(dt)
            except:
                pass
        
        if stats.get('last_successful_sync'):
            try:
                dt = datetime.fromisoformat(stats['last_successful_sync'])
                stats['last_successful_sync_display'] = format_central_time(dt)
            except:
                pass
        
        return jsonify(stats)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/validate-sync', methods=['POST'])
def validate_sync():
    """Validate current sync state"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        # Get calendar IDs
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        
        if not source_id or not target_id:
            return jsonify({"error": "Required calendars not found"}), 404
        
        # Get events
        source_events = sync_engine.reader.get_public_events(source_id)
        target_events = sync_engine.reader.get_calendar_events(target_id)
        
        if not source_events or not target_events:
            return jsonify({"error": "Could not retrieve calendar events"}), 500
        
        # Validate
        validation_report = sync_engine.validator.generate_validation_report(
            source_events, target_events, {"success": True}
        )
        
        # Add formatted timestamp
        validation_report['report_time_display'] = format_central_time(get_central_time())
        
        return jsonify(validation_report)
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/dry-run/enable')
def enable_dry_run():
    """Enable dry run mode"""
    config.DRY_RUN_MODE = True
    return jsonify({"message": "Dry run mode enabled", "dry_run": True})

@app.route('/dry-run/disable') 
def disable_dry_run():
    """Disable dry run mode"""
    config.DRY_RUN_MODE = False
    return jsonify({"message": "Dry run mode disabled", "dry_run": False})

@app.route('/restart-scheduler')
def restart_scheduler():
    """Restart the scheduler"""
    global scheduler
    try:
        if scheduler:
            scheduler.stop()
        
        if sync_engine:
            from sync.scheduler import SyncScheduler
            scheduler = SyncScheduler(sync_engine)
            scheduler.start()
            return jsonify({"message": "Scheduler restarted", "running": scheduler.is_running()})
        else:
            return jsonify({"error": "Sync engine not available"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/debug/calendars')
def debug_calendars():
    """Debug: List all available calendars"""
    try:
        if not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        if not sync_engine:
            return jsonify({"error": "Sync engine not initialized"}), 500
        
        # Get all calendars
        all_calendars = sync_engine.reader.get_calendars()
        
        if not all_calendars:
            return jsonify({"error": "Could not retrieve calendars"}), 500
        
        # Format calendar info
        calendar_info = []
        for cal in all_calendars:
            calendar_info.append({
                "name": cal.get('name'),
                "id": cal.get('id'),
                "owner": cal.get('owner', {}).get('name', 'Unknown'),
                "canEdit": cal.get('canEdit', False),
                "canShare": cal.get('canShare', False)
            })
        
        # Also show which ones we're configured to use
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        
        return jsonify({
            "all_calendars": calendar_info,
            "configuration": {
                "source_calendar_name": config.SOURCE_CALENDAR,
                "target_calendar_name": config.TARGET_CALENDAR,
                "source_calendar_id": source_id,
                "target_calendar_id": target_id,
                "shared_mailbox": config.SHARED_MAILBOX
            },
            "timezone": "America/Chicago",
            "current_time": format_central_time(get_central_time()),
            "web_calendar_url": "https://outlook.office365.com/owa/calendar/fbd704b50f2540068cb048469a830830@stedward.org/e399b41349f3441ca9b092248fc807f56673636587944979855/calendar.html"
        })
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

def signal_handler(sig, frame):
    """Handle shutdown signals"""
    logger.info("Received shutdown signal, cleaning up...")
    if scheduler:
        scheduler.stop()
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Initialize on startup
logger.info("🚀 Starting St. Edward Calendar Sync")
logger.info(f"🕐 Server timezone: America/Chicago")
logger.info(f"🕐 Current time: {format_central_time(get_central_time())}")
initialize_components()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting calendar sync service on port {port}")
    app.run(host='0.0.0.0', port=port)
