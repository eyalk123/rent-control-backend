"""Root logging configuration.

Without this the app's own log records go nowhere useful. Uvicorn configures only its
own ``uvicorn*`` loggers and sets no root config, so the root logger is left with zero
handlers: ``logger.info(...)`` is discarded outright, and ``logger.warning(...)`` falls
back to ``logging.lastResort``, which writes the bare message to stderr with no
timestamp, level or logger name — nothing to filter or correlate on in the Railway
stream.

Adding a root handler is safe alongside uvicorn: its ``uvicorn`` and ``uvicorn.access``
loggers are configured with ``propagate: False``, so access lines are not duplicated
through the root handler installed here.

**Never log tenant data.** Owner UIDs and row ids only — never renter names, addresses,
phone numbers or lease text. Log records at INFO and above are also attached to Sentry
error events as breadcrumbs (see ``app/monitoring.py``), so anything logged here can
leave the box when something else fails.
"""

import logging
import logging.config

from app.config import settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


def configure_logging() -> None:
    """Install a formatted stderr handler on the root logger. Idempotent."""
    logging.config.dictConfig(
        {
            "version": 1,
            # Uvicorn has already configured its own loggers by the time the app is
            # imported. Leave them running.
            "disable_existing_loggers": False,
            "formatters": {"standard": {"format": _FORMAT}},
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "standard",
                }
            },
            "root": {"handlers": ["stderr"], "level": settings.LOG_LEVEL.upper()},
        }
    )
