"""Private Firestore mailbox for on-demand checker health checks."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time
from uuid import uuid4

from firebase_admin import credentials
from google.api_core.exceptions import AlreadyExists, FailedPrecondition
from google.cloud.firestore import Client, SERVER_TIMESTAMP


POLL_SECONDS = 15
HOST_TTL = timedelta(seconds=90)
FEED_NAMES = ("notices", "placement_company")
TIMEOUT_SECONDS = 40
RPC_OPTIONS = {"retry": None, "timeout": 5}


def mailbox(key_path, database):
    credential = credentials.Certificate(str(key_path))
    client = Client(
        project=credential.project_id, credentials=credential.get_credential(),
        database=database,
    )
    return client, client.document("checker_status/homelab")


def publish_heartbeat(client, host_id, feed_state):
    roster = client.document("checker_status/hosts")
    public = client.document("checker_status/public")
    for _ in range(3):
        previous = roster.get(**RPC_OPTIONS)
        entries = (previous.to_dict() or {}).get("hosts", {})
        # Compare against Firestore's read time to avoid host clock drift.
        current = previous.read_time
        hosts = {
            key: entry for key, entry in entries.items()
            if isinstance(entry.get("expires_at"), datetime) and entry["expires_at"] > current
        }
        local_now = datetime.now(timezone.utc)
        health = {}
        for name in FEED_NAMES:
            feed = feed_state.get(name, {})
            checked = feed.get("checked_at")
            health[name] = bool(
                checked and not feed.get("retry_count")
                and local_now - datetime.fromisoformat(checked) < HOST_TTL
            )
        hosts[host_id] = {"expires_at": current + HOST_TTL, "feeds": health}
        combined = {
            name: any(entry["feeds"].get(name) for entry in hosts.values())
            for name in FEED_NAMES
        }
        batch = client.batch()
        if previous.exists:
            batch.update(
                roster, {"hosts": hosts},
                option=client.write_option(last_update_time=previous.update_time),
            )
        else:
            batch.create(roster, {"hosts": hosts})
        batch.set(public, {"updated_at": SERVER_TIMESTAMP, **combined})
        try:
            batch.commit(**RPC_OPTIONS)
            return
        except (AlreadyExists, FailedPrecondition):
            # Another checker published first. Merge again from its revision.
            continue
    logging.warning("Heartbeat contention; will retry on the next cycle")


def publish_app_versions(key_path, database, stop):
    from app_versions import INTERVAL_SECONDS, publish_versions

    try:
        client, _ = mailbox(key_path, database)
    except Exception as error:
        logging.warning("Could not initialize version publisher: %s", type(error).__name__)
        return
    while not stop.is_set():
        publish_versions(client)
        stop.wait(INTERVAL_SECONDS)


def respond(key_path, database, snapshot, stop, homelab=None):
    try:
        client, ref = mailbox(key_path, database)
    except Exception as error:
        logging.warning("Could not initialize status mailbox: %s", type(error).__name__)
        return
    host_id = uuid4().hex
    next_heartbeat = 0
    while not stop.is_set():
        if time.monotonic() >= next_heartbeat:
            next_heartbeat = time.monotonic() + 30
            try:
                state = json.loads(snapshot.data or b'{"feeds": {}}')
                publish_heartbeat(client, host_id, state["feeds"])
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
                        "last_push_accepted": state["feeds"].get(name, {}).get("last_push_accepted"),
                        "consecutive_failures": state["feeds"].get(name, {}).get("retry_count", 0),
                    }
                    for name in FEED_NAMES
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
        if homelab is not None:
            respond_to_control(client, *homelab)
        stop.wait(POLL_SECONDS)


def respond_to_control(client, version, commit, update_request):
    ref = client.document("checker_status/homelab_control")
    try:
        request = ref.get(**RPC_OPTIONS)
        ping = request.to_dict() or {}
        requested = ping.get("requested_at")
        if (not ping.get("request_id") or ping.get("responded_at")
                or not isinstance(requested, datetime)
                or not timedelta(0) <= request.read_time - requested < timedelta(minutes=1)):
            return
        result = "status"
        if ping.get("action") == "update":
            if update_request is None:
                result = "launcher_restart_required"
            else:
                update_request.touch()
                result = "update_requested"
        ref.update(
            {"responded_at": SERVER_TIMESTAMP, "version": version,
             "commit": commit, "result": result},
            option=client.write_option(last_update_time=request.update_time),
            **RPC_OPTIONS,
        )
    except FailedPrecondition:
        pass
    except Exception as error:
        logging.warning("Homelab control unavailable: %s", type(error).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--database", default="checker-status")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--version", action="store_true", help="Ask the homelab which release it is running.")
    actions.add_argument("--update", action="store_true", help="Ask the homelab launcher to check GitHub now.")
    args = parser.parse_args()

    try:
        client, ref = mailbox(args.key, args.database)
        if args.version or args.update:
            ref = client.document("checker_status/homelab_control")
        request_id = uuid4().hex
        start = time.monotonic()
        action = "update" if args.update else "version" if args.version else "status"
        ref.set({"request_id": request_id, "requested_at": SERVER_TIMESTAMP, "action": action},
                **RPC_OPTIONS)
        while time.monotonic() - start < TIMEOUT_SECONDS:
            reply = ref.get(**RPC_OPTIONS).to_dict() or {}
            if reply.get("request_id") != request_id:
                parser.exit(1, "Another ping replaced this request. Try again.\n")
            if reply.get("responded_at"):
                elapsed = time.monotonic() - start
                print(f"Checker replied in {elapsed:.1f}s (includes waiting for its mailbox check).")
                if args.version or args.update:
                    print(f"Homelab release: {reply.get('version') or 'untagged'} "
                          f"({reply.get('commit') or 'unknown commit'}).")
                    if args.update:
                        if reply.get("result") != "update_requested":
                            print("Restart the homelab launcher once to enable remote update requests.")
                            return 1
                        print("Update check requested. Ping --version again to confirm the result.")
                for name, info in reply.get("feeds", {}).items():
                    print(f"{name}: last success {info['last_success'] or 'none yet'}; "
                          f"consecutive failures {info['consecutive_failures']}; "
                          f"last push accepted {info.get('last_push_accepted') or 'none yet'}")
                return 0
            time.sleep(1)
        parser.exit(1, f"No reply within {TIMEOUT_SECONDS}s. Check the homelab process and Firestore access.\n")
    except Exception as error:
        parser.exit(1, f"Firestore ping failed: {type(error).__name__}. Check the key, database and IAM.\n")


if __name__ == "__main__":
    raise SystemExit(main())
