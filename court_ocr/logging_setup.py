"""Console + file logging for the automation."""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "court_ocr"

_LOGGERS: dict[str, logging.Logger] = {}


def _build(log_dir: Path | None, verbose: bool) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    stream.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.addHandler(stream)

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Rotate: an office PC runs this for years, and one permanent log per
        # run would otherwise fill the disk with stale run history.
        file_handler = RotatingFileHandler(
            log_dir / f"run_{stamp}.log",
            maxBytes=1_000_000,      # 1 MB per run log
            backupCount=5,           # keep the 5 newest, then roll over
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)
    return logger


def get_logger(log_dir: str | Path | None = "logs", verbose: bool = True) -> logging.Logger:
    """Return the shared logger, creating it on first use.

    Calling this again with a different ``log_dir`` rebuilds the handlers.
    """
    key = f"{log_dir}|{verbose}"
    cached = _LOGGERS.get(key)
    if cached is not None:
        return cached
    logger = _build(Path(log_dir) if log_dir else None, verbose)
    _LOGGERS[key] = logger
    return logger


def reset_logger() -> None:
    """Detach all handlers (used by the GUI between runs)."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    _LOGGERS.clear()


class StepTimer:
    """Context manager that logs how long a pipeline step took."""

    def __init__(self, logger: logging.Logger, label: str) -> None:
        self.logger = logger
        self.label = label
        self.seconds = 0.0

    def __enter__(self) -> "StepTimer":
        self.logger.info(">>> %s ...", self.label)
        self._start = datetime.now()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.seconds = (datetime.now() - self._start).total_seconds()
        if exc_type is None:
            self.logger.info("<<< %s done in %.1fs", self.label, self.seconds)
        else:
            self.logger.error("!!! %s FAILED after %.1fs: %s", self.label, self.seconds, exc)
        return False
