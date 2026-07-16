"""Prove every application Celery task resolves to a declared DLX-backed queue."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES_ROOT = ROOT / "backend/app/modules"
CELERY_APP_PATH = ROOT / "backend/app/workers/celery_app.py"
TOPOLOGY_PATH = ROOT / "backend/app/workers/rabbitmq_topology.py"
REPLAY_PATH = ROOT / "backend/app/workers/rabbitmq_replay.py"

EXPECTED_QUEUE_BY_PREFIX = {
    "auth": "notification_queue",
    "document": "document_queue",
    "ai": "ai_queue",
    "ragflow": "ragflow_queue",
    "notification": "notification_queue",
}
DECLARED_QUEUES = frozenset(EXPECTED_QUEUE_BY_PREFIX.values())
EXPECTED_SAFE_REPLAY_TASKS = {
    "ragflow.create_upload_task": "ragflow_queue",
    "ragflow.create_delete_task": "ragflow_queue",
}
EXPECTED_TASK_DELIVERY_CONTRACTS: dict[str, dict[str, object]] = {
    "ai.analyze_file": {
        "acks_late": True,
        "acks_on_failure_or_timeout": False,
        "reject_on_worker_lost": True,
        "max_retries": 10,
    },
    "ragflow.create_upload_task": {
        "acks_late": True,
        "acks_on_failure_or_timeout": False,
        "reject_on_worker_lost": True,
        "max_retries": 3,
    },
    "ragflow.create_delete_task": {
        "acks_late": True,
        "acks_on_failure_or_timeout": False,
        "reject_on_worker_lost": True,
        "max_retries": 3,
    },
    "ragflow.upload": {
        "acks_late": True,
        "acks_on_failure_or_timeout": False,
        "reject_on_worker_lost": True,
        "max_retries": 40,
    },
    "ragflow.delete": {
        "acks_late": True,
        "acks_on_failure_or_timeout": False,
        "reject_on_worker_lost": True,
        "max_retries": 40,
    },
}


def _constant_keyword(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            return value if isinstance(value, str) else None
    return None


def _task_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.Call | None:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        if decorator.func.attr == "task":
            return decorator
    return None


def _registered_tasks() -> dict[str, str | None]:
    tasks: dict[str, str | None] = {}
    for path in MODULES_ROOT.rglob("tasks.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            decorator = _task_decorator(node)
            if decorator is None:
                continue
            task_name = _constant_keyword(decorator, "name")
            if task_name is None:
                raise RuntimeError(f"Celery task must have a literal name: {path}:{node.lineno}")
            tasks[task_name] = _constant_keyword(decorator, "queue")
    return tasks


def _task_delivery_contracts() -> dict[str, dict[str, object]]:
    contracts: dict[str, dict[str, object]] = {}
    for path in MODULES_ROOT.rglob("tasks.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants: dict[str, object] = {}
        for statement in tree.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
            ):
                try:
                    constants[statement.targets[0].id] = ast.literal_eval(statement.value)
                except (ValueError, TypeError):
                    pass
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            decorator = _task_decorator(node)
            if decorator is None:
                continue
            task_name = _constant_keyword(decorator, "name")
            if task_name not in EXPECTED_TASK_DELIVERY_CONTRACTS:
                continue
            values: dict[str, object] = {}
            for keyword in decorator.keywords:
                if keyword.arg not in EXPECTED_TASK_DELIVERY_CONTRACTS[task_name]:
                    continue
                if isinstance(keyword.value, ast.Name):
                    values[keyword.arg] = constants.get(keyword.value.id)
                else:
                    try:
                        values[keyword.arg] = ast.literal_eval(keyword.value)
                    except (ValueError, TypeError):
                        values[keyword.arg] = None
            contracts[task_name] = values
    return contracts


def _send_task_queues() -> list[tuple[Path, int, str | None]]:
    calls: list[tuple[Path, int, str | None]] = []
    for path in (ROOT / "backend/app").rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "send_task":
                queue = _constant_keyword(node, "queue")
                if path == REPLAY_PATH and _is_controlled_replay_send(node):
                    queue = "__controlled_replay__"
                calls.append((path, node.lineno, queue))
    return calls


def _is_controlled_replay_send(call: ast.Call) -> bool:
    queue_keyword = next((item for item in call.keywords if item.arg == "queue"), None)
    routing_keyword = next((item for item in call.keywords if item.arg == "routing_key"), None)
    delivery_mode_keyword = next(
        (item for item in call.keywords if item.arg == "delivery_mode"),
        None,
    )
    return (
        queue_keyword is not None
        and isinstance(queue_keyword.value, ast.Name)
        and queue_keyword.value.id == "queue_name"
        and routing_keyword is not None
        and isinstance(routing_keyword.value, ast.Name)
        and routing_keyword.value.id == "queue_name"
        and delivery_mode_keyword is not None
        and isinstance(delivery_mode_keyword.value, ast.Constant)
        and delivery_mode_keyword.value.value == 2
    )


def _literal_dict_assignment(path: Path, assignment_name: str) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != assignment_name or not isinstance(node.value, ast.Dict):
            continue
        result: dict[str, str] = {}
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if not (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                raise RuntimeError(f"{assignment_name} must be a literal string mapping")
            result[key.value] = value.value
        return result
    raise RuntimeError(f"could not find {assignment_name} in {path}")


def _celery_config_assignments() -> dict[str, object]:
    tree = ast.parse(CELERY_APP_PATH.read_text(encoding="utf-8"), filename=str(CELERY_APP_PATH))
    assignments: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Attribute)
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "celery_app"
            and target.value.attr == "conf"
        ):
            continue
        try:
            assignments[target.attr] = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
    return assignments


def main() -> int:
    errors: list[str] = []
    tasks = _registered_tasks()
    for task_name, explicit_queue in sorted(tasks.items()):
        prefix = task_name.partition(".")[0]
        expected_queue = EXPECTED_QUEUE_BY_PREFIX.get(prefix)
        if expected_queue is None:
            errors.append(f"task has no approved route prefix: {task_name}")
            continue
        actual_queue = explicit_queue or expected_queue
        if actual_queue != expected_queue:
            errors.append(
                f"task {task_name} resolves to {actual_queue}, expected {expected_queue}"
            )

    for path, line, queue in _send_task_queues():
        if queue not in DECLARED_QUEUES and queue != "__controlled_replay__":
            errors.append(
                f"send_task queue must be a declared literal: {path.relative_to(ROOT)}:{line}"
            )

    task_delivery_contracts = _task_delivery_contracts()
    for task_name, expected_delivery in EXPECTED_TASK_DELIVERY_CONTRACTS.items():
        if task_delivery_contracts.get(task_name) != expected_delivery:
            errors.append(
                f"task {task_name} delivery policy does not match its reviewed queue/DLQ semantics"
            )

    celery_config = _celery_config_assignments()
    required_celery_contracts: dict[str, object] = {
        "task_default_queue": "document_queue",
        "task_create_missing_queues": False,
        "task_serializer": "json",
        "result_serializer": "json",
        "accept_content": ["json"],
        "task_publish_retry": True,
    }
    for key, expected_value in required_celery_contracts.items():
        if celery_config.get(key) != expected_value:
            errors.append(f"Celery delivery contract missing or invalid: {key}")
    unsafe_global_ack_overrides = {
        "task_acks_late": True,
        "task_acks_on_failure_or_timeout": False,
        "task_reject_on_worker_lost": True,
    }
    for key, unsafe_value in unsafe_global_ack_overrides.items():
        if celery_config.get(key) == unsafe_value:
            errors.append(
                "Celery global ack policy must remain early-ack until every task is idempotent: "
                f"{key}"
            )
    transport_options = celery_config.get("broker_transport_options")
    confirms_enabled = (
        isinstance(transport_options, dict)
        and transport_options.get("confirm_publish") is True
    )
    if not confirms_enabled:
        errors.append("Celery broker publisher confirms are not enabled")
    retry_policy = celery_config.get("task_publish_retry_policy")
    if not isinstance(retry_policy, dict) or int(retry_policy.get("max_retries", 0)) < 3:
        errors.append("Celery publish retry policy is not bounded to at least three retries")

    safe_replay_tasks = _literal_dict_assignment(REPLAY_PATH, "SAFE_REPLAY_TASK_QUEUES")
    if safe_replay_tasks != EXPECTED_SAFE_REPLAY_TASKS:
        errors.append("RabbitMQ replay allowlist is not the reviewed domain reconstruction set")

    topology_text = TOPOLOGY_PATH.read_text(encoding="utf-8")
    for queue in DECLARED_QUEUES:
        if queue not in topology_text:
            errors.append(f"RabbitMQ topology missing queue: {queue}")
    if "x-dead-letter-exchange" not in topology_text:
        errors.append("RabbitMQ topology has no dead-letter exchange argument")

    if errors:
        sys.stderr.write("\n".join(f"ERROR: {error}" for error in errors) + "\n")
        return 1
    sys.stdout.write(
        f"Celery queue contract ok: {len(tasks)} tasks, "
        f"{len(DECLARED_QUEUES)} DLX-backed queues\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
