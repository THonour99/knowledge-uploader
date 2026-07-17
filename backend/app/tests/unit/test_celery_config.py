from __future__ import annotations

from app.workers.celery_app import celery_app


def test_global_task_ack_policy_remains_early_ack() -> None:
    assert celery_app.conf.task_acks_late is False
    assert celery_app.conf.task_acks_on_failure_or_timeout is True
    assert not celery_app.conf.task_reject_on_worker_lost


def test_celery_does_not_configure_or_store_unconsumed_task_results() -> None:
    assert celery_app.conf.result_backend is None
    assert celery_app.conf.task_ignore_result is True
