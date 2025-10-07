# Utils Restructure Rollback Plan

## If Production Breaks After Deployment

### Immediate Rollback (< 5 minutes)
```bash
# Revert to pre-cleanup state
git checkout pre-utils-cleanup

# Force deploy (if using automated deployment)
git push origin main --force-with-lease

# Manual deploy steps: 
# 1. SSH into Render deployment
# 2. Pull latest changes: git pull origin main
# 3. Restart application: sudo systemctl restart stedward-calendar-sync
# 4. Check logs: journalctl -u stedward-calendar-sync -f
```

### Verification After Rollback
1. Application starts: `python app.py`
2. Health check responds: `curl https://stedward-calendar-sync.onrender.com/health`
3. Check logs for import errors
4. Test sync operation manually

### Monitoring Points
- Watch application logs for import errors
- Check error rate in monitoring dashboard
- Verify scheduled syncs continue running
- Test manual sync via web interface

## If Restructure Breaks Locally

```bash
# Discard all changes
git checkout pre-utils-cleanup

# Or reset branch
git reset --hard pre-utils-cleanup

# Verify rollback worked
python3 test_utils_imports.py
```

## Contact Information
- Primary: Erich Harnisch (ericharnisch@stedward.org)
- Backup: St. Edward's IT Department
- Deployment docs: Render.com dashboard for stedward-calendar-sync
- Repository: https://github.com/eharnischStEdword/stedward-calendar-sync

## Pre-Cleanup State Verification

**Tag:** pre-utils-cleanup
**Branch:** cleanup/utils-structure
**Test Results:** ✅ ALL TESTS PASS
- All utils imports successful
- DateTimeUtils.get_central_time() works
- No import errors detected

## Files Created During Preparation

- `UTILS_IMPORT_MAP.md` - Complete documentation of all utils imports
- `test_utils_imports.py` - Test harness for verification
- `UTILS_ROLLBACK_PLAN.md` - This rollback plan

## Risk Assessment

**Current Risk Level:** LOW (preparation only)
- No production code modified
- Only documentation and safety nets created
- All tests passing

**Next Phase Risk Level:** HIGH (actual restructure)
- Will modify core import structure
- Could break production if done incorrectly
- Requires careful testing before deployment