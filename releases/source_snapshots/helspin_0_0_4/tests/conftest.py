"""Test configuration.

Forces the offscreen Qt platform so the suite runs without a display (CI, this
sandbox). Must be set before any PySide6 import happens anywhere in the
process, hence doing it here rather than in an individual test module.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
