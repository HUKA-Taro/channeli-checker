"""Run the two Channel i feeds using a local snapshot."""

import argparse
import json
import logging
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import firebase_admin
from firebase_admin import credentials

from poller import feeds, poll_feeds


POLL_INTERVAL = 5
RUNTIME_DIR = Path(__file__).resolve().parent / ".local-checker"


class Snapshot:
    """Store the same snapshot format as the Azure worker, on disk or in memory."""

    def __init__(self, path=None):
        self.path = path
        self.data = None

    def download_blob(self):
        try:
            data = self.path.read_bytes() if self.path else self.data
        except FileNotFoundError:
            data = None
        if data is None:
            raise FileNotFoundError()
        return SimpleNamespace(readall=lambda: data)

    def upload_blob(self, data, *, overwrite):
        content = data.encode("utf-8")
        if self.path and (not self.path.exists() or self.path.read_bytes() != content):
            pending = self.path.with_suffix(".tmp")
            pending.write_bytes(content)
            pending.replace(self.path)
        self.data = content

    def counts(self):
        state = json.loads(self.data)
        return {
            name: len(feed["records"])
            for name, feed in state["feeds"].items()
            if "records" in feed and not feed.get("retry_count")
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run", action="store_true", help="Start using your saved key path.")
    mode.add_argument("--check", action="store_true", help="Fetch both feeds once without sending alerts.")
    mode.add_argument("--firebase-key", type=Path, help="Path to your Firebase Admin key JSON.")
    parser.add_argument("--status-key", type=Path, help="Enable remote pings with a separate Firestore key.")
    parser.add_argument("--status-database", default="checker-status")
    parser.add_argument("--without-status", action="store_true",
                        help="Disable Firestore status even on the Linux homelab.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.check:
        snapshot = Snapshot()
        poll_feeds(snapshot)
        counts = snapshot.counts()
        expected = feeds()
        for name in expected:
            if name in counts:
                print(f"{name}: OK, fetched {counts[name]} records")
            else:
                print(f"{name}: FAILED, see the warning above")
        return 0 if len(counts) == len(expected) else 1

    try:
        key_path_file = RUNTIME_DIR / "firebase-key-path.txt"
        key_path = args.firebase_key
        if args.run:
            if key_path_file.exists():
                key_path = Path(key_path_file.read_text(encoding="utf-8").strip())
            if key_path is None or not key_path.is_file():
                key_path = Path(input("Paste the path to your Firebase Admin JSON key: ").strip().strip('"'))
        key_path = key_path.resolve(strict=True)
        if not key_path.is_file():
            raise ValueError("Expected a Firebase Admin key file")
        firebase_admin.initialize_app(credentials.Certificate(str(key_path)))
        if args.run:
            RUNTIME_DIR.mkdir(exist_ok=True)
            key_path_file.write_text(str(key_path), encoding="utf-8")
    except (OSError, ValueError, EOFError):
        parser.exit(1, "Unable to open the Firebase Admin key. Check the path and file format.\n")

    RUNTIME_DIR.mkdir(exist_ok=True)
    snapshot = Snapshot(RUNTIME_DIR / "poll-state.json")
    with (RUNTIME_DIR / "run.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            parser.exit(1, "The local checker is already running.\n")

        logging.info("Local checker started. Poll interval: %s seconds.", POLL_INTERVAL)
        logging.info("Snapshot: %s", snapshot.path)
        status_stop = threading.Event()
        status_key = args.status_key
        if status_key is None and os.name == "posix" and args.firebase_key:
            status_key = key_path
        if status_key and not args.without_status:
            from checker_status import publish_app_versions, respond

            if not status_key.is_file():
                parser.exit(1, "The Firestore status key file does not exist.\n")
            threading.Thread(
                target=respond, args=(status_key, args.status_database, snapshot, status_stop),
                daemon=True, name="checker-status",
            ).start()
            if (os.environ.get("NOTICEBOARDV2_RELEASE_REPO")
                    or os.environ.get("PLACEMENTV2_RELEASE_REPO")):
                threading.Thread(
                    target=publish_app_versions,
                    args=(status_key, args.status_database, status_stop),
                    daemon=True, name="app-versions",
                ).start()
        activity_port = os.environ.get("TEST_ACTIVITY_PORT")
        if activity_port:
            from test_activity import ActivityServer

            try:
                activity = ActivityServer(
                    int(activity_port),
                    RUNTIME_DIR / "test-activity.json",
                    RUNTIME_DIR / "test-activity.sqlite3",
                )
                threading.Thread(
                    target=activity.serve_forever, daemon=True, name="test-activity",
                ).start()
            except (OSError, ValueError, KeyError, TypeError) as error:
                logging.warning("Test activity server unavailable: %s", type(error).__name__)
        seen = set()
        try:
            while True:
                started = time.monotonic()
                poll_feeds(snapshot)
                for name, count in snapshot.counts().items():
                    if name not in seen:
                        logging.info("%s ready, %s records in the snapshot", name, count)
                        seen.add(name)
                time.sleep(max(0, POLL_INTERVAL - (time.monotonic() - started)))
        except KeyboardInterrupt:
            logging.info("Local checker stopped.")
        finally:
            status_stop.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
