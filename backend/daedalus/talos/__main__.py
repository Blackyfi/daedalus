"""Entry point for the Talos PTY runner process."""
from __future__ import annotations

import signal as stdlib_signal

import redis
import structlog

from daedalus.core.logging import configure_logging
from daedalus.core.settings import get_settings
from daedalus.talos.runner import TalosRunner

log = structlog.get_logger()


def main() -> None:
    configure_logging()
    settings = get_settings()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    runner = TalosRunner(r)

    def _handle_signal(signum: int, frame: object) -> None:
        # Keep this minimal — anything that raises here aborts the handler
        # before `request_shutdown()` runs, which is exactly what stopped the
        # drain from firing on SIGTERM until 2026-05-06 (we logged
        # `runner._running`, an attribute that doesn't exist).
        try:
            log.info(
                "talos.signal_received",
                signal=stdlib_signal.Signals(signum).name,
                in_flight=len(runner.contexts),
            )
        except Exception:
            pass
        runner.request_shutdown()

    stdlib_signal.signal(stdlib_signal.SIGTERM, _handle_signal)
    stdlib_signal.signal(stdlib_signal.SIGINT, _handle_signal)
    runner.run_loop()


if __name__ == "__main__":
    main()
