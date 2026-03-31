from datetime import datetime

LOG_FILE = "logs.txt"

def log_message(level, message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log = f"{timestamp} - {level} - {message}\n"

    with open(LOG_FILE, "a") as file:
        file.write(log)