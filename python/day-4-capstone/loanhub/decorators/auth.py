import functools
import logging

from exceptions.custom_exceptions import ForbiddenError

logger = logging.getLogger(__name__)


def require_role(role: str):
    """
    Service-layer decorator that checks the current user's role.
    Usage: @require_role("admin")
    The decorated function must receive `current_user` as first positional arg.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Expect current_user to be passed as keyword arg
            current_user = kwargs.get("current_user") or (args[1] if len(args) > 1 else None)
            if current_user is None or current_user.role != role:
                raise ForbiddenError(
                    f"This action requires the '{role}' role."
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator
