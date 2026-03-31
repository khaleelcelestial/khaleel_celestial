import json
import os
from logger import log_message

FILE = "users.json"

def load_users():
    try:
        if not os.path.exists(FILE):
            return {"users": []}

        with open(FILE, "r") as file:
            return json.load(file)

    except json.JSONDecodeError:
        log_message("ERROR", "Corrupted JSON file")
        return {"users": []}


def save_users(data):
    try:
        with open(FILE, "w") as file:
            json.dump(data, file, indent=4)

    except Exception as e:
        log_message("ERROR", f"Error saving users: {str(e)}")