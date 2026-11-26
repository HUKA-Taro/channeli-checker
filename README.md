# Channel i Checker

Channel i Checker runs on a computer that can reach IIT Roorkee's Channel i feeds. It checks for new or changed notices and company-wise placement results, then sends Firebase Cloud Messaging (FCM) pushes to the V2 apps.

## What it checks

| Feed | What triggers an alert | App notified |
| --- | --- | --- |
| Noticeboard | A notice is added or its contents change | Noticeboard V2 |
| Placement results | A company-wise placement result entry is added or changes | Placement V2 |

The checker does not monitor internship results or application deadline changes. On its first successful run, it saves the current data as a baseline without sending alerts for existing items. Later changes can trigger pushes.

## Before you start

- The machine must use IITR Wi-Fi or another IITR-approved connection that can reach both feeds. Outside that network, requests may time out or return `403 Forbidden`.
- Install Python 3.11 or newer. The Linux auto-updating launcher also needs Git and Python virtual-environment support.
- Have a Firebase Admin service-account JSON for the same Firebase project used by the apps. It is used to send FCM pushes and, when configured, update Firestore status and app-version policies. The checker does not need your Channel i session cookie.
- Run only one checker for a Firebase project. Running the laptop checker and homelab checker together can send duplicate notifications.

Never commit the Firebase JSON key. On Windows it is copied into the Git-ignored `.local-checker` folder. On Linux, keep the key outside the repository and restrict it to the service account.

## Choose how to run it

### Windows laptop: tray app

Use this when you want to start and stop checking yourself on the laptop.

1. Connect the laptop to IITR Wi-Fi or an approved network.
2. Double-click **Start Checker.cmd**. The first launch creates a virtual environment and installs the required packages.
3. If asked, use **Configure Firebase key** and choose the Admin JSON file. The tray app stores its private copy under `.local-checker`.
4. Click **Start checking**. The window shows the status of both feeds.
5. To stop polling, click **Stop checking**. To send a test push, choose **Noticeboard V2**, **Placement V2**, or **Both apps**, then click **Send test notification**.

Closing the window hides it in the Windows notification area; it does not stop checking. Use the tray menu to reopen the window or quit the checker. Starting the checker at Windows sign-in is optional. If you only want to use the laptop checker when the homelab is down, open **Start Checker.cmd** manually and do not add it to Windows Startup.

To check feed access once without starting continuous polling or sending alerts, run:

```powershell
Start Checker.cmd --check
```

### Linux homelab: persistent, self-updating service

Use this for the always-on checker. **Run `run_homelab.py` as the service command.** It starts `run_local.py` as an internal worker, keeps that worker running, and checks for official checker releases. Do not configure `run_local.py` as the homelab service command; it is only the worker and does not update its own source.

1. Clone the checker repository onto the homelab:

   ```sh
   git clone https://github.com/HUKA-Taro/channeli-checker.git
   cd channeli-checker
   ```

2. Put the Firebase Admin JSON outside the checkout. Restrict access to it:

   ```sh
   chmod 600 /path/to/firebase-fcm.json
   ```

3. Start the launcher:

   ```sh
   python3 run_homelab.py /path/to/firebase-fcm.json
   ```

4. Keep that process running with the homelab's service manager so it restarts after reboot. Run it under a dedicated, unprivileged Linux account.

The launcher checks for a newer stable GitHub release at startup and once an hour. It accepts only forward updates that are part of the official `main` branch, installs the release's Python dependencies, and restarts the worker automatically. No inbound network port or Channel i session cookie is needed.

## Timing and retry behavior

- The checker normally attempts both feeds on a five-second cycle. Requests run one after another.
- Each request has a ten-second timeout.
- A failed feed retries with increasing delays, capped at 30 seconds. The other feed continues to be checked.
- The last good snapshot is kept when a request fails. Once access returns, the checker compares against that saved snapshot.

A five-second cycle is the normal polling target, not a guarantee that every alert arrives within five seconds. Feed response time, network access, retry delays, Firebase delivery, and Android notification settings all affect delivery time.

## Firestore status and app updates

The homelab can publish checker health and the minimum supported app versions to Firestore. This is separate from polling the Channel i feeds; feed polling continues if Firestore status is temporarily unavailable.

One-time setup by the Firebase project owner requires:

- The Firestore database named `checker-status`.
- The rules in `firestore.status.rules` deployed to that database.
- The homelab service account configured with the required Firestore permissions and FCM access.

When enabled, the homelab:

- Publishes feed health every 30 seconds. The apps show the status in **Profile > Notification settings** and refresh it every 30 seconds while that screen is open.
- Checks its private ping mailbox every 15 seconds. A status credential can request a reply or ask which checker release is running.
- Checks app releases every five minutes and advances Firestore's minimum-build policy when it finds a newer valid stable release.

The Windows tray publishes heartbeat status and checks app releases every five minutes to advance Firestore app-version policies when a newer valid release is available. It does not update its own checker code; self-updating the checker remains the Linux launcher's job.

### Check the homelab from Windows

Use the private status credential provided for your laptop. Run these commands from the checker folder:

```powershell
.venv\Scripts\python.exe checker_status.py --key "E:/path/to/laptop-status.json"
```

To ask which checker release and commit are running:

```powershell
.venv\Scripts\python.exe checker_status.py --key "E:/path/to/laptop-status.json" --version
```

To ask the running homelab to check GitHub for a checker update immediately:

```powershell
.venv\Scripts\python.exe checker_status.py --key "E:/path/to/laptop-status.json" --update
```

The request goes through the existing private Firestore mailbox. The homelab acknowledges it, checks the latest stable checker release within a few seconds, and if a newer release is available, installs it and restarts the checker worker automatically. This does not restart the homelab machine or service. The command requires a laptop status credential with write access to `checker_status/homelab_control`; the homelab must be running `run_homelab.py` with Firestore status enabled.

A reply confirms the homelab reached Firestore and responded to the ping. No reply within 40 seconds usually means the homelab process is stopped, the status credential or IAM is wrong, or Firestore is unavailable.

## Publishing app releases

The homelab can publish the apps' update policies from their latest public GitHub releases. For an app release to be accepted:

1. The release must be stable (not a draft or prerelease) and use a tag such as `v2.4.0`.
2. The tag's `pubspec.yaml` version must match the tag. The build number must be present in that file.
3. Attach exactly one APK asset for that app and let GitHub finish uploading it. The release asset must have a valid SHA-256 digest.
4. Keep the app's source and APK release publicly readable so the app can download the APK.

The homelab reads the version from the release tag and the minimum Android build from `pubspec.yaml`. It publishes the policy only when that build is newer than the one already stored. APK names can follow the pattern `noticeboardv2-2.4.0.apk` or `placementv2-2.4.0.apk`; keep the filename, app version, and release tag consistent.

## Troubleshooting

### A feed shows an error

Run the one-time feed check on an IITR-approved network. A timeout or `403 Forbidden` means the checker could not read that feed at that moment; it will preserve the last good snapshot and retry. Check the warning for the feed name and the HTTP or connection error.

### The app says the checker is online, but the feed is not ready

The heartbeat is recent, so a checker has reported in, but it has not reported a successful read of that feed. Check that feed's recent checker logs and confirm the homelab has IITR network access.

### The test push succeeds but the phone shows nothing

Confirm that the app and checker use the same Firebase project, Android notifications are allowed, and the matching alert switch is enabled in the app. A tray test confirms Firebase accepted the test push; Android permission and device settings still control display.

### The app does not see a new release

Confirm the tag and `pubspec.yaml` version match, the release is stable, and exactly one APK is attached and fully uploaded. The homelab checks app releases every five minutes. The policy is not advanced for an invalid release or a build number that is not newer.

## Local state and logs

- Windows key copy, feed snapshot, and log: `.local-checker`.
- Linux key: outside the repository. Linux snapshot and runtime files: `.local-checker`.
- Python dependencies: `.venv`.

These local folders are Git-ignored. Do not delete the snapshot while the checker is running; it is used to compare new data with the last successful check.
