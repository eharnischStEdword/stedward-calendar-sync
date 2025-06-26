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

# Add this improved duplicate detector to your app.py

@app.route('/debug/precise-duplicates')
def precise_duplicates():
    """Find TRUE duplicates - identical events that shouldn't exist twice"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        target_events = sync_engine.reader.get_calendar_events(target_id)
        if not target_events:
            return jsonify({"error": "Could not retrieve target events"}), 500
        
        # Group events by EXACT match criteria for true duplicates
        exact_groups = {}
        
        for event in target_events:
            subject = event.get('subject', '').strip()
            start_datetime = event.get('start', {}).get('dateTime', '')
            end_datetime = event.get('end', {}).get('dateTime', '')
            is_all_day = event.get('isAllDay', False)
            event_type = event.get('type', 'singleInstance')
            
            # Skip recurring series masters and occurrences for now - focus on single events
            if event_type in ['seriesMaster', 'occurrence']:
                continue
                
            # Create precise matching key
            # For single events: subject + start + end + all-day flag
            key = f"{subject}|{start_datetime}|{end_datetime}|{is_all_day}"
            
            if key not in exact_groups:
                exact_groups[key] = []
            
            exact_groups[key].append({
                'id': event.get('id'),
                'subject': event.get('subject'),
                'start': start_datetime,
                'end': end_datetime,
                'isAllDay': is_all_day,
                'type': event_type,
                'created': event.get('createdDateTime'),
                'modified': event.get('lastModifiedDateTime'),
                'categories': event.get('categories', [])
            })
        
        # Find groups with multiple identical events
        true_duplicates = {}
        for key, events in exact_groups.items():
            if len(events) > 1:
                # Parse the key for display
                parts = key.split('|')
                subject = parts[0]
                start = parts[1]
                
                # Extract date for easier reading
                date_part = start.split('T')[0] if 'T' in start else start
                
                true_duplicates[key] = {
                    'subject': subject,
                    'date': date_part,
                    'start_time': start,
                    'count': len(events),
                    'events': events,
                    'description': f"'{subject}' on {date_part}"
                }
        
        return jsonify({
            "total_events_checked": len(target_events),
            "true_duplicate_groups": len(true_duplicates),
            "true_duplicates": true_duplicates,
            "summary": {
                "total_duplicate_events": sum(dup['count'] for dup in true_duplicates.values()),
                "duplicate_descriptions": [dup['description'] for dup in true_duplicates.values()]
            }
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/clean-precise-duplicates', methods=['POST'])
def clean_precise_duplicates():
    """Remove TRUE duplicates only - keeps legitimate recurring series"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        target_events = sync_engine.reader.get_calendar_events(target_id)
        if not target_events:
            return jsonify({"error": "Could not retrieve target events"}), 500
        
        # Group events by EXACT match criteria
        exact_groups = {}
        
        for event in target_events:
            subject = event.get('subject', '').strip()
            start_datetime = event.get('start', {}).get('dateTime', '')
            end_datetime = event.get('end', {}).get('dateTime', '')
            is_all_day = event.get('isAllDay', False)
            event_type = event.get('type', 'singleInstance')
            
            # Skip recurring series for this cleanup - only handle single events
            if event_type in ['seriesMaster', 'occurrence']:
                continue
                
            # Create precise matching key
            key = f"{subject}|{start_datetime}|{end_datetime}|{is_all_day}"
            
            if key not in exact_groups:
                exact_groups[key] = []
            exact_groups[key].append(event)
        
        # Find true duplicates and keep only the newest
        to_delete = []
        kept_count = 0
        duplicate_groups_found = 0
        
        for key, events in exact_groups.items():
            if len(events) > 1:
                duplicate_groups_found += 1
                
                # Sort by creation time, keep the newest
                events.sort(key=lambda x: x.get('createdDateTime', ''), reverse=True)
                kept_event = events[0]
                duplicates = events[1:]
                
                kept_count += 1
                to_delete.extend(duplicates)
                
                subject = kept_event.get('subject', 'Unknown')
                start = kept_event.get('start', {}).get('dateTime', '')
                date_part = start.split('T')[0] if 'T' in start else start
                
                logger.info(f"Found {len(duplicates)} TRUE duplicates for '{subject}' on {date_part}, keeping newest")
        
        # Delete the true duplicates
        deleted_count = 0
        failed_deletes = []
        
        for event in to_delete:
            event_id = event.get('id')
            subject = event.get('subject', 'Unknown')
            
            if sync_engine.writer.delete_event(target_id, event_id):
                deleted_count += 1
                logger.info(f"Deleted TRUE duplicate: {subject} (ID: {event_id[:8]}...)")
            else:
                failed_deletes.append(event_id)
                logger.error(f"Failed to delete TRUE duplicate: {subject} (ID: {event_id[:8]}...)")
        
        return jsonify({
            "success": True,
            "total_events_found": len(target_events),
            "true_duplicate_groups": duplicate_groups_found,
            "true_duplicates_found": len(to_delete),
            "true_duplicates_deleted": deleted_count,
            "failed_deletes": len(failed_deletes),
            "events_kept": kept_count + len([g for g in exact_groups.values() if len(g) == 1]),
            "failed_delete_ids": failed_deletes,
            "note": "This only removes TRUE duplicates (identical single events), not legitimate recurring series with same names"
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

# Add this BETTER duplicate detector that catches what you see visually

@app.route('/debug/visual-duplicates')
def visual_duplicates():
    """Find ALL duplicates - including recurring event occurrences that show up as visual duplicates"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        target_events = sync_engine.reader.get_calendar_events(target_id)
        if not target_events:
            return jsonify({"error": "Could not retrieve target events"}), 500
        
        # Group by VISUAL appearance - how they look in the calendar
        visual_groups = {}
        
        for event in target_events:
            subject = event.get('subject', '').strip()
            start_datetime = event.get('start', {}).get('dateTime', '')
            
            # Extract date and time for visual grouping
            if 'T' in start_datetime:
                date_part = start_datetime.split('T')[0]  # YYYY-MM-DD
                time_part = start_datetime.split('T')[1][:5]  # HH:MM
                visual_key = f"{subject}|{date_part}|{time_part}"
            else:
                visual_key = f"{subject}|{start_datetime}|no-time"
            
            if visual_key not in visual_groups:
                visual_groups[visual_key] = []
            
            visual_groups[visual_key].append({
                'id': event.get('id'),
                'subject': event.get('subject'),
                'start': start_datetime,
                'type': event.get('type'),
                'created': event.get('createdDateTime'),
                'modified': event.get('lastModifiedDateTime'),
                'series_master_id': event.get('seriesMasterId'),
                'categories': event.get('categories', [])
            })
        
        # Find visual duplicates
        visual_duplicates = {}
        for key, events in visual_groups.items():
            if len(events) > 1:
                # Parse the key for display
                parts = key.split('|')
                subject = parts[0]
                date = parts[1]
                time = parts[2] if len(parts) > 2 else 'no-time'
                
                visual_duplicates[key] = {
                    'subject': subject,
                    'date': date,
                    'time': time,
                    'count': len(events),
                    'events': events,
                    'description': f"'{subject}' on {date} at {time}",
                    'types': [e['type'] for e in events],
                    'series_masters': list(set([e['series_master_id'] for e in events if e['series_master_id']]))
                }
        
        return jsonify({
            "total_events_checked": len(target_events),
            "visual_duplicate_groups": len(visual_duplicates),
            "visual_duplicates": visual_duplicates,
            "summary": {
                "total_duplicate_events": sum(dup['count'] for dup in visual_duplicates.values()),
                "duplicate_descriptions": [dup['description'] for dup in visual_duplicates.values()]
            }
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/clean-visual-duplicates', methods=['POST'])
def clean_visual_duplicates():
    """Remove visual duplicates - keeps one copy of each event that appears multiple times"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        target_events = sync_engine.reader.get_calendar_events(target_id)
        if not target_events:
            return jsonify({"error": "Could not retrieve target events"}), 500
        
        # Group by visual appearance
        visual_groups = {}
        
        for event in target_events:
            subject = event.get('subject', '').strip()
            start_datetime = event.get('start', {}).get('dateTime', '')
            
            # Create visual key
            if 'T' in start_datetime:
                date_part = start_datetime.split('T')[0]
                time_part = start_datetime.split('T')[1][:5]
                visual_key = f"{subject}|{date_part}|{time_part}"
            else:
                visual_key = f"{subject}|{start_datetime}|no-time"
            
            if visual_key not in visual_groups:
                visual_groups[visual_key] = []
            visual_groups[visual_key].append(event)
        
        # Find duplicates and delete extras
        to_delete = []
        kept_count = 0
        duplicate_groups_found = 0
        
        for key, events in visual_groups.items():
            if len(events) > 1:
                duplicate_groups_found += 1
                
                # Sort by preference: seriesMaster > singleInstance > occurrence
                # Then by creation time (newest first)
                def sort_preference(event):
                    type_priority = {
                        'seriesMaster': 0,
                        'singleInstance': 1, 
                        'occurrence': 2
                    }
                    event_type = event.get('type', 'singleInstance')
                    created = event.get('createdDateTime', '')
                    return (type_priority.get(event_type, 9), created)
                
                events.sort(key=sort_preference, reverse=True)
                kept_event = events[0]
                duplicates = events[1:]
                
                kept_count += 1
                to_delete.extend(duplicates)
                
                # Parse key for logging
                parts = key.split('|')
                subject = parts[0]
                date = parts[1]
                time = parts[2] if len(parts) > 2 else 'no-time'
                
                logger.info(f"Found {len(duplicates)} VISUAL duplicates for '{subject}' on {date} at {time}")
                logger.info(f"  Keeping: {kept_event.get('type')} (ID: {kept_event.get('id', '')[:8]}...)")
                for dup in duplicates:
                    logger.info(f"  Deleting: {dup.get('type')} (ID: {dup.get('id', '')[:8]}...)")
        
        # Delete the visual duplicates
        deleted_count = 0
        failed_deletes = []
        
        for event in to_delete:
            event_id = event.get('id')
            subject = event.get('subject', 'Unknown')
            event_type = event.get('type', 'unknown')
            
            if sync_engine.writer.delete_event(target_id, event_id):
                deleted_count += 1
                logger.info(f"✅ Deleted VISUAL duplicate: {subject} ({event_type}) (ID: {event_id[:8]}...)")
            else:
                failed_deletes.append(event_id)
                logger.error(f"❌ Failed to delete VISUAL duplicate: {subject} ({event_type}) (ID: {event_id[:8]}...)")
        
        return jsonify({
            "success": True,
            "total_events_found": len(target_events),
            "visual_duplicate_groups": duplicate_groups_found,
            "visual_duplicates_found": len(to_delete),
            "visual_duplicates_deleted": deleted_count,
            "failed_deletes": len(failed_deletes),
            "events_kept": kept_count + len([g for g in visual_groups.values() if len(g) == 1]),
            "failed_delete_ids": failed_deletes,
            "note": "Removed events that appear identical in calendar view (same subject, date, time)"
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/calendar-view-duplicates')
def calendar_view_duplicates():
    """Check duplicates using calendarView (actual calendar instances)"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        headers = sync_engine.auth.get_headers()
        if not headers:
            return jsonify({"error": "No valid headers"}), 401
        
        # Use calendarView to get actual calendar instances
        import requests
        from datetime import datetime, timedelta
        
        # Get events for next 30 days (including the dates you showed)
        start_date = "2025-06-20T00:00:00.000Z"
        end_date = "2025-07-10T23:59:59.999Z"
        
        url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{target_id}/calendarView"
        params = {
            'startDateTime': start_date,
            'endDateTime': end_date,
            '$select': 'id,subject,start,end,type,seriesMasterId,categories,createdDateTime'
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=30)
        
        if response.status_code != 200:
            return jsonify({"error": f"API error: {response.status_code}", "details": response.text}), 500
        
        events = response.json().get('value', [])
        
        # Group by visual appearance (subject + start time)
        visual_groups = {}
        
        for event in events:
            subject = event.get('subject', '').strip()
            start_time = event.get('start', {}).get('dateTime', '')
            
            # Create visual key
            visual_key = f"{subject}|{start_time}"
            
            if visual_key not in visual_groups:
                visual_groups[visual_key] = []
            visual_groups[visual_key].append({
                'id': event.get('id'),
                'subject': event.get('subject'),
                'start': start_time,
                'type': event.get('type'),
                'seriesMasterId': event.get('seriesMasterId'),
                'created': event.get('createdDateTime')
            })
        
        # Find duplicates
        duplicates = {}
        for key, events_list in visual_groups.items():
            if len(events_list) > 1:
                parts = key.split('|')
                subject = parts[0]
                start_time = parts[1] if len(parts) > 1 else ''
                
                duplicates[key] = {
                    'subject': subject,
                    'start_time': start_time,
                    'count': len(events_list),
                    'events': events_list
                }
        
        return jsonify({
            "total_calendar_instances": len(events),
            "duplicate_groups": len(duplicates),
            "duplicates": duplicates,
            "summary": {
                "total_duplicate_events": sum(dup['count'] for dup in duplicates.values()),
                "date_range": f"{start_date} to {end_date}"
            }
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

# Add these endpoints to your app.py for better diagnostics

@app.route('/debug/comprehensive-duplicates')
def comprehensive_duplicates():
    """Comprehensive duplicate detection across multiple date ranges"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        headers = sync_engine.auth.get_headers()
        if not headers:
            return jsonify({"error": "No valid headers"}), 401
        
        import requests
        from datetime import datetime, timedelta
        
        # Check multiple approaches
        results = {
            "timestamp": datetime.now().isoformat(),
            "calendar_view_analysis": {},
            "events_analysis": {},
            "date_ranges_checked": []
        }
        
        # Test multiple date ranges
        date_ranges = [
            # Past 30 days
            {
                "name": "past_30_days",
                "start": (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000Z"),
                "end": datetime.now().strftime("%Y-%m-%dT23:59:59.999Z")
            },
            # Next 30 days  
            {
                "name": "next_30_days",
                "start": datetime.now().strftime("%Y-%m-%dT00:00:00.000Z"),
                "end": (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%dT23:59:59.999Z")
            },
            # Specific range around June 22-28 (where you saw duplicates)
            {
                "name": "june_focus",
                "start": "2025-06-20T00:00:00.000Z",
                "end": "2025-06-30T23:59:59.999Z"
            }
        ]
        
        for date_range in date_ranges:
            range_name = date_range["name"]
            results["date_ranges_checked"].append(date_range)
            
            # Method 1: CalendarView (actual instances)
            try:
                url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{target_id}/calendarView"
                params = {
                    'startDateTime': date_range["start"],
                    'endDateTime': date_range["end"],
                    '$select': 'id,subject,start,end,type,seriesMasterId,categories,createdDateTime,lastModifiedDateTime'
                }
                
                response = requests.get(url, headers=headers, params=params, timeout=30)
                
                if response.status_code == 200:
                    calendar_events = response.json().get('value', [])
                    
                    # Analyze for visual duplicates
                    visual_groups = {}
                    for event in calendar_events:
                        subject = event.get('subject', '').strip()
                        start_time = event.get('start', {}).get('dateTime', '')
                        
                        # Extract date and hour for grouping
                        if 'T' in start_time:
                            date_part = start_time.split('T')[0]
                            hour_part = start_time.split('T')[1][:2]  # Just hour
                            visual_key = f"{subject}|{date_part}|{hour_part}"
                        else:
                            visual_key = f"{subject}|{start_time}"
                        
                        if visual_key not in visual_groups:
                            visual_groups[visual_key] = []
                        visual_groups[visual_key].append({
                            'id': event.get('id'),
                            'subject': subject,
                            'start': start_time,
                            'type': event.get('type'),
                            'created': event.get('createdDateTime'),
                            'modified': event.get('lastModifiedDateTime')
                        })
                    
                    # Find duplicates
                    duplicates = {k: v for k, v in visual_groups.items() if len(v) > 1}
                    
                    results["calendar_view_analysis"][range_name] = {
                        "total_instances": len(calendar_events),
                        "duplicate_groups": len(duplicates),
                        "duplicates": duplicates,
                        "all_events": calendar_events[:10]  # First 10 for inspection
                    }
                else:
                    results["calendar_view_analysis"][range_name] = {
                        "error": f"API error: {response.status_code}",
                        "details": response.text[:200]
                    }
            except Exception as e:
                results["calendar_view_analysis"][range_name] = {
                    "error": str(e)
                }
            
            # Method 2: Regular events endpoint (for comparison)
            try:
                url = f"https://graph.microsoft.com/v1.0/users/{config.SHARED_MAILBOX}/calendars/{target_id}/events"
                params = {
                    '$select': 'id,subject,start,end,type,seriesMasterId,categories,createdDateTime,lastModifiedDateTime',
                    '$top': 100
                }
                
                response = requests.get(url, headers=headers, params=params, timeout=30)
                
                if response.status_code == 200:
                    logical_events = response.json().get('value', [])
                    
                    results["events_analysis"][range_name] = {
                        "total_events": len(logical_events),
                        "event_types": {},
                        "recurring_events": [],
                        "single_events": []
                    }
                    
                    # Analyze event types
                    type_counts = {}
                    for event in logical_events:
                        event_type = event.get('type', 'unknown')
                        type_counts[event_type] = type_counts.get(event_type, 0) + 1
                        
                        if event_type == 'seriesMaster':
                            results["events_analysis"][range_name]["recurring_events"].append({
                                'subject': event.get('subject'),
                                'id': event.get('id'),
                                'start': event.get('start', {}).get('dateTime'),
                                'recurrence': event.get('recurrence', {}).get('pattern', {}).get('type')
                            })
                        else:
                            results["events_analysis"][range_name]["single_events"].append({
                                'subject': event.get('subject'),
                                'id': event.get('id'),
                                'start': event.get('start', {}).get('dateTime'),
                                'type': event_type
                            })
                    
                    results["events_analysis"][range_name]["event_types"] = type_counts
                else:
                    results["events_analysis"][range_name] = {
                        "error": f"API error: {response.status_code}"
                    }
            except Exception as e:
                results["events_analysis"][range_name] = {
                    "error": str(e)
                }
        
        return jsonify(results)
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

@app.route('/debug/realtime-sync-test', methods=['POST'])
def realtime_sync_test():
    """Trigger a sync and immediately check for duplicates"""
    try:
        if not sync_engine or not auth_manager or not auth_manager.is_authenticated():
            return jsonify({"error": "Not authenticated"}), 401
        
        logger.info("🧪 REALTIME SYNC TEST - Before sync analysis")
        
        # 1. Get state before sync
        target_id = sync_engine.reader.find_calendar_id(config.TARGET_CALENDAR)
        if not target_id:
            return jsonify({"error": "Target calendar not found"}), 404
        
        before_events = sync_engine.reader.get_calendar_events(target_id)
        before_count = len(before_events) if before_events else 0
        
        # 2. Trigger sync
        logger.info("🔄 Triggering sync...")
        sync_result = sync_engine.sync_calendars()
        
        # 3. Get state after sync
        after_events = sync_engine.reader.get_calendar_events(target_id)
        after_count = len(after_events) if after_events else 0
        
        # 4. Check for duplicates immediately after sync
        visual_duplicates = {}
        if after_events:
            visual_groups = {}
            for event in after_events:
                subject = event.get('subject', '').strip()
                start_time = event.get('start', {}).get('dateTime', '')
                
                if 'T' in start_time:
                    date_part = start_time.split('T')[0]
                    time_part = start_time.split('T')[1][:5]
                    visual_key = f"{subject}|{date_part}|{time_part}"
                else:
                    visual_key = f"{subject}|{start_time}"
                
                if visual_key not in visual_groups:
                    visual_groups[visual_key] = []
                visual_groups[visual_key].append({
                    'id': event.get('id'),
                    'subject': subject,
                    'start': start_time,
                    'type': event.get('type'),
                    'created': event.get('createdDateTime')
                })
            
            visual_duplicates = {k: v for k, v in visual_groups.items() if len(v) > 1}
        
        return jsonify({
            "sync_result": sync_result,
            "before_sync": {
                "event_count": before_count
            },
            "after_sync": {
                "event_count": after_count,
                "change": after_count - before_count
            },
            "immediate_duplicate_check": {
                "duplicate_groups_found": len(visual_duplicates),
                "duplicates": visual_duplicates
            },
            "analysis": {
                "duplicates_created_by_sync": len(visual_duplicates) > 0,
                "sync_added_events": sync_result.get('added', 0),
                "net_change": after_count - before_count
            }
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting calendar sync service on port {port}")
    app.run(host='0.0.0.0', port=port)
