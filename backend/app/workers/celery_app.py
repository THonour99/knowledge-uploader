from __future__ import annotations

from celery import Celery

from app.core.config import get_settings
from app.core.logging import configure_logging

configure_logging()
settings = get_settings()

celery_app = Celery(
    "knowledge_uploader",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.task_default_queue = "default"
celery_app.conf.task_routes = {
    "document.*": {"queue": "document_queue"},
    "ai.*": {"queue": "ai_queue"},
    "ragflow.*": {"queue": "ragflow_queue"},
    "statistics.*": {"queue": "statistics_queue"},
    "notification.*": {"queue": "notification_queue"},
}

app = celery_app
