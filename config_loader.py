# filename: config_loader.py
"""
Configuration loader with environment variable support and validation.
Centralizes all configuration management for the NOC Buzzer System.
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass
import re

logger = logging.getLogger(__name__)

@dataclass
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str
    connection_timeout: int = 10
    pool_size: int = 5

@dataclass
class ModbusConfig:
    host: str
    port: int
    register: int
    buzzer_duration: int
    connection_timeout: int = 5

@dataclass
class GmailConfig:
    client_secrets_file: str
    token_file: str
    scopes: list
    encryption_key: Optional[str] = None
    refresh_threshold_hours: int = 1

@dataclass
class EmailProcessingConfig:
    check_interval: int
    max_description_length: int
    false_alert_threshold: float
    false_alerts: list

@dataclass
class MLModelsConfig:
    dataset_path: str
    model_cache_dir: str
    validation_enabled: bool
    min_accuracy_threshold: float

@dataclass
class DashboardConfig:
    host: str
    port: int
    debug: bool
    cors_origins: list

@dataclass
class LoggingConfig:
    level: str
    format: str
    file_path: str
    max_file_size: int
    backup_count: int
    rotation_when: str
    console_output: bool

@dataclass
class SystemConfig:
    restart_delay: int
    max_restart_attempts: int
    health_check_interval: int
    pid_file: str
    status_file: str

@dataclass
class SecurityConfig:
    secret_key: str
    token_encryption: bool
    log_sensitive_data: bool

@dataclass
class Config:
    database: DatabaseConfig
    modbus: ModbusConfig
    gmail: GmailConfig
    email_processing: EmailProcessingConfig
    ml_models: MLModelsConfig
    dashboard: DashboardConfig
    logging: LoggingConfig
    system: SystemConfig
    security: SecurityConfig

class ConfigLoader:
    """Loads and validates configuration from YAML with environment variable substitution."""
    
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        self._config = None
        
    def _substitute_env_vars(self, value: Any) -> Any:
        """Recursively substitute environment variables in configuration values."""
        if isinstance(value, str):
            # Pattern: ${VAR_NAME:default_value} or ${VAR_NAME}
            pattern = r'\$\{([^:}]+)(?::([^}]*))?\}'
            
            def replace_var(match):
                var_name = match.group(1)
                default_value = match.group(2) if match.group(2) is not None else ""
                return os.getenv(var_name, default_value)
            
            return re.sub(pattern, replace_var, value)
        elif isinstance(value, dict):
            return {k: self._substitute_env_vars(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._substitute_env_vars(item) for item in value]
        else:
            return value
    
    def _convert_types(self, value: Any, expected_type: type) -> Any:
        """Convert string values to expected types."""
        if expected_type == bool and isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on')
        elif expected_type == int and isinstance(value, str):
            return int(value)
        elif expected_type == float and isinstance(value, str):
            return float(value)
        return value
    
    def _validate_required_env_vars(self) -> list:
        """Check for required environment variables."""
        required_vars = [
            'DB_PASSWORD',
            'SECRET_KEY'
        ]
        
        missing_vars = []
        for var in required_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        return missing_vars
    
    def load(self) -> Config:
        """Load and validate configuration."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        
        # Check required environment variables
        missing_vars = self._validate_required_env_vars()
        if missing_vars:
            raise ValueError(f"Required environment variables not set: {', '.join(missing_vars)}")
        
        try:
            with open(self.config_path, 'r') as f:
                raw_config = yaml.safe_load(f)
            
            # Substitute environment variables
            config_dict = self._substitute_env_vars(raw_config)
            
            # Convert to dataclasses
            self._config = Config(
                database=DatabaseConfig(**config_dict['database']),
                modbus=ModbusConfig(**config_dict['modbus']),
                gmail=GmailConfig(**config_dict['gmail']),
                email_processing=EmailProcessingConfig(**config_dict['email_processing']),
                ml_models=MLModelsConfig(**config_dict['ml_models']),
                dashboard=DashboardConfig(**config_dict['dashboard']),
                logging=LoggingConfig(**config_dict['logging']),
                system=SystemConfig(**config_dict['system']),
                security=SecurityConfig(**config_dict['security'])
            )
            
            # Validate configuration
            self._validate_config()
            
            logger.info("Configuration loaded and validated successfully")
            return self._config
            
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise
    
    def _validate_config(self):
        """Validate configuration values."""
        config = self._config
        
        # Database validation
        if not config.database.password:
            raise ValueError("Database password cannot be empty")
        
        # Security validation
        if not config.security.secret_key:
            raise ValueError("Secret key cannot be empty")
        if len(config.security.secret_key) < 32:
            logger.warning("Secret key should be at least 32 characters long")
        
        # File path validation
        Path(config.logging.file_path).parent.mkdir(parents=True, exist_ok=True)
        Path(config.ml_models.model_cache_dir).mkdir(parents=True, exist_ok=True)
        
        # Gmail config validation
        if not Path(config.gmail.client_secrets_file).exists():
            raise FileNotFoundError(f"Gmail client secrets file not found: {config.gmail.client_secrets_file}")
        
        logger.info("Configuration validation completed")

# Global configuration instance
_config_loader = ConfigLoader()
config: Optional[Config] = None

def load_config(config_path: str = "config.yaml") -> Config:
    """Load global configuration."""
    global config, _config_loader
    _config_loader = ConfigLoader(config_path)
    config = _config_loader.load()
    return config

def get_config() -> Config:
    """Get the global configuration instance."""
    if config is None:
        raise RuntimeError("Configuration not loaded. Call load_config() first.")
    return config