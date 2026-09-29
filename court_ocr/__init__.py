"""Court filing PDF automation - local, offline, zero-cost pipeline."""

import sys

__version__ = "1.0.0"

# Checked early and loudly: the code uses 3.10+ syntax (PEP 604 unions at
# runtime, match-free but `X | Y` annotations evaluated by dataclasses/pydantic
# style helpers).  Failing here beats an obscure SyntaxError on a staff PC.
MIN_PYTHON = (3, 10)
if sys.version_info < MIN_PYTHON:               # pragma: no cover - guard only
    raise RuntimeError(
        f"court_ocr needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer, "
        f"but this is Python {sys.version_info.major}.{sys.version_info.minor}. "
        "Install a newer Python from https://www.python.org/downloads/ and re-run "
        "SETUP.ps1."
    )

__all__ = ["__version__"]
