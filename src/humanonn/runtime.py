from __future__ import annotations

import sys


def terminal_log(message: str, enabled: bool = True) -> None:
    if not enabled:
        return
    print(f"[humanonn] {message}", file=sys.stderr, flush=True)

