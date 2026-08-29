import logging
from datetime import datetime
from pathlib import Path

from app import paths

# logs/<categoria>/<prefix>-<YYYYmmdd-HHMMSS>.log — un archivo por sesion
# (un proceso de app = 1 sesion; cada spawn del TTS server = 1 sesion suya).
# Al abrir una sesion nueva se limpian las categorias y se conservan los
# ultimos 10 archivos por carpeta.

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
KEEP_LATEST = 10

# logger -> carpeta. Los hijos propagan al logger padre de la lista.
_CATEGORIES = {
    "app.services.tts": "tts",
    "app.routers.tts": "tts",
    "app.routers.chat": "chat",
    "app.services.persona_router": "chat",
}

_session_tag: str | None = None
_session_paths: dict[str, Path] = {}


def _log_dir() -> Path:
    return paths.BASE_DIR / "logs"


def _session() -> str:
    """Tag unico por proceso: el primer arranque lo fija; los re-llamados de
    setup_logging() dentro del mismo proceso reusan el mismo archivo."""
    global _session_tag
    if _session_tag is None:
        _session_tag = datetime.now().strftime("%Y%m%d-%H%M%S")
    return _session_tag


def unique_log_path(directory: Path, prefix: str, fresh: bool = False) -> Path:
    """Ruta unica con tag de sesion. fresh=True usa el instante actual
    (para procesos hijos con vida propia, p. ej. cada spawn del TTS server);
    si no, usa el tag del proceso actual. Colision (mismo segundo) -> -2, -3."""
    tag = datetime.now().strftime("%Y%m%d-%H%M%S") if fresh else _session()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{prefix}-{tag}.log"
    n = 2
    while path.exists():
        path = directory / f"{prefix}-{tag}-{n}.log"
        n += 1
    return path


def session_log_path(directory: Path, prefix: str) -> Path:
    """Archivo de sesion cacheado por proceso: re-llamadas reusan la misma
    ruta (setup_logging es idempotente dentro del proceso)."""
    if prefix in _session_paths:
        return _session_paths[prefix]
    path = unique_log_path(directory, prefix)
    _session_paths[prefix] = path
    return path


def keep_latest(directory: Path, keep: int = KEEP_LATEST) -> None:
    """Borra los archivos mas viejos de la carpeta, conservando los ultimos
    `keep` (orden por nombre: el timestamp fijo al inicio ordena cronologico)."""
    try:
        files = sorted(directory.glob("*.log"))
    except OSError:
        return
    for old in files[: max(0, len(files) - keep)]:
        try:
            old.unlink()
        except OSError:
            pass


def _ftts_handler(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    handler._ftts = True  # noqa: SLF001 - marca para limpieza idempotente
    return handler


def _remove_ftts_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, "_ftts", False):
            logger.removeHandler(handler)


def setup_logging() -> None:
    """Idempotente: se puede llamar varias veces (tests, re-arranques).
    Por proceso crea un archivo por categoria y limpia lo que excede 10."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    _remove_ftts_handlers(root)
    app_dir = _log_dir() / "app"
    root.addHandler(_ftts_handler(session_log_path(app_dir, "app")))

    for name, category in _CATEGORIES.items():
        logger = logging.getLogger(name)
        _remove_ftts_handlers(logger)
        cat_dir = _log_dir() / category
        logger.addHandler(_ftts_handler(session_log_path(cat_dir, category)))
        logger.propagate = False  # no duplicar en app.log

    for d in (app_dir, *(_log_dir() / c for c in dict.fromkeys(_CATEGORIES.values()))):
        keep_latest(d)
