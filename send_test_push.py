import argparse
import json
import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, messaging


APPS = {
    "noticeboard": ("noticeboard_v2_alerts", "noticeboard_alerts", "Noticeboard V2"),
    "placement": ("placement_v2_alerts", "placement_v2_alerts", "Placement V2"),
}


def service_account_json():
    raw = os.environ.get("FCM_SERVICE_ACCOUNT_JSON")
    if not raw:
        saved_path = Path(__file__).parent / ".local-checker" / "firebase-key-path.txt"
        if saved_path.exists():
            raw = Path(saved_path.read_text(encoding="utf-8").strip()).read_text(encoding="utf-8")
    if not raw:
        raise SystemExit(
            "Set FCM_SERVICE_ACCOUNT_JSON or run Start Checker.cmd to save the key path."
        )
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(
        description="Send one real FCM test notification to a V2 app topic."
    )
    parser.add_argument("--app", choices=APPS, required=True)
    args = parser.parse_args()

    topic, channel_id, app_name = APPS[args.app]
    firebase_admin.initialize_app(
        credentials.Certificate(service_account_json())
    )
    message = messaging.Message(
        notification=messaging.Notification(
            title=f"{app_name} test notification",
            body="The custom Firebase push connection is working.",
        ),
        topic=topic,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(channel_id=channel_id),
        ),
    )
    message_id = messaging.send(message)
    print(f"Sent test notification to topic {topic}: {message_id}")


if __name__ == "__main__":
    main()
