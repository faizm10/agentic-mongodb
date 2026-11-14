"""Command line: ``python -m agents live-check messaging [--url URL] [--timeout SECONDS] [--fixture]``."""

import sys

from .live_check import main as live_check_main

COMMANDS = {"live-check": live_check_main}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in COMMANDS:
        print(f"usage: python -m agents {{{','.join(COMMANDS)}}} ...", file=sys.stderr)
        return 2
    return COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
