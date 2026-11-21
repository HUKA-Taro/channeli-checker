"""Private Firestore mailbox for on-demand checker health checks."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time
from uuid import uuid4

from firebase_admin import credentials
from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore import Client, SERVER_TIMESTAMP


POLL_SECONDS = 15
TIMEOUT_SECONDS = 40
RPC_OPTIONS = {"retry": None, "timeout": 5}


def mailbox(key_path, database):
    credential = credentials.Certificate(str(key_path))
    client = Client(
        project=credential.project_id, credentials=credential.get_credential(),
        database=database,
    )
    return client, client.document("checker_status/homelab")


def respond(key_path, database, snapshot, stop):
    client, ref = mailbox(key_path, database)
    heartbeat = client.document("checker_status/public")
    next_heartbeat = 0
    while not stop.is_set():
        if time.monotonic() >= next_heartbeat:
            next_heartbeat = time.monotonic() + 30
            try:
                state = json.loads(snapshot.data or b'{"feeds": {}}')["feeds"]
                current = datetime.now(timezone.utc)
                health = {}
                for name in ("notices", "placement_company"):
                    feed = state.get(name, {})
                    checked_at = feed.get("checked_at")
                    health[name] = bool(
                        checked_at and not feed.get("retry_count")
                        and current - datetime.fromisoformat(checked_at) < timedelta(seconds=90)
                    )
                heartbeat.set(
                    {"updated_at": SERVER_TIMESTAMP, **health}, **RPC_OPTIONS,
                )
            except Exception as error:
                logging.warning("Public heartbeat unavailable: %s", type(error).__name__)
        try:
            request = ref.get(**RPC_OPTIONS)
            ping = request.to_dict() or {}
            requested = ping.get("requested_at")
            if (ping.get("request_id") and not ping.get("responded_at")
                    and isinstance(requested, datetime)
                    and datetime.now(timezone.utc) - requested < timedelta(minutes=1)):
                state = json.loads(snapshot.data or b'{"feeds": {}}')
                feeds = {
                    name: {
                        "last_success": state["feeds"].get(name, {}).get("checked_at"),
                        "consecutive_failures": state["feeds"].get(name, {}).get("retry_count", 0),
                    }
                    for name in ("notices", "placement_company")
                }
                ref.update(
                    {"responded_at": SERVER_TIMESTAMP, "feeds": feeds},
                    option=client.write_option(last_update_time=request.update_time),
                    **RPC_OPTIONS,
                )
        except FailedPrecondition:
            # A newer ping replaced the one that was read.
            pass
        except Exception as error:
            logging.warning("Status mailbox unavailable: %s", type(error).__name__)
        stop.wait(POLL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--database", default="checker-status")
    args = parser.parse_args()

    try:
        _, ref = mailbox(args.key, args.database)
        request_id = uuid4().hex
        start = time.monotonic()
        ref.set({"request_id": request_id, "requested_at": SERVER_TIMESTAMP}, **RPC_OPTIONS)
        while time.monotonic() - start < TIMEOUT_SECONDS:
            reply = ref.get(**RPC_OPTIONS).to_dict() or {}
            if reply.get("request_id") != request_id:
                parser.exit(1, "Another ping replaced this request. Try again.\n")
            if reply.get("responded_at"):
                elapsed = time.monotonic() - start
                print(f"Homelab replied in {elapsed:.1f}s (includes waiting for its mailbox check).")
                for name, info in reply.get("feeds", {}).items():
                    print(f"{name}: last success {info['last_success'] or 'none yet'}; "
                          f"consecutive failures {info['consecutive_failures']}")
                return 0
            time.sleep(1)
        parser.exit(1, f"No reply within {TIMEOUT_SECONDS}s. Check the homelab process and Firestore access.\n")
    except Exception as error:
        parser.exit(1, f"Firestore ping failed: {type(error).__name__}. Check the key, database and IAM.\n")


if __name__ == "__main__":
    raise SystemExit(main())
