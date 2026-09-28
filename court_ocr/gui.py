"""Compatibility alias - the desktop app now lives in :mod:`court_ocr.app`.

Any older shortcut or script that runs ``python -m court_ocr.gui`` keeps
working and simply opens the current app.
"""

from .app import APP_NAME, App, main      # noqa: F401

__all__ = ["App", "main", "APP_NAME"]


if __name__ == "__main__":
    raise SystemExit(main())
