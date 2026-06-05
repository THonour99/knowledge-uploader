from __future__ import annotations

from importlib import import_module

import_module("app.core.outbox")
import_module("app.modules.audit.models")
import_module("app.modules.auth.models")
import_module("app.modules.review.models")
import_module("app.modules.document.models")
import_module("app.modules.user.models")
