#!/usr/bin/env python3
"""Run candidate-bound local OBS-001 Prometheus acceptance.

Alertmanager is intentionally absent. This command cannot verify, send, or
emulate the protected EXT-WEBHOOK-001 receipt.
"""

from observability_acceptance import main

if __name__ == "__main__":
    raise SystemExit(main())
