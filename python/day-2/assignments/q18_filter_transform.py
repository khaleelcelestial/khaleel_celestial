users = [
    {"username": "alice", "email": "a@b.com", "age": 25, "active": True},
    {"username": "bob",   "email": "b@b.com", "age": 17, "active": True},
    {"username": "carol", "email": "c@b.com", "age": 30, "active": False},
    {"username": "dave",  "email": "d@b.com", "age": 22, "active": True},
]

# ─── SINGLE DICTIONARY COMPREHENSION ─────────────────
result = {
    user["username"]: user["email"]      # key: value
    for user in users                    # loop
    if user["active"] and user["age"] >= 18  # both conditions
}

print(result)