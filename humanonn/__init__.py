from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parent
_SRC_PACKAGE = _ROOT.parent / "src" / "humanonn"

# Allow `python -m humanonn` to work from the repo root without requiring
# an editable install by exposing the real package modules from `src/humanonn`.
__path__ = [str(_ROOT)]
if _SRC_PACKAGE.exists():
    __path__.append(str(_SRC_PACKAGE))

