"""Alpha Live Translator — application entry point (V0 refactor)."""

import sys

from alpha.constants import APP_CODENAME, APP_VERSION
from alpha.ui.main_window import AlphaApp


if __name__ == "__main__":
    try:
        print(f"Alpha V{APP_VERSION} ({APP_CODENAME})")
        app = AlphaApp()
        app.mainloop()
    except ValueError as exc:  # CHANGED: surface missing API key at startup (fix 10)
        print(exc)  # CHANGED: (fix 10)
        sys.exit(1)  # CHANGED: (fix 10)
