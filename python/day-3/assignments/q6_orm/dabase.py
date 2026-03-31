from database import SessionLocal
from crud import create_user, get_all_users, update_user_email, delete_user

session = SessionLocal()

# ── Edge Case 1: duplicate username ───────────────────────
try:
    create_user(session, "charlie", "charlie@mail.com", "pass1234")
    create_user(session, "charlie", "other@mail.com",   "pass5678")
except ValueError as e:
    print(f"Caught: {e}")
# Caught: User 'charlie' already exists


# ── Edge Case 2: update non-existent user ─────────────────
try:
    update_user_email(session, "ghost", "ghost@mail.com")
except ValueError as e:
    print(f"Caught: {e}")
# Caught: User 'ghost' not found


# ── Edge Case 3: delete non-existent user ─────────────────
try:
    delete_user(session, "nobody")
except ValueError as e:
    print(f"Caught: {e}")
# Caught: User 'nobody' not found


# ── Edge Case 4: get_all when table is empty ──────────────
users = get_all_users(session)
print(f"Total users: {len(users)}")
# Total users: 0  (empty list, never None)


session.close()