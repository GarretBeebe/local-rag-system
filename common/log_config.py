"""Logging setup shared by the API, the watcher and the maintenance scripts."""

import logging

# This project's loggers log at INFO; third-party libraries stay at WARNING (httpx, for one,
# logs every Qdrant request at INFO). "__main__" covers modules run with `python -m`.
_APP_LOGGERS = ("__main__", "api", "common", "indexer", "ingest", "web")


def configure_logging() -> None:
    """Configure process-wide logging. Call from entry points, never at import time."""
    logging.basicConfig(
        level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    for name in _APP_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
