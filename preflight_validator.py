
# filename: preflight_validator.py
"""
Preflight validation for NOC Buzzer System.
Validates environment, database connectivity, ML models, and dependencies.
"""

import os
import sys
import mysql.connector
import socket
from pathlib import Path
from typing import Dict, Any, List, Tuple
import importlib
import pickle
import pandas as pd
from config_loader import get_config
from logger_setup import get_logger

logger = get_logger(__name__)

class PreflightValidator:
    """Comprehensive preflight validation for system startup."""
    
    def __init__(self):
        self.config = get_config()
        self.validation_results = {}
        self.critical_failures = []
        self.warnings = []
    
    def validate_all(self) -> Tuple[bool, Dict[str, Any]]:
        """Run all validation checks."""
        logger.info("Starting preflight validation")
        
        checks = [
            ('environment', self._validate_environment),
            ('dependencies', self._validate_dependencies),
            ('database', self._validate_database),
            ('modbus', self._validate_modbus_connectivity),
            ('gmail', self._validate_gmail_setup),
            ('ml_models', self._validate_ml_models),
            ('file_permissions', self._validate_file_permissions),
            ('disk_space', self._validate_disk_space)
        ]
        
        for check_name, check_func in checks:
            try:
                result = check_func()
                self.validation_results[check_name] = result
                if not result['passed']:
                    if result.get('critical', False):
                        self.critical_failures.append(check_name)
                    else:
                        self.warnings.append(check_name)
            except Exception as e:
                self.validation_results[check_name] = {
                    'passed': False,
                    'critical': True,
                    'error': str(e)
                }
                self.critical_failures.append(check_name)
                logger.error(f"Validation check {check_name} failed with exception: {e}")
        
        success = len(self.critical_failures) == 0
        
        summary = {
            'overall_success': success,
            'critical_failures': self.critical_failures,
            'warnings': self.warnings,
            'results': self.validation_results
        }
        
        if success:
            logger.info("Preflight validation completed successfully")
        else:
            logger.error(f"Preflight validation failed. Critical failures: {self.critical_failures}")
        
        return success, summary
    
    def _validate_environment(self) -> Dict[str, Any]:
        """Validate environment variables and configuration."""
        required_vars = ['DB_PASSWORD', 'SECRET_KEY']
        missing_vars = []
        
        for var in required_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        # Check Python version
        python_version = sys.version_info
        min_python = (3, 8)
        python_ok = python_version >= min_python
        
        # Check configuration loading
        config_ok = True
        config_error = None
        try:
            config = get_config()
        except Exception as e:
            config_ok = False
            config_error = str(e)
        
        result = {
            'passed': len(missing_vars) == 0 and python_ok and config_ok,
            'critical': True,
            'details': {
                'missing_env_vars': missing_vars,
                'python_version': f"{python_version.major}.{python_version.minor}.{python_version.micro}",
                'python_version_ok': python_ok,
                'min_python_required': f"{min_python[0]}.{min_python[1]}",
                'config_loaded': config_ok,
                'config_error': config_error
            }
        }
        
        return result
    
    def _validate_dependencies(self) -> Dict[str, Any]:
        """Validate required Python packages."""
        required_packages = [
            'mysql.connector',
            'simplegmail',
            'pyModbusTCP',
            'sklearn',
            'pandas',
            'numpy',
            'nltk',
            'flask',
            'cryptography',
            'yaml'
        ]
        
        missing_packages = []
        package_versions = {}
        
        for package in required_packages:
            try:
                module = importlib.import_module(package)
                version = getattr(module, '__version__', 'unknown')
                package_versions[package] = version
            except ImportError:
                missing_packages.append(package)
        
        result = {
            'passed': len(missing_packages) == 0,
            'critical': True,
            'details': {
                'missing_packages': missing_packages,
                'package_versions': package_versions
            }
        }
        
        return result
    
    def _validate_database(self) -> Dict[str, Any]:
        """Validate database connectivity and schema."""
        try:
            # Test connection
            connection = mysql.connector.connect(

               host=self.config.database.host,
               port=self.config.database.port,
               user=self.config.database.user,
               password=self.config.database.password,
               database=self.config.database.database,
               connection_timeout=self.config.database.connection_timeout
           )
           
           cursor = connection.cursor()
           
           # Check if required tables exist
           cursor.execute("SHOW TABLES")
           tables = [table[0] for table in cursor.fetchall()]
           
           required_tables = ['Faults']
           missing_tables = [table for table in required_tables if table not in tables]
           
           # Check table structure if tables exist
           table_structure_ok = True
           structure_details = {}
           
           if 'Faults' in tables:
               cursor.execute("DESCRIBE Faults")
               columns = [col[0] for col in cursor.fetchall()]
               required_columns = ['fault_description', 'fault_category', 'fault_date_time']
               missing_columns = [col for col in required_columns if col not in columns]
               
               structure_details['Faults'] = {
                   'exists': True,
                   'columns': columns,
                   'missing_columns': missing_columns
               }
               
               if missing_columns:
                   table_structure_ok = False
           else:
               structure_details['Faults'] = {'exists': False}
           
           # Test basic operations
           cursor.execute("SELECT 1")
           cursor.fetchone()
           
           cursor.close()
           connection.close()
           
           result = {
               'passed': len(missing_tables) == 0 and table_structure_ok,
               'critical': True,
               'details': {
                   'connection_successful': True,
                   'missing_tables': missing_tables,
                   'table_structure': structure_details,
                   'database_name': self.config.database.database
               }
           }
           
       except Exception as e:
           result = {
               'passed': False,
               'critical': True,
               'details': {
                   'connection_successful': False,
                   'error': str(e)
               }
           }
       
       return result
   
   def _validate_modbus_connectivity(self) -> Dict[str, Any]:
       """Validate Modbus server connectivity."""
       try:
           sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
           sock.settimeout(self.config.modbus.connection_timeout)
           result_code = sock.connect_ex((self.config.modbus.host, self.config.modbus.port))
           sock.close()
           
           connection_ok = result_code == 0
           
           result = {
               'passed': connection_ok,
               'critical': False,  # Modbus failure is not critical for startup
               'details': {
                   'host': self.config.modbus.host,
                   'port': self.config.modbus.port,
                   'connection_successful': connection_ok,
                   'error': 'Connection refused' if not connection_ok else None
               }
           }
           
       except Exception as e:
           result = {
               'passed': False,
               'critical': False,
               'details': {
                   'connection_successful': False,
                   'error': str(e)
               }
           }
       
       return result
   
   def _validate_gmail_setup(self) -> Dict[str, Any]:
       """Validate Gmail configuration and credentials."""
       client_secrets_path = Path(self.config.gmail.client_secrets_file)
       token_path = Path(self.config.gmail.token_file)
       
       client_secrets_exist = client_secrets_path.exists()
       token_exists = token_path.exists()
       
       # Validate client secrets file structure
       client_secrets_valid = False
       if client_secrets_exist:
           try:
               import json
               with open(client_secrets_path, 'r') as f:
                   secrets = json.load(f)
                   required_keys = ['installed']
                   client_secrets_valid = all(key in secrets for key in required_keys)
           except Exception:
               client_secrets_valid = False
       
       result = {
           'passed': client_secrets_exist and client_secrets_valid,
           'critical': True,
           'details': {
               'client_secrets_exist': client_secrets_exist,
               'client_secrets_valid': client_secrets_valid,
               'token_exists': token_exists,
               'client_secrets_path': str(client_secrets_path),
               'token_path': str(token_path)
           }
       }
       
       return result
   
   def _validate_ml_models(self) -> Dict[str, Any]:
       """Validate ML models and dataset."""
       dataset_path = Path(self.config.ml_models.dataset_path)
       model_cache_dir = Path(self.config.ml_models.model_cache_dir)
       
       # Check dataset
       dataset_exists = dataset_path.exists()
       dataset_valid = False
       dataset_details = {}
       
       if dataset_exists:
           try:
               df = pd.read_csv(dataset_path, encoding='unicode_escape')
               required_columns = ['email_text', 'type']
               has_required_columns = all(col in df.columns for col in required_columns)
               
               dataset_details = {
                   'rows': len(df),
                   'columns': list(df.columns),
                   'has_required_columns': has_required_columns,
                   'sample_types': df['type'].unique().tolist() if 'type' in df.columns else []
               }
               
               dataset_valid = has_required_columns and len(df) > 0
               
           except Exception as e:
               dataset_details['error'] = str(e)
       
       # Check model cache directory
       model_cache_dir.mkdir(parents=True, exist_ok=True)
       
       # Try to validate existing models if any
       model_files = list(model_cache_dir.glob('*.pkl'))
       model_validation = {}
       
       for model_file in model_files:
           try:
               with open(model_file, 'rb') as f:
                   model = pickle.load(f)
                   model_validation[model_file.name] = {
                       'loadable': True,
                       'type': type(model).__name__
                   }
           except Exception as e:
               model_validation[model_file.name] = {
                   'loadable': False,
                   'error': str(e)
               }
       
       result = {
           'passed': dataset_exists and dataset_valid,
           'critical': True,
           'details': {
               'dataset_exists': dataset_exists,
               'dataset_valid': dataset_valid,
               'dataset_details': dataset_details,
               'model_cache_dir': str(model_cache_dir),
               'cached_models': model_validation
           }
       }
       
       return result
   
   def _validate_file_permissions(self) -> Dict[str, Any]:
       """Validate file permissions for critical paths."""
       paths_to_check = [
           self.config.logging.file_path,
           self.config.gmail.token_file,
           self.config.system.pid_file,
           self.config.system.status_file
       ]
       
       permission_issues = []
       
       for path_str in paths_to_check:
           path = Path(path_str)
           
           # Create parent directories if they don't exist
           try:
               path.parent.mkdir(parents=True, exist_ok=True)
           except Exception as e:
               permission_issues.append(f"Cannot create directory for {path}: {e}")
               continue
           
           # Check write permissions
           try:
               if path.exists():
                   # Check if we can write to existing file
                   with open(path, 'a'):
                       pass
               else:
                   # Check if we can create new file
                   with open(path, 'w') as f:
                       f.write('test')
                   path.unlink()  # Remove test file
           except Exception as e:
               permission_issues.append(f"Cannot write to {path}: {e}")
       
       result = {
           'passed': len(permission_issues) == 0,
           'critical': True,
           'details': {
               'permission_issues': permission_issues,
               'checked_paths': paths_to_check
           }
       }
       
       return result
   
   def _validate_disk_space(self) -> Dict[str, Any]:
       """Validate available disk space."""
       import shutil
       
       # Check disk space for log directory
       log_dir = Path(self.config.logging.file_path).parent
       
       try:
           total, used, free = shutil.disk_usage(log_dir)
           free_mb = free // (1024 * 1024)
           
           # Require at least 100MB free space
           min_free_mb = 100
           space_ok = free_mb >= min_free_mb
           
           result = {
               'passed': space_ok,
               'critical': False,
               'details': {
                   'log_directory': str(log_dir),
                   'free_space_mb': free_mb,
                   'min_required_mb': min_free_mb,
                   'total_space_mb': total // (1024 * 1024),
                   'used_space_mb': used // (1024 * 1024)
               }
           }
           
       except Exception as e:
           result = {
               'passed': False,
               'critical': False,
               'details': {
                   'error': str(e)
               }
           }
       
       return result

def run_preflight_checks() -> Tuple[bool, Dict[str, Any]]:
   """Run preflight validation checks."""
   validator = PreflightValidator()
   return validator.validate_all()

def print_validation_summary(results: Dict[str, Any]):
   """Print human-readable validation summary."""
   print("=" * 60)
   print("NOC BUZZER SYSTEM - PREFLIGHT VALIDATION")
   print("=" * 60)
   
   if results['overall_success']:
       print("✓ VALIDATION PASSED - System ready for startup")
   else:
       print("✗ VALIDATION FAILED - Critical issues found")
   
   print(f"\nCritical Failures: {len(results['critical_failures'])}")
   print(f"Warnings: {len(results['warnings'])}")
   
   for check_name, check_result in results['results'].items():
       status = "✓" if check_result['passed'] else "✗"
       critical = " (CRITICAL)" if check_result.get('critical', False) else ""
       print(f"  {status} {check_name.upper()}{critical}")
       
       if not check_result['passed'] and 'details' in check_result:
           if 'error' in check_result['details']:
               print(f"    Error: {check_result['details']['error']}")
   
   print("=" * 60)