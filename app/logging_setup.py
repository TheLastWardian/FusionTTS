import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app import paths

# logs/<categoria>/<archivo>.log — por categoria, rotacion por tamano,
# se conservan los ultimos 10 archivos por categoria (actual + 9 backups).
def _log_dir() -> Path:
    return paths.BASE_DIR / "logs"


MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 9
_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# logger -> carpeta. Los hijos propagan al logger padre de la lista.
_CATEGORIES = {
    "app.services.tts": "tts",
    "app.routers.tts": "tts",
    "app.routers.chat": "chat",
    "app.services.persona_router": "chat",
}


def _ftts_handler(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler._ftts = True  # noqa: SLF001 - marca para limpieza idempotente
    return handler


def _remove_ftts_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, "_ftts", False):
            logger.removeHandler(handler)


def setup_logging() -> None:
    """Idempotente: se puede llamar varias veces (tests, re-arranques)."""
    log_dir = _log_dir()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    _remove_ftts_handlers(root)
    root.addHandler(_ftts_handler(log_dir / "app" / "app.log"))
    for name, category in _CATEGORIES.items():
        logger = logging.getLogger(name)
        _remove_ftts_handlers(logger)
        logger.addHandler(_ftts_handler(log_dir / category / f"{category}.log"))
        logger.propagate = False  # no duplicar en app.log
