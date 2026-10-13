"""a6700_rawsmith — AI-native RAW photo development for Sony α6700.

Inspired by rawsmith's JSON-recipe loop, but backed by rawpy/libraw so we
support the A6700's ARW files without waiting for rawsmith upstream.

Public API
----------
- develop(path, recipe, out, size=1400) -> dict
- recipe_schema() -> dict
- make_test_arw(path) -> str
"""

from .a6700_rawsmith import (  # noqa: F401
    SCHEMA,
    DevelopResult,
    _compute_stats,
    _process_rgb,
    develop,
    make_test_arw,
    recipe_schema,
)

__all__ = [
    "SCHEMA",
    "DevelopResult",
    "_compute_stats",
    "_process_rgb",
    "develop",
    "make_test_arw",
    "recipe_schema",
]
__version__ = "0.1.0"
