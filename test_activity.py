"""Record activity for paired test accounts through a private HTTPS proxy."""
import argparse
from contextlib import closing
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import time

from app_versions import APP_IDS

HEX = re.compile(r"[a-f0-9]{64}")


class ActivityServer(ThreadingHTTPServer):
    def __init__(self, port, config_path, db_path):
        self.config_path = config_path
        self.db_path = db_path
        self.read_config()
        with closing(sqlite3.connect(db_path)) as db, db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS activity (
                    app TEXT NOT NULL,
                    enrollment TEXT NOT NULL,
                    name TEXT NOT NULL,
                    device TEXT NOT NULL,
                    build INTEGER NOT NULL,
                    last_active INTEGER NOT NULL,
                    PRIMARY KEY (app, enrollment, device)
                )
            """)
        super().__init__(("127.0.0.1", port), ActivityRequest)

    def read_config(self):
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        key = config["device_key"]
        accounts = config["accounts"]
        if not HEX.fullmatch(key) or not isinstance(accounts, dict):
            raise ValueError("Invalid activity configuration")
        return bytes.fromhex(key), accounts


class ActivityRequest(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_args):
        pass

    def reply(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_POST(self):
        if self.path != "/activity":
            return self.reply(404)
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not HEX.fullmatch(token) or not self.headers.get("Authorization", "").startswith("Bearer "):
            return self.reply(401)
        try:
            key, accounts = self.server.read_config()
        except (OSError, ValueError, TypeError, KeyError):
            return self.reply(503)
        account = accounts.get(hashlib.sha256(token.encode("ascii")).hexdigest())
        if not isinstance(account, dict):
            return self.reply(401)
        name, enrollment = account.get("name"), account.get("enrollment")
        if (not isinstance(name, str) or not name.strip()
                or not isinstance(enrollment, str) or not enrollment.strip()):
            return self.reply(503)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 512 or self.headers.get_content_type() != "application/json":
                return self.reply(400)
            payload = json.loads(self.rfile.read(length))
            app, device, build = (payload[field] for field in ("app", "android_id", "build"))
            if (app not in APP_IDS or not isinstance(device, str)
                    or not re.fullmatch(r"[0-9a-f]{1,16}", device)
                    or type(build) is not int or not 0 < build <= 2_100_000_000):
                return self.reply(400)
        except (KeyError, OSError, ValueError, TypeError):
            return self.reply(400)
        pseudonym = hmac.new(key, device.encode("ascii"), hashlib.sha256).hexdigest()
        try:
            with closing(sqlite3.connect(self.server.db_path)) as db, db:
                db.execute("""
                    INSERT INTO activity (app, enrollment, name, device, build, last_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (app, enrollment, device) DO UPDATE SET
                        name = excluded.name, build = excluded.build,
                        last_active = excluded.last_active
                """, (app, enrollment, name, pseudonym, build, int(time.time())))
        except sqlite3.Error:
            return self.reply(503)
        self.reply(204)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--active-days", type=int, help="Report counts instead of serving")
    args = parser.parse_args()
    if args.active_days is not None:
        if args.active_days < 1:
            parser.error("--active-days must be positive")
        if not args.db.is_file():
            parser.error("The activity database does not exist")
        cutoff = int(time.time()) - args.active_days * 86400
        with closing(sqlite3.connect(args.db)) as db:
            for app in (*APP_IDS, "both"):
                where = "last_active >= ?"
                params = [cutoff]
                if app != "both":
                    where += " AND app = ?"
                    params.append(app)
                users, devices = db.execute(
                    "SELECT COUNT(DISTINCT enrollment), COUNT(DISTINCT device) "
                    "FROM activity WHERE " + where, params
                ).fetchone()
                print(f"{app}: {users} active test accounts, {devices} devices")
        return
    if args.config is None:
        parser.error("--config is required when serving")
    with ActivityServer(args.port, args.config, args.db) as server:
        server.serve_forever()


if __name__ == "__main__":
    import os

    os.umask(0o077)
    main()
