#!/usr/bin/env python3
"""
Complete Integrated NOC Buzzer System with Working Dashboard Controls
Run this single file to get both the email monitoring and dashboard working together
"""

import os
import sys
import time
import signal
import threading
import logging
import json
import difflib
import csv
from pathlib import Path
from datetime import datetime
from io import StringIO
from decisions import should_sound_buzzer
# Flask and web dependencies
from flask import Flask, jsonify, request
from flask_cors import CORS

# Import your existing modules
try:
    from simplegmail import Gmail
    from pyModbusTCP.server import ModbusServer
    import mysql.connector
    import prediction_fin
    import mysql_insert
    from bs4 import BeautifulSoup
except ImportError as e:
    print(f"Missing required module: {e}")
    print("Please install: pip install flask flask-cors simplegmail pyModbusTCP mysql-connector-python beautifulsoup4")
    sys.exit(1)

# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    'MODBUS_HOST': '192.168.69.43',
    'MODBUS_PORT': 502,
    'MODBUS_REGISTER': 10,  # Register to control buzzer
    'DASHBOARD_PORT': 5000,
    'DB_CONFIG': {
        'user': 'root',
        'password': 'deTen.12345',
        'host': '127.0.0.1',
        'database': 'nocbuzzer'
    },
    'EMAIL_CHECK_INTERVAL': 30,
    'BUZZER_DURATION': 10,
    'MAX_DESC_LEN': 255,
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
# GLOBAL SYSTEM STATE
# ============================================================================

class SystemState:
    def __init__(self):
        self.running = True
        self.buzzer_active = False
        self.modbus_server = None
        self.gmail = None
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
        self.daily_alerts = []
        self.logs = []
        self._lock = threading.RLock()

    def add_log(self, message, log_type='info'):
        with self._lock:
            timestamp = datetime.now().strftime('%H:%M:%S')
            self.logs.insert(0, {'timestamp': timestamp, 'message': message, 'type': log_type})
            self.logs[:] = self.logs[:100]
            logger.info(f"[{log_type.upper()}] {message}")

    def update_buzzer_state(self, active):
        with self._lock:
            self.buzzer_active = active

    def get_status_dict(self):
        with self._lock:
            return {
                'system_running': self.running,
                'buzzer_active': self.buzzer_active,
                'last_email_check': self.last_email_check,
                'total_emails_today': self.email_stats['total_today'],
                'alarm_count_today': self.email_stats['alarms_today'],
                'info_count_today': self.email_stats['info_today'],
                'latest_email': self.email_stats['last_email'],
                'daily_alerts': list(self.daily_alerts),
                'logs': list(self.logs)[:20],
                'components_status': dict(self.components_status),
                'timestamp': datetime.now().isoformat()
            }

system_state = SystemState()

# ============================================================================
# MODBUS BUZZER CONTROL
# ============================================================================

class BuzzerController:
    def __init__(self):
        self.server = None
        self.active_timer = None

    def initialize(self):
        try:
            self.server = ModbusServer(
                host=CONFIG['MODBUS_HOST'],
                port=CONFIG['MODBUS_PORT'],
                no_block=True
            )
            self.server.start()
            self.server.data_bank.set_holding_registers(CONFIG['MODBUS_REGISTER'], [0])
            system_state.components_status['modbus'] = True
            system_state.add_log("Modbus server initialized and buzzer set to OFF", 'info')
            return True
        except Exception as e:
            system_state.components_status['modbus'] = False
            system_state.add_log(f"Failed to initialize buzzer controller: {e}", 'error')
            return False

    def activate_buzzer(self, duration=None):
        if duration is None:
            duration = CONFIG['BUZZER_DURATION']
        try:
            if not self.server or not self.server.is_run:
                raise Exception("Modbus server not running")

            if self.active_timer:
                self.active_timer.cancel()

            # Example register pattern from your working code
            self.server.data_bank.set_holding_registers(1, [1, 10, 3, 10, 10])
            system_state.update_buzzer_state(True)
            system_state.add_log(f"BUZZER ACTIVATED for {duration} seconds", 'alarm')

            def auto_deactivate():
                try:
                    self.deactivate_buzzer()
                    system_state.add_log("Buzzer automatically deactivated", 'info')
                except Exception as e:
                    system_state.add_log(f"Failed to auto-deactivate buzzer: {e}", 'error')

            self.active_timer = threading.Timer(duration, auto_deactivate)
            self.active_timer.start()
            return True
        except Exception as e:
            system_state.add_log(f"Failed to activate buzzer: {e}", 'error')
            return False

    def deactivate_buzzer(self):
        try:
            if not self.server or not self.server.is_run:
                raise Exception("Modbus server not running")
            if self.active_timer:
                self.active_timer.cancel()
                self.active_timer = None
            self.server.data_bank.set_holding_registers(1, [1, 0, 3, 10, 0])
            system_state.update_buzzer_state(False)
            system_state.add_log("Buzzer deactivated", 'info')
            return True
        except Exception as e:
            system_state.add_log(f"Failed to deactivate buzzer: {e}", 'error')
            return False

    def test_buzzer(self, duration=5):
        system_state.add_log(f"Testing buzzer for {duration} seconds", 'info')
        return self.activate_buzzer(duration)

    def cleanup(self):
        try:
            if self.active_timer:
                self.active_timer.cancel()
            if self.server and self.server.is_run:
                self.server.data_bank.set_holding_registers(1, [1, 0, 3, 10, 0])
                self.server.stop()
            system_state.add_log("Buzzer controller cleaned up", 'info')
        except Exception as e:
            logger.error(f"Error cleaning up buzzer controller: {e}")

buzzer_controller = BuzzerController()

# ============================================================================
# DATABASE OPERATIONS
# ============================================================================

def get_db_connection():
    try:
        cnx = mysql.connector.connect(**CONFIG['DB_CONFIG'])
        system_state.components_status['database'] = True
        return cnx
    except Exception as e:
        system_state.components_status['database'] = False
        system_state.add_log(f"Database connection failed: {e}", 'error')
        return None

def update_email_stats_from_db():
    try:
        cnx = get_db_connection()
        if not cnx:
            return

        cursor = cnx.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM Faults 
            WHERE DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) = CURDATE()
        """)
        total = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM Faults 
            WHERE fault_category = 'Alarm' 
            AND DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) = CURDATE()
        """)
        alarms = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM Faults 
            WHERE fault_category != 'Alarm' 
            AND DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) = CURDATE()
        """)
        info = cursor.fetchone()[0]

        cursor.execute("""
            SELECT fault_description, fault_category, fault_date_time 
            FROM Faults 
            ORDER BY STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s') DESC 
            LIMIT 1
        """)
        latest = cursor.fetchone()

        system_state.email_stats['total_today'] = total
        system_state.email_stats['alarms_today'] = alarms
        system_state.email_stats['info_today'] = info

        if latest:
            system_state.email_stats['last_email'] = {
                'description': latest[0],
                'category': latest[1],
                'timestamp': latest[2]
            }

        cursor.execute("""
            SELECT fault_description, fault_category, fault_date_time 
            FROM Faults 
            WHERE DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) = CURDATE()
            ORDER BY STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s') DESC
        """)
        alerts = []
        for row in cursor.fetchall():
            alerts.append({
                'description': row[0],
                'category': row[1],
                'timestamp': row[2],
                'severity': 'high' if row[1].lower() == 'alarm' else 'medium'
            })

        system_state.daily_alerts = alerts

        cursor.close()
        cnx.close()
    except Exception as e:
        system_state.add_log(f"Failed to get database stats: {e}", 'error')

# ============================================================================
# EMAIL PROCESSING SYSTEM
# ============================================================================

def is_similar_to_false_alert(text, threshold=0.7):
    for alert in FALSE_ALERTS:
        if difflib.SequenceMatcher(None, text, alert).ratio() >= threshold:
            system_state.add_log("Email filtered as false alert", 'info')
            return True
    return False

def extract_text_from_html(html_content):
    if not html_content:
        return ""
    try:
        return BeautifulSoup(html_content, "html.parser").get_text(separator=" ", strip=True)
    except:
        return html_content

def process_email(message_body, prediction):
    try:
        mysql_insert.insert_mysql(message_body, prediction)
        system_state.add_log(f"Email stored in DB with classification: {prediction}", 'info')

        system_state.email_stats['total_today'] += 1
        if prediction.lower() == 'alarm':
            system_state.email_stats['alarms_today'] += 1
        else:
            system_state.email_stats['info_today'] += 1

        system_state.email_stats['last_email'] = {
            'description': message_body[:100] + "..." if len(message_body) > 100 else message_body,
            'category': prediction,
            'timestamp': datetime.now().strftime('%d/%m/%Y %H:%M:%S')
        }

        if prediction.lower() == 'alarm':
            system_state.add_log(f"ALARM DETECTED: {message_body[:50]}...", 'alarm')
            buzzer_controller.activate_buzzer()
        else:
            system_state.add_log("Info email processed", 'info')

        return True
    except Exception as e:
        system_state.add_log(f"Failed to process email: {e}", 'error')
        return False

def email_monitoring_loop():
    system_state.add_log("Email monitoring started", 'info')
    try:
        gmail = Gmail()
        system_state.gmail = gmail
        system_state.components_status['gmail'] = True
        system_state.add_log("Gmail connection established", 'info')
    except Exception as e:
        system_state.components_status['gmail'] = False
        system_state.add_log(f"Failed to initialize Gmail: {e}", 'error')
        return

    while system_state.running:
        try:
            system_state.last_email_check = datetime.now().strftime('%H:%M:%S')
            messages = gmail.get_unread_inbox() or []

            if messages:
                system_state.add_log(f"Processing {len(messages)} unread message(s)", 'info')
                for message in messages:
                    if not system_state.running:
                        break
                    try:
                        message_body = message.plain or message.html or ""
                        if message.html and not message.plain:
                            message_body = extract_text_from_html(message.html)
                        if not message_body:
                            message.mark_as_read()
                            continue
                        if is_similar_to_false_alert(message_body):
                            message.mark_as_read()
                            continue
                        if len(message_body) > CONFIG['MAX_DESC_LEN']:
                            message_body = message_body[:CONFIG['MAX_DESC_LEN']]

                        prediction = prediction_fin.classifier_pred(message_body)
                        process_email(message_body, prediction)
                        message.mark_as_read()
                    except Exception as e:
                        system_state.add_log(f"Error processing message: {e}", 'error')
                        try:
                            message.mark_as_read()
                        except:
                            pass
            update_email_stats_from_db()
        except Exception as e:
            system_state.add_log(f"Email monitoring error: {e}", 'error')
            time.sleep(60)
        time.sleep(CONFIG['EMAIL_CHECK_INTERVAL'])

# ============================================================================
# FLASK APP & DASHBOARD
# ============================================================================

app = Flask(__name__)
CORS(app)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>NOC Buzzer System Dashboard - Enhanced</title>
  <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
  <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
  <style>
    *{margin:0;padding:0;box-sizing:border-box}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,Cantarell,sans-serif;background:#f5f7fa;min-height:100vh;color:#2d3748;line-height:1.6}
    .dashboard{max-width:1200px;margin:0 auto;padding:24px}
    .header{background:#fff;padding:32px;border-radius:12px;margin-bottom:24px;box-shadow:0 1px 3px rgba(0,0,0,.1);border:1px solid #e2e8f0}
    .header h1{color:#1a202c;font-size:28px;font-weight:600;margin-bottom:16px;text-align:center;display:flex;align-items:center;justify-content:center;gap:12px}
    .tab-container{display:flex;justify-content:center;margin-bottom:24px}
    .tab-buttons{display:flex;background:#fff;border-radius:8px;padding:4px;box-shadow:0 1px 3px rgba(0,0,0,.1)}
    .tab-btn{padding:12px 24px;border:none;background:transparent;cursor:pointer;border-radius:6px;font-weight:500;transition:.2s;display:flex;align-items:center;gap:8px}
    .tab-btn.active{background:#3b82f6;color:#fff}
    .tab-btn:not(.active):hover{background:#f1f5f9}
    .tab-content{display:none}
    .tab-content.active{display:block}
    .charts-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(500px,1fr));gap:24px;margin-bottom:24px}
    .chart-card{background:#fff;padding:24px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.1);border:1px solid #e2e8f0}
    .chart-card h3{color:#1a202c;margin-bottom:20px;font-size:18px;font-weight:600;display:flex;align-items:center;gap:8px}
    .alerts-table{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.1);border:1px solid #e2e8f0;overflow:hidden}
    .alerts-table h3{padding:24px;margin:0;border-bottom:1px solid #e2e8f0;background:#f8fafc;font-size:18px;font-weight:600;display:flex;align-items:center;justify-content:space-between}
    .table-container{max-height:600px;overflow-y:auto}
    table{width:100%;border-collapse:collapse}
    th,td{padding:12px 16px;text-align:left;border-bottom:1px solid #e2e8f0}
    th{background:#f8fafc;font-weight:600;color:#374151;position:sticky;top:0;z-index:10}
    .severity-high{color:#dc2626;font-weight:600}
    .severity-medium{color:#d97706}
    .alert-description{max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .controls,.card,.stat-card,.buzzer-status{background:#fff;padding:24px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.1);border:1px solid #e2e8f0}
    .controls{margin-bottom:24px}
    .controls h3,.card h3{color:#1a202c;margin-bottom:20px;font-size:18px;font-weight:600;display:flex;align-items:center;gap:8px;padding-bottom:12px;border-bottom:1px solid #e2e8f0}
    .status-bar{display:flex;justify-content:center;gap:24px;flex-wrap:wrap}
    .status-item{display:flex;align-items:center;gap:8px;padding:12px 20px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0;font-size:14px;font-weight:500}
    .status-dot{width:8px;height:8px;border-radius:50%;animation:pulse 2s infinite}
    .status-dot.online{background:#10b981}.status-dot.offline{background:#ef4444}.status-dot.warning{background:#f59e0b}
    @keyframes pulse{0%{opacity:1}50%{opacity:.7}100%{opacity:1}}
    .stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;margin-bottom:24px}
    .stat-card{text-align:center}.stat-number{font-size:32px;font-weight:700;margin-bottom:8px}.stat-label{color:#64748b;font-size:14px;font-weight:500}.stat-icon{font-size:24px;margin-bottom:12px}
    .alarm{color:#ef4444}.info{color:#3b82f6}.total{color:#6366f1}.time{color:#10b981}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:24px;margin-bottom:24px}
    .control-buttons{display:flex;gap:12px;flex-wrap:wrap}
    .btn{padding:12px 20px;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:600;transition:.2s;display:flex;align-items:center;gap:8px;min-width:120px;justify-content:center}
    .btn:hover:not(:disabled){transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.15)}
    .btn-primary{background:#3b82f6;color:#fff}.btn-success{background:#10b981;color:#fff}.btn-danger{background:#ef4444;color:#fff}
    .btn:disabled{opacity:.5;cursor:not-allowed}
    .buzzer-status{display:flex;align-items:center;gap:16px;margin-bottom:24px;border-left:4px solid #f59e0b}
    .buzzer-icon{font-size:24px;color:#f59e0b}
    .buzzer-status.active{border-left-color:#ef4444;background:#fef2f2}
    .buzzer-status.active .buzzer-icon{color:#ef4444;animation:shake .5s infinite}
    @keyframes shake{0%,100%{transform:translateX(0)}25%{transform:translateX(-2px)}75%{transform:translateX(2px)}}
    .log-container{max-height:400px;overflow-y:auto;background:#1e293b;border-radius:8px;padding:16px;font-family:SFMono,Monaco,Cascadia Mono,Consolas,'Courier New',monospace}
    .log-entry{color:#e2e8f0;padding:6px 0;font-size:13px;line-height:1.4}
    .log-entry.alarm,.log-entry.error{color:#fca5a5}.log-entry.info{color:#93c5fd}.log-entry.warning{color:#fcd34d}
    .email-preview{background:#f8fafc;border-left:3px solid #3b82f6;padding:16px;margin:12px 0;border-radius:6px}
    .email-meta{font-size:13px;color:#64748b;margin-bottom:8px;font-weight:500}
    .email-content{font-size:14px;line-height:1.5}
    .response-message{padding:12px 16px;border-radius:8px;margin:12px 0;text-align:center;font-weight:500;font-size:14px}
    .success{background:#dcfce7;color:#166534;border:1px solid #bbf7d0}.error{background:#fee2e2;color:#991b1b;border:1px solid #fecaca}
    .empty-state{text-align:center;padding:40px 20px;color:#64748b}
    .empty-state i{font-size:48px;margin-bottom:16px;opacity:.5}
    .connection-status{position:fixed;top:20px;right:20px;padding:8px 16px;border-radius:6px;font-size:12px;font-weight:600;z-index:1000}
    .connection-status.connected{background:#dcfce7;color:#166534;border:1px solid #bbf7d0}
    .connection-status.disconnected{background:#fee2e2;color:#991b1b;border:1px solid #fecaca}
  </style>
</head>
<body>
  <div class="connection-status connected" id="connectionStatus">
    <i class="fas fa-circle"></i> Connected
  </div>

  <div class="dashboard">
    <div class="header">
      <h1><i class="fas fa-bell"></i> NOC Buzzer System Dashboard</h1>
      <div class="status-bar">
        <div class="status-item"><div class="status-dot online" id="systemStatus"></div><span>System Status</span></div>
        <div class="status-item"><div class="status-dot online" id="gmailStatus"></div><span>Gmail Connection</span></div>
        <div class="status-item"><div class="status-dot online" id="dbStatus"></div><span>Database</span></div>
        <div class="status-item"><div class="status-dot online" id="modbusStatus"></div><span>Modbus Server</span></div>
      </div>
    </div>

    <div class="tab-container">
      <div class="tab-buttons">
        <button class="tab-btn active" onclick="switchTab('overview', this)"><i class="fas fa-tachometer-alt"></i> Overview</button>
        <button class="tab-btn" onclick="switchTab('alerts', this)"><i class="fas fa-exclamation-triangle"></i> Daily Alerts</button>
        <button class="tab-btn" onclick="switchTab('charts', this)"><i class="fas fa-chart-line"></i> Analytics</button>
        <button class="tab-btn" onclick="switchTab('reports', this)"><i class="fas fa-file-alt"></i> Reports</button>
        <button class="tab-btn" onclick="switchTab('controls', this)"><i class="fas fa-sliders-h"></i> Controls</button>
      </div>
    </div>

    <!-- Overview Tab -->
    <div id="overview-tab" class="tab-content active">
      <div class="stats-grid">
        <div class="stat-card"><div class="stat-icon total"><i class="fas fa-envelope"></i></div><div class="stat-number total" id="totalEmails">--</div><div class="stat-label">Total Emails Today</div></div>
        <div class="stat-card"><div class="stat-icon alarm"><i class="fas fa-exclamation-triangle"></i></div><div class="stat-number alarm" id="alarmCount">--</div><div class="stat-label">Alarm Notifications</div></div>
        <div class="stat-card"><div class="stat-icon info"><i class="fas fa-info-circle"></i></div><div class="stat-number info" id="infoCount">--</div><div class="stat-label">Info Notifications</div></div>
        <div class="stat-card"><div class="stat-icon time"><i class="fas fa-clock"></i></div><div class="stat-number time" id="lastCheck">--:--</div><div class="stat-label">Last Email Check</div></div>
      </div>

      <div class="alerts-table">
        <h3><span><i class="fas fa-list"></i> Recent Alerts</span><span id="alertsCount" class="stat-number" style="font-size:18px;">0</span></h3>
        <div class="table-container">
          <table>
            <thead><tr><th>Time</th><th>Category</th><th>Description</th><th>Severity</th></tr></thead>
            <tbody id="alertsTableBody">
              <tr><td colspan="4" style="text-align:center;padding:40px;"><i class="fas fa-inbox" style="font-size:48px;opacity:.3;margin-bottom:16px;"></i><br>No alerts today yet</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="grid">
        <div class="card">
          <h3><i class="fas fa-envelope-open-text"></i> Latest Email</h3>
          <div id="latestEmail" class="empty-state"><i class="fas fa-inbox"></i><br>No email processed yet</div>
        </div>
        <div class="card">
          <h3><i class="fas fa-terminal"></i> Recent Logs</h3>
          <div id="logContainer" class="log-container"></div>
        </div>
      </div>
    </div>

    <!-- Daily Alerts Tab -->
    <div id="alerts-tab" class="tab-content">
      <div class="alerts-table">
        <h3>
          <span><i class="fas fa-calendar-day"></i> Today's Alerts</span>
          <div class="export-buttons">
            <button class="btn btn-primary" onclick="exportAlerts('csv')"><i class="fas fa-download"></i> Export CSV</button>
            <button class="btn btn-success" onclick="exportAlerts('json')"><i class="fas fa-download"></i> Export JSON</button>
          </div>
        </h3>
        <div class="table-container">
          <table>
            <thead><tr><th>Time</th><th>Category</th><th>Description</th><th>Severity</th></tr></thead>
            <tbody id="dailyAlertsTableBody">
              <tr><td colspan="4" style="text-align:center;padding:40px;"><i class="fas fa-spinner fa-spin" style="font-size:24px;"></i><br>Loading alerts...</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Analytics Tab -->
    <div id="charts-tab" class="tab-content">
      <div class="charts-grid">
        <div class="chart-card"><h3><i class="fas fa-chart-bar"></i> Hourly Distribution</h3><canvas id="hourlyChart"></canvas></div>
        <div class="chart-card"><h3><i class="fas fa-chart-pie"></i> Alert Categories</h3><canvas id="categoryChart"></canvas></div>
      </div>
      <div class="chart-card"><h3><i class="fas fa-chart-line"></i> Weekly Trend</h3><canvas id="weeklyChart"></canvas></div>
    </div>

    <!-- Reports Tab -->
    <div id="reports-tab" class="tab-content">
      <div class="controls">
        <h3><i class="fas fa-file-alt"></i> Generate Reports</h3>
        <div class="report-controls" style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:16px;">
          <label>Start Date:</label><input type="date" id="startDate" value="">
          <label>End Date:</label><input type="date" id="endDate" value="">
          <label>Report Type:</label>
          <select id="reportType">
            <option value="daily">Detailed Daily Report</option>
            <option value="summary">Summary Report</option>
          </select>
          <button class="btn btn-primary" onclick="generateReport()"><i class="fas fa-file-alt"></i> Generate Report</button>
        </div>
        <div id="reportResponse"></div>
      </div>

      <div class="alerts-table" id="reportTable" style="display:none;">
        <h3>
          <span id="reportTitle">Report Results</span>
          <div class="export-buttons">
            <button class="btn btn-primary" onclick="exportReport('csv')"><i class="fas fa-download"></i> Export CSV</button>
            <button class="btn btn-success" onclick="exportReport('pdf')"><i class="fas fa-file-pdf"></i> Export PDF</button>
          </div>
        </h3>
        <div id="reportSummary" style="padding:16px;background:#f8fafc;border-bottom:1px solid #e2e8f0;"></div>
        <div class="table-container">
          <table>
            <thead id="reportTableHead"></thead>
            <tbody id="reportTableBody"></tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Controls Tab -->
    <div id="controls-tab" class="tab-content">
      <div class="buzzer-status" id="buzzerStatus">
        <div class="buzzer-icon"><i class="fas fa-bell"></i></div>
        <div class="buzzer-details">
          <h3>Buzzer Status: <span id="buzzerState">Inactive</span></h3>
          <p>Last activated: <span id="lastBuzzer">Never</span></p>
        </div>
      </div>
      <div class="controls">
        <h3><i class="fas fa-sliders-h"></i> Buzzer Controls</h3>
        <div class="control-buttons">
          <button class="btn btn-primary" onclick="testBuzzer()"><i class="fas fa-volume-up"></i> Test Buzzer</button>
          <button class="btn btn-danger" onclick="stopBuzzer()"><i class="fas fa-stop"></i> Stop Buzzer</button>
          <button class="btn btn-success" onclick="startSystem()"><i class="fas fa-play"></i> Start System</button>
        </div>
        <div id="controlResponse"></div>
      </div>
    </div>
  </div>

  <script>
    const API_BASE = '/api';
    const dashboardState = { connected: false, buzzerActive: false };
    let currentReportData = null;
    let charts = {};

    document.addEventListener('DOMContentLoaded', function() {
      const today = new Date().toISOString().split('T')[0];
      document.getElementById('startDate').value = today;
      document.getElementById('endDate').value = today;
      updateDashboard();
      setInterval(updateDashboard, 3000);
    });

    function switchTab(tabName, btnEl){
      document.querySelectorAll('.tab-content').forEach(t=>t.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
      document.getElementById(tabName+'-tab').classList.add('active');
      if (btnEl) btnEl.classList.add('active');
      if (tabName==='alerts') loadDailyAlerts();
      else if (tabName==='charts') loadCharts();
    }

    async function apiCall(endpoint, method='GET', data=null){
      try{
        const opts={method,headers:{'Content-Type':'application/json'}};
        if(data && method!=='GET') opts.body=JSON.stringify(data);
        const res=await fetch(`${API_BASE}/${endpoint}`,opts);
        return await res.json();
      }catch(e){
        console.error('API call failed',endpoint,e);
        return {success:false,error:e.message};
      }
    }

    async function updateDashboard(){
      try{
        const status=await apiCall('status');
        if(status.system_running!==undefined){
          dashboardState.connected=true;
          updateConnectionStatus(true);
          if (!status.components_status) status.components_status = {};
          updateStatusIndicators(status.components_status);

          document.getElementById('totalEmails').textContent=status.total_emails_today||0;
          document.getElementById('alarmCount').textContent=status.alarm_count_today||0;
          document.getElementById('infoCount').textContent=status.info_count_today||0;
          document.getElementById('lastCheck').textContent=status.last_email_check||'--:--';

          dashboardState.buzzerActive=status.buzzer_active;
          updateBuzzerStatus(status.buzzer_active);

          if(status.latest_email) updateLatestEmail(status.latest_email);
          if(status.daily_alerts) updateRecentAlertsTable(status.daily_alerts.slice(0,10));
          if(status.logs && status.logs.length>0) updateLogs(status.logs);
        }else{
          dashboardState.connected=false;
          updateConnectionStatus(false);
          updateStatusIndicators({});
        }
      }catch(e){
        console.error('Dashboard update failed:',e);
        dashboardState.connected=false;
        updateConnectionStatus(false);
        updateStatusIndicators({});
      }
    }

    function updateConnectionStatus(connected){
      const el=document.getElementById('connectionStatus');
      if(connected){el.className='connection-status connected'; el.innerHTML='<i class="fas fa-circle"></i> Connected';}
      else{el.className='connection-status disconnected'; el.innerHTML='<i class="fas fa-circle"></i> Disconnected';}
    }

    function updateStatusIndicators(components={}){
      const indicators={
        systemStatus: ('system' in components)?components.system:dashboardState.connected,
        gmailStatus: !!components.gmail,
        dbStatus: !!components.database,
        modbusStatus: !!components.modbus
      };
      Object.entries(indicators).forEach(([id,ok])=>{
        const el=document.getElementById(id);
        if(el) el.className = 'status-dot ' + (ok?'online':'offline');
      });
      if(dashboardState.buzzerActive && components.modbus){
        const m=document.getElementById('modbusStatus');
        if(m) m.className='status-dot warning';
      }
    }

    function updateBuzzerStatus(active){
      const wrap=document.getElementById('buzzerStatus');
      const state=document.getElementById('buzzerState');
      if(active){
        wrap.classList.add('active');
        state.textContent='Active';
        const ts=document.getElementById('lastBuzzer'); if(ts) ts.textContent=new Date().toLocaleString();
      }else{
        wrap.classList.remove('active');
        state.textContent='Inactive';
      }
    }

    function updateLatestEmail(email){
      const container=document.getElementById('latestEmail');
      const isAlarm=(email.category||'').toLowerCase()==='alarm';
      const color=isAlarm?'#ef4444':'#3b82f6';
      const icon=isAlarm?'fas fa-exclamation-triangle':'fas fa-info-circle';
      container.innerHTML=`
        <div class="email-preview">
          <div class="email-meta">
            <i class="fas fa-clock"></i> ${email.timestamp} |
            <i class="${icon}" style="color:${color}"></i>
            <strong style="color:${color}">${email.category}</strong>
          </div>
          <div class="email-content">${email.description}</div>
        </div>`;
    }

    function updateLogs(logs){
      const c=document.getElementById('logContainer');
      c.innerHTML='';
      logs.forEach(log=>{
        const div=document.createElement('div');
        div.className='log-entry '+(log.type||'info');
        div.textContent=`[${log.timestamp}] ${log.message}`;
        c.appendChild(div);
      });
    }

    function updateRecentAlertsTable(alerts){
      const tbody=document.getElementById('alertsTableBody');
      const alertsCount=document.getElementById('alertsCount');
      if(!alerts || alerts.length===0){
        tbody.innerHTML=`<tr><td colspan="4" style="text-align:center;padding:40px;">
          <i class="fas fa-inbox" style="font-size:48px;opacity:.3;margin-bottom:16px;"></i><br>No alerts today yet
        </td></tr>`;
        alertsCount.textContent='0'; return;
      }
      alertsCount.textContent=alerts.length;
      tbody.innerHTML=alerts.map(a=>`
        <tr>
          <td>${formatTime(a.timestamp)}</td>
          <td><span class="severity-${a.severity}">${a.category}</span></td>
          <td class="alert-description" title="${a.description}">${a.description}</td>
          <td><span class="severity-${a.severity}">${String(a.severity||'').toUpperCase()}</span></td>
        </tr>`).join('');
    }

    async function loadDailyAlerts(){
      try{
        const res=await apiCall('daily-alerts');
        if(res.success){
          const tbody=document.getElementById('dailyAlertsTableBody');
          const alerts=res.alerts||[];
          if(alerts.length===0){
            tbody.innerHTML=`<tr><td colspan="4" style="text-align:center;padding:40px;">
              <i class="fas fa-inbox" style="font-size:48px;opacity:.3;margin-bottom:16px;"></i><br>No alerts today
            </td></tr>`; return;
          }
          tbody.innerHTML=alerts.map(a=>`
            <tr>
              <td>${formatTime(a.timestamp)}</td>
              <td><span class="severity-${a.severity}">${a.category}</span></td>
              <td class="alert-description" title="${a.description}">${a.description}</td>
              <td><span class="severity-${a.severity}">${String(a.severity||'').toUpperCase()}</span></td>
            </tr>`).join('');
        }
      }catch(e){console.error('Failed to load daily alerts:',e);}
    }

    async function loadCharts(){
      try{
        const hourly=await apiCall('hourly-stats');
        const weekly=await apiCall('weekly-summary');
        if(hourly.success){createHourlyChart(hourly.data); createCategoryChart(hourly.data);}
        if(weekly.success){createWeeklyChart(weekly.data);}
      }catch(e){console.error('Failed to load charts:',e);}
    }

    function createHourlyChart(hourlyData){
      const ctx=document.getElementById('hourlyChart').getContext('2d');
      if(charts.hourly) charts.hourly.destroy();
      const hours=Array.from({length:24},(_,i)=>i);
      const alarmData=hours.map(h=>hourlyData[h]?hourlyData[h].alarm:0);
      const infoData=hours.map(h=>hourlyData[h]?hourlyData[h].info:0);
      charts.hourly=new Chart(ctx,{
        type:'bar',
        data:{labels:hours.map(h=>String(h).padStart(2,'0')+':00'),
          datasets:[
            {label:'Alarms',data:alarmData,backgroundColor:'rgba(239,68,68,0.8)',borderColor:'rgba(239,68,68,1)',borderWidth:1},
            {label:'Info',data:infoData,backgroundColor:'rgba(59,130,246,0.8)',borderColor:'rgba(59,130,246,1)',borderWidth:1}
          ]},
        options:{responsive:true,scales:{y:{beginAtZero:true,ticks:{stepSize:1}}}}
      });
    }

    function createCategoryChart(hourlyData){
      const ctx=document.getElementById('categoryChart').getContext('2d');
      if(charts.category) charts.category.destroy();
      let totalAlarms=0,totalInfo=0;
      Object.values(hourlyData||{}).forEach(h=>{totalAlarms+=h.alarm||0; totalInfo+=h.info||0;});
      charts.category=new Chart(ctx,{
        type:'doughnut',
        data:{labels:['Alarms','Info'],datasets:[{data:[totalAlarms,totalInfo],
          backgroundColor:['rgba(239,68,68,0.8)','rgba(59,130,246,0.8)'],
          borderColor:['rgba(239,68,68,1)','rgba(59,130,246,1)'],borderWidth:2}]},
        options:{responsive:true,plugins:{legend:{position:'bottom'}}}
      });
    }

    function createWeeklyChart(weeklyData){
      const ctx=document.getElementById('weeklyChart').getContext('2d');
      if(charts.weekly) charts.weekly.destroy();
      const dates=Object.keys(weeklyData||{}).sort();
      const alarmData=dates.map(d=>weeklyData[d].alarm||0);
      const infoData=dates.map(d=>weeklyData[d].info||0);
      charts.weekly=new Chart(ctx,{
        type:'line',
        data:{labels:dates,datasets:[
          {label:'Alarms',data:alarmData,borderColor:'rgba(239,68,68,1)',backgroundColor:'rgba(239,68,68,0.1)',tension:.4,fill:true},
          {label:'Info',data:infoData,borderColor:'rgba(59,130,246,1)',backgroundColor:'rgba(59,130,246,0.1)',tension:.4,fill:true}
        ]},
        options:{responsive:true,scales:{y:{beginAtZero:true,ticks:{stepSize:1}}}}
      });
    }

    function showResponse(message,isSuccess=true,elementId='controlResponse'){
      const div=document.getElementById(elementId);
      div.innerHTML=`<div class="response-message ${isSuccess?'success':'error'}">${message}</div>`;
      setTimeout(()=>{div.innerHTML='';},5000);
    }

    async function testBuzzer(){
      try{
        const res=await apiCall('test-buzzer','POST',{duration:5});
        if(res.success) showResponse('<i class="fas fa-check"></i> '+res.message);
        else showResponse('<i class="fas fa-times"></i> Failed to test buzzer: '+(res.message||'error'),false);
      }catch(e){showResponse('<i class="fas fa-times"></i> Failed to test buzzer: '+e.message,false);}
    }

    async function stopBuzzer(){
      try{
        const res=await apiCall('stop-buzzer','POST');
        if(res.success) showResponse('<i class="fas fa-check"></i> '+res.message);
        else showResponse('<i class="fas fa-times"></i> Failed to stop buzzer: '+(res.message||'error'),false);
      }catch(e){showResponse('<i class="fas fa-times"></i> Failed to stop buzzer: '+e.message,false);}
    }

    async function startSystem(){
      try{
        const res=await apiCall('start-system','POST');
        if(res.success) showResponse('<i class="fas fa-check"></i> '+res.message);
        else showResponse('<i class="fas fa-times"></i> Failed to start system: '+(res.message||'error'),false);
      }catch(e){showResponse('<i class="fas fa-times"></i> Failed to start system: '+e.message,false);}
    }

    function formatTime(ts){
      try{
        let d;
        if(typeof ts==='number') d=new Date(ts);
        else if(typeof ts==='string' && ts.includes('/')){
          const [datePart, timePart='00:00:00']=ts.split(' ');
          const [dd,mm,yyyy]=datePart.split('/').map(Number);
          const [HH=0,MM=0,SS=0]=timePart.split(':').map(Number);
          d=new Date(yyyy,mm-1,dd,HH,MM,SS);
        }else d=new Date(ts);
        return d.toLocaleTimeString('en-GB',{hour12:false,hour:'2-digit',minute:'2-digit'});
      }catch{return String(ts);}
    }

    async function generateReport(){
      const startDate=document.getElementById('startDate').value;
      const endDate=document.getElementById('endDate').value;
      const reportType=document.getElementById('reportType').value;
      if(!startDate||!endDate){showResponse('Please select both start and end dates',false,'reportResponse');return;}
      try{
        const res=await apiCall('generate-report','POST',{start_date:startDate,end_date:endDate,report_type:reportType});
        if(res.success){
          currentReportData=res.data;
          displayReport(res.data);
          showResponse('Report generated successfully',true,'reportResponse');
        }else showResponse('Failed to generate report: '+(res.message||'error'),false,'reportResponse');
      }catch(e){showResponse('Error generating report: '+e.message,false,'reportResponse');}
    }

    function displayReport(reportData){
      const reportTable=document.getElementById('reportTable');
      const reportTitle=document.getElementById('reportTitle');
      const reportSummary=document.getElementById('reportSummary');
      const reportTableHead=document.getElementById('reportTableHead');
      const reportTableBody=document.getElementById('reportTableBody');

      reportTable.style.display='block';
      reportTitle.textContent = `Report: ${reportData.summary.date_range}`;
      reportSummary.innerHTML=`
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;">
          <div><strong>Total Alerts:</strong> ${reportData.summary.total_alerts}</div>
          <div><strong>Alarms:</strong> <span class="severity-high">${reportData.summary.total_alarms}</span></div>
          <div><strong>Info:</strong> <span class="severity-medium">${reportData.summary.total_info}</span></div>
          <div><strong>Report Type:</strong> ${reportData.summary.report_type}</div>
        </div>`;

      if(reportData.summary.report_type==='daily'){
        reportTableHead.innerHTML = `<tr><th>Timestamp</th><th>Category</th><th>Description</th><th>Severity</th></tr>`;
        reportTableBody.innerHTML = reportData.data.map(a=>`
          <tr>
            <td>${a.timestamp}</td>
            <td><span class="severity-${a.severity}">${a.category}</span></td>
            <td class="alert-description" title="${a.description}">${a.description}</td>
            <td><span class="severity-${a.severity}">${a.severity.toUpperCase()}</span></td>
          </tr>`).join('');
      }else{
        reportTableHead.innerHTML = `<tr><th>Date</th><th>Category</th><th>Count</th><th>Sample Alerts</th></tr>`;
        reportTableBody.innerHTML = reportData.data.map(i=>`
          <tr>
            <td>${i.date}</td>
            <td><span class="severity-${i.category.toLowerCase()==='alarm'?'high':'medium'}">${i.category}</span></td>
            <td><strong>${i.count}</strong></td>
            <td class="alert-description" title="${i.sample_alerts}">${i.sample_alerts}</td>
          </tr>`).join('');
      }
    }

    async function exportAlerts(format){
      try{
        const res=await apiCall('export-alerts/'+format);
        if(res.success){
          downloadFile(res.data, `daily_alerts_${new Date().toISOString().split('T')[0]}.${format}`, format);
        }
      }catch(e){console.error('Export failed:',e);}
    }

    async function exportReport(format){
      if(!currentReportData){alert('Please generate a report first');return;}
      try{
        const res=await apiCall('export-report/'+format,'POST',currentReportData);
        if(res.success){
          const startDate=document.getElementById('startDate').value;
          const endDate=document.getElementById('endDate').value;
          downloadFile(res.data, `report_${startDate}_to_${endDate}.${format}`, format);
        }
      }catch(e){console.error('Export failed:',e);}
    }

    function downloadFile(data, filename, format){
      const blob=new Blob([data],{
        type: format==='csv'?'text/csv': format==='json'?'application/json':'text/html'
      });
      const url=window.URL.createObjectURL(blob);
      const a=document.createElement('a'); a.href=url; a.download=filename; document.body.appendChild(a); a.click();
      window.URL.revokeObjectURL(url); document.body.removeChild(a);
    }
  </script>
</body>
</html>
"""

# ============================================================================
# API HELPERS REQUIRED BY FRONTEND
# ============================================================================

def get_hourly_statistics():
    """Return dict: {0..23: {'alarm': n, 'info': m}} for today."""
    cnx = get_db_connection()
    stats = {h: {'alarm': 0, 'info': 0} for h in range(24)}
    if not cnx:
        return stats
    try:
        cur = cnx.cursor()
        cur.execute("""
            SELECT HOUR(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) AS hh,
                   SUM(fault_category='Alarm') AS alarms,
                   SUM(fault_category!='Alarm') AS infos
            FROM Faults
            WHERE DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) = CURDATE()
            GROUP BY hh
        """)
        for hh, alarms, infos in cur.fetchall():
            if hh is not None:
                stats[int(hh)] = {'alarm': int(alarms or 0), 'info': int(infos or 0)}
    finally:
        try: cur.close(); cnx.close()
        except: pass
    return stats

def get_weekly_summary():
    """Return dict keyed by ISO date: {'YYYY-MM-DD': {'alarm': n, 'info': m}} for last 7 days."""
    cnx = get_db_connection()
    summary = {}
    if not cnx:
        return summary
    try:
        cur = cnx.cursor()
        cur.execute("""
            SELECT DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) AS d,
                   SUM(fault_category='Alarm') AS alarms,
                   SUM(fault_category!='Alarm') AS infos
            FROM Faults
            WHERE STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s') >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
            GROUP BY d
            ORDER BY d
        """)
        for d, alarms, infos in cur.fetchall():
            summary[d.strftime('%Y-%m-%d')] = {'alarm': int(alarms or 0), 'info': int(infos or 0)}
    finally:
        try: cur.close(); cnx.close()
        except: pass
    return summary

def generate_report_data(start_date, end_date, report_type='daily'):
    """
    start_date, end_date: 'YYYY-MM-DD'
    report_type: 'daily' or 'summary'
    """
    cnx = get_db_connection()
    if not cnx:
        raise RuntimeError("DB unavailable")

    base_where = "DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) BETWEEN %s AND %s"

    try:
        cur = cnx.cursor(dictionary=True)
        cur.execute(f"""
            SELECT 
              COUNT(*) AS total_alerts,
              SUM(fault_category='Alarm') AS total_alarms,
              SUM(fault_category!='Alarm') AS total_info
            FROM Faults
            WHERE {base_where}
        """, (start_date, end_date))
        s = cur.fetchone() or {}

        summary = {
            'date_range': f"{start_date} to {end_date}",
            'total_alerts': int(s.get('total_alerts') or 0),
            'total_alarms': int(s.get('total_alarms') or 0),
            'total_info': int(s.get('total_info') or 0),
            'report_type': report_type
        }

        if report_type == 'daily':
            cur.execute(f"""
                SELECT fault_date_time AS timestamp,
                       fault_category AS category,
                       fault_description AS description,
                       CASE WHEN fault_category='Alarm' THEN 'high' ELSE 'medium' END AS severity
                FROM Faults
                WHERE {base_where}
                ORDER BY STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s') DESC
            """, (start_date, end_date))
            data = cur.fetchall()
        else:
            cur.execute(f"""
                SELECT DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) AS d,
                       fault_category AS category,
                       COUNT(*) AS cnt
                FROM Faults
                WHERE {base_where}
                GROUP BY d, category
                ORDER BY d DESC, category
            """, (start_date, end_date))
            rows = cur.fetchall()
            data = []
            for r in rows:
                cur2 = cnx.cursor()
                cur2.execute(f"""
                    SELECT fault_description FROM Faults
                    WHERE {base_where}
                      AND DATE(STR_TO_DATE(fault_date_time, '%d/%m/%Y %H:%i:%s')) = %s
                      AND fault_category = %s
                    LIMIT 2
                """, (start_date, end_date, r['d'], r['category']))
                samples = [x[0] for x in cur2.fetchall()]
                cur2.close()
                data.append({
                    'date': r['d'].strftime('%Y-%m-%d'),
                    'category': r['category'],
                    'count': int(r['cnt']),
                    'sample_alerts': " | ".join(samples)
                })

        return {'summary': summary, 'data': data}
    finally:
        try: cur.close(); cnx.close()
        except: pass

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/')
def dashboard():
    return DASHBOARD_HTML

@app.route('/api/status')
def get_status():
    d = system_state.get_status_dict()
    d['components_status']['system'] = True if d.get('system_running') else False
    return jsonify(d)

@app.route('/api/test-buzzer', methods=['POST'])
def test_buzzer_api():
    try:
        data = request.get_json() or {}
        duration = data.get('duration', 5)
        system_state.add_log(f"Dashboard: Testing buzzer for {duration} seconds", 'info')
        success = buzzer_controller.test_buzzer(duration)
        if success:
            return jsonify({'success': True, 'message': f'Buzzer test started for {duration} seconds'})
        else:
            return jsonify({'success': False, 'message': 'Failed to activate buzzer - check Modbus connection'}), 500
    except Exception as e:
        system_state.add_log(f"Dashboard: Buzzer test failed: {e}", 'error')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/stop-buzzer', methods=['POST'])
def stop_buzzer_api():
    try:
        system_state.add_log("Dashboard: Stopping buzzer", 'info')
        success = buzzer_controller.deactivate_buzzer()
        if success:
            return jsonify({'success': True, 'message': 'Buzzer stopped successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to stop buzzer - check Modbus connection'}), 500
    except Exception as e:
        system_state.add_log(f"Dashboard: Failed to stop buzzer: {e}", 'error')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/start-system', methods=['POST'])
def start_system_api():
    try:
        system_state.running = True
        system_state.add_log("Dashboard: System start requested", 'info')
        if not system_state.components_status.get('modbus', False):
            buzzer_controller.initialize()
        return jsonify({'success': True, 'message': 'System start signal sent'})
    except Exception as e:
        system_state.add_log(f"Dashboard: Failed to start system: {e}", 'error')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/daily-alerts')
def get_daily_alerts_api():
    try:
        update_email_stats_from_db()
        return jsonify({'success': True, 'alerts': system_state.daily_alerts, 'count': len(system_state.daily_alerts)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/hourly-stats')
def get_hourly_stats_api():
    try:
        stats = get_hourly_statistics()
        return jsonify({'success': True, 'data': stats})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/weekly-summary')
def get_weekly_summary_api():
    try:
        summary = get_weekly_summary()
        return jsonify({'success': True, 'data': summary})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/generate-report', methods=['POST'])
def generate_report_api():
    try:
        data = request.get_json() or {}
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        report_type = data.get('report_type', 'daily')
        report_data = generate_report_data(start_date, end_date, report_type)
        if report_data:
            return jsonify({'success': True, 'data': report_data, 'message': 'Report generated successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to generate report'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/export-alerts/<format>')
def export_alerts_api(format):
    try:
        update_email_stats_from_db()
        alerts = system_state.daily_alerts
        if format == 'csv':
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(['Timestamp', 'Category', 'Description', 'Severity'])
            for a in alerts:
                writer.writerow([a['timestamp'], a['category'], a['description'], a['severity']])
            return jsonify({'success': True, 'data': output.getvalue()})
        elif format == 'json':
            return jsonify({'success': True, 'data': json.dumps({
                'export_date': datetime.now().isoformat(),
                'total_alerts': len(alerts),
                'alerts': alerts
            }, indent=2)})
        else:
            return jsonify({'success': False, 'message': 'Unsupported format'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/export-report/<format>', methods=['POST'])
def export_report_api(format):
    try:
        report_data = request.get_json() or {}
        if format == 'csv':
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(['Report Summary'])
            writer.writerow(['Date Range', report_data['summary']['date_range']])
            writer.writerow(['Total Alerts', report_data['summary']['total_alerts']])
            writer.writerow(['Total Alarms', report_data['summary']['total_alarms']])
            writer.writerow(['Total Info', report_data['summary']['total_info']])
            writer.writerow([])
            if report_data['summary']['report_type'] == 'daily':
                writer.writerow(['Timestamp', 'Category', 'Description', 'Severity'])
                for item in report_data['data']:
                    writer.writerow([item['timestamp'], item['category'], item['description'], item['severity']])
            else:
                writer.writerow(['Date', 'Category', 'Count', 'Sample Alerts'])
                for item in report_data['data']:
                    writer.writerow([item['date'], item['category'], item['count'], item['sample_alerts']])
            return jsonify({'success': True, 'data': output.getvalue()})
        elif format == 'pdf':
            html = f"""
            <html><head><title>NOC Buzzer Report</title>
            <style>
            body{{font-family:Arial,sans-serif;margin:20px}}
            .header{{background:#f8f9fa;padding:20px;border-radius:5px;margin-bottom:20px}}
            table{{width:100%;border-collapse:collapse;margin-top:20px}}
            th,td{{border:1px solid #ddd;padding:8px;text-align:left}}
            th{{background:#f8f9fa}} .alarm{{color:#dc3545;font-weight:bold}} .info{{color:#0d6efd}}
            </style></head><body>
              <div class="header">
                <h1>NOC Buzzer System Report</h1>
                <p><strong>Date Range:</strong> {report_data['summary']['date_range']}</p>
                <p><strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p><strong>Total Alerts:</strong> {report_data['summary']['total_alerts']}
                  (Alarms: {report_data['summary']['total_alarms']}, Info: {report_data['summary']['total_info']})</p>
              </div>
              <table>
            """
            if report_data['summary']['report_type'] == 'daily':
                html += "<tr><th>Timestamp</th><th>Category</th><th>Description</th><th>Severity</th></tr>"
                for item in report_data['data']:
                    cls = 'alarm' if item['severity']=='high' else 'info'
                    html += f"<tr><td>{item['timestamp']}</td><td class='{cls}'>{item['category']}</td><td>{item['description']}</td><td class='{cls}'>{item['severity'].upper()}</td></tr>"
            else:
                html += "<tr><th>Date</th><th>Category</th><th>Count</th><th>Sample Alerts</th></tr>"
                for item in report_data['data']:
                    cls = 'alarm' if item['category'].lower()=='alarm' else 'info'
                    html += f"<tr><td>{item['date']}</td><td class='{cls}'>{item['category']}</td><td><strong>{item['count']}</strong></td><td>{item['sample_alerts']}</td></tr>"
            html += "</table></body></html>"
            return jsonify({'success': True, 'data': html})
        else:
            return jsonify({'success': False, 'message': 'Unsupported format'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ============================================================================
# SIGNAL HANDLERS & MAIN
# ============================================================================

def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    system_state.running = False
    cleanup_and_exit()

def cleanup_and_exit():
    logger.info("Cleaning up system resources...")
    system_state.add_log("System shutting down", 'info')
    try:
        buzzer_controller.cleanup()
        logger.info("System shutdown complete")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
if hasattr(signal, 'SIGHUP'):
    signal.signal(signal.SIGHUP, signal_handler)

def run_flask_dashboard():
    try:
        system_state.add_log("Starting Flask dashboard server", 'info')
        app.run(host='0.0.0.0', port=CONFIG['DASHBOARD_PORT'], debug=False, use_reloader=False)
    except Exception as e:
        system_state.add_log(f"Flask dashboard error: {e}", 'error')

def run_email_monitoring():
    try:
        email_monitoring_loop()
    except Exception as e:
        system_state.add_log(f"Email monitoring error: {e}", 'error')

def initialize_system():
    logger.info("Initializing Complete NOC Buzzer System...")
    system_state.add_log("System initializing...", 'info')
    print("🚀 Initializing NOC Buzzer System with Integrated Dashboard...")

    success_count = 0
    if buzzer_controller.initialize():
        success_count += 1
        print("✅ Buzzer controller initialized")
    else:
        print("❌ Buzzer controller initialization failed")

    if get_db_connection():
        success_count += 1
        print("✅ Database connection established")
    else:
        print("❌ Database connection failed")

    update_email_stats_from_db()
    logger.info(f"System initialized with {success_count}/2 critical components successful")
    return success_count >= 1

def main():
    try:
        print("="*60)
        print("🔔 NOC BUZZER SYSTEM WITH INTEGRATED DASHBOARD")
        print("="*60)
        print("🌐 Dashboard URL: http://localhost:5000")
        if not initialize_system():
            logger.error("Critical system initialization failed!")
            print("❌ Critical system initialization failed!")
            sys.exit(1)

        print("✅ System initialization complete!")
        system_state.add_log("System fully operational", 'info')

        dashboard_thread = threading.Thread(target=run_flask_dashboard, daemon=True)
        dashboard_thread.start()
        time.sleep(2)
        print("🌐 Dashboard server started at http://localhost:5000")
        print("📧 Starting email monitoring...")
        run_email_monitoring()
    except KeyboardInterrupt:
        print("\n🛑 Shutdown requested by user...")
    except Exception as e:
        logger.error(f"Fatal system error: {e}")
        print(f"❌ Fatal error: {e}")
    finally:
        cleanup_and_exit()

if __name__ == "__main__":
    main()
