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

Use a Linux host on the IITR network with Git, Python 3.11 or later, and
Python's venv support. Store the Firebase JSON outside the checkout under the
account that runs the checker.

```sh
git clone https://github.com/HUKA-Taro/channeli-checker.git
cd channeli-checker
chmod 600 /path/to/firebase-fcm.json
python3 run_homelab.py /path/to/firebase-fcm.json
```

The launcher installs dependencies locally, keeps the checker running and
checks for a new stable GitHub release at startup and then once an hour. It accepts only
forward updates from the official main branch and keeps local snapshots in
`.local-checker`. Keep the launcher running under your service manager if you
want it to start after reboot. Use a dedicated unprivileged service account
on the host. Run one checker per Firebase project. No inbound port or session
cookie is needed.

Remote ping and heartbeat setup is paused. The launcher starts the notice and result checker with only the FCM key.
