from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.rabbitmq_topology import TASK_EXCHANGE, task_queues

configure_logging()
settings = get_settings()

celery_app = Celery(
    "knowledge_uploader",
    broker=settings.celery_broker_url,
)
celery_app.conf.task_default_queue = "document_queue"
celery_app.conf.task_default_exchange = TASK_EXCHANGE.name
celery_app.conf.task_default_exchange_type = "direct"
celery_app.conf.task_default_routing_key = "document_queue"
celery_app.conf.task_create_missing_queues = False
celery_app.conf.task_queues = task_queues()
celery_app.conf.task_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.task_ignore_result = True
celery_app.conf.task_routes = {
    "document.*": {"queue": "document_queue", "routing_key": "document_queue"},
    "ai.*": {"queue": "ai_queue", "routing_key": "ai_queue"},
    "ragflow.*": {"queue": "ragflow_queue", "routing_key": "ragflow_queue"},
    "notification.*": {
        "queue": "notification_queue",
        "routing_key": "notification_queue",
    },
}
celery_app.conf.task_publish_retry = True
celery_app.conf.task_publish_retry_policy = {
    "max_retries": 3,
    "interval_start": 0,
    "interval_step": 0.5,
    "interval_max": 2,
}
celery_app.conf.broker_transport_options = {"confirm_publish": True}
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
