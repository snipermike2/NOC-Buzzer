import logging
import threading
import time

from simplegmail import Gmail
from bs4 import BeautifulSoup
from time import sleep

import prediction_fin
import mysql_insert

from pyModbusTCP.server import ModbusServer

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION & MODBUS SERVER SETUP
# ──────────────────────────────────────────────────────────────────────────────

POLL_INTERVAL    = 30          # seconds between Gmail polls
MAX_DESC_LEN     = 255         # truncate long messages
FALSE_ALERTS     = [
    "From : +263772151364 ZIMPLATS NGEZI SERVER ROOM:"
    "There is a problem with the environment in server room. Please attend."
]

# Create Modbus TCP server instance on localhost
server = ModbusServer(host='192.168.69.43', port=502, no_block=True)

BUZZER_FLAG_REG   = 10         # holding register index to signal PLC
BUZZER_SIGNAL_ON  = 1          # PLC sees “1” → turn buzzer on
BUZZER_SIGNAL_OFF = 0          # PLC sees “0” → turn buzzer off

def start_modbus_server():
    print("[MODBUS SERVER] Starting...")
    server.start()
    print("[MODBUS SERVER] Running on localhost:502")

# ──────────────────────────────────────────────────────────────────────────────
# INITIALIZATION
# ──────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s %(levelname)s: %(message)s"
)

# Start Modbus server in background
threading.Thread(target=start_modbus_server, daemon=True).start()
# Initialize register state
server.data_bank.set_holding_registers(BUZZER_FLAG_REG, [BUZZER_SIGNAL_OFF])

# Gmail client
gmail = Gmail()

# ──────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ──────────────────────────────────────────────────────────────────────────────

def is_similar(body: str, alerts: list[str], threshold: float = 0.5) -> bool:
    from difflib import SequenceMatcher
    for alert in alerts:
        if SequenceMatcher(None, body, alert).ratio() >= threshold:
            logging.info("Skipping known false alert")
            return True
    return False

def extract_text(html: str) -> str:
    return BeautifulSoup(html, "html.parser") \
           .get_text(separator=" ", strip=True)

def signal_buzzer(duration: int = 15):
    """Set register to 1, wait, then reset to 0."""
    try:
        server.data_bank.set_holding_registers(BUZZER_FLAG_REG, [BUZZER_SIGNAL_ON])
        logging.info("Buzzer flag set → ON")
        time.sleep(duration)
    finally:
        server.data_bank.set_holding_registers(BUZZER_FLAG_REG, [BUZZER_SIGNAL_OFF])
        logging.info("Buzzer flag reset → OFF")

def activate_buzzer_async(duration: int = 15):
    threading.Thread(target=signal_buzzer, args=(duration,), daemon=True).start()

# ──────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ──────────────────────────────────────────────────────────────────────────────

def main():
    logging.info("Starting Gmail→Modbus-Server monitor…")

    while True:
        try:
            messages = gmail.get_unread_inbox() or []
            if not messages:
                logging.debug("No unread messages.")
            for msg in messages:
                raw = msg.plain or msg.html or ""
                body = raw if msg.plain else extract_text(raw)
                logging.info(f"Email body: {body!r}")

                # skip empty / false alerts
                if not body or is_similar(body, FALSE_ALERTS):
                    msg.mark_as_read()
                    continue

                # truncate if too long
                if len(body) > MAX_DESC_LEN:
                    body = body[:MAX_DESC_LEN]

                # classify & store
                pred = prediction_fin.classifier_pred(body)
                logging.info(f"Classification: {pred}")
                try:
                    mysql_insert.insert_mysql(body, pred)
                except Exception as e:
                    logging.error(f"DB insert error: {e}")

                # if alarm → flag the register
                if pred.lower() == 'alarm':
                    logging.info("Alarm detected → signaling buzzer")
                    activate_buzzer_async(duration=30)

                msg.mark_as_read()
                logging.debug("Marked message as read.")

        except Exception as e:
            logging.error(f"Main loop exception: {e}")

        sleep(POLL_INTERVAL)


if __name__ == '__main__':
    main()
