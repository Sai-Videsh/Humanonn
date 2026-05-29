from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
import sys
from typing import Iterator


class _TeeStream:
    def __init__(self, stream: object, log_file: object) -> None:
        self._stream = stream
        self._log_file = log_file

    def write(self, data: str) -> int:
        stream_write = getattr(self._stream, "write")
        log_write = getattr(self._log_file, "write")
        stream_write(data)
        log_write(data)
        return len(data)

    def flush(self) -> None:
        getattr(self._stream, "flush")()
        getattr(self._log_file, "flush")()


@contextmanager
def tee_output(log_path: str | Path) -> Iterator[Path]:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as log_file:
        stdout_tee = _TeeStream(sys.stdout, log_file)
        stderr_tee = _TeeStream(sys.stderr, log_file)
        with redirect_stdout(stdout_tee), redirect_stderr(stderr_tee):
            yield path


def terminal_log(message: str, enabled: bool = True) -> None:
    if not enabled:
        return
    print(f"[humanonn] {message}", file=sys.stderr, flush=True)

