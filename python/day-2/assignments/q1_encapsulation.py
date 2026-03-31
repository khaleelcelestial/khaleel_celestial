#Q1. User Profile with Encapsulation
class User:
    def __init__(self, username, email, age):
        self._username = username        # private attribute
        self.set_email(email)            # validate on creation
        self.set_age(age)                # validate on creation

    # ─── EMAIL ───────────────────────────
    def get_email(self):
        return self._email

    def set_email(self, email):
        if "@" not in email or "." not in email:
            raise ValueError("Invalid email format")
        self._email = email

    # ─── AGE ─────────────────────────────
    def get_age(self):
        return self._age

    def set_age(self, age):
        if age < 18 or age > 120:
            raise ValueError("Age must be between 18 and 120")
        self._age = age

    # ─── USERNAME ─────────────────────────
    def get_username(self):
        return self._username


# ─── RUNNING THE CODE ─────────────────────
user = User("alice", "alice@mail.com", 25)

# Test invalid email
try:
    user.set_email("invalid")
except ValueError as e:
    print(f"ValueError: {e}")

# Test invalid age
try:
    user.set_age(150)
except ValueError as e:
    print(f"ValueError: {e}")

# Print original values (unchanged)
print(user.get_email())
print(user.get_age())