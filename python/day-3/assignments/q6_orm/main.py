from database import SessionLocal
from crud import create_user, get_all_users, update_user_email, delete_user

session = SessionLocal()

try:
    # ── CREATE ────────────────────────────────────────────
    print(create_user(session, "charlie", "charlie@mail.com", "pass1234"))

    # ── READ ──────────────────────────────────────────────
    users = get_all_users(session)
    for u in users:
        print(u)

    # ── UPDATE ────────────────────────────────────────────
    print(update_user_email(session, "charlie", "charlie.new@mail.com"))

    # ── DELETE ────────────────────────────────────────────
    print(delete_user(session, "charlie"))

except ValueError as e:
    print(f"Error: {e}")

finally:
    session.close()