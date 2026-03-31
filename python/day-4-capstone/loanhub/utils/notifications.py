import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime

logger = logging.getLogger("notifications")


# ─── OCP: Notification Strategy (Open for extension, closed for modification) ─

class NotificationStrategy(ABC):
    @abstractmethod
    async def send(self, message: str) -> None:
        raise NotImplementedError


class ConsoleNotification(NotificationStrategy):
    async def send(self, message: str) -> None:
        print(f"[CONSOLE NOTIFICATION] {message}")


class LogFileNotification(NotificationStrategy):
    async def send(self, message: str) -> None:
        logger.info(message)


# Adding EmailNotification later does NOT change existing code (OCP)
class EmailNotification(NotificationStrategy):
    async def send(self, message: str) -> None:
        # Simulated — no real SMTP call
        await asyncio.sleep(0.05)
        print(f"[EMAIL NOTIFICATION] {message}")


class SMSNotification(NotificationStrategy):
    async def send(self, message: str) -> None:
        await asyncio.sleep(0.05)
        print(f"[SMS NOTIFICATION] {message}")


class PushNotification(NotificationStrategy):
    async def send(self, message: str) -> None:
        await asyncio.sleep(0.05)
        print(f"[PUSH NOTIFICATION] {message}")


# ─── In-memory review counter ─────────────────────────────────────────────────

_review_counter: dict = {"count": 0, "date": datetime.utcnow().date()}


def increment_review_counter():
    today = datetime.utcnow().date()
    if _review_counter["date"] != today:
        _review_counter["count"] = 0
        _review_counter["date"] = today
    _review_counter["count"] += 1
    return _review_counter["count"]


def get_review_count() -> int:
    return _review_counter["count"]


# ─── Async multi-channel notification ────────────────────────────────────────

async def notify_all_channels(message: str) -> None:
    """Concurrently send to email, SMS, and push using asyncio.gather()."""
    strategies: list[NotificationStrategy] = [
        EmailNotification(),
        SMSNotification(),
        PushNotification(),
        LogFileNotification(),
    ]
    await asyncio.gather(*[s.send(message) for s in strategies])


# ─── Background task functions ────────────────────────────────────────────────

def background_loan_reviewed(loan_id: int, username: str, status: str):
    """Background task triggered on loan review (PATCH /admin/loans/{id}/review)."""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"[{ts}] Loan #{loan_id} for user '{username}' has been {status} "
        f"— notification sent"
    )
    logger.info(msg)

    # Run async notifications in a new event loop (background task context)
    asyncio.run(notify_all_channels(msg))

    count = increment_review_counter()
    logger.info(f"[COUNTER] Total reviews processed today: {count}")


def background_loan_applied(loan_id: int, username: str, purpose: str, amount: int):
    """Background task triggered on new loan application (POST /loans)."""
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"[{ts}] New loan application #{loan_id} by '{username}' "
        f"for {purpose} — ₹{amount}"
    )
    logger.info(msg)
    print(f"[NOTIFICATION] {msg}")
