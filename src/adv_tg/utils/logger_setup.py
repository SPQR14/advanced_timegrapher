from __future__ import annotations

import logging
from pathlib import Path


class LoggerSetup:
    """
    A utility class for setting up the application's root logger.

    This class provides a static method to configure the root logger with
    a DEBUG logging level and two handlers: a FileHandler that writes log
    messages to a specified log file and a StreamHandler that outputs log
    messages to the console.

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
    LOG_FILE: Path = LOG_DIR / "wea.log"
    _configured: bool = False

    @staticmethod
    def setup_logger(name: str = "wea") -> logging.Logger:
        """
        Configure the application's root logger.

        This function creates a dedicated ``logs`` directory in the same
        directory as this module if it does not already exist. It then
        configures the root logger with a DEBUG logging level and two handlers:

        - A FileHandler that writes log messages to ``logs/<name>.log``.
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
                format="%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s",
                datefmt="%d-%m-%Y %H:%M:%S",
                handlers=[
                    logging.FileHandler(LoggerSetup.LOG_FILE, encoding="utf-8"),
                    logging.StreamHandler(),
                ],
                force=True,
            )
            # Console stays at INFO (terminal output is slow); the file keeps full DEBUG detail.
            for handler in logging.getLogger().handlers:
                if isinstance(handler, logging.StreamHandler) and not isinstance(
                    handler, logging.FileHandler
                ):
                    handler.setLevel(logging.INFO)
            LoggerSetup._configured = True

        logger = logging.getLogger(name)

        logger.debug(
            "Logger '%s' initialized and attached to unified log '%s'",
            name,
            LoggerSetup.LOG_FILE.name,
        )

        return logger
