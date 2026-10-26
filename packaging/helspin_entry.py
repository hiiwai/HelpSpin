"""Entry script for the frozen (PyInstaller) app.

helspin/__main__.py cannot be the entry script itself: PyInstaller runs the
entry script as a top-level ``__main__`` module, outside its package, so its
relative imports (``from . import __version__``) fail and the bundled app
died on launch with "attempted relative import with no known parent
package". Importing it as ``helspin.__main__`` keeps it inside the package.
"""

import sys

from helspin.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
