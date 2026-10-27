from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR_ENV_VAR = "ADV_TG_LOG_DIR"


def default_log_dir() -> Path:
    """Folder of the log files (never inside the installed package, which may be read-only).

    ``$ADV_TG_LOG_DIR`` if set (e.g. ``ADV_TG_LOG_DIR=logs`` keeps them in the repository while
    developing); otherwise ``~/Library/Logs/adv_tg`` on macOS, ``%LOCALAPPDATA%\\adv_tg\\logs``
    on Windows and ``$XDG_STATE_HOME/adv_tg/logs`` (default ``~/.local/state/adv_tg/logs``)
    elsewhere. Child processes inherit the environment, so they log to the same folder.
    """
    env = os.environ.get(LOG_DIR_ENV_VAR, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Logs" / "adv_tg"
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / "adv_tg" / "logs"
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else home / ".local" / "state") / "adv_tg" / "logs"


class LoggerSetup:
    """
    A utility class for setting up the application's root logger.

    This class provides a static method to configure the root logger with
    a DEBUG logging level and two handlers: a RotatingFileHandler (5 MB x 3
    backups) that writes log messages to a specified log file and a
    StreamHandler that outputs log messages to the console (INFO, or DEBUG
    when the environment variable ``ADV_TG_DEBUG=1`` is set).

    The log file is created in the user's log folder (``default_log_dir``); it is created
    automatically if it does not exist.

    Attributes:
        None

    Methods:
        setup_logger(name): Configures the root logger with the specified name.
    """

    LOG_DIR: Path = default_log_dir()
    LOG_FILE: Path = LOG_DIR / "adv_tg.log"
    LOG_MAX_BYTES: int = 5 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 3
    DEBUG_ENV_VAR: str = "ADV_TG_DEBUG"
    # Name of the log file (without ``.log``). The isolated capture process sets it to
    # ``adv_tg_capture`` so it never shares a file/handler with the GUI process.
    LOG_NAME_ENV_VAR: str = "ADV_TG_LOG_NAME"
    _configured: bool = False

    @staticmethod
    def console_level() -> int:
        """Console level: DEBUG when ``ADV_TG_DEBUG`` is truthy, otherwise INFO."""
        value = os.environ.get(LoggerSetup.DEBUG_ENV_VAR, "").strip().lower()
        return logging.DEBUG if value in ("1", "true", "yes", "on") else logging.INFO

    @staticmethod
    def log_file() -> Path:
        """Log file of this process: ``logs/<ADV_TG_LOG_NAME or adv_tg>.log``."""
        raw = os.environ.get(LoggerSetup.LOG_NAME_ENV_VAR, "").strip()
        safe = "".join(c for c in raw if c.isalnum() or c in "-_")
        if not safe:
            return LoggerSetup.LOG_FILE
        return LoggerSetup.LOG_DIR / f"{safe}.log"

    @staticmethod
    def setup_logger(name: str = "adv_tg") -> logging.Logger:
        """
        Configure the application's root logger.

        This function creates a dedicated ``logs`` directory in the same
        directory as this module if it does not already exist. It then
        configures the root logger with a DEBUG logging level and two handlers:

        - A RotatingFileHandler that writes log messages to ``logs/adv_tg.log``.
        - A StreamHandler that outputs log messages to the console.

        Any existing logging configuration is replaced to ensure that the
        specified handlers and logging level are applied.

        Args:
            name (str): Name used to generate the log file. The resulting log
                file will be created as ``logs/<name>.log``.

        Returns:
            None
        """

        # Defines log directory and creates it if it doesn't exist with owner-only permissions (0700)
        LoggerSetup.LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        if sys.platform != "win32" and LoggerSetup.LOG_DIR.is_dir():
            try:
                LoggerSetup.LOG_DIR.chmod(0o700)
            except OSError:
                pass

        log_file = LoggerSetup.log_file()
        root_logger = logging.getLogger()
        has_unified_handler = any(
            isinstance(h, logging.FileHandler)
            and Path(getattr(h, "baseFilename", "")).resolve() == log_file.resolve()
            for h in root_logger.handlers
        )

        if not LoggerSetup._configured or not has_unified_handler:
            logging.basicConfig(
                level=logging.DEBUG,
                format=(
                    "%(asctime)s.%(msecs)03d - %(threadName)s - %(name)s - %(levelname)s"
                    " - %(message)s"
                ),
                datefmt="%d-%m-%Y %H:%M:%S",
                handlers=[
                    RotatingFileHandler(
                        log_file,
                        maxBytes=LoggerSetup.LOG_MAX_BYTES,
                        backupCount=LoggerSetup.LOG_BACKUP_COUNT,
                        encoding="utf-8",
                    ),
                    logging.StreamHandler(),
                ],
                force=True,
            )
            # Console stays at INFO (terminal output is slow) unless ADV_TG_DEBUG=1;
            # the file keeps full DEBUG detail.
            for handler in logging.getLogger().handlers:
                if isinstance(handler, logging.StreamHandler) and not isinstance(
                    handler, logging.FileHandler
                ):
                    handler.setLevel(LoggerSetup.console_level())
            LoggerSetup._configured = True

        logger = logging.getLogger(name)

        logger.debug(
            "Logger '%s' initialized and attached to unified log '%s'",
            name,
            log_file.name,
        )

        return logger
