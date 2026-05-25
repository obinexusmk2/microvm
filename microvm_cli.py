#!/usr/bin/env python3
"""Compatibility entrypoint for the packaged MicroVM CLI."""

from microvm.cli import *  # noqa: F401,F403
from microvm.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
