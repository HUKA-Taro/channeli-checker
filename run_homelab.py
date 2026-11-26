"""Keep the Linux checker running and update it from official GitHub releases."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from threading import Event
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".local-checker"
ORIGIN = "https://github.com/HUKA-Taro/channeli-checker.git"
LATEST = "https://api.github.com/repos/HUKA-Taro/channeli-checker/releases/latest"
UPDATE_INTERVAL = 3600


def git(*args):
    return subprocess.check_output(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=ROOT, text=True, timeout=45,
    ).strip()


def release_target(current):
    request = Request(LATEST, headers={"Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=10) as response:
        release = json.load(response)
    tag = release.get("tag_name", "")
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("The latest release does not have a stable version tag")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Local tracked changes prevent an automatic update")
    git("fetch", "--no-tags", ORIGIN,
        "refs/heads/main:refs/remotes/checker-updater/main",
        f"refs/tags/{tag}:refs/tags/{tag}")
    target = git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    if git("merge-base", current, target) != current:
        raise ValueError("Release would diverge from or downgrade the installed code")
    if git("merge-base", target, "refs/remotes/checker-updater/main") != target:
        raise ValueError("Release is outside the official main branch")
    return target


def prepare_python(requirements):
    digest = hashlib.sha256(requirements).hexdigest()[:20]
    env = RUNTIME / "environments" / digest
    python = env / "bin" / "python"
    ready = env / ".ready"
    if not ready.is_file():
        env.mkdir(parents=True, exist_ok=True)
        req = env / "requirements.txt"
        req.write_bytes(requirements)
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True, timeout=120)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(req)],
            check=True, timeout=300,
        )
        ready.touch()
    return python


def stop_worker(worker):
    if worker is not None and worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("firebase_key", type=Path)
    args = parser.parse_args()
    key = args.firebase_key.resolve()
    if not key.is_file():
        parser.error("Firebase key file does not exist")
    if key.stat().st_mode & 0o077:
        parser.error("Restrict the key to the service user first: chmod 600 <key>")
    RUNTIME.mkdir(mode=0o700, exist_ok=True)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    with (RUNTIME / "updater.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("The checker launcher is already running")
        current = git("rev-parse", "HEAD")
        python = prepare_python((ROOT / "requirements.txt").read_bytes())
        worker_args = ["run_local.py", "--firebase-key", str(key)]
        worker = None
        next_update = 0
        try:
            while not stop.is_set():
                if worker is None or worker.poll() is not None:
                    worker = subprocess.Popen([str(python), *worker_args], cwd=ROOT)
                update_request = RUNTIME / "update-request"
                if update_request.exists():
                    update_request.unlink()
                    next_update = 0
                if time.monotonic() >= next_update:
                    next_update = time.monotonic() + UPDATE_INTERVAL
                    try:
                        target = release_target(current)
                        if target != current:
                            requirements = git("show", f"{target}:requirements.txt").encode() + b"\n"
                            new_python = prepare_python(requirements)
                            if stop.is_set():
                                break
                            stop_worker(worker)
                            worker = None
                            git("switch", "--detach", target)
                            current, python = target, new_python
                            print(f"Updated checker to {current[:12]}", flush=True)
                            continue
                    except (OSError, ValueError, subprocess.SubprocessError) as error:
                        print(f"Update skipped: {type(error).__name__}", file=sys.stderr, flush=True)
                if stop.wait(5):
                    break
        finally:
            stop_worker(worker)
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
