"""Alpha Live Translator — application entry point (V2 event architecture)."""

import sys

from alpha.constants import APP_CODENAME, APP_VERSION
from alpha.ui.main_window import AlphaApp
from alpha.utils.logging_utils import setup_logging


if __name__ == "__main__":
    try:
        setup_logging()
        print(f"Alpha V{APP_VERSION} ({APP_CODENAME})")
        app = AlphaApp()
        app.mainloop()
    except Exception as exc:
        print(f"Fatal error: {exc}")
        sys.exit(1)
