# Channel i Checker

This Windows checker watches the latest Noticeboard page and the current placement company index. It checks both feeds every five seconds and backs off after errors. Changes trigger Firebase pushes to Noticeboard V2 or Placement V2.

1. Connect to IITR Wi-Fi or an IITR-approved connection that reaches both feeds.
2. Install Python 3.11 or later if needed.
3. Double-click **Start Checker.cmd**. On first launch it installs the dependencies. The window opens paused.
4. Select **Start checking**. If the Firebase key is missing, use **Configure Firebase key** once. The checker stores a copy in the Git-ignored .local-checker folder.

Close the window to hide the checker in the Windows notification area. Right-click its icon to reopen, pause, resume, or quit. To send a test push, open the window, select one app or **Both apps**, and click **Send test notification**. Keep the computer awake and connected while checking.

The snapshot and saved key path live in the ignored .local-checker folder. The first successful fetch quietly saves a baseline. Later changes in the notice list or the placement company index trigger pushes. The apps must use the same Firebase project and allow notifications.

For a one-time connectivity check, run .venv\Scripts\python.exe run_local.py --check.

On the configured laptop, the Windows Startup shortcut opens the checker in the tray and starts polling after sign-in. Remove Channel i Checker.lnk from the current user Startup folder to disable automatic launch.
