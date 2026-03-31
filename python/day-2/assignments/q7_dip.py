import json
import os
from abc import ABC, abstractmethod

class UserRepository(ABC):
    @abstractmethod
    def save(self, user):
        pass

    @abstractmethod
    def find(self, username):
        pass

class InMemoryUserRepository(UserRepository):
    def __init__(self):
        self.users = {}

    def save(self, user):
        self.users[user["username"]] = user
        print(f"User saved in memory")

    def find(self, username):
        return self.users.get(username, None)

class JSONUserRepository(UserRepository):
    def __init__(self, filename="users.json"):
        self.filename = filename

    def save(self, user):
        users = self._read()
        users[user["username"]] = user
        with open(self.filename, "w") as f:
            json.dump(users, f, indent=4)
        print(f"User saved to {self.filename}")

    def find(self, username):
        users = self._read()
        return users.get(username, None)

    def _read(self):
        if os.path.exists(self.filename):
            with open(self.filename, "r") as f:
                data = json.load(f)
                if isinstance(data, list):   # ← handles old format ✅
                    return {}
                return data
        return {}                            # fresh start ✅

class UserService:
    def __init__(self, repository: UserRepository):
        self.repository = repository

    def register(self, user):
        self.repository.save(user)

    def get_user(self, username):
        return self.repository.find(username)

# ─── RUNNING THE CODE ─────────────────────────────────
print("─── IN MEMORY ───")
repo    = InMemoryUserRepository()
service = UserService(repo)
service.register({"username": "alice", "email": "a@b.com"})
print(service.get_user("alice"))

print("\n─── JSON FILE ───")
repo    = JSONUserRepository()
service = UserService(repo)
service.register({"username": "bob", "email": "b@c.com"})
print(service.get_user("bob"))