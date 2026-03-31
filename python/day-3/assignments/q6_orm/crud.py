from sqlalchemy.orm import Session
from models import User, Task


# ─────────────────────────────────────────────────────────
# CREATE — add new user
# ─────────────────────────────────────────────────────────
def create_user(
    session:  Session,
    username: str,
    email:    str,
    password: str,
) -> str:

    # check duplicate username
    existing = session.query(User)\
                      .filter_by(username=username)\
                      .first()
    if existing:
        raise ValueError(f"User '{username}' already exists")

    # create and save
    user = User(
        username = username,
        email    = email,
        password = password,
    )
    session.add(user)
    session.commit()                    # write to DB ✅
    session.refresh(user)               # load DB-generated id ✅

    return f"User '{user.username}' created with id {user.id}"


# ─────────────────────────────────────────────────────────
# READ — get all users
# ─────────────────────────────────────────────────────────
def get_all_users(session: Session) -> list[User]:

    users = session.query(User)\
                   .order_by(User.id)\
                   .all()

    return users                        # list of User objects ✅


# ─────────────────────────────────────────────────────────
# UPDATE — change user email
# ─────────────────────────────────────────────────────────
def update_user_email(
    session:   Session,
    username:  str,
    new_email: str,
) -> str:

    # find user
    user = session.query(User)\
                  .filter_by(username=username)\
                  .first()

    # raise if not found ✅
    if user is None:
        raise ValueError(f"User '{username}' not found")

    # update and commit
    user.email = new_email
    session.commit()                    # write change to DB ✅

    return f"Updated {user.username}'s email to {new_email}"


# ─────────────────────────────────────────────────────────
# DELETE — remove user
# ─────────────────────────────────────────────────────────
def delete_user(
    session:  Session,
    username: str,
) -> str:

    # find user
    user = session.query(User)\
                  .filter_by(username=username)\
                  .first()

    # raise if not found ✅
    if user is None:
        raise ValueError(f"User '{username}' not found")

    # delete and commit
    session.delete(user)
    session.commit()                    # write deletion to DB ✅

    return f"User '{username}' deleted successfully"