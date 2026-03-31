import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from database import get_db
from models.db_models import User
from models.schemas import AnalyticsSummary
from services.analytics_service import AnalyticsService
from utils.dependencies import get_current_admin

router = APIRouter(prefix="/analytics", tags=["Analytics"])
logger = logging.getLogger(__name__)


@router.get(
    "/summary",
    response_model=AnalyticsSummary,
    status_code=200,
    summary="Loan statistics dashboard (admin only)",
    description="Returns aggregated loan stats. Requires an **admin** JWT token.",
)
def get_analytics_summary(
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    service = AnalyticsService(db)
    return service.get_summary()
