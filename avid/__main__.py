"""Module entry point: runs the command line interface for ``python -m avid``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
