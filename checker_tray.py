"""Windows tray controls for the private Channel i checker."""

import logging
from logging.handlers import RotatingFileHandler
import msvcrt
from pathlib import Path
from queue import Empty, SimpleQueue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import firebase_admin
from firebase_admin import credentials
from PIL import Image, ImageDraw
import pystray

from poller import NOTICE_TOPIC, PLACEMENT_TOPIC, poll_feeds, send_push
from run_local import POLL_INTERVAL, RUNTIME_DIR, Snapshot


APPS = {"Noticeboard V2": NOTICE_TOPIC, "Placement V2": PLACEMENT_TOPIC}
LOCAL_KEY = RUNTIME_DIR / "firebase-admin.json"


def tray_icon():
    image = Image.new("RGBA", (64, 64))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 61, 61), radius=14, fill="#234766")
    draw.line([(17, 33), (27, 43), (47, 22)], fill="#ffffff", width=6)
    return image


class CheckerWindow:
    def __init__(self, root):
        self.root = root
        self.actions = SimpleQueue()
        self.stop_requested = threading.Event()
        self.polling = False
        self.sending = False
        self.closing = False
        self.feed_names = {"notices": "Noticeboard", "placement_company": "Placement results"}
        self.feed_status = {name: tk.StringVar(value="Waiting to start") for name in self.feed_names}
        self.last_check = tk.StringVar(value="Checks every 5 seconds while running")
        self.test_status = tk.StringVar()
        self.target = tk.StringVar(value="Both apps")
        self.key_status = tk.StringVar(value="Firebase key ready" if LOCAL_KEY.is_file() else "Firebase key needed")

        root.title("Channel i Checker")
        root.geometry("560x460")
        root.minsize(525, 440)
        root.configure(bg="#eef3f7")
        root.protocol("WM_DELETE_WINDOW", root.withdraw)

        styles = ttk.Style(root)
        styles.theme_use("clam")
        styles.configure("Panel.TFrame", background="#ffffff")
        styles.configure("Panel.TLabel", background="#ffffff", foreground="#203546", font=("Segoe UI", 10))
        styles.configure("Section.TLabel", background="#ffffff", foreground="#142b3a", font=("Segoe UI", 11, "bold"))
        styles.configure("Muted.TLabel", background="#ffffff", foreground="#486075", font=("Segoe UI", 9))
        styles.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        styles.configure("Primary.TButton", background="#175b8f", foreground="white", bordercolor="#175b8f")
        styles.map("Primary.TButton", background=[("disabled", "#d8e1e9"), ("active", "#1d6da8")],
                   foreground=[("disabled", "#53697c")])

        header = tk.Frame(root, bg="#17354b", padx=22, pady=16)
        header.pack(fill="x")
        tk.Label(header, text="Channel i Checker", bg="#17354b", fg="white",
                 font=("Segoe UI", 19, "bold")).pack(side="left")
        self.badge = tk.Label(header, bg="#dae6ef", fg="#21394c", font=("Segoe UI", 10, "bold"), padx=10, pady=4)
        self.badge.pack(side="right")
        self.set_status("Paused")

        poll = ttk.Frame(root, style="Panel.TFrame", padding=(16, 12))
        poll.pack(fill="x", padx=18, pady=(14, 8))
        ttk.Label(poll, text="Feed checks", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(poll, textvariable=self.last_check, style="Muted.TLabel").grid(row=0, column=1, sticky="e")
        poll.columnconfigure(0, weight=1)
        for row, (name, label) in enumerate(self.feed_names.items(), start=1):
            ttk.Label(poll, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=(6, 0))
            ttk.Label(poll, textvariable=self.feed_status[name], style="Panel.TLabel").grid(row=row, column=1, sticky="e", pady=(6, 0))
        controls = ttk.Frame(poll, style="Panel.TFrame")
        controls.grid(row=3, column=0, columnspan=2, sticky="w", pady=(12, 0))
        self.start_button = ttk.Button(controls, text="Start checking", style="Primary.TButton", command=self.start)
        self.start_button.pack(side="left", padx=(0, 6))
        self.stop_button = ttk.Button(controls, text="Stop checking", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")

        push = ttk.Frame(root, style="Panel.TFrame", padding=(16, 12))
        push.pack(fill="x", padx=18, pady=(0, 8))
        ttk.Label(push, text="Test notifications", style="Section.TLabel").pack(anchor="w")
        send = ttk.Frame(push, style="Panel.TFrame")
        send.pack(fill="x", pady=(8, 0))
        ttk.Combobox(send, textvariable=self.target, values=("Both apps", *APPS), state="readonly", width=18).pack(side="left")
        self.test_button = ttk.Button(send, text="Send test notification", command=self.send_test)
        self.test_button.pack(side="left", padx=(8, 0))
        ttk.Label(push, textvariable=self.test_status, style="Muted.TLabel", wraplength=470).pack(anchor="w", pady=(6, 0))

        footer = ttk.Frame(root, style="Panel.TFrame", padding=(16, 8))
        footer.pack(fill="x", padx=18, pady=(0, 10))
        ttk.Label(footer, textvariable=self.key_status, style="Muted.TLabel").pack(side="left")
        ttk.Button(footer, text="Configure Firebase key", command=self.configure_key).pack(side="right")

        menu = pystray.Menu(
            pystray.MenuItem("Open", lambda icon, item: self.actions.put((self.show, ())), default=True),
            pystray.MenuItem(
                "Start checking",
                lambda icon, item: self.actions.put((self.start, ())),
                enabled=lambda item: not self.polling and not self.closing,
            ),
            pystray.MenuItem(
                "Stop checking",
                lambda icon, item: self.actions.put((self.stop, ())),
                enabled=lambda item: self.polling and not self.stop_requested.is_set() and not self.closing,
            ),
            pystray.MenuItem("Quit", lambda icon, item: self.actions.put((self.quit, ()))),
        )
        self.tray = pystray.Icon("ChanneliChecker", tray_icon(), "Channel i Checker", menu)
        self.tray.run_detached()
        root.after(120, self.drain)

    def drain(self):
        while True:
            try:
                action, args = self.actions.get_nowait()
            except Empty:
                break
            action(*args)
            if not self.root.winfo_exists():
                return
        self.root.after(120, self.drain)

    def show(self):
        self.root.deiconify()
        self.root.lift()

    def set_status(self, status):
        tones = {"Checking": ("#caeddd", "#175339"), "Stopping": ("#feebc8", "#68470c")}
        background, foreground = tones.get(status, ("#dae6ef", "#21394c"))
        self.badge.configure(text=status, bg=background, fg=foreground)

    def update_feed_status(self, counts):
        for name, label in self.feed_status.items():
            label.set(f"{counts[name]} records" if name in counts else "Waiting to retry")
        self.last_check.set(time.strftime("Last cycle %H:%M:%S"))

    def configure_key(self):
        if self.polling or self.sending:
            messagebox.showinfo("Firebase key", "Stop checking and wait for any test push to finish before changing the key.", parent=self.root)
            return
        selected = filedialog.askopenfilename(
            parent=self.root, title="Choose the Firebase Admin key", filetypes=[("JSON files", "*.json")]
        )
        if not selected:
            return
        try:
            content = Path(selected).read_bytes()
            sender = credentials.Certificate(str(selected))
            try:
                installed = firebase_admin.get_app()
            except ValueError:
                installed = None
            pending = LOCAL_KEY.with_suffix(".tmp")
            pending.write_bytes(content)
            pending.replace(LOCAL_KEY)
            if installed is not None:
                firebase_admin.delete_app(installed)
            firebase_admin.initialize_app(sender, {"httpTimeout": 15})
            self.key_status.set("Firebase key ready")
            self.test_status.set("")
        except (OSError, ValueError):
            messagebox.showerror("Firebase key", "Could not save this Firebase Admin key.", parent=self.root)

    def prepare_sender(self, *, quiet=False):
        try:
            firebase_admin.get_app()
            return True
        except ValueError:
            pass
        try:
            firebase_admin.initialize_app(credentials.Certificate(str(LOCAL_KEY)), {"httpTimeout": 15})
            self.key_status.set("Firebase key ready")
            return True
        except (OSError, ValueError):
            self.key_status.set("Firebase key needed")
            if not quiet:
                self.show()
                messagebox.showinfo("Firebase key", "Use Configure Firebase key once to set up notifications.", parent=self.root)
            return False

    def start(self, *, quiet=False):
        if self.polling or self.closing or not self.prepare_sender(quiet=quiet):
            return
        self.polling = True
        self.stop_requested.clear()
        self.set_status("Checking")
        self.last_check.set("Fetching both feeds...")
        self.start_button.state(["disabled"])
        self.stop_button.state(["!disabled"])
        self.tray.update_menu()
        threading.Thread(target=self.poll_loop, daemon=True).start()

    def poll_loop(self):
        snapshot = Snapshot(RUNTIME_DIR / "poll-state.json")
        status_stop = threading.Event()
        error = False
        try:
            from checker_status import respond

            threading.Thread(
                target=respond, args=(LOCAL_KEY, "checker-status", snapshot, status_stop),
                daemon=True, name="checker-status",
            ).start()
        except ImportError:
            logging.warning("Install requirements.txt to enable Firestore status")
        try:
            while not self.stop_requested.is_set():
                started = time.monotonic()
                poll_feeds(snapshot)
                counts = snapshot.counts()
                self.actions.put((self.update_feed_status, (counts,)))
                self.stop_requested.wait(max(0, POLL_INTERVAL - (time.monotonic() - started)))
        except Exception:
            logging.exception("Checker stopped unexpectedly")
            error = True
        finally:
            status_stop.set()
            self.actions.put((self.poll_finished, (error,)))

    def stop(self):
        if self.polling:
            self.stop_requested.set()
            self.set_status("Stopping")
            self.stop_button.state(["disabled"])
            self.tray.update_menu()

    def poll_finished(self, error):
        self.polling = False
        self.set_status("Paused")
        self.start_button.state(["!disabled"])
        self.stop_button.state(["disabled"])
        self.tray.update_menu()
        if error:
            self.last_check.set("Stopped. See checker.log.")
        self.finish_quit()

    def send_test(self):
        if self.sending or self.closing or not self.prepare_sender():
            return
        app = self.target.get()
        self.sending = True
        self.test_button.state(["disabled"])
        self.test_status.set(f"Sending test to {app.lower() if app == 'Both apps' else app}...")
        threading.Thread(target=self.send_in_background, args=(app,), daemon=True).start()

    def send_in_background(self, selected):
        targets = tuple(APPS) if selected == "Both apps" else (selected,)
        results = []
        for app in targets:
            try:
                send_push(APPS[app], f"{app} test notification", "Sent from your Channel i Checker.", {"type": "test"})
                results.append(f"{app}: accepted by Firebase")
            except Exception:
                logging.exception("Test push failed for %s", app)
                results.append(f"{app}: failed. See checker.log.")
        self.actions.put((self.test_finished, ("\n".join(results),)))

    def test_finished(self, result):
        self.sending = False
        self.test_status.set(result)
        self.test_button.state(["!disabled"])
        self.finish_quit()

    def quit(self):
        self.closing = True
        self.stop()
        self.root.withdraw()
        self.finish_quit()

    def finish_quit(self):
        if self.closing and not self.polling and not self.sending:
            self.tray.stop()
            self.root.quit()


def main():
    startup = "--startup" in sys.argv[1:]
    RUNTIME_DIR.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[RotatingFileHandler(RUNTIME_DIR / "checker.log", maxBytes=512_000, backupCount=2)],
    )
    with (RUNTIME_DIR / "run.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            if not startup:
                messagebox.showinfo("Channel i Checker", "The checker is already running. Look for it in the system tray.")
            return
        root = tk.Tk()
        try:
            if startup:
                root.withdraw()
            window = CheckerWindow(root)
            if startup:
                root.after(15_000, lambda: window.start(quiet=True))
            root.mainloop()
        except Exception:
            logging.exception("Tray startup failed")
            messagebox.showerror("Channel i Checker", "Could not open the tray app. See checker.log.", parent=root)
        finally:
            root.destroy()


if __name__ == "__main__":
    main()
