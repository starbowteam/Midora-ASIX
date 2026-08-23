from __future__ import annotations


import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(base_dir: Path) -> logging.Logger:
    """Configure concise console output and a bounded production log file."""
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("asixez_bot")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(logs_dir / "bot.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger
