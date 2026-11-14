#!/usr/bin/env python3
"""Run the NovaMart API and storefront in one terminal."""

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def stop(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def main() -> int:
    uvicorn = BACKEND / ".venv" / "bin" / "uvicorn"
    if not uvicorn.is_file():
        print("Backend dependencies missing. Run `make install` first.", file=sys.stderr)
        return 1
    if not (FRONTEND / "node_modules").is_dir() or not shutil.which("npm"):
        print("Frontend dependencies missing. Run `make install` first.", file=sys.stderr)
        return 1

    processes: list[subprocess.Popen] = []
    try:
        print("Starting API on http://localhost:8100 and storefront on http://localhost:3100", flush=True)
        processes.append(subprocess.Popen(
            [str(uvicorn), "app.main:app", "--reload", "--port", "8100"],
            cwd=BACKEND,
            start_new_session=True,
        ))
        processes.append(subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=FRONTEND,
            start_new_session=True,
        ))
        while True:
            for process in processes:
                code = process.poll()
                if code is not None:
                    print(f"A server exited with status {code}; stopping both.", file=sys.stderr)
                    return code or 0
            time.sleep(0.2)
    except KeyboardInterrupt:
        return 0
    finally:
        stop(processes)


if __name__ == "__main__":
    sys.exit(main())
