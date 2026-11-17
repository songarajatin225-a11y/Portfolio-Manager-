"""Registry of supervised worker loops. Each phase adds its loops here."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


def registry() -> dict[str, Callable[[], Awaitable[None]]]:
    return {}
