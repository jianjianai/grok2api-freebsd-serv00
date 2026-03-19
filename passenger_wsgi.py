"""Passenger entrypoint for WSGI hosts such as Serv00 Python sites."""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from wsgi import application as application  # noqa: E402,F401
