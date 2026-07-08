from __future__ import annotations

import asyncio
import logging

from .bot import run_bot
from .config import Settings


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    main()
