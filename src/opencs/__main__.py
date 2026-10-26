"""``python -m opencs [--config PATH] [--port N] [--host H]``."""

import sys

from opencs.app import main

try:
    code = main()
except KeyboardInterrupt:
    # Ctrl-C before the server takes SIGINT over (or after it let go): nothing is open to save.
    code = 130
sys.exit(code)
