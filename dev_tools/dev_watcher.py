"""Simple dev watcher that restarts a process when project files change.

Usage:
  python dev_tools/dev_watcher.py            # runs streamlit by default
  python dev_tools/dev_watcher.py --test    # run a self-test using a dummy child process

This file is intentionally small and uses `watchdog` for reliable file watching
on Windows/macOS/Linux.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class RestartHandler(FileSystemEventHandler):
    def __init__(self, restart_callback):
        super().__init__()
        self.restart = restart_callback

    def on_modified(self, event):
        # ignore directory events
        if event.is_directory:
            return
        self.restart(event.src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        self.restart(event.src_path)


def start_child(cmd: List[str]):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(f"[watcher] started child pid={proc.pid} cmd={' '.join(cmd)}")
    return proc


def kill_child(proc: subprocess.Popen):
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            time.sleep(0.5)
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def run_watcher(paths: List[Path], cmd: List[str], stop_event: threading.Event, max_restarts: int | None = None):
    proc = start_child(cmd)
    restarts = 0

    def do_restart(changed_path: str):
        nonlocal proc, restarts
        print(f"[watcher] change detected: {changed_path}; restarting child...")
        kill_child(proc)
        proc = start_child(cmd)
        restarts += 1
        if max_restarts is not None and restarts >= max_restarts:
            stop_event.set()

    observer = Observer()
    handler = RestartHandler(do_restart)
    for p in paths:
        # if p is a directory watch it recursively; if it's a file watch its parent
        if p.is_dir():
            observer.schedule(handler, str(p), recursive=True)
        else:
            observer.schedule(handler, str(p.parent), recursive=False)
    observer.start()

    try:
        # stream child's stdout to our stdout in a non-blocking manner
        def drain_output(p):
            try:
                for line in p.stdout or []:
                    print(f"[child] {line.rstrip()}")
            except Exception:
                pass

        drain_thread = threading.Thread(target=drain_output, args=(proc,), daemon=True)
        drain_thread.start()

        while not stop_event.is_set():
            time.sleep(0.2)
            # if child exited unexpectedly, restart it
            if proc.poll() is not None and not stop_event.is_set():
                print(f"[watcher] child exited with code {proc.returncode}; restarting")
                proc = start_child(cmd)
    finally:
        kill_child(proc)
        observer.stop()
        observer.join()


def test_run():
    # Create a small trigger file and watch it.
    base = Path(__file__).resolve().parent
    trigger = base / "watch_test_trigger.txt"
    trigger.write_text("initial\n")

    stop_event = threading.Event()

    # Use a lightweight dummy child process (prints then sleeps) so test runs quickly
    cmd = [sys.executable, "-u", "-c", "import time; print('dummy child started'); time.sleep(300)"]
    watcher_thread = threading.Thread(target=run_watcher, args=([trigger], cmd, stop_event, 1), daemon=True)
    watcher_thread.start()

    # wait for child to start
    time.sleep(1.0)
    # touch the file to trigger restart
    print("[test] touching trigger file to cause restart")
    trigger.write_text("updated\n")

    # allow watcher to observe and restart
    timeout = 8
    started = time.time()
    while time.time() - started < timeout and not stop_event.is_set():
        time.sleep(0.2)

    stop_event.set()
    watcher_thread.join(timeout=3)
    print("[test] done")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--cmd", nargs=argparse.REMAINDER, help="command to run instead of streamlit")
    args = ap.parse_args()

    if args.test:
        test_run()
        return

    cmd = args.cmd if args.cmd else [sys.executable, "-m", "streamlit", "run", "streamlit_app.py"]
    # watch repo root and .env by default
    root = Path.cwd()
    paths = [root]
    stop_event = threading.Event()
    try:
        run_watcher(paths, cmd, stop_event)
    except KeyboardInterrupt:
        print("[watcher] interrupted, exiting")


if __name__ == "__main__":
    main()
