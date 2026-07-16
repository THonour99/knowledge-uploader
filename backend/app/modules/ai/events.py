from __future__ import annotations

AI_TEXT_EXTRACTED = "ai.text.extracted"
AI_FILE_ANALYZED = "ai.file.analyzed"
AI_SENSITIVE_DETECTED = "ai.sensitive.detected"
AI_CONFIG_CHANGED = "ai.config.changed"

# AI 模块通过 outbox 发布审核提交事件, 不直接依赖 review service/repository。
REVIEW_FILE_SUBMITTED = "review.file.submitted"
