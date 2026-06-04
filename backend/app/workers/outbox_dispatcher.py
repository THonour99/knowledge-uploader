from __future__ import annotations

import signal
import time

_running = True


def _stop(_signum: int, _frame: object) -> None:
    global _running
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while _running:
        time.sleep(5)


if __name__ == "__main__":
    main()
