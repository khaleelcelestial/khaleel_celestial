#Q4. SRP — Separate Validation, Storage, and Notification 
import json
import os

# ─── CLASS 1: VALIDATOR ───────────────────────────────
# Only responsible for validating user data
class UserValidator:
    def validate(self, data):
        if "username" not in data or not data["username"]:
            raise ValueError("Username is required")

        if "email" not in data or "@" not in data["email"] or "." not in data["email"]:
            raise ValueError("Invalid email format")

        print("Validation passed")


# ─── CLASS 2: STORAGE ─────────────────────────────────
# Only responsible for reading and writing to JSON file
class UserStorage:
    def __init__(self, filename="users.json"):
        self.filename = filename

    def save(self, data):
        # read existing users
        users = self._read()

        # add new user
        users.append(data)

        # write back to file
        with open(self.filename, "w") as f:
            json.dump(users, f, indent=4)

        print(f"User saved to {self.filename}")

    def _read(self):
        # if file exists read it
        if os.path.exists(self.filename):
            with open(self.filename, "r") as f:
                return json.load(f)
        # if file doesn't exist return empty list
        return []


# ─── CLASS 3: NOTIFIER ────────────────────────────────
# Only responsible for sending notifications
class UserNotifier:
    def send_welcome(self, email):
        print(f"Welcome email sent to {email}")


# ─── ORCHESTRATOR FUNCTION ────────────────────────────
# Combines all three classes together
def register_user(data):
    validator = UserValidator()
    storage   = UserStorage()
    notifier  = UserNotifier()

    # Step 1 — validate
    validator.validate(data)

    # Step 2 — save
    storage.save(data)

    # Step 3 — notify
    notifier.send_welcome(data["email"])


# ─── RUNNING THE CODE ─────────────────────────────────
data = {"username": "alice", "email": "alice@mail.com"}
register_user(data)