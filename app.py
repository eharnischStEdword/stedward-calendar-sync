#!/usr/bin/env python3
"""
St. Edward Calendar Sync - Complete Production Version
"""
import os
import logging
import secrets
import traceback
import signal
import sys
import config  # <-- ADDED THIS IMPORT
from datetime import datetime
from flask import Flask, render_template, jsonify, redirect, session, request

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
        "timestamp": datetime.now().isoformat(),
        "service": "st-edward-calendar-sync"
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
        "timestamp": datetime.now().isoformat(),
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
            "last_sync_result": {"success": False, "message": "Not synced yet"},
            "scheduler_running": scheduler.is_running() if scheduler else False,
            "dry_run_mode": getattr(config, 'DRY_RUN_MODE', False),
            "circuit_breaker_state": "closed",
            "rate_limit_remaining": 20,
            "total_syncs": 0
        }
        
        if sync_engine:
            engine_status = sync_engine.get_status()
            status.update(engine_status)
        
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
                        St. Edward Church & School
                    </p>
                </div>
            </body>
            </html>
            '''
        
        # User is authenticated - show dashboard
        last_sync_time = None
        if sync_engine:
            status = sync_engine.get_status()
            last_sync_time = status.get('last_sync_time')
        
        return render_template('index.html', 
                             last_sync_time=last_sync_time,
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
    """Debug information"""
    try:
        debug_data = {
            "timestamp": datetime.now().isoformat(),
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

@app.route('/debug/signatures')
def debug_signatures():
    """Debug endpoint to analyze event signatures"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated or sync engine not available"}), 401
        
        # Get calendar IDs
        source_id = sync_engine.reader.find_calendar_id(config.SOURCE_CALENDAR)
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        
        if not source_id or not target_id:
            return jsonify({"error": "Could not find required calendars"}), 404
        
        # Get events
        source_events = sync_engine.reader.get_public_events(source_id)
        target_events = sync_engine.reader.get_calendar_events(target_id)
        
        if not source_events or not target_events:
            return jsonify({"error": "Could not retrieve calendar events"}), 500
        
        # Analyze signatures
        analysis = {
            "source_events": len(source_events),
            "target_events": len(target_events),
            "source_signatures": {},
            "target_signatures": {},
            "signature_analysis": {
                "source_duplicates": [],
                "target_duplicates": [],
                "missing_in_target": [],
                "extra_in_target": [],
                "signature_collisions": []
            }
        }
        
        # Analyze source events
        source_sig_count = {}
        for event in source_events:
            sig = sync_engine._create_event_signature(event)
            if sig.startswith("skip:"):
                continue
                
            analysis["source_signatures"][sig] = {
                "subject": event.get('subject'),
                "start": event.get('start', {}).get('dateTime'),
                "type": event.get('type'),
                "id": event.get('id')
            }
            
            source_sig_count[sig] = source_sig_count.get(sig, 0) + 1
            if source_sig_count[sig] > 1:
                analysis["signature_analysis"]["source_duplicates"].append({
                    "signature": sig,
                    "count": source_sig_count[sig],
                    "subject": event.get('subject')
                })
        
        # Analyze target events
        target_sig_count = {}
        for event in target_events:
            sig = sync_engine._create_event_signature(event)
            if sig.startswith("skip:"):
                continue
                
            analysis["target_signatures"][sig] = {
                "subject": event.get('subject'),
                "start": event.get('start', {}).get('dateTime'),
                "type": event.get('type'),
                "id": event.get('id')
            }
            
            target_sig_count[sig] = target_sig_count.get(sig, 0) + 1
            if target_sig_count[sig] > 1:
                analysis["signature_analysis"]["target_duplicates"].append({
                    "signature": sig,
                    "count": target_sig_count[sig],
                    "subject": event.get('subject')
                })
        
        # Find missing and extra events
        source_sigs = set(analysis["source_signatures"].keys())
        target_sigs = set(analysis["target_signatures"].keys())
        
        missing_sigs = source_sigs - target_sigs
        extra_sigs = target_sigs - source_sigs
        
        for sig in missing_sigs:
            analysis["signature_analysis"]["missing_in_target"].append({
                "signature": sig,
                "subject": analysis["source_signatures"][sig]["subject"]
            })
        
        for sig in extra_sigs:
            analysis["signature_analysis"]["extra_in_target"].append({
                "signature": sig,
                "subject": analysis["target_signatures"][sig]["subject"]
            })
        
        # Check for signature collisions (same signature, different events)
        for sig in source_sigs & target_sigs:
            source_event = analysis["source_signatures"][sig]
            target_event = analysis["target_signatures"][sig]
            
            if (source_event["subject"] != target_event["subject"] or 
                source_event["start"] != target_event["start"]):
                analysis["signature_analysis"]["signature_collisions"].append({
                    "signature": sig,
                    "source": source_event,
                    "target": target_event
                })
        
        return jsonify(analysis)
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/duplicates')
def debug_duplicates():
    """Find and analyze duplicate events in target calendar"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        target_events = sync_engine.reader.get_calendar_events(target_id)
        if not target_events:
            return jsonify({"error": "Could not retrieve target events"}), 500
        
        # Group events by subject and start time
        duplicates = {}
        subject_groups = {}
        
        for event in target_events:
            subject = event.get('subject', '').lower().strip()
            start = event.get('start', {}).get('dateTime', '')
            
            # Group by subject
            if subject not in subject_groups:
                subject_groups[subject] = []
            subject_groups[subject].append({
                'id': event.get('id'),
                'subject': event.get('subject'),
                'start': start,
                'created': event.get('createdDateTime'),
                'modified': event.get('lastModifiedDateTime'),
                'signature': sync_engine._create_event_signature(event)
            })
        
        # Find duplicates
        for subject, events in subject_groups.items():
            if len(events) > 1:
                # Group by start time within same subject
                time_groups = {}
                for event in events:
                    start_key = event['start'][:16] if event['start'] else 'no-time'  # Group by date+hour+minute
                    if start_key not in time_groups:
                        time_groups[start_key] = []
                    time_groups[start_key].append(event)
                
                # Find time groups with multiple events
                for start_time, time_events in time_groups.items():
                    if len(time_events) > 1:
                        duplicates[f"{subject}_{start_time}"] = {
                            'subject': subject,
                            'start_time': start_time,
                            'count': len(time_events),
                            'events': time_events
                        }
        
        return jsonify({
            "total_events": len(target_events),
            "duplicate_groups": len(duplicates),
            "duplicates": duplicates,
            "summary": {
                "total_duplicate_events": sum(dup['count'] for dup in duplicates.values()),
                "subjects_with_duplicates": list(duplicates.keys())
            }
        })
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/clean-duplicates', methods=['POST'])
def clean_duplicates():
    """Remove duplicate events from target calendar"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        # Get target calendar
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        target_events = sync_engine.reader.get_calendar_events(target_id)
        if not target_events:
            return jsonify({"error": "Could not retrieve target events"}), 500
        
        # Group events by signature
        signature_groups = {}
        for event in target_events:
            sig = sync_engine._create_event_signature(event)
            if sig.startswith("skip:"):
                continue
                
            if sig not in signature_groups:
                signature_groups[sig] = []
            signature_groups[sig].append(event)
        
        # Find duplicates and keep only the newest
        to_delete = []
        kept_count = 0
        
        for sig, events in signature_groups.items():
            if len(events) > 1:
                # Sort by creation time, keep the newest
                events.sort(key=lambda x: x.get('createdDateTime', ''), reverse=True)
                kept_event = events[0]
                duplicates = events[1:]
                
                kept_count += 1
                to_delete.extend(duplicates)
                
                logger.info(f"Found {len(duplicates)} duplicates for '{kept_event.get('subject')}', keeping newest")
        
        # Delete duplicates
        deleted_count = 0
        failed_deletes = []
        
        for event in to_delete:
            event_id = event.get('id')
            if sync_engine.writer.delete_event(target_id, event_id):
                deleted_count += 1
                logger.info(f"Deleted duplicate: {event.get('subject')} (ID: {event_id[:8]}...)")
            else:
                failed_deletes.append(event_id)
                logger.error(f"Failed to delete: {event.get('subject')} (ID: {event_id[:8]}...)")
        
        return jsonify({
            "success": True,
            "total_events_found": len(target_events),
            "duplicate_groups": len([g for g in signature_groups.values() if len(g) > 1]),
            "duplicates_found": len(to_delete),
            "duplicates_deleted": deleted_count,
            "failed_deletes": len(failed_deletes),
            "events_kept": kept_count + len([g for g in signature_groups.values() if len(g) == 1]),
            "failed_delete_ids": failed_deletes
        })
        
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

@app.route('/debug/status')
def debug_status():
    """Debug the current state and show what's wrong"""
    return '''
    <html><body style="font-family: Arial; padding: 20px;">
        <h1>🔧 Debug Status & Cleanup</h1>
        
        <button onclick="checkStatus()" style="background: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; margin: 5px;">
            Check Status
        </button>
        
        <button onclick="checkDuplicates()" style="background: #ffc107; color: black; padding: 10px 20px; border: none; border-radius: 5px; margin: 5px;">
            Check Duplicates
        </button>
        
        <button onclick="cleanDupes()" style="background: #dc3545; color: white; padding: 10px 20px; border: none; border-radius: 5px; margin: 5px;">
            Clean Duplicates
        </button>
        
        <div id="result" style="margin-top: 20px; padding: 15px; background: #f8f9fa; border-radius: 5px; max-height: 400px; overflow-y: auto;"></div>
        
        <script>
        function log(message) {
            const result = document.getElementById('result');
            result.innerHTML += '<div style="margin: 5px 0; padding: 5px; background: white; border-left: 3px solid #007bff;">' + message + '</div>';
            result.scrollTop = result.scrollHeight;
        }
        
        async function checkStatus() {
            log('🔍 Checking system status...');
            try {
                const response = await fetch('/status');
                const data = await response.json();
                log('<strong>✅ Status Response:</strong><br><pre>' + JSON.stringify(data, null, 2) + '</pre>');
            } catch (error) {
                log('❌ Status Error: ' + error.message);
            }
        }
        
        async function checkDuplicates() {
            log('🔍 Checking for duplicates...');
            try {
                const response = await fetch('/debug/duplicates');
                const data = await response.json();
                log('<strong>📊 Duplicates Found:</strong><br><pre>' + JSON.stringify(data, null, 2) + '</pre>');
            } catch (error) {
                log('❌ Duplicates Check Error: ' + error.message);
            }
        }
        
        async function cleanDupes() {
            log('🧹 Starting cleanup...');
            try {
                const response = await fetch('/debug/clean-duplicates', {method: 'POST'});
                const data = await response.json();
                
                log('<strong>🗑️ Cleanup Response:</strong><br><pre>' + JSON.stringify(data, null, 2) + '</pre>');
                
                if (data.success) {
                    log('<strong>✅ Cleanup Summary:</strong>');
                    log('• Total events: ' + (data.total_events_found || 'unknown'));
                    log('• Duplicates deleted: ' + (data.duplicates_deleted || 'unknown'));
                    log('• Events kept: ' + (data.events_kept || 'unknown'));
                    log('• Failed deletes: ' + (data.failed_deletes || 'unknown'));
                } else {
                    log('<strong>❌ Cleanup failed:</strong> ' + (data.error || 'Unknown error'));
                }
            } catch (error) {
                log('❌ Cleanup Error: ' + error.message);
            }
        }
        
        // Clear log
        function clearLog() {
            document.getElementById('result').innerHTML = '';
        }
        </script>
        
        <br><button onclick="clearLog()" style="background: #6c757d; color: white; padding: 5px 15px; border: none; border-radius: 3px;">Clear Log</button>
    </body></html>
    '''

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
initialize_components()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting calendar sync service on port {port}")
    app.run(host='0.0.0.0', port=port)
