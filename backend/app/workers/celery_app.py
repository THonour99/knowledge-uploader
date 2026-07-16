from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

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
    "notification.*": {"queue": "notification_queue"},
}
celery_app.conf.imports = (
    "app.modules.ai.tasks",
    "app.modules.auth.tasks",
    "app.modules.document.tasks",
    "app.modules.notification.tasks",
    "app.modules.ragflow.tasks",
)
celery_app.conf.beat_schedule = {
    "document-expiry-scan-daily": {
        "task": "document.scan_expiring_files",
        "schedule": crontab(minute=0, hour=1),
        "args": (
            30,
            500,
        ),
    },
}

app = celery_app
