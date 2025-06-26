"""
Background Scheduler for automatic sync - IMPROVED for Render Uptime
"""
import logging
import threading
import time
import schedule
from threading import Lock

logger = logging.getLogger(__name__)


class SyncScheduler:
    """Manages background sync scheduling"""
    
    def __init__(self, sync_engine):
        self.sync_engine = sync_engine
        self.scheduler_lock = Lock()
        self.scheduler_running = False
        self.scheduler_thread = None
    
    def start(self):
        """Start the scheduler"""
        with self.scheduler_lock:
            if self.scheduler_thread is None or not self.scheduler_thread.is_alive():
                logger.info("Starting scheduler thread...")
                self.scheduler_running = True
                self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
                self.scheduler_thread.start()
            else:
                logger.info("Scheduler already running")
    
    def stop(self):
        """Stop the scheduler"""
        with self.scheduler_lock:
            self.scheduler_running = False
        
        logger.info("Stopping scheduler...")
    
    def is_running(self):
        """Check if scheduler is running"""
        with self.scheduler_lock:
            return self.scheduler_running and self.scheduler_thread and self.scheduler_thread.is_alive()
    
    def _run_scheduler(self):
        """Run the scheduler loop"""
        # Schedule sync to run every 15 minutes to keep Render awake and ensure frequent syncing
        schedule.every(15).minutes.do(self._scheduled_sync)
        
        logger.info("Scheduler started - sync will run every 15 minutes")
        
        while True:
            with self.scheduler_lock:
                if not self.scheduler_running:
                    break
            
            schedule.run_pending()
            time.sleep(60)  # Check every minute
        
        logger.info("Scheduler stopped")
    
    def _scheduled_sync(self):
        """Function called by scheduler - IMPROVED with error handling"""
        try:
            logger.info("Running scheduled sync (every 15 minutes)")
            
            # Check if authenticated before trying to sync
            if not self.sync_engine.auth.is_authenticated():
                logger.warning("⚠️ Scheduled sync skipped - not authenticated")
                return
            
            result = self.sync_engine.sync_calendars()
            
            if result.get('needs_auth'):
                logger.warning("⚠️ Scheduled sync indicates authentication needed")
            elif result.get('success'):
                logger.info(f"✅ Scheduled sync completed successfully")
            else:
                logger.warning(f"⚠️ Scheduled sync completed with issues: {result.get('message')}")
                
        except Exception as e:
            # Don't let sync errors crash the scheduler
            logger.error(f"❌ Scheduled sync failed: {e}")
