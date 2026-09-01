"""Standard-library logging configuration."""

import logging
from logging.config import dictConfig

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure concise console logging without serializing settings or secrets."""

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": (
                        f"%(asctime)s %(levelname)s %(name)s "
                        f"[env={settings.app_env}] %(message)s"
                    ),
                    "datefmt": "%Y-%m-%dT%H:%M:%S%z",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "stream": "ext://sys.stdout",
                }
            },
            "root": {"handlers": ["console"], "level": settings.log_level},
        }
    )
    logging.getLogger(__name__).info("Logging initialized for %s", settings.app_name)

