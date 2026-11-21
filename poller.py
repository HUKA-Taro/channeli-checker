import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import firebase_admin
import requests
from firebase_admin import credentials, messaging

NOTICE_TOPIC = "noticeboard_v2_alerts"
PLACEMENT_TOPIC = "placement_v2_alerts"
REQUEST_TIMEOUT = 10
MAX_BACKOFF = 30


def now():
    return datetime.now(timezone.utc)


def parse_time(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def load_state(blob):
    try:
        return json.loads(blob.download_blob().readall())
    except FileNotFoundError:
        return {"feeds": {}}


def send_push(topic, title, body, data):
    try:
        firebase_admin.get_app()
    except ValueError:
        raw = os.environ["FCM_SERVICE_ACCOUNT_JSON"]
        firebase_admin.initialize_app(credentials.Certificate(json.loads(raw)))
    message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data={k: str(v) for k, v in data.items()},
        topic=topic,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(
                channel_id="noticeboard_alerts" if topic == NOTICE_TOPIC else "placement_v2_alerts"
            ),
        ),
    )
    messaging.send(message)


def season_year():
    current = now()
    return current.year if current.month >= 7 else current.year - 1


def feeds():
    return {
        "notices": {
            "url": "https://channeli.in/api/noticeboard/new/?page=1",
            "topic": NOTICE_TOPIC, "kind": "notice",
        },
        "placement_company": {
            "url": f"https://results.channeli.in/{season_year()}/placement/company/index.json",
            "topic": PLACEMENT_TOPIC, "kind": "result", "season": "placement", "view": "company",
        },
    }


def notices_diff(old, payload):
    new, changes = {}, []
    for item in payload.get("results", []):
        key = str(item.get("id", ""))
        if not key:
            continue
        digest = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        new[key] = {"hash": digest}
        previous = old.get(key)
        if previous is None or previous.get("hash") != digest:
            changes.append(item)
    return new, changes


def result_diff(old, payload, view):
    if not isinstance(payload, list):
        raise ValueError("Expected results index to be a JSON array")
    new, changes = {}, []
    for item in payload:
        if view == "company":
            key = str(item.get("companyId") or item.get("companyName") or item.get("detail") or "")
        else:
            key = "|".join(str(item.get(k) or "") for k in ("studentBranchCode", "studentDegree", "studentBranchName"))
        if not key:
            continue
        digest = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        new[key] = {"hash": digest}
        previous = old.get(key)
        if previous is None or previous.get("hash") != digest:
            changes.append(item)
    return new, changes


def retry_delay(response, count):
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(MAX_BACKOFF, max(5, int(header)))
        except ValueError:
            try:
                return min(MAX_BACKOFF, max(5, int((parsedate_to_datetime(header) - now()).total_seconds())))
            except (TypeError, ValueError):
                pass
    return min(MAX_BACKOFF, 5 * (2 ** min(count, 6)))


def poll_feeds(blob):
    state = load_state(blob)
    active_feeds = feeds()
    state["feeds"] = {
        name: saved for name, saved in state["feeds"].items() if name in active_feeds
    }
    first_run = not state["feeds"]

    for name, feed in active_feeds.items():
        saved = state["feeds"].get(name, {})
        if parse_time(saved.get("retry_until")) > now():
            continue
        headers = {"User-Agent": "ChanneliNoticeResultChecker/1.0"}
        if saved.get("etag"):
            headers["If-None-Match"] = saved["etag"]
        try:
            response = requests.get(feed["url"], headers=headers, timeout=REQUEST_TIMEOUT)
            if response.status_code == 304:
                saved["checked_at"] = now().isoformat()
                saved["retry_count"] = 0
                saved.pop("retry_until", None)
                state["feeds"][name] = saved
                continue
            if response.status_code == 429 or response.status_code >= 500:
                count = int(saved.get("retry_count", 0)) + 1
                saved["retry_count"] = count
                saved["retry_until"] = (now() + timedelta(seconds=retry_delay(response, count))).isoformat()
                state["feeds"][name] = saved
                logging.warning("%s returned %s; backing off", name, response.status_code)
                continue
            response.raise_for_status()
            payload = response.json()
            old = saved.get("records", {})
            last_push = saved.get("last_push_accepted")
            url_changed = saved.get("url") not in (None, feed["url"])
            quiet_baseline = first_run or url_changed or "records" not in saved
            if url_changed:
                old = {}
            if feed["kind"] == "notice":
                records, changes = notices_diff(old, payload)
                if changes and not quiet_baseline:
                    titles = [str(x.get("title") or "Notice") for x in changes[:3]]
                    body = titles[0] if len(changes) == 1 else f"{len(changes)} new/updated notices: " + "; ".join(titles)
                    send_push(feed["topic"], "Noticeboard update", body[:500],
                              {"type": "notice_update", "count": len(changes)})
                    last_push = now().isoformat()
            else:
                records, changes = result_diff(old, payload, feed["view"])
                if changes and not quiet_baseline:
                    if feed["view"] == "company":
                        labels = [str(x.get("companyName") or "Company") for x in changes[:3]]
                    else:
                        labels = [str(x.get("studentBranchName") or x.get("studentDegree") or "Branch") for x in changes[:3]]
                    body = f"{feed['season'].title()} {feed['view']} results changed: " + ", ".join(labels)
                    send_push(feed["topic"], "Placement results updated", body[:500],
                              {"type": "placement_result_update", "feed": name, "count": len(changes)})
                    last_push = now().isoformat()
            state["feeds"][name] = {
                "records": records, "etag": response.headers.get("ETag"), "url": feed["url"],
                "retry_count": 0, "checked_at": now().isoformat(),
                "last_push_accepted": last_push,
            }
        except requests.RequestException as error:
            count = int(saved.get("retry_count", 0)) + 1
            delay = min(MAX_BACKOFF, 5 * (2 ** min(count, 6)))
            saved["retry_count"] = count
            saved["retry_until"] = (now() + timedelta(seconds=delay)).isoformat()
            state["feeds"][name] = saved
            logging.warning("%s failed; backing off %ss: %s", name, delay, error)
        except Exception:
            failures = int(saved.get("retry_count", 0)) + 1
            delay = min(MAX_BACKOFF, 5 * (2 ** min(failures, 6)))
            saved["retry_count"] = failures
            saved["retry_until"] = (now() + timedelta(seconds=delay)).isoformat()
            state["feeds"][name] = saved
            logging.exception("Could not process feed %s; backing off %ss and retaining prior snapshot", name, delay)

    blob.upload_blob(json.dumps(state, separators=(",", ":")), overwrite=True)
