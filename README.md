# Channel i Checker

This Windows checker watches the latest Noticeboard page and the current placement company index. It checks both feeds every five seconds and backs off for up to 30 seconds per feed after errors. Changes trigger Firebase pushes to Noticeboard V2 or Placement V2.

1. Connect to IITR Wi-Fi or an IITR-approved connection that reaches both feeds.
2. Install Python 3.11 or later if needed.
3. Double-click **Start Checker.cmd**. On first launch it installs the dependencies. The window opens paused.
4. Select **Start checking**. If the Firebase key is missing, use **Configure Firebase key** once. The checker stores a copy in the Git-ignored .local-checker folder.

Close the window to hide the checker in the Windows notification area. Right-click its icon to reopen, pause, resume, or quit. To send a test push, open the window, select one app or **Both apps**, and click **Send test notification**. Keep the computer awake and connected while checking.

The snapshot and saved key path live in the ignored .local-checker folder. The first successful fetch quietly saves a baseline. Later changes in the notice list or the placement company index trigger pushes. The apps must use the same Firebase project and allow notifications.

For a one-time connectivity check, run .venv\Scripts\python.exe run_local.py --check.

On the configured laptop, the Windows Startup shortcut opens the checker in the tray and starts polling after sign-in. Remove Channel i Checker.lnk from the current user Startup folder to disable automatic launch.

## Headless Linux setup

Run the checker from a host that can access the IITR feeds. No inbound port,
public tunnel, Channel i account, or campus session cookie is required.

```sh
git clone https://github.com/HUKA-Taro/channeli-checker.git
cd channeli-checker
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run_local.py --check
```

Once both feeds report OK, run the following under a dedicated service user.
Store the Firebase service account JSON outside the checkout in a private
directory, readable only by that user. The service account needs permission
to send FCM messages in the Firebase project used by the V2 apps.

```sh
chmod 600 /path/to/firebase-fcm.json
.venv/bin/python run_local.py --firebase-key /path/to/firebase-fcm.json
```

For unattended operation, use that absolute command as the service entry point
and configure your service manager to start it at boot and restart on failure.
Keep the Git-ignored `.local-checker` folder writable by the service user so
snapshots survive a restart. Run only one active checker per Firebase project
to avoid duplicate pushes. The first successful fetch establishes a baseline.

The checker watches notices and the placement company results index.
