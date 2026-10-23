from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


class LoggerSetup:
    """
    A utility class for setting up the application's root logger.

    This class provides a static method to configure the root logger with
    a DEBUG logging level and two handlers: a RotatingFileHandler (5 MB x 3
    backups) that writes log messages to a specified log file and a
    StreamHandler that outputs log messages to the console (INFO, or DEBUG
    when the environment variable ``ADV_TG_DEBUG=1`` is set).

    The log file is created in a dedicated ``logs`` directory located in
    the same directory as this module. If the directory does not exist,
    it will be created automatically.

    Attributes:
        None

    Methods:
        setup_logger(name): Configures the root logger with the specified name.
    """

    BASE_DIR: Path = Path(__file__).resolve().parents[3]
    LOG_DIR: Path = BASE_DIR / "logs"
    LOG_FILE: Path = LOG_DIR / "adv_tg.log"
    LOG_MAX_BYTES: int = 5 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 3
    DEBUG_ENV_VAR: str = "ADV_TG_DEBUG"
    _configured: bool = False

    @staticmethod
    def console_level() -> int:
        """Console level: DEBUG when ``ADV_TG_DEBUG`` is truthy, otherwise INFO."""
        value = os.environ.get(LoggerSetup.DEBUG_ENV_VAR, "").strip().lower()
        return logging.DEBUG if value in ("1", "true", "yes", "on") else logging.INFO

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

        # Defines log directory and creates it if it doesn't exist
        LoggerSetup.LOG_DIR.mkdir(parents=True, exist_ok=True)

        root_logger = logging.getLogger()
        has_unified_handler = any(
            isinstance(h, logging.FileHandler)
            and Path(getattr(h, "baseFilename", "")).resolve() == LoggerSetup.LOG_FILE.resolve()
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
                        LoggerSetup.LOG_FILE,
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
            LoggerSetup.LOG_FILE.name,
        )

        return logger
