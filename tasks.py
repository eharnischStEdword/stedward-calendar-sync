# © 2024–2025 Harnisch LLC. All Rights Reserved.
# Licensed exclusively for use by St. Edward Church & School (Nashville, TN).
# Unauthorized use, distribution, or modification is prohibited.

"""
Background sync tasks and scheduling for St. Edward Calendar Sync
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
import schedule

import config
from utils import DateTimeUtils, structured_logger, cache_manager
from models import CalendarSync

logger = logging.getLogger(__name__)

class SyncScheduler:
    """Manages automated calendar synchronization"""
    
    def __init__(self):
        self.scheduler_running = False
        self.scheduler_lock = threading.Lock()
        self.scheduler_thread = None
        self.sync_engine = None
        
        # State tracking
        self.last_sync_time = None
        self.last_sync_result = None
        self.sync_in_progress = False
        
        # Health check state
        self.last_health_check = None
        self.health_check_failures = 0
    
    def start(self):
        """Start the scheduler"""
        with self.scheduler_lock:
            if self.scheduler_running:
                logger.info("Scheduler already running")
                return
            
            self.scheduler_running = True
            self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
            self.scheduler_thread.start()
            logger.info("✅ Scheduler started")
    
    def stop(self):
        """Stop the scheduler"""
        with self.scheduler_lock:
            self.scheduler_running = False
        
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
            logger.info("✅ Scheduler stopped")
    
    def is_running(self) -> bool:
        """Check if scheduler is running"""
        with self.scheduler_lock:
            return self.scheduler_running
    
    def set_sync_engine(self, sync_engine):
        """Set the sync engine instance"""
        self.sync_engine = sync_engine
    
    def _run_scheduler(self):
        """Run the scheduler loop"""
        # Schedule sync to run every 23 minutes with built-in health check
        schedule.every(config.SYNC_INTERVAL_MIN).minutes.do(self._scheduled_sync_with_health_check)
        
        logger.info(f"Scheduler started - sync with health check every {config.SYNC_INTERVAL_MIN} minutes (CT) - started at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
        
        # Add startup delay to prevent immediate sync after deployment
        logger.info("⏳ Waiting 2 minutes before first scheduled sync to allow deployment to stabilize...")
        time.sleep(120)  # Wait 2 minutes before first sync
        
        while True:
            with self.scheduler_lock:
                if not self.scheduler_running:
                    break
            
            schedule.run_pending()
            time.sleep(60)  # Check every minute
        
        logger.info(f"Scheduler stopped at {DateTimeUtils.format_central_time(DateTimeUtils.get_central_time())}")
    
    def _scheduled_sync_with_health_check(self):
        """Run health check before sync"""
        logger.info("🔍 Running scheduled sync with health check...")
        
        # Run health check first
        if not self._run_health_check():
            logger.warning("⚠️ Health check failed, skipping scheduled sync")
            return
        
        # Health check passed, run sync
        self._scheduled_sync()
    
    def _run_health_check(self) -> bool:
        """Run comprehensive health check"""
        try:
            logger.info("🏥 Running health check...")
            
            # Check authentication
            if not self.sync_engine or not self.sync_engine.auth_manager:
                logger.error("❌ Health check failed: No sync engine or auth manager")
                return False
            
            if not self.sync_engine.auth_manager.is_authenticated():
                logger.error("❌ Health check failed: Not authenticated")
                return False
            
            # Check sync engine availability
            if not hasattr(self.sync_engine, 'reader') or not self.sync_engine.reader:
                logger.error("❌ Health check failed: No calendar reader")
                return False
            
            # Test calendar access (lightweight)
            try:
                calendars = self.sync_engine.reader.get_calendars()
                if not calendars:
                    logger.error("❌ Health check failed: Cannot access calendars")
                    return False
                
                logger.info(f"✅ Health check passed: Found {len(calendars)} calendars")
                self.last_health_check = DateTimeUtils.get_central_time()
                self.health_check_failures = 0
                return True
                
            except Exception as e:
                logger.error(f"❌ Health check failed: Calendar access error - {e}")
                self.health_check_failures += 1
                return False
                
        except Exception as e:
            logger.error(f"❌ Health check failed: {e}")
            self.health_check_failures += 1
            return False
    
    def _scheduled_sync(self):
        """Run scheduled sync operation"""
        if not self.sync_engine:
            logger.error("❌ No sync engine available for scheduled sync")
            return
        
        if self.sync_in_progress:
            logger.info("⏳ Sync already in progress, skipping scheduled sync")
            return
        
        try:
            logger.info("🔄 Starting scheduled sync...")
            self.sync_in_progress = True
            
            result = self.sync_engine.sync_calendars()
            
            self.last_sync_time = DateTimeUtils.get_central_time()
            self.last_sync_result = result
            
            if result.get('success'):
                logger.info("✅ Scheduled sync completed successfully")
                structured_logger.log_sync_event('scheduled_sync_success', {
                    'duration': result.get('duration', 0),
                    'operations': result.get('successful_operations', 0)
                })
            else:
                logger.error(f"❌ Scheduled sync failed: {result.get('message', 'Unknown error')}")
                structured_logger.log_sync_event('scheduled_sync_failed', {
                    'error': result.get('message', 'Unknown error')
                })
                
        except Exception as e:
            logger.error(f"❌ Scheduled sync exception: {e}")
            structured_logger.log_sync_event('scheduled_sync_exception', {
                'error': str(e)
            })
        finally:
            self.sync_in_progress = False
    
    def get_status(self) -> Dict[str, Any]:
        """Get scheduler status"""
        return {
            'running': self.is_running(),
            'last_sync_time': self.last_sync_time.isoformat() if self.last_sync_time else None,
            'last_sync_time_display': DateTimeUtils.format_central_time(self.last_sync_time),
            'last_sync_result': self.last_sync_result,
            'sync_in_progress': self.sync_in_progress,
            'last_health_check': self.last_health_check.isoformat() if self.last_health_check else None,
            'health_check_failures': self.health_check_failures,
            'sync_interval_minutes': config.SYNC_INTERVAL_MIN
        }

class BackgroundTasks:
    """Manages background task execution"""
    
    def __init__(self):
        self.tasks = {}
        self.task_threads = {}
    
    def run_in_background(self, task_name: str, task_func, *args, **kwargs):
        """Run a task in the background"""
        if task_name in self.task_threads and self.task_threads[task_name].is_alive():
            logger.warning(f"Task {task_name} already running")
            return False
        
        def run_task():
            try:
                logger.info(f"🔄 Starting background task: {task_name}")
                result = task_func(*args, **kwargs)
                logger.info(f"✅ Background task completed: {task_name}")
                return result
            except Exception as e:
                logger.error(f"❌ Background task failed: {task_name} - {e}")
                raise
        
        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()
        
        self.task_threads[task_name] = thread
        return True
    
    def is_task_running(self, task_name: str) -> bool:
        """Check if a task is running"""
        thread = self.task_threads.get(task_name)
        return thread is not None and thread.is_alive()
    
    def wait_for_task(self, task_name: str, timeout: int = 60) -> bool:
        """Wait for a task to complete"""
        thread = self.task_threads.get(task_name)
        if thread:
            thread.join(timeout=timeout)
            return not thread.is_alive()
        return True

class TaskManager:
    """Main task manager"""
    
    def __init__(self):
        self.scheduler = SyncScheduler()
        self.background_tasks = BackgroundTasks()
    
    def start_scheduler(self):
        """Start the sync scheduler"""
        self.scheduler.start()
    
    def stop_scheduler(self):
        """Stop the sync scheduler"""
        self.scheduler.stop()
    
    def set_sync_engine(self, sync_engine):
        """Set the sync engine for the scheduler"""
        self.scheduler.set_sync_engine(sync_engine)
    
    def run_sync_in_background(self, sync_engine) -> bool:
        """Run sync in background thread"""
        return self.background_tasks.run_in_background(
            'manual_sync',
            sync_engine.sync_calendars
        )
    
    def get_status(self) -> Dict[str, Any]:
        """Get task manager status"""
        return {
            'scheduler': self.scheduler.get_status(),
            'background_tasks': {
                'manual_sync_running': self.background_tasks.is_task_running('manual_sync')
            }
        }

# =============================================================================
# INITIALIZATION
# =============================================================================

# Create global task manager instance
task_manager = TaskManager() 