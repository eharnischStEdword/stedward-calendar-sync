# utils/audit.py
class AuditLogger:
    def log_sync_operation(self, operation_type, user, details):
        audit_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'operation': operation_type,
            'user': user,
            'ip_address': request.remote_addr if request else 'system',
            'details': details
        }
        # Log to separate audit file or service
        audit_logger.info(json.dumps(audit_entry))
