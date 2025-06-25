"""
Audit Logger - Track and log all security-relevant operations
"""
import json
import logging
from datetime import datetime
from typing import Dict, Optional, Any
import os
import hashlib
from enum import Enum

# Set up a separate audit logger
audit_logger = logging.getLogger('audit')
audit_handler = logging.StreamHandler()
audit_handler.setFormatter(logging.Formatter('%(message)s'))
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)


class AuditEventType(Enum):
    """Types of audit events"""
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    LOGOUT = "logout"
    SYNC_TRIGGERED = "sync_triggered"
    SYNC_COMPLETED = "sync_completed"
    CONFIG_CHANGED = "config_changed"
    PERMISSION_DENIED = "permission_denied"
    DATA_ACCESS = "data_access"
    DATA_MODIFICATION = "data_modification"
    ERROR = "error"
    SECURITY_EVENT = "security_event"


class AuditLogger:
    """
    Handles audit logging for security and compliance
    
    Logs are structured for easy parsing and analysis by SIEM tools
    """
    
    def __init__(self, service_name: str = "calendar-sync"):
        self.service_name = service_name
        self.deployment_id = os.environ.get('RENDER_SERVICE_ID', 'local')
        self.environment = os.environ.get('ENVIRONMENT', 'production')
    
    def log_sync_operation(
        self,
        operation_type: str,
        user: Optional[str],
        details: Dict[str, Any],
        ip_address: Optional[str] = None
    ):
        """Log a sync-related operation"""
        audit_entry = self._create_base_entry(operation_type, user, ip_address)
        audit_entry['details'] = details
        
        # Determine severity
        if 'error' in operation_type.lower() or 'failed' in operation_type.lower():
            audit_entry['severity'] = 'ERROR'
        elif 'warning' in operation_type.lower():
            audit_entry['severity'] = 'WARNING'
        else:
            audit_entry['severity'] = 'INFO'
        
        self._write_audit_log(audit_entry)
    
    def log_authentication(
        self,
        success: bool,
        user: Optional[str] = None,
        ip_address: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """Log authentication attempts"""
        event_type = AuditEventType.AUTH_SUCCESS if success else AuditEventType.AUTH_FAILURE
        audit_entry = self._create_base_entry(event_type.value, user, ip_address)
        
        audit_entry['auth_success'] = success
        audit_entry['severity'] = 'INFO' if success else 'WARNING'
        
        if details:
            audit_entry['details'] = details
        
        self._write_audit_log(audit_entry)
    
    def log_data_access(
        self,
        resource_type: str,
        resource_id: str,
        action: str,
        user: Optional[str],
        ip_address: Optional[str] = None,
        success: bool = True
    ):
        """Log data access operations"""
        audit_entry = self._create_base_entry(AuditEventType.DATA_ACCESS.value, user, ip_address)
        
        audit_entry['resource'] = {
            'type': resource_type,
            'id': self._hash_sensitive_id(resource_id),
            'action': action
        }
        audit_entry['success'] = success
        audit_entry['severity'] = 'INFO' if success else 'WARNING'
        
        self._write_audit_log(audit_entry)
    
    def log_configuration_change(
        self,
        setting_name: str,
        old_value: Any,
        new_value: Any,
        user: Optional[str],
        ip_address: Optional[str] = None
    ):
        """Log configuration changes"""
        audit_entry = self._create_base_entry(AuditEventType.CONFIG_CHANGED.value, user, ip_address)
        
        # Don't log sensitive values
        audit_entry['config_change'] = {
            'setting': setting_name,
            'old_value': self._sanitize_value(old_value),
            'new_value': self._sanitize_value(new_value)
        }
        audit_entry['severity'] = 'WARNING'  # Config changes are always notable
        
        self._write_audit_log(audit_entry)
    
    def log_security_event(
        self,
        event_description: str,
        severity: str = 'WARNING',
        user: Optional[str] = None,
        ip_address: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        """Log security-relevant events"""
        audit_entry = self._create_base_entry(AuditEventType.SECURITY_EVENT.value, user, ip_address)
        
        audit_entry['description'] = event_description
        audit_entry['severity'] = severity
        
        if details:
            audit_entry['details'] = self._sanitize_details(details)
        
        self._write_audit_log(audit_entry)
    
    def log_sync_metrics(
        self,
        sync_id: str,
        duration_seconds: float,
        operations: Dict[str, int],
        success: bool,
        user: Optional[str] = None
    ):
        """Log sync operation metrics for analysis"""
        audit_entry = self._create_base_entry(
            AuditEventType.SYNC_COMPLETED.value,
            user,
            None
        )
        
        audit_entry['sync_metrics'] = {
            'sync_id': sync_id,
            'duration_seconds': duration_seconds,
            'operations': operations,
            'success': success
        }
        audit_entry['severity'] = 'INFO' if success else 'ERROR'
        
        self._write_audit_log(audit_entry)
    
    def _create_base_entry(
        self,
        event_type: str,
        user: Optional[str],
        ip_address: Optional[str]
    ) -> Dict[str, Any]:
        """Create base audit log entry"""
        return {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'event_type': event_type,
            'service': self.service_name,
            'deployment_id': self.deployment_id,
            'environment': self.environment,
            'user': user or 'system',
            'ip_address': ip_address or 'unknown',
            'session_id': self._get_session_id()
        }
    
    def _write_audit_log(self, audit_entry: Dict[str, Any]):
        """Write audit log entry"""
        # Add checksum for integrity
        audit_entry['checksum'] = self._calculate_checksum(audit_entry)
        
        # Write as JSON for easy parsing
        audit_logger.info(json.dumps(audit_entry, sort_keys=True))
    
    def _hash_sensitive_id(self, resource_id: str) -> str:
        """Hash sensitive IDs for privacy"""
        if not resource_id:
            return 'unknown'
        
        # Keep first 4 chars for identification, hash the rest
        if len(resource_id) > 8:
            prefix = resource_id[:4]
            hashed = hashlib.sha256(resource_id.encode()).hexdigest()[:8]
            return f"{prefix}...{hashed}"
        
        return resource_id
    
    def _sanitize_value(self, value: Any) -> Any:
        """Sanitize configuration values to avoid logging secrets"""
        if value is None:
            return None
        
        value_str = str(value)
        
        # List of patterns that might indicate sensitive data
        sensitive_patterns = [
            'password', 'secret', 'token', 'key', 'credential',
            'authorization', 'auth', 'api_key', 'private'
        ]
        
        # Check if the value name suggests it's sensitive
        for pattern in sensitive_patterns:
            if pattern in value_str.lower():
                return '***REDACTED***'
        
        # For long strings that might be tokens/keys
        if isinstance(value, str) and len(value) > 20:
            return f"{value[:4]}...{value[-4:]}"
        
        return value
    
    def _sanitize_details(self, details: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize a dictionary of details"""
        sanitized = {}
        
        for key, value in details.items():
            # Check if key name suggests sensitive data
            if any(pattern in key.lower() for pattern in ['password', 'secret', 'token', 'key']):
                sanitized[key] = '***REDACTED***'
            else:
                sanitized[key] = self._sanitize_value(value)
        
        return sanitized
    
    def _get_session_id(self) -> str:
        """Get current session ID if available"""
        # In a web context, this would get the actual session ID
        # For now, return a placeholder
        return os.environ.get('SESSION_ID', 'cli-session')
    
    def _calculate_checksum(self, entry: Dict[str, Any]) -> str:
        """Calculate checksum for audit entry integrity"""
        # Remove checksum field if it exists
        entry_copy = {k: v for k, v in entry.items() if k != 'checksum'}
        
        # Create stable string representation
        entry_str = json.dumps(entry_copy, sort_keys=True)
        
        # Calculate SHA256 checksum
        return hashlib.sha256(entry_str.encode()).hexdigest()[:16]


# Global audit logger instance
audit = AuditLogger()
