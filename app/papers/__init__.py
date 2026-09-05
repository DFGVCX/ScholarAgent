from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from app.papers.ingestion import PaperIngestionService
    from app.papers.models import PaperInput, PaperRecord
    from app.papers.repository import PaperRepository


__all__ = ["PaperInput", "PaperRecord", "PaperRepository", "PaperIngestionService"]

_EXPORTS = {
    "PaperInput": ("app.papers.models", "PaperInput"),
    "PaperRecord": ("app.papers.models", "PaperRecord"),
    "PaperRepository": ("app.papers.repository", "PaperRepository"),
    "PaperIngestionService": ("app.papers.ingestion", "PaperIngestionService"),
}


def __getattr__(name: str) -> Any:
    """Load service-layer exports only when callers explicitly request them.

    Parsing and chunking are valid offline operations. Importing their package
    must therefore not initialize SQLAlchemy, runtime settings, or PostgreSQL.
    """
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
