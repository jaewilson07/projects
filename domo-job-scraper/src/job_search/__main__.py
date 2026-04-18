"""Entry point: python -m job_search"""

import logging
import sys

from .runner import run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    stream=sys.stdout,
)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
