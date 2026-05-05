"""Entry point for the Hermes scheduler process."""
from __future__ import annotations

import asyncio

from daedalus.core.logging import configure_logging
from daedalus.hermes.scheduler import HermesScheduler


def main() -> None:
    configure_logging()
    asyncio.run(HermesScheduler().run())


if __name__ == "__main__":
    main()
