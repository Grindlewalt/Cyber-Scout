#!/usr/bin/env python3
"""Cyber-Scout Client: serves the Cyber-Scout web app at http://127.0.0.1:7600/ with an offline copy of a server's
data (docs/SYNC.md part 2). Pure Python standard library, Python >= 3.9.

    python3 cyberscout_client.py [--server https://scout.example.dev] [--port 7600] [--data DIR] [--no-browser]
"""
import os
import sys

if sys.version_info < (3, 9):
    sys.exit("Cyber-Scout Client needs Python 3.9 or newer")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from csclient.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
