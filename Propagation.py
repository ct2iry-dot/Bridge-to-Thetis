#!/usr/bin/env python3
"""
Propagation.py — AI-Driven Real-Time Propagation Window v6.4 PRO
══════════════════════════════════════════════════════════════════
Standalone propagation assistant for amateur radio operators.
Displays real-time band conditions driven by:
  • Live spots from Bridge.py (when running alongside it)  ← ANCHOR
  • Telnet DX clusters (up to 6 slots with live LEDs)
  • PSK Reporter feed          (TODO: implement)
  • WSPR network feed          (TODO: implement)
  • HamCAP-style world map with SNR overlay

When Bridge.py is running, this window receives logger-aware spots
over localhost:9877 and enriches the propagation display with
contest-context data (mult status, mode, SNR).

Can also run COMPLETELY STANDALONE — just double-click Propagation.py.

Author : Nuno Lopes CT2IRY
Version: 6.4 PRO
"""

import socket
import threading
import time
import os
import tkinter as tk
from tkinter import ttk
import tkinter.messagebox as messagebox

# ── Optional deps ────────────────────────────────────────────────────────────
try:
    from PIL import Image, ImageTk
    HAS_PILLOW = True
    Image.MAX_IMAGE_PIXELS = None
except ImportError:
    HAS_PILLOW = False

# ── Shared anchor layer ──────────────────────────────────────────────────────
try:
    from shared import (
        SpotEvent, SpotSubscriber,
        DEFAULT_GRID, DEFAULT_REGION, DEFAULT_TX_POWER,
    )
    HAS_SHARED = True
except ImportError:
    HAS_SHARED = False
    # Graceful fallback if shared.py is missing
    DEFAULT_GRID       = "IN51"
    DEFAULT_REGION     = "EU"
    DEFAULT_TX_POWER   = "100"


# ═══════════════════════════════════════════════════════════════════════════
#  RAW DATA WINDOW  (Telnet cluster raw stream viewer)
# ═══════════════════════════════════════════════════════════════════════════
class RawDataWindow(tk.Toplevel):
    def __init__(self, parent: tk.Tk, cluster_name: str):
        super().__init__(parent)
        self.title(f"RAW Data — {cluster_name}")
        self.geometry("900x600")

        self.text = tk.Text(self, font=("Consolas", 10),
                            bg="#0d0d0d", fg="#39ff14")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        send_frame = ttk.Frame(self)
        send_frame.pack(fill="x", padx=10, pady=5)
        self.send_entry = ttk.Entry(send_frame, width=60)
        self.send_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.send_entry.bind("<Return>", lambda e: self.send_command())
        ttk.Button(send_frame, text="Send", command=self.send_command).pack(side="left", padx=5)

        self._sock: socket.socket | None = None

    def bind_socket(self, sock: socket.socket):
        self._sock = sock

    def append(self, data: str):
        self.text.insert("end", data + "\n")
        self.text.see("end")

    def send_command(self):
        cmd = self.send_entry.get().strip()
        if cmd and self._sock:
            try:
                self._sock.sendall((cmd + "\r\n").encode())
                self.append(f">>> {cmd}")
                self.send_entry.delete(0, "end")
            except Exception as e:
                self.append(f"[send error] {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  PROPAGATION CONFIGURATION WINDOW  (Telnet clusters + future feeds)
# ═══════════════════════════════════════════════════════════════════════════
class PropagationConfigWindow(tk.Toplevel):
    """Configuration window for Propagation.py — clusters, PSK Reporter, WSPR."""

    def __init__(self, app: "PropApp"):
        super().__init__(app.root)
        self.app = app
        self.title("Propagation Configuration")
        self.geometry("860x660")

        self.cluster_threads: list[threading.Thread | None] = [None] * 6
        self.cluster_sockets: list[socket.socket | None]   = [None] * 6
        self.raw_windows:     list[RawDataWindow | None]   = [None] * 6
        self.led_labels:      list[tk.Label | None]        = [None] * 6

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        # ── Telnet Cluster tab ────────────────────────────────────────────
        cluster_tab = ttk.Frame(nb)
        nb.add(cluster_tab, text="Telnet Cluster")
        self._build_cluster_tab(cluster_tab)

        # ── PSK Reporter tab (placeholder) ───────────────────────────────
        psk_tab = ttk.Frame(nb)
        nb.add(psk_tab, text="PSK Reporter")
        ttk.Label(psk_tab, text="PSK Reporter real-time feed\n(coming next — needs callsign + band filter)",
                  font=("Segoe UI", 11), foreground="gray").pack(pady=40)

        # ── WSPR tab (placeholder) ────────────────────────────────────────
        wspr_tab = ttk.Frame(nb)
        nb.add(wspr_tab, text="WSPR")
        ttk.Label(wspr_tab, text="WSPR network feed\n(coming next — wsprnet.org API)",
                  font=("Segoe UI", 11), foreground="gray").pack(pady=40)

        # ── Bridge Status tab ─────────────────────────────────────────────
        bridge_tab = ttk.Frame(nb)
        nb.add(bridge_tab, text="Bridge Link")
        self._build_bridge_tab(bridge_tab)

    def _build_cluster_tab(self, parent: ttk.Frame):
        f = ttk.LabelFrame(parent, text="Telnet Cluster Connections  (6 slots)")
        f.pack(fill="both", expand=True, padx=10, pady=10)

        for i in range(6):
            row = ttk.Frame(f)
            row.pack(fill="x", padx=10, pady=8)

            user_var = tk.StringVar(value="CT2IRY")
            ip_var   = tk.StringVar(value="telnet.reversebeacon.net")
            port_var = tk.StringVar(value="7000")
            auto_var = tk.BooleanVar(value=False)

            ttk.Label(row, text=f"#{i+1}", width=3).pack(side="left", padx=4)
            ttk.Label(row, text="User").pack(side="left", padx=4)
            ttk.Entry(row, textvariable=user_var, width=10).pack(side="left", padx=4)
            ttk.Label(row, text="Host").pack(side="left", padx=4)
            ttk.Entry(row, textvariable=ip_var, width=28).pack(side="left", padx=4)
            ttk.Label(row, text="Port").pack(side="left", padx=4)
            ttk.Entry(row, textvariable=port_var, width=7).pack(side="left", padx=4)
            ttk.Checkbutton(row, text="Auto", variable=auto_var).pack(side="left", padx=6)

            ttk.Button(
                row, text="Connect",
                command=lambda i=i, u=user_var, h=ip_var, p=port_var:
                    self.connect_cluster(i, u.get(), h.get(), int(p.get()))
            ).pack(side="left", padx=4)
            ttk.Button(
                row, text="Disconnect",
                command=lambda i=i: self.disconnect_cluster(i)
            ).pack(side="left", padx=4)

            led = ttk.Label(row, text="🔴", font=("Segoe UI", 16))
            led.pack(side="left", padx=10)
            self.led_labels[i] = led

            ttk.Button(
                row, text="RAW",
                command=lambda i=i: self.open_raw_window(i)
            ).pack(side="left", padx=4)

    def _build_bridge_tab(self, parent: ttk.Frame):
        f = ttk.LabelFrame(parent, text="Bridge.py Anchor  (localhost:9877)")
        f.pack(fill="x", padx=15, pady=15)
        self.bridge_status_label = ttk.Label(
            f,
            text="Checking…",
            font=("Segoe UI", 12)
        )
        self.bridge_status_label.pack(pady=20)
        self._update_bridge_status()

    def _update_bridge_status(self):
        if HAS_SHARED and self.app.spot_subscriber.is_connected:
            self.bridge_status_label.config(
                text="🟢 Connected to Bridge.py — receiving logger-aware spots",
                foreground="green"
            )
        else:
            self.bridge_status_label.config(
                text="🔴 Bridge.py not detected — using standalone cluster feeds",
                foreground="gray"
            )
        self.after(3000, self._update_bridge_status)

    # ── Cluster connect / disconnect ──────────────────────────────────────
    def connect_cluster(self, idx: int, user: str, ip: str, port: int):
        if self.cluster_threads[idx] and self.cluster_threads[idx].is_alive():
            return

        def receiver():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(10)
                sock.connect((ip, port))
                self.cluster_sockets[idx] = sock
                self._set_led(idx, "🟢")
                sock.sendall(f"{user}\r\n".encode())
                self.app.log(f"Cluster {idx+1}: connected to {ip}:{port}")

                if self.raw_windows[idx] and self.raw_windows[idx].winfo_exists():
                    self.raw_windows[idx].bind_socket(sock)

                buf = ""
                while True:
                    data = sock.recv(4096).decode(errors="ignore")
                    if not data:
                        break
                    buf += data
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        line = line.strip()
                        if line:
                            self.app.log(f"Cluster {idx+1}: {line[:80]}")
                            if self.raw_windows[idx] and self.raw_windows[idx].winfo_exists():
                                self.raw_windows[idx].append(line)
                            # ── Parse and ingest spot ─────────────────────
                            spot = self._parse_cluster_line(line, idx)
                            if spot:
                                self.app.ingest_spot(spot)
            except Exception as e:
                self.app.log(f"Cluster {idx+1} error: {e}")
            finally:
                self.cluster_sockets[idx] = None
                self._set_led(idx, "🔴")
                self.app.log(f"Cluster {idx+1}: disconnected")

        t = threading.Thread(target=receiver, daemon=True)
        self.cluster_threads[idx] = t
        t.start()

    def disconnect_cluster(self, idx: int):
        if self.cluster_sockets[idx]:
            try:
                self.cluster_sockets[idx].close()
            except Exception:
                pass
            self.cluster_sockets[idx] = None
        self._set_led(idx, "🔴")
        self.app.log(f"Cluster {idx+1}: disconnected by user")

    def _set_led(self, idx: int, icon: str):
        if self.led_labels[idx]:
            try:
                self.led_labels[idx].config(text=icon)
            except Exception:
                pass

    def open_raw_window(self, idx: int):
        if not self.raw_windows[idx] or not self.raw_windows[idx].winfo_exists():
            self.raw_windows[idx] = RawDataWindow(self.app.root, f"Cluster {idx+1}")
            if self.cluster_sockets[idx]:
                self.raw_windows[idx].bind_socket(self.cluster_sockets[idx])
        self.raw_windows[idx].lift()

    @staticmethod
    def _parse_cluster_line(line: str, idx: int) -> "SpotEvent | None":
        """
        Parse a standard DX cluster spot line.
        Format: DX de <spotter>: <freq> <callsign> <comment> <time>Z
        TODO: handle RBN / skimmer formats, extract SNR from comment.
        """
        try:
            if not line.startswith("DX de"):
                return None
            # Basic parser — refine per cluster flavour
            parts = line.split()
            spotter  = parts[2].rstrip(":")
            freq     = float(parts[3])
            callsign = parts[4]
            comment  = " ".join(parts[5:-1]) if len(parts) > 6 else ""
            snr = None
            # RBN lines have "dB" in comment: extract SNR
            for part in comment.split():
                if part.endswith("dB"):
                    try:
                        snr = int(part[:-2])
                    except ValueError:
                        pass
            return SpotEvent(
                freq      = freq,
                callsign  = callsign,
                mode      = "CW",
                snr       = snr,
                spotter   = spotter,
                comment   = comment,
                timestamp = time.time(),
                source    = f"Cluster{idx+1}",
                color_key = "new",
            ) if HAS_SHARED else None
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════
#  MAIN PROPAGATION WINDOW  (1800 × 1100, fixed)
# ═══════════════════════════════════════════════════════════════════════════
class PropagationWindow(ttk.Frame):
    """
    The main propagation display.
    Embedded in PropApp's root window.
    """

    def __init__(self, parent: tk.Tk, app: "PropApp"):
        super().__init__(parent)
        self.app = app
        self.pack(fill="both", expand=True)
        self._build()

    def _build(self):
        # ── Top controls ──────────────────────────────────────────────────
        ctrl = ttk.LabelFrame(self, text="Parameters & Map Selection")
        ctrl.pack(fill="x", pady=(0, 8), padx=12)

        ttk.Label(ctrl, text="My Grid Square").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        ttk.Entry(ctrl, textvariable=self.app.my_grid, width=12).grid(row=0, column=1, padx=10, pady=8)

        ttk.Label(ctrl, text="Target Region").grid(row=0, column=2, sticky="w", padx=10, pady=8)
        ttk.Combobox(ctrl, textvariable=self.app.target_region,
                     values=["EU", "NA", "JA", "SA", "AF", "OC", "All"],
                     state="readonly", width=8).grid(row=0, column=3, padx=10, pady=8)

        ttk.Label(ctrl, text="TX Power (W)").grid(row=0, column=4, sticky="w", padx=10, pady=8)
        ttk.Entry(ctrl, textvariable=self.app.tx_power, width=10).grid(row=0, column=5, padx=10, pady=8)

        ttk.Button(ctrl, text="🔄 Refresh AI",
                   command=self.app.update_ai_suggestion).grid(row=0, column=6, padx=12, pady=8)

        # Bridge status indicator
        self.bridge_lbl = ttk.Label(ctrl, text="⬤ Bridge: unknown",
                                    font=("Segoe UI", 10))
        self.bridge_lbl.grid(row=0, column=7, padx=16, pady=8)

        ttk.Label(ctrl, text="Map:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
        self.map_combo = ttk.Combobox(ctrl, width=50, state="readonly")
        self.map_combo.grid(row=1, column=1, columnspan=5, padx=10, pady=8, sticky="ew")
        self.map_combo.bind("<<ComboboxSelected>>", self.load_selected_map)

        ttk.Button(ctrl, text="Refresh List",
                   command=self.refresh_map_list).grid(row=1, column=6, padx=8, pady=8)
        ttk.Button(ctrl, text="Open Maps Folder",
                   command=self.app.open_maps_folder).grid(row=1, column=7, padx=8, pady=8)

        # ── Live Spot Feed (top-right panel) ─────────────────────────────
        content = ttk.Frame(self)
        content.pack(fill="both", expand=True, padx=12)

        # Map area (left, fills)
        map_frame = ttk.LabelFrame(content, text="HamCAP-Style Propagation Map")
        map_frame.pack(side="left", fill="both", expand=True, pady=8)
        self.map_label = tk.Label(map_frame,
                                  text="No map loaded.\nPlace .jpg/.png files in the Maps/ folder,\nthen click Refresh List.",
                                  font=("Segoe UI", 12), fg="gray")
        self.map_label.pack(fill="both", expand=True)

        # Spot sidebar (right, fixed width)
        sidebar = ttk.Frame(content, width=320)
        sidebar.pack(side="right", fill="y", padx=(8, 0), pady=8)
        sidebar.pack_propagate(False)

        spot_frame = ttk.LabelFrame(sidebar, text="Live Spots (SNR)")
        spot_frame.pack(fill="both", expand=True)
        self.spot_tree = ttk.Treeview(
            spot_frame,
            columns=("time", "call", "freq", "mode", "snr", "src"),
            show="headings", height=20
        )
        for col, w, label in [
            ("time", 60,  "Time"),
            ("call", 80,  "Call"),
            ("freq", 70,  "kHz"),
            ("mode", 50,  "Mode"),
            ("snr",  45,  "SNR"),
            ("src",  70,  "Source"),
        ]:
            self.spot_tree.heading(col, text=label)
            self.spot_tree.column(col, width=w, anchor="center")
        sb = ttk.Scrollbar(spot_frame, orient="vertical",
                           command=self.spot_tree.yview)
        self.spot_tree.configure(yscrollcommand=sb.set)
        self.spot_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # ── Smart AI Band Suggestion (always visible at bottom) ───────────
        ai_frame = ttk.LabelFrame(self, text="Smart AI Band Suggestion")
        ai_frame.pack(fill="x", padx=12, pady=(0, 10))
        self.ai_tree = ttk.Treeview(
            ai_frame,
            columns=("band", "score", "direction", "comment"),
            show="headings", height=5
        )
        for col, w, label in [
            ("band",      60,  "Band"),
            ("score",     80,  "Reliability"),
            ("direction", 100, "Direction"),
            ("comment",   500, "Reason"),
        ]:
            self.ai_tree.heading(col, text=label)
            self.ai_tree.column(col, width=w)
        self.ai_tree.pack(fill="x", padx=10, pady=8)

        # Kick off periodic bridge status poll
        self._poll_bridge_status()
        self.refresh_map_list()

    # ── Bridge status indicator ───────────────────────────────────────────
    def _poll_bridge_status(self):
        if HAS_SHARED and self.app.spot_subscriber.is_connected:
            self.bridge_lbl.config(text="⬤ Bridge: 🟢 Connected", foreground="green")
        else:
            self.bridge_lbl.config(text="⬤ Bridge: 🔴 Standalone", foreground="gray")
        self.after(3000, self._poll_bridge_status)

    # ── Map handling ──────────────────────────────────────────────────────
    def refresh_map_list(self):
        maps_dir = self.app.maps_dir()
        files = [f for f in os.listdir(maps_dir)
                 if f.lower().endswith(('.jpg', '.jpeg', '.png', '.gif'))]
        self.map_combo['values'] = files
        if files:
            self.map_combo.set(files[0])
            self.load_selected_map(None)

    def load_selected_map(self, _event):
        selected = self.map_combo.get()
        if not selected:
            return
        path = os.path.join(self.app.maps_dir(), selected)
        try:
            if HAS_PILLOW:
                img = Image.open(path)
                target_w = 1460
                ratio     = target_w / img.width
                target_h  = int(img.height * ratio)
                img       = img.resize((target_w, target_h), Image.LANCZOS)
                self._map_photo = ImageTk.PhotoImage(img)
            else:
                self._map_photo = tk.PhotoImage(file=path)
            self.map_label.config(image=self._map_photo, text="")
            self.app.log(f"Map loaded: {selected}")
        except Exception as e:
            self.map_label.config(image="", text=f"Error loading {selected}\n{e}")
            self.app.log(f"Map load error: {e}")

    # ── Spot display ──────────────────────────────────────────────────────
    def add_spot_row(self, spot: "SpotEvent"):
        t = time.strftime("%H:%M", time.localtime(spot.timestamp))
        snr_str = f"{spot.snr:+d}dB" if spot.snr is not None else "—"
        self.spot_tree.insert(
            "", 0,
            values=(t, spot.callsign, f"{spot.freq:.1f}",
                    spot.mode, snr_str, spot.source)
        )
        # Keep last 200 rows
        rows = self.spot_tree.get_children()
        if len(rows) > 200:
            self.spot_tree.delete(rows[-1])

    # ── AI suggestion table ───────────────────────────────────────────────
    def refresh_ai_table(self, rows: list[tuple]):
        self.ai_tree.delete(*self.ai_tree.get_children())
        for row in rows:
            self.ai_tree.insert("", "end", values=row)


# ═══════════════════════════════════════════════════════════════════════════
#  APPLICATION  (standalone Tk root)
# ═══════════════════════════════════════════════════════════════════════════
class PropApp:
    """
    Standalone entry point for Propagation.py.
    Creates the Tk root, the PropagationWindow frame, and all services.
    """

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Propagation Assistant v6.4 — Live")
        self.root.geometry("1800x1100")
        self.root.resizable(False, False)

        # ── State vars ────────────────────────────────────────────────────
        self.my_grid       = tk.StringVar(value=DEFAULT_GRID)
        self.target_region = tk.StringVar(value=DEFAULT_REGION)
        self.tx_power      = tk.StringVar(value=DEFAULT_TX_POWER)

        # ── Menu ──────────────────────────────────────────────────────────
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        menubar.add_command(label="Configuration", command=self._open_config)
        menubar.add_command(label="Debug",         command=self._open_debug)
        menubar.add_command(label="Help",          command=self._show_help)
        menubar.add_command(label="About",         command=self._show_about)

        # ── Spot subscriber (Bridge anchor) ───────────────────────────────
        if HAS_SHARED:
            self.spot_subscriber = SpotSubscriber(
                on_spot   = self._on_bridge_spot,
                on_status = self.log,
            )
            self.spot_subscriber.start()
        else:
            self.spot_subscriber = None

        # ── Sub-windows ───────────────────────────────────────────────────
        self._config_win = None
        self._debug_win  = None
        self._debug_text = []

        # ── Main UI ───────────────────────────────────────────────────────
        self.window = PropagationWindow(self.root, self)

        # ── Ensure Maps folder exists ─────────────────────────────────────
        os.makedirs(self.maps_dir(), exist_ok=True)

    # ── Bridge spot callback ──────────────────────────────────────────────
    def _on_bridge_spot(self, spot: "SpotEvent"):
        """Called from SpotSubscriber thread — schedule GUI update safely."""
        self.root.after(0, self.ingest_spot, spot)

    def ingest_spot(self, spot: "SpotEvent"):
        """Ingest a spot from ANY source and update the display."""
        self.window.add_spot_row(spot)
        # TODO: update map overlay, SNR heatmap, etc.

    # ── AI band suggestion ────────────────────────────────────────────────
    def update_ai_suggestion(self):
        """
        Generate band recommendations.
        TODO: replace with real AI model using accumulated spot SNR data,
              solar indices (A/K/SFI), and time-of-day propagation models.
        """
        rows = [
            ("160m", "45%",  self.target_region.get(), "Night path, check K-index"),
            ("80m",  "70%",  self.target_region.get(), "Good evening path"),
            ("40m",  "85%",  self.target_region.get(), "Strong — many spots"),
            ("20m",  "90%",  self.target_region.get(), "Best band — high SNR cluster activity"),
            ("15m",  "65%",  self.target_region.get(), "Moderate — monitor SFI"),
            ("10m",  "30%",  self.target_region.get(), "Marginal — solar-dependent"),
        ]
        self.window.refresh_ai_table(rows)
        self.log("AI suggestion refreshed")

    # ── Maps folder ───────────────────────────────────────────────────────
    def maps_dir(self) -> str:
        d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Maps")
        os.makedirs(d, exist_ok=True)
        return d

    def open_maps_folder(self):
        d = self.maps_dir()
        if os.name == "nt":
            os.startfile(d)
        elif os.name == "posix":
            import subprocess
            subprocess.Popen(["xdg-open", d])
        self.log(f"Opened Maps folder: {d}")

    # ── Config window ─────────────────────────────────────────────────────
    def _open_config(self):
        if self._config_win and self._config_win.winfo_exists():
            self._config_win.lift()
        else:
            self._config_win = PropagationConfigWindow(self)

    # ── Debug window ──────────────────────────────────────────────────────
    def _open_debug(self):
        if self._debug_win and self._debug_win.winfo_exists():
            self._debug_win.lift()
        else:
            self._debug_win = DebugWindowProp(self.root)
            for msg in self._debug_text[-200:]:
                self._debug_win.append(msg)

    def log(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        full = f"[{timestamp}] {msg}"
        print(full)
        self._debug_text.append(full)
        if self._debug_win and self._debug_win.winfo_exists():
            self._debug_win.append(full)

    # ── Help / About ──────────────────────────────────────────────────────
    def _show_help(self):
        messagebox.showinfo(
            "Help",
            "Propagation.py — AI Propagation Window\n\n"
            "• Connects to Bridge.py for logger-aware spots (localhost:9877)\n"
            "• Also pulls spots from Telnet clusters (up to 6 slots)\n"
            "• PSK Reporter + WSPR feeds coming soon\n"
            "• Place .jpg/.png maps in the Maps/ folder\n\n"
            "Run standalone or alongside Bridge.py."
        )

    def _show_about(self):
        messagebox.showinfo(
            "About",
            "Propagation Assistant v6.4 PRO\n"
            "Author: Nuno Lopes CT2IRY\n\n"
            "Part of the DXLog–Thetis Bridge Suite.\n"
            "Companion: Bridge.py"
        )

    def run(self):
        self.root.mainloop()


# ═══════════════════════════════════════════════════════════════════════════
#  LIGHTWEIGHT DEBUG WINDOW (standalone version)
# ═══════════════════════════════════════════════════════════════════════════
class DebugWindowProp(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Debug Console — Propagation")
        self.geometry("900x500")
        self.text = tk.Text(self, font=("Consolas", 10),
                            bg="#1e1e1e", fg="#dcdcdc")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        ttk.Button(self, text="Clear",
                   command=lambda: self.text.delete("1.0", "end")).pack(side="bottom", pady=5)

    def append(self, msg: str):
        self.text.insert("end", msg + "\n")
        self.text.see("end")


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    if not HAS_PILLOW:
        print("⚠️  Pillow not found — map display limited to .gif/.png via tkinter.")
        print("   pip install Pillow")
    if not HAS_SHARED:
        print("⚠️  shared.py not found — Bridge anchor disabled. Running fully standalone.")

    app = PropApp()
    app.run()
