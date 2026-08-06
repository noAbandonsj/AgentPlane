from __future__ import annotations

import asyncio
import selectors
import sys
from collections.abc import Coroutine
from typing import Any


def run_async[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run async entry points with a psycopg-compatible loop on Windows."""
    if sys.platform == "win32":

        def loop_factory() -> asyncio.SelectorEventLoop:
            return asyncio.SelectorEventLoop(selectors.SelectSelector())

        with asyncio.Runner(loop_factory=loop_factory) as runner:
            return runner.run(coroutine)
    return asyncio.run(coroutine)
