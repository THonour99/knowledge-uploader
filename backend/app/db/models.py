from __future__ import annotations

from importlib import import_module

import_module("app.core.outbox")
import_module("app.modules.audit.models")
import_module("app.modules.ai.models")
import_module("app.modules.auth.models")
import_module("app.modules.config.models")
import_module("app.modules.department.models")
import_module("app.modules.document.models")
import_module("app.modules.notification.models")
import_module("app.modules.ragflow.models")
import_module("app.modules.review.models")
import_module("app.modules.user.models")
