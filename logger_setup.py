# filename: logger_setup.py
"""
Centralized logging setup with rotation and structured output.
Provides consistent logging across all NOC Buzzer System components.
"""

import logging
import logging.handlers
import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured logging output."""
    
    def __init__(self, include_extra: bool = True):
        super().__init__()
        self.include_extra = include_extra
    
    def format(self, record: logging.LogRecord) -> str:
        # Base log data
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }
        
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields if requested
        if self.include_extra and hasattr(record, '__dict__'):
            for key, value in record.__dict__.items():
                if key not in ['name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 
                              'filename', 'module', 'lineno', 'funcName', 'created', 
                              'msecs', 'relativeCreated', 'thread', 'threadName', 
                              'processName', 'process', 'exc_info', 'exc_text', 'stack_info']:
                    log_data[f'extra_{key}'] = value
        
        return json.dumps(log_data, ensure_ascii=False)

class SecurityAwareFormatter(logging.Formatter):
    """Formatter that redacts sensitive information."""
    
    SENSITIVE_PATTERNS = [
        'password', 'token', 'secret', 'key', 'credential'
    ]
    
    def format(self, record: logging.LogRecord) -> str:
        # Redact sensitive information from message
        message = record.getMessage()
        for pattern in self.SENSITIVE_PATTERNS:
            if pattern.lower() in message.lower():
                # Replace with placeholder
                message = message.replace(message, "[REDACTED: Contains sensitive data]")
                break
        
        # Update record message
        record.msg = message
        record.args = ()
        
        return super().format(record)

class NOCLogger:
    """Centralized logger setup for NOC Buzzer System."""
    
    def __init__(self, config):
        self.config = config
        self._loggers = {}
        self._setup_root_logger()
    
    def _setup_root_logger(self):
        """Setup root logger with configured handlers."""
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, self.config.level.upper()))
        
        # Clear existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        
        # Setup file handler with rotation
        self._setup_file_handler(root_logger)
        
        # Setup console handler if enabled
        if self.config.console_output:
            self._setup_console_handler(root_logger)
    
    def _setup_file_handler(self, logger: logging.Logger):
        """Setup rotating file handler."""
        try:
            # Ensure log directory exists
            log_path = Path(self.config.file_path)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create rotating file handler
            file_handler = logging.handlers.RotatingFileHandler(
                filename=self.config.file_path,
                maxBytes=self.config.max_file_size,
                backupCount=self.config.backup_count,
                encoding='utf-8'
            )
            
            # Use structured formatter for file output
            formatter = StructuredFormatter()
            file_handler.setFormatter(formatter)
            
            logger.addHandler(file_handler)
            
        except Exception as e:
            print(f"Failed to setup file logging: {e}")
            # Fall back to console only
    
    def _setup_console_handler(self, logger: logging.Logger):
        """Setup console handler."""
        console_handler = logging.StreamHandler(sys.stdout)
        
        # Use security-aware formatter for console
        formatter = SecurityAwareFormatter(self.config.format)
        console_handler.setFormatter(formatter)
        
        logger.addHandler(console_handler)
    
    def get_logger(self, name: str) -> logging.Logger:
        """Get a logger instance for a specific component."""
        if name not in self._loggers:
            self._loggers[name] = logging.getLogger(name)
        
        return self._loggers[name]
    
    def log_system_event(self, event_type: str, details: Dict[str, Any], 
                        level: str = 'INFO'):
        """Log structured system events."""
        logger = self.get_logger('system')
        log_method = getattr(logger, level.lower())
        
        log_method(
            f"System event: {event_type}",
            extra={
                'event_type': event_type,
                'event_details': details,
                'timestamp': datetime.now().isoformat()
            }
        )
    
    def log_security_event(self, event_type: str, details: Dict[str, Any]):
        """Log security-related events."""
        logger = self.get_logger('security')
        logger.warning(
            f"Security event: {event_type}",
            extra={
                'security_event': event_type,
                'details': details,
                'timestamp': datetime.now().isoformat()
            }
        )

# Global logger instance
_noc_logger: Optional[NOCLogger] = None

def setup_logging(config) -> NOCLogger:
    """Setup global logging configuration."""
    global _noc_logger
    _noc_logger = NOCLogger(config)
    return _noc_logger

def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    if _noc_logger is None:
        raise RuntimeError("Logging not setup. Call setup_logging() first.")
    return _noc_logger.get_logger(name)

def log_system_event(event_type: str, details: Dict[str, Any], level: str = 'INFO'):
    """Log structured system events."""
    if _noc_logger:
        _noc_logger.log_system_event(event_type, details, level)

def log_security_event(event_type: str, details: Dict[str, Any]):
    """Log security-related events."""
    if _noc_logger:
        _noc_logger.log_security_event(event_type, details)