#!/usr/bin/env python3
"""
run.py
Launcher for the Anki Drill flashcard app.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from anki_drill.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
