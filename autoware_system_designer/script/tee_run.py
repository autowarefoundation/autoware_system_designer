#!/usr/bin/env python3

import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import List


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a command while writing full stdout/stderr to a log file, "
            "and printing only stderr (warnings/errors) to the terminal."
        )
    )
    parser.add_argument(
        "--log-file",
        required=True,
        help="Path to the log file to write (combined stdout+stderr).",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to the log file instead of overwriting.",
    )
    parser.add_argument(
        "--print-stdout",
        action="store_true",
        help="Also print stdout to the terminal (default: false).",
    )
    parser.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Command to run, preceded by -- (e.g. -- python3 script.py ...)",
    )
    args = parser.parse_args(argv)
    if not args.cmd or args.cmd[0] != "--":
        raise SystemExit("tee_run.py: expected command after '--'")
    args.cmd = args.cmd[1:]
    if not args.cmd:
        raise SystemExit("tee_run.py: empty command")
    return args


def _pump_stream(
    *,
    stream,
    log_fp,
    log_lock: threading.Lock,
    stop_event: threading.Event,
    terminal_stream,
    print_to_terminal: bool,
) -> None:
    # Read in binary chunks until EOF or we are asked to stop.
    # Note: if the child spawns background processes that inherit stdout/stderr,
    # the pipe may stay open even after the main process exits. In that case we
    # stop reading once the main process ends to avoid hanging the build.
    while not stop_event.is_set():
        try:
            chunk = stream.read(4096)
        except Exception:
            break
        if not chunk:
            break

        with log_lock:
            log_fp.write(chunk)
            log_fp.flush()

        if print_to_terminal:
            terminal_stream.buffer.write(chunk)
            terminal_stream.flush()


def main(argv: List[str]) -> int:
    args = _parse_args(argv)

    log_path = Path(args.log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    mode = "ab" if args.append else "wb"

    env = os.environ.copy()
    # Encourage line-buffered / unbuffered Python output from child processes.
    env.setdefault("PYTHONUNBUFFERED", "1")

    with open(log_path, mode) as log_file:
        proc = subprocess.Popen(
            args.cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        assert proc.stdout is not None
        assert proc.stderr is not None

        log_lock = threading.Lock()
        stop_event = threading.Event()

        t_out = threading.Thread(
            target=_pump_stream,
            kwargs={
                "stream": proc.stdout,
                "log_fp": log_file,
                "log_lock": log_lock,
                "stop_event": stop_event,
                "terminal_stream": sys.stdout,
                "print_to_terminal": bool(args.print_stdout),
            },
            daemon=True,
        )
        t_err = threading.Thread(
            target=_pump_stream,
            kwargs={
                "stream": proc.stderr,
                "log_fp": log_file,
                "log_lock": log_lock,
                "stop_event": stop_event,
                "terminal_stream": sys.stderr,
                "print_to_terminal": True,
            },
            daemon=True,
        )

        t_out.start()
        t_err.start()

        return_code = proc.wait()

        # Stop readers even if pipes never close (e.g., inherited by background processes).
        stop_event.set()
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.stderr.close()
        except Exception:
            pass

        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)

        return return_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
