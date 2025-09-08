#!/usr/bin/env python3
"""
Complete Updated NOC Buzzer System with Enhanced Dashboard Integration
Full code ready for copy and paste - replaces your final.py
"""

import os
import sys
import time
import signal
import threading
import logging
import socket
import json
import difflib
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

# Import your existing modules
try:
    from simplegmail import Gmail
    from pyModbusTCP.server import ModbusServer
    import mysql.connector
    import prediction_fin
    import mysql_insert
except ImportError as e:
    print(f"Missing required module: {e}")
    print("Please install: pip install simplegmail pyModbusTCP mysql-connector-python")
    sys.exit(1)

# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    'MODBUS_HOST': '192.168.69.43',
    'MODBUS_PORT': 502,
    'DASHBOARD_HOST': 'localhost',
    'DASHBOARD_PORT': 5000,
    'DB_CONFIG': {
        'user': 'root',
        'password': 'deTen.12345',
        'host': '127.0.0.1',
        'database': 'nocbuzzer'
    },
    'EMAIL_CHECK_INTERVAL': 30,
    'RESTART_DELAY': 5,
    'MAX_RESTART_ATTEMPTS': 3,
    'HEALTH_CHECK_INTERVAL': 60,
    'DASHBOARD_TIMEOUT': 2,
    'BUZZER_DURATION': 10,
}

# False alerts to filter out
FALSE_ALERTS = [
    "From : +263772151364 ZIMPLATS NGEZI SERVER ROOM:There is a problem with the environment in server room. Please attend."
]

# ============================================================================
# LOGGING SETUP
# ============================================================================

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / f"noc_system_{datetime.now().strftime('%Y%m%d')}.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# SMART DASHBOARD CONNECTOR
# ============================================================================

class SmartDashboardConnector:
    """Handles dashboard connectivity with graceful fallback"""
    
    def __init__(self, host, port, timeout=2):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.available = False
        self.last_check = 0
        self.check_interval = 300  # Check every 5 minutes
        self.consecutive_failures = 0
        
    def _check_port_open(self):
        """Quick check if dashboard port is open"""
        try:
            with socket.create_connection((self.host, self.port), timeout=self.timeout):
                return True
        except (socket.error, ConnectionRefusedError, OSError):
            return False
    
    def _should_check_availability(self):
        """Determine if we should check dashboard availability"""
        current_time = time.time()
        return (current_time - self.last_check) > self.check_interval
    
    def check_availability(self):
        """Check if dashboard is available"""
        self.last_check = time.time()
        
        if self._check_port_open():
            if not self.available:
                logger.info("Dashboard connection restored")
            self.available = True
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
            if self.available:
                logger.warning("Dashboard connection lost - continuing in standalone mode")
            self.available = False
        
        return self.available
    
    def is_available(self):
        """Check if dashboard is available (with smart caching)"""
        if self._should_check_availability():
            return self.check_availability()
        return self.available
    
    @contextmanager
    def safe_request(self):
        """Context manager for safe dashboard requests"""
        try:
            if self.is_available():
                yield True
            else:
                yield False
        except Exception:
            self.available = False
            yield False

# ============================================================================
# SYSTEM STATE MANAGEMENT
# ============================================================================

class SystemState:
    def __init__(self):
        self.running = True
        self.buzzer_active = False
        self.modbus_server = None
        self.gmail = None
        self.restart_count = 0
        self.last_health_check = None
        self.last_email_check = None
        self.email_stats = {
            'total_today': 0,
            'alarms_today': 0,
            'info_today': 0,
            'last_email': None
        }
        self.components_status = {
            'modbus': False,
            'database': False,
            'gmail': False,
        }
        
        # Dashboard integration
        self.dashboard = SmartDashboardConnector(
            CONFIG['DASHBOARD_HOST'], 
            CONFIG['DASHBOARD_PORT'],
            CONFIG['DASHBOARD_TIMEOUT']
        )

system_state = SystemState()

# ============================================================================
# ENHANCED DASHBOARD INTEGRATION
# ============================================================================

def safe_dashboard_notify(endpoint, data=None, method='POST'):
    """Safely notify dashboard without blocking the main system"""
    try:
        with system_state.dashboard.safe_request() as available:
            if not available:
                return False
            
            import requests
            url = f"http://{CONFIG['DASHBOARD_HOST']}:{CONFIG['DASHBOARD_PORT']}/api/{endpoint}"
            
            if method.upper() == 'POST' and data:
                response = requests.post(url, json=data, timeout=CONFIG['DASHBOARD_TIMEOUT'])
            else:
                response = requests.get(url, timeout=CONFIG['DASHBOARD_TIMEOUT'])
                
            return response.status_code == 200
            
    except Exception:
        # Silently fail - dashboard is optional
        return False

def log_to_dashboard(message, log_type='info'):
    """Send log message to dashboard (non-blocking)"""
    def _async_log():
        log_data = {
            'message': message,
            'type': log_type,
            'timestamp': datetime.now().isoformat()
        }
        safe_dashboard_notify('log', log_data)
    
    # Run in background thread to avoid blocking
    threading.Thread(target=_async_log, daemon=True).start()

def update_dashboard_stats():
    """Update dashboard with current stats (non-blocking)"""
    def _async_update():
        stats_data = {
            'system_running': system_state.running,
            'buzzer_active': system_state.buzzer_active,
            'last_email_check': system_state.last_email_check,
            'total_emails_today': system_state.email_stats['total_today'],
            'alarm_count_today': system_state.email_stats['alarms_today'],
            'info_count_today': system_state.email_stats['info_today'],
            'latest_email': system_state.email_stats['last_email'],
            'components_status': system_state.components_status,
            'timestamp': datetime.now().isoformat()
        }
        safe_dashboard_notify('update-stats', stats_data)
    
    threading.Thread(target=_async_update, daemon=True).start()

# ============================================================================
# PROCESS MANAGEMENT
# ============================================================================

class ProcessManager:
    def __init__(self):
        self.pid_file = Path("noc_system.pid")
        self.status_file = Path("noc_system_status.json")
    
    def write_pid(self):
        """Write current process ID to file"""
        try:
            with open(self.pid_file, 'w') as f:
                f.write(str(os.getpid()))
            logger.info(f"PID {os.getpid()} written to {self.pid_file}")
        except Exception as e:
            logger.error(f"Failed to write PID file: {e}")
    
    def cleanup_pid_file(self):
        """Remove PID file"""
        try:
            if self.pid_file.exists():
                self.pid_file.unlink()
                logger.info("PID file cleaned up")
        except Exception as e:
            logger.error(f"Failed to cleanup PID file: {e}")
    
    def save_status(self):
        """Save current system status"""
        try:
            status = {
                'timestamp': datetime.now().isoformat(),
                'pid': os.getpid(),
                'restart_count': system_state.restart_count,
                'components': system_state.components_status,
                'running': system_state.running,
                'dashboard_available': system_state.dashboard.available,
                'email_stats': system_state.email_stats,
                'buzzer_active': system_state.buzzer_active
            }
            with open(self.status_file, 'w') as f:
                json.dump(status, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save status: {e}")

process_manager = ProcessManager()

# ============================================================================
# DATABASE INTEGRATION FOR ENHANCED REPORTING
# ============================================================================

def get_db_connection():
    """Get database connection with error handling"""
    try:
        return mysql.connector.connect(**CONFIG['DB_CONFIG'])
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return None

def update_email_stats():
    """Update email statistics from database"""
    try:
        cnx = get_db_connection()
        if not cnx:
            return
            
        cursor = cnx.cursor()
        
        # Get total emails today
        query = """
        SELECT COUNT(*) FROM Faults 
        WHERE DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) = CURDATE()
        """
        cursor.execute(query)
        total = cursor.fetchone()[0]
        
        # Get alarm count today
        query = """
        SELECT COUNT(*) FROM Faults 
        WHERE fault_category = 'Alarm' 
        AND DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) = CURDATE()
        """
        cursor.execute(query)
        alarms = cursor.fetchone()[0]
        
        # Get info count today  
        query = """
        SELECT COUNT(*) FROM Faults 
        WHERE fault_category != 'Alarm' 
        AND DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) = CURDATE()
        """
        cursor.execute(query)
        info = cursor.fetchone()[0]
        
        # Update state
        system_state.email_stats['total_today'] = total
        system_state.email_stats['alarms_today'] = alarms
        system_state.email_stats['info_today'] = info
        
        cursor.close()
        cnx.close()
        
    except Exception as e:
        logger.error(f"Failed to get database stats: {e}")

# ============================================================================
# COMPONENT HEALTH MONITORING
# ============================================================================

def check_database_health():
    """Check if database connection is healthy"""
    try:
        cnx = mysql.connector.connect(**CONFIG['DB_CONFIG'])
        cursor = cnx.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        cnx.close()
        system_state.components_status['database'] = True
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        system_state.components_status['database'] = False
        return False

def check_modbus_health():
    """Check if Modbus server is running"""
    try:
        if system_state.modbus_server and system_state.modbus_server.is_run:
            system_state.components_status['modbus'] = True
            return True
        else:
            system_state.components_status['modbus'] = False
            return False
    except Exception as e:
        logger.error(f"Modbus health check failed: {e}")
        system_state.components_status['modbus'] = False
        return False

def check_gmail_health():
    """Check if Gmail connection is healthy"""
    try:
        if system_state.gmail:
            system_state.components_status['gmail'] = True
            return True
        else:
            system_state.components_status['gmail'] = False
            return False
    except Exception as e:
        logger.error(f"Gmail health check failed: {e}")
        system_state.components_status['gmail'] = False
        return False

def perform_health_check():
    """Perform comprehensive system health check"""
    logger.debug("Performing system health check...")
    
    db_healthy = check_database_health()
    modbus_healthy = check_modbus_health()
    gmail_healthy = check_gmail_health()
    
    system_state.last_health_check = datetime.now()
    process_manager.save_status()
    
    # Update dashboard with health status
    update_dashboard_stats()
    
    # Restart failed components
    if not (db_healthy and modbus_healthy):
        logger.warning("Critical components unhealthy, attempting component restart...")
        restart_failed_components()
    
    return db_healthy and modbus_healthy and gmail_healthy

def restart_failed_components():
    """Restart individual failed components"""
    try:
        # Restart Modbus server if failed
        if not system_state.components_status['modbus']:
            logger.info("Restarting Modbus server...")
            if system_state.modbus_server:
                system_state.modbus_server.stop()
            time.sleep(2)
            system_state.modbus_server = ModbusServer(
                host=CONFIG['MODBUS_HOST'], 
                port=CONFIG['MODBUS_PORT'], 
                no_block=True
            )
            system_state.modbus_server.start()
            logger.info("Modbus server restarted")
            log_to_dashboard("Modbus server restarted", 'info')
        
        # Reinitialize Gmail if failed
        if not system_state.components_status['gmail']:
            logger.info("Reinitializing Gmail connection...")
            system_state.gmail = Gmail()
            logger.info("Gmail connection reinitialized")
            log_to_dashboard("Gmail connection reinitialized", 'info')
            
    except Exception as e:
        logger.error(f"Failed to restart components: {e}")

# ============================================================================
# SIGNAL HANDLERS
# ============================================================================

def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    system_state.running = False
    cleanup_and_exit()

def cleanup_and_exit():
    """Cleanup resources and exit"""
    logger.info("Cleaning up system resources...")
    
    try:
        # Stop buzzer
        if system_state.modbus_server and system_state.modbus_server.is_run:
            try:
                system_state.modbus_server.data_bank.set_holding_registers(1, [1, 0, 3, 10, 0])
                logger.info("Buzzer deactivated")
            except:
                pass
        
        # Stop Modbus server
        if system_state.modbus_server:
            system_state.modbus_server.stop()
            logger.info("Modbus server stopped")
        
        # Notify dashboard of shutdown
        log_to_dashboard("System shutting down", 'info')
        
        # Cleanup PID file
        process_manager.cleanup_pid_file()
        
        logger.info("System shutdown complete")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
if hasattr(signal, 'SIGHUP'):
    signal.signal(signal.SIGHUP, signal_handler)

# ============================================================================
# CORE SYSTEM COMPONENTS
# ============================================================================

def initialize_modbus():
    """Initialize Modbus server"""
    try:
        system_state.modbus_server = ModbusServer(
            host=CONFIG['MODBUS_HOST'],
            port=CONFIG['MODBUS_PORT'],
            no_block=True
        )
        system_state.modbus_server.start()
        logger.info("Modbus server initialized and started")
        log_to_dashboard("Modbus server started", 'info')
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Modbus server: {e}")
        log_to_dashboard(f"Modbus initialization failed: {e}", 'error')
        return False

def initialize_gmail():
    """Initialize Gmail connection"""
    try:
        system_state.gmail = Gmail()
        logger.info("Gmail connection initialized")
        log_to_dashboard("Gmail initialized", 'info')
        return True
    except Exception as e:
        logger.error(f"Failed to initialize Gmail: {e}")
        log_to_dashboard(f"Gmail initialization failed: {e}", 'error')
        return False

def is_similar_to_false_alert(text, threshold=0.7):
    """Check if email text is similar to known false alerts"""
    for alert in FALSE_ALERTS:
        similarity = difflib.SequenceMatcher(None, text, alert).ratio()
        if similarity >= threshold:
            logger.info(f"Email filtered as false alert (similarity: {similarity*100:.2f}%)")
            log_to_dashboard(f"Filtered false alert (similarity: {similarity*100:.2f}%)", 'info')
            return True
    return False

def activate_buzzer(duration=None):
    """Activate buzzer for specified duration"""
    if duration is None:
        duration = CONFIG['BUZZER_DURATION']
    
    try:
        if system_state.modbus_server and system_state.modbus_server.is_run:
            # Set buzzer ON
            system_state.modbus_server.data_bank.set_holding_registers(1, [1, 10, 3, 10, 10])
            system_state.buzzer_active = True
            logger.warning(f"Buzzer activated for {duration} seconds")
            log_to_dashboard(f"ALARM: Buzzer activated for {duration}s", 'alarm')
            print("Buzzer registers set to ON")
            
            def deactivate_after_delay():
                time.sleep(duration)
                try:
                    if system_state.modbus_server and system_state.modbus_server.is_run:
                        system_state.modbus_server.data_bank.set_holding_registers(1, [1, 0, 3, 10, 0])
                        system_state.buzzer_active = False
                        logger.info("Buzzer deactivated")
                        log_to_dashboard("Buzzer deactivated", 'info')
                        print("Buzzer registers set to OFF")
                        # Update dashboard after buzzer state change
                        update_dashboard_stats()
                except Exception as e:
                    logger.error(f"Failed to deactivate buzzer: {e}")
            
            threading.Thread(target=deactivate_after_delay, daemon=True).start()
            # Update dashboard immediately when buzzer activates
            update_dashboard_stats()
            return True
        else:
            logger.error("Cannot activate buzzer: Modbus server not running")
            return False
    except Exception as e:
        logger.error(f"Failed to activate buzzer: {e}")
        return False

def process_email(message_body, prediction):
    """Process email and take appropriate action"""
    try:
        # Store in database
        mysql_insert.insert_mysql(message_body, prediction)
        logger.info(f"Email stored in database with classification: {prediction}")
        
        # Update stats
        system_state.email_stats['total_today'] += 1
        if prediction.lower() == 'alarm':
            system_state.email_stats['alarms_today'] += 1
        else:
            system_state.email_stats['info_today'] += 1
        
        # Update latest email
        system_state.email_stats['last_email'] = {
            'description': message_body[:100] + "..." if len(message_body) > 100 else message_body,
            'category': prediction,
            'timestamp': datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        }
        
        # Activate buzzer if alarm
        if prediction.lower() == 'alarm':
            logger.warning("ALARM detected - activating buzzer")
            log_to_dashboard(f"ALARM DETECTED: {message_body[:50]}...", 'alarm')
            activate_buzzer()
        else:
            log_to_dashboard(f"Info email processed: {prediction}", 'info')
        
        # Update dashboard with new stats
        update_dashboard_stats()
        
        return True
    except Exception as e:
        logger.error(f"Failed to process email: {e}")
        return False

def email_monitoring_loop():
    """Main email monitoring loop"""
    logger.info("Starting email monitoring loop...")
    log_to_dashboard("Email monitoring started", 'info')
    
    while system_state.running:
        try:
            # Update last check time
            system_state.last_email_check = datetime.now().strftime('%H:%M:%S')
            
            if not system_state.gmail:
                logger.warning("Gmail not initialized, attempting reinitialization...")
                if not initialize_gmail():
                    time.sleep(60)
                    continue
            
            # Get unread messages
            messages = system_state.gmail.get_unread_inbox()
            
            if messages:
                logger.info(f"Processing {len(messages)} unread message(s)")
                log_to_dashboard(f"Processing {len(messages)} unread message(s)", 'info')
                
                for message in messages:
                    if not system_state.running:
                        break
                    
                    try:
                        message_body = message.plain or ""
                        
                        if not message_body:
                            logger.info("Empty message body, skipping")
                            message.mark_as_read()
                            continue
                        
                        print(f"Message Body: {message_body}")
                        
                        # Check for false alerts
                        if is_similar_to_false_alert(message_body):
                            logger.info("False alert detected, skipping")
                            message.mark_as_read()
                            print("Message marked as read.")
                            continue
                        
                        # Truncate if too long
                        if len(message_body) > 255:
                            message_body = message_body[:255]
                        
                        # Classify email
                        prediction = prediction_fin.classifier_pred(message_body)
                        logger.info(f"Email classified as: {prediction}")
                        print(f"Notification is: {prediction}")
                        
                        # Process email
                        process_email(message_body, prediction)
                        
                        # Mark as read
                        message.mark_as_read()
                        logger.info("Email marked as read")
                        print("Message marked as read.")
                        
                    except Exception as e:
                        logger.error(f"Error processing individual message: {e}")
                        try:
                            message.mark_as_read()
                        except:
                            pass
            else:
                print("No unread messages.")
            
            # Update stats periodically
            update_email_stats()
            update_dashboard_stats()
            
        except Exception as e:
            logger.error(f"Error in email monitoring loop: {e}")
            log_to_dashboard(f"Email monitoring error: {e}", 'error')
            print(f"An error occurred: {e}")
            time.sleep(60)  # Wait longer on error
        
        # Regular sleep
        time.sleep(CONFIG['EMAIL_CHECK_INTERVAL'])

def health_monitoring_loop():
    """Periodic health monitoring"""
    while system_state.running:
        try:
            time.sleep(CONFIG['HEALTH_CHECK_INTERVAL'])
            if system_state.running:
                perform_health_check()
                update_email_stats()
        except Exception as e:
            logger.error(f"Error in health monitoring: {e}")

def initialize_system():
    """Initialize all system components"""
    logger.info("Initializing NOC Buzzer System with Enhanced Dashboard...")
    log_to_dashboard("System initializing...", 'info')
    
    # Write PID file
    process_manager.write_pid()
    
    # Check dashboard availability
    system_state.dashboard.check_availability()
    if system_state.dashboard.available:
        logger.info("Enhanced dashboard connection established")
        log_to_dashboard("Dashboard connection established", 'info')
    else:
        logger.info("Dashboard not available - running in standalone mode")
    
    # Initialize components
    success_count = 0
    
    if initialize_modbus():
        success_count += 1
    
    if initialize_gmail():
        success_count += 1
    
    if check_database_health():
        success_count += 1
    
    logger.info(f"System initialized with {success_count}/3 components successful")
    
    # Get initial stats
    update_email_stats()
    
    # Perform initial health check
    perform_health_check()
    
    return success_count >= 2  # At least 2/3 components must work

def main():
    """Main system entry point"""
    try:
        logger.info("=" * 60)
        logger.info("NOC BUZZER SYSTEM WITH ENHANCED DASHBOARD STARTING")
        logger.info("=" * 60)
        print("Starting NOC Buzzer System with Enhanced Dashboard...")
        print("📊 Dashboard features: Daily Alerts, Charts, Reports, Export")
        
        # Initialize system
        if not initialize_system():
            logger.error("Critical system initialization failed!")
            if system_state.restart_count < CONFIG['MAX_RESTART_ATTEMPTS']:
                logger.info(f"Attempting restart ({system_state.restart_count + 1}/{CONFIG['MAX_RESTART_ATTEMPTS']})...")
                system_state.restart_count += 1
                time.sleep(CONFIG['RESTART_DELAY'])
                os.execv(sys.executable, [sys.executable] + sys.argv)
            else:
                logger.error("Maximum restart attempts reached. Exiting.")
                sys.exit(1)
        
        # Start health monitoring in background
        health_thread = threading.Thread(target=health_monitoring_loop, daemon=True)
        health_thread.start()
        
        # Start main email monitoring loop
        logger.info("System fully initialized. Starting email monitoring...")
        log_to_dashboard("System fully operational with enhanced dashboard", 'info')
        print("System initialized successfully. Monitoring emails...")
        
        email_monitoring_loop()
        
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
        print("\nShutdown requested by user...")
    except Exception as e:
        logger.error(f"Fatal system error: {e}")
        print(f"Fatal error: {e}")
        if system_state.restart_count < CONFIG['MAX_RESTART_ATTEMPTS']:
            logger.info("Attempting automatic restart due to fatal error...")
            system_state.restart_count += 1
            time.sleep(CONFIG['RESTART_DELAY'])
            os.execv(sys.executable, [sys.executable] + sys.argv)
    finally:
        cleanup_and_exit()

if __name__ == "__main__":
    main()