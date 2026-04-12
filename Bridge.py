#!/usr/bin/env python3
# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportAttributeAccessIssue=false, reportUnusedFunction=false
"""
Bridge.py  v7.0  CT2IRY  —  DXLab Edition
Commander WaterfallBandmap :13063  →  Thetis TCI WebSocket :50001

Workflow:
  Commander UDP :13063  →  Bridge  →  Thetis TCI WS :50001
  (WaterfallBandmap spots)            (paint spots on panadapter)

Enrichment (all auto-discovered from Windows registry — no user config needed):
  fontcolor from Commander → foreground color (worked/mult status from SpotCollector)
  DXView LoTW/eQSL .mdb   → background color per callsign
  BigCTY.csv (DXView)     → country, continent, beam heading in comment
  SC PaneColor3/4/8/9     → bg colors follow SpotCollector user settings

Commander ↔ Thetis radio control (VFO, mode, split, spot-click QSY) is handled
natively by Commander connecting to Thetis TCP server on port 13013.
Bridge is spot-painting only.
"""

import threading, re, os, time, json
from typing import Any
import tkinter as tk
from tkinter import ttk
import tkinter.messagebox as messagebox

try:
    import websocket
except ImportError:
    websocket = None  # type: ignore
HAS_WEBSOCKET = websocket is not None

try:
    from commander_spots import CommanderSpotsListener
except ImportError:
    CommanderSpotsListener = None  # type: ignore
HAS_CDR_SPOTS = CommanderSpotsListener is not None

try:
    from dxview_db import DXViewCache
except ImportError:
    DXViewCache = None  # type: ignore
HAS_DXVIEW = DXViewCache is not None

try:
    from cty_parser import CTYDatabase
except ImportError:
    CTYDatabase = None  # type: ignore
HAS_CTY = CTYDatabase is not None

try:
    from band_modes import BandModesMap
except ImportError:
    BandModesMap = None  # type: ignore
HAS_BAND_MODES = BandModesMap is not None

try:
    from flex_server import FlexServer
except ImportError:
    FlexServer = None  # type: ignore
HAS_FLEX = FlexServer is not None

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _vb6_to_hex(v: int) -> str:
    """VB6 OLE_COLOR (BGR signed long) → '#RRGGBB'."""
    v &= 0xFFFFFF
    return "#{:02X}{:02X}{:02X}".format(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)

def hex_to_argb(hex_color: str) -> int:
    """Convert #RRGGBB to unsigned ARGB int (alpha=0xFF) for Thetis TCI spot command.
    Thetis parses with uint.TryParse then Color.FromArgb(int) — alpha must be 0xFF for opaque."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0xFF << 24) | (r << 16) | (g << 8) | b

def load_sc_bg_colors() -> "dict[str, str]":
    """
    Read LoTW/eQSL background colors from SpotCollector registry.
      PaneColor3 = bg_normal    (white  #FFFFFF)
      PaneColor4 = bg_lotw      (yellow #FFFF00)
      PaneColor8 = bg_lotw_eqsl (silver #C0C0C0)
      PaneColor9 = bg_eqsl      (cyan   #D7FFFF)
    Falls back to SC defaults if not found.
    """
    colors = {
        "bg_normal":    "#FFFFFF",
        "bg_lotw":      "#FFFF00",
        "bg_eqsl":      "#D7FFFF",
        "bg_lotw_eqsl": "#C0C0C0",
    }
    try:
        import winreg
        key = r"Software\VB and VBA Program Settings\SpotCollector\Spot"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            for idx, name in {3: "bg_normal", 4: "bg_lotw", 8: "bg_lotw_eqsl", 9: "bg_eqsl"}.items():
                try:
                    val, _ = winreg.QueryValueEx(k, "PaneColor{}".format(idx))
                    colors[name] = _vb6_to_hex(int(val))
                except (OSError, ValueError):
                    pass
    except Exception:
        pass
    return colors

def _dxlab_dms_to_decimal(deg: str, mins: str, secs: str, sign: str) -> float:
    """
    Convert DXLab DMS registry values to decimal degrees.
    DXLab convention (confirmed from registry inspection):
      LatSign = -1 → North (positive),  LatSign = +1 → South (negative)
      LonSign = -1 → West  (negative),  LonSign = +1 → East  (positive)
    Formula: lat = -LatSign × DMS,  lon = LonSign × DMS
    """
    try:
        d = float(deg); m = float(mins); s = float(secs); sg = int(sign)
        return sg * (d + m / 60.0 + s / 3600.0)
    except (ValueError, TypeError):
        return 0.0


def _read_sc_qth() -> "tuple[float, float, str]":
    """
    Read operator QTH (lat, lon, grid) from DXLab registry.

    DXLab stores QTH as DMS + sign under the \\QTH subkey of each app:
      HKCU\\...\\SpotCollector\\QTH  (or DXView\\QTH, DXKeeper\\QTH)
      LatDeg, LatMin, LatSec, LatSign, LonDeg, LonMin, LonSec, LonSign

    Grid square from WinWarbler\\Position\\MyGrid.
    Returns (lat_decimal, lon_decimal, grid_square).
    """
    try:
        import winreg

        _ROOT = r"Software\VB and VBA Program Settings"

        def _v(k, name, default="0"):
            try:
                v, _ = winreg.QueryValueEx(k, name)
                return str(v).strip()
            except OSError:
                return default

        def _read_dms(app: str) -> "tuple[float, float]":
            """Read DMS QTH from one DXLab app's registry. Returns (lat, lon)."""
            path = _ROOT + "\\" + app + "\\QTH"
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                    lat = _dxlab_dms_to_decimal(
                        _v(k, "LatDeg"), _v(k, "LatMin"), _v(k, "LatSec"),
                        _v(k, "LatSign", "0"))
                    # lat formula: -LatSign × DMS  (sign=-1 → North = positive)
                    lat = -lat
                    lon = _dxlab_dms_to_decimal(
                        _v(k, "LonDeg"), _v(k, "LonMin"), _v(k, "LonSec"),
                        _v(k, "LonSign", "0"))
                    # lon formula: LonSign × DMS  (sign=-1 → West = negative)
                    return lat, lon
            except OSError:
                return 0.0, 0.0

        # Grid square — WinWarbler\Position\MyGrid is the most reliable source
        grid = ""
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                _ROOT + r"\WinWarbler\Position") as k:
                grid = _v(k, "MyGrid", "")
        except OSError:
            pass

        # Try each DXLab app's QTH subkey (SpotCollector first as most relevant)
        for app in ("SpotCollector", "DXView", "DXKeeper"):
            lat, lon = _read_dms(app)
            if lat != 0.0 or lon != 0.0:
                print("[QTH] {}: {:.4f}°, {:.4f}°  grid={}".format(app, lat, lon, grid or "?"))
                return lat, lon, grid

    except Exception as e:
        print("[QTH] Registry error: {}".format(e))

    return 0.0, 0.0, ""

def _bearing_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> "tuple[int, int]":
    """Return (bearing_deg, distance_km) from point 1 to point 2."""
    import math
    R = 6371
    la1, lo1, la2, lo2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlo = lo2 - lo1
    x = math.sin(dlo) * math.cos(la2)
    y = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dlo)
    brg = (math.degrees(math.atan2(x, y)) + 360) % 360
    a = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return round(brg), round(R * 2 * math.asin(math.sqrt(a)))

# Mode string → TCI mode string
# Covers both Commander XML mode names and SpotCollector BandModes.txt names
DXLAB_MODE_MAP = {
    "CW": "CW", "CW-R": "CWL", "CWR": "CWL",
    "USB": "USB", "LSB": "LSB", "AM": "AM", "FM": "FM",
    "DIGU": "DIGU", "DIGL": "DIGL",
    "RTTY": "DIGU", "RTTYR": "DIGL",
    "PSK31": "DIGU", "PSK63": "DIGU", "PSK": "DIGU",
    "FT8": "DIGU", "FT4": "DIGU", "FT2": "DIGU",
    "JT65": "DIGU", "JT9": "DIGU",
    "WSPR": "DIGU", "JS8": "DIGU", "Q65": "DIGU",
    "MSK144": "DIGU", "PKT": "DIGU", "HELL": "DIGU",
}

# Ham band edges in Hz — used for band filter
_BAND_EDGES = [
    (1_800_000,   2_000_000),
    (3_500_000,   4_000_000),
    (5_330_500,   5_403_500),
    (7_000_000,   7_300_000),
    (10_100_000, 10_150_000),
    (14_000_000, 14_350_000),
    (18_068_000, 18_168_000),
    (21_000_000, 21_450_000),
    (24_890_000, 24_990_000),
    (28_000_000, 29_700_000),
    (50_000_000, 54_000_000),
    (144_000_000,148_000_000),
]

def _same_band(freq_a: int, freq_b: int) -> bool:
    """Return True if both frequencies fall within the same ham band."""
    for lo, hi in _BAND_EDGES:
        a_in = lo <= freq_a <= hi
        b_in = lo <= freq_b <= hi
        if a_in and b_in:
            return True
        if a_in or b_in:
            return False  # one inside, one outside — different bands
    return False

def resolve_cw_mode(freq_hz: int) -> str:
    """CW sideband by standard band convention: CWL at/below 10 MHz, CWU above.
    Thetis handles the actual sideband selection when the user clicks a spot."""
    return "CWL" if freq_hz <= 10_000_000 else "CW"

# ─────────────────────────────────────────────────────────────────────────────
# TCI CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class TCIClient:
    RECONNECT_DELAY = 5
    PING_INTERVAL   = 3

    STATE_CLOSED     = "closed"
    STATE_CONNECTING = "connecting"
    STATE_READY      = "ready"
    STATE_ERROR      = "error"

    def __init__(self, host, port, on_message=None, on_status=None, label="R1"):
        self.host = host; self.port = port; self.label = label
        self.on_message = on_message; self.on_status = on_status
        self._ws = None; self._running = False; self._ready = False
        self._lock = threading.Lock()
        self.state = self.STATE_CLOSED
        self.latency_ms = -1
        self._ping_sent = 0.0
        self.vfo_hz = 14025000

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="tci-" + self.label).start()

    def stop(self):
        self._running = False; self.state = self.STATE_CLOSED; self.latency_ms = -1
        if self._ws:
            try: self._ws.close()
            except: pass

    @property
    def ready(self): return self._ready

    def send(self, cmd: str) -> bool:
        with self._lock:
            if self._ws and self._ready:
                try: self._ws.send(cmd); return True
                except: pass
        return False

    def send_spot(self, callsign, mode, freq_hz, color_hex,
                  spotter="", comment="", extended=True, text_color_hex=None,
                  country="", continent="", heading=0) -> bool:
        argb = hex_to_argb(color_hex)
        if extended:
            # TextColor: send as #RRGGBB — Thetis applies alpha=255 automatically.
            # Empty string = no text color override (Thetis default).
            tcolor = text_color_hex if text_color_hex else ""
            # Exact PascalCase field names from Thetis SpotManager2.JsonSpotData.
            # Continent exists in JsonSpotData but is never read by AddSpot — omitted.
            payload = json.dumps({
                "Spotter":          spotter,
                "Comment":          comment,
                "Heading":          heading,   # int, -1 = no heading
                "Country":          country,
                "UtcTime":          time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "TextColor":        tcolor,
                "IsSWL":            False,
                "SWLSecondsToLive": 0,
            }, separators=(",", ":"))
            cmd = "spot:{},{},{},{},[json]{};".format(callsign, mode, freq_hz, argb, payload)
        else:
            cmd = "spot:{},{},{},{};".format(callsign, mode, freq_hz, argb)
        return self.send(cmd)

    def _ping_loop(self):
        while self._running:
            if self._ready:
                with self._lock:
                    ws = self._ws
                if ws:
                    try:
                        self._ping_sent = time.perf_counter()
                        ws.send("audio_samplerate:0;")
                    except: pass
            time.sleep(self.PING_INTERVAL)

    def _loop(self):
        if websocket is None:
            print("[TCI] websocket-client not installed"); return
        url = "ws://{}:{}".format(self.host, self.port)
        threading.Thread(target=self._ping_loop, daemon=True,
                         name="tci-ping-" + self.label).start()
        while self._running:
            try:
                ws = websocket.WebSocketApp(url,
                    on_open=self._on_open, on_message=self._on_message,
                    on_error=self._on_error, on_close=self._on_close)
                with self._lock: self._ws = ws
                ws.run_forever()
            except: pass
            if self._running: time.sleep(self.RECONNECT_DELAY)

    def _on_open(self, ws):
        self._ready = False; self.state = self.STATE_CONNECTING; self.latency_ms = -1
        if self.on_status: self.on_status(self.label, "connecting")

    def _on_message(self, ws, message):
        msg = message.strip()
        if self._ping_sent > 0 and msg.lower().startswith("audio_samplerate:"):
            self.latency_ms = round((time.perf_counter() - self._ping_sent) * 1000)
            self._ping_sent = 0.0
        if msg.lower() == "ready;":
            self._ready = True; self.state = self.STATE_READY
            if self.on_status: self.on_status(self.label, "ready")
        m = re.match(r"vfo_frequency:(\d+),(\d+),(\d+);", msg, re.I)
        if m and int(m.group(1)) == 0: self.vfo_hz = int(m.group(3))
        if self.on_message: self.on_message(self.label, msg)

    def _on_error(self, ws, e):
        self._ready = False; self.state = self.STATE_ERROR; self.latency_ms = -1
        if self.on_status: self.on_status(self.label, "error")

    def _on_close(self, ws, *a):
        self._ready = False; self.state = self.STATE_CLOSED; self.latency_ms = -1
        if self.on_status: self.on_status(self.label, "disconnected")

# ─────────────────────────────────────────────────────────────────────────────
# DEBUG WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class DebugWindow(tk.Toplevel):
    MAX_LINES = 5000

    def __init__(self, app):
        super().__init__(app); self.app = app
        self.title("Bridge Debug Log"); self.geometry("900x480")
        tb = ttk.Frame(self); tb.pack(fill="x", padx=4, pady=4)
        ttk.Button(tb, text="Clear Log",           command=self.clear).pack(side="left", padx=4)
        ttk.Button(tb, text="Clear Spots (Thetis)",command=app.clear_all_spots).pack(side="left", padx=4)
        ttk.Button(tb, text="Send Test Spot",       command=app.send_test_spot).pack(side="left", padx=4)
        ttk.Button(tb, text="Reconnect TCI",        command=app.reconnect_tci).pack(side="left", padx=4)
        self.text = tk.Text(self, font=("Consolas", 10), bg="#1e1e1e", fg="#dcdcdc", state="disabled")
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.text.tag_config("tci",   foreground="#4fc3f7")
        self.text.tag_config("spot",  foreground="#a5d6a7")
        self.text.tag_config("dxlab", foreground="#ce93d8")
        self.text.tag_config("warn",  foreground="#ffcc02")
        self.text.tag_config("error", foreground="#ef9a9a")

    def append(self, msg, tag=""):
        self.text.configure(state="normal")
        self.text.insert("end", msg + "\n", tag or "")
        n = int(self.text.index("end-1c").split(".")[0])
        if n > self.MAX_LINES:
            self.text.delete("1.0", "{}.0".format(n - self.MAX_LINES + 1))
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class ConfigWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app); self.app = app
        self.title("Bridge Configuration"); self.resizable(True, True)
        nb = ttk.Notebook(self); nb.pack(fill="both", expand=True, padx=10, pady=10)
        self._net_tab   = ttk.Frame(nb)
        self._color_tab = ttk.Frame(nb)
        self._misc_tab  = ttk.Frame(nb)
        nb.add(self._net_tab,   text="  Network  ")
        nb.add(self._color_tab, text="  Spot Colors  ")
        nb.add(self._misc_tab,  text="  Misc  ")
        self._build_net()
        self._build_colors()
        self._build_misc()
        self.update_idletasks()
        req_w = max(720, self._net_tab.winfo_reqwidth() + 40)
        req_h = self._net_tab.winfo_reqheight() + 120
        self.geometry("{}x{}".format(req_w, min(req_h, self.winfo_screenheight() - 80)))

    @staticmethod
    def _row(p, row, lbl, var, width=18, hint=""):
        ttk.Label(p, text=lbl, width=24, anchor="w").grid(row=row, column=0, padx=8, pady=3, sticky="w")
        ttk.Entry(p, textvariable=var, width=width).grid(row=row, column=1, padx=4, sticky="w")
        if hint:
            ttk.Label(p, text=hint, font=("Segoe UI", 8), foreground="#666").grid(
                row=row, column=2, padx=8, sticky="w")

    @staticmethod
    def _iprow(p, row, lbl, ip_v, port_v, hint=""):
        ttk.Label(p, text=lbl, width=24, anchor="w").grid(row=row, column=0, padx=8, pady=3, sticky="w")
        ttk.Entry(p, textvariable=ip_v, width=16).grid(row=row, column=1, padx=2, sticky="w")
        ttk.Label(p, text=":").grid(row=row, column=2)
        ttk.Entry(p, textvariable=port_v, width=7).grid(row=row, column=3, padx=2, sticky="w")
        if hint:
            ttk.Label(p, text=hint, font=("Segoe UI", 8), foreground="#666").grid(
                row=row, column=4, padx=8, sticky="w")

    def _build_net(self):
        app = self.app; p = self._net_tab

        # Thetis TCI
        tci = ttk.LabelFrame(p, text="Thetis TCI  (spot painting)")
        tci.pack(fill="x", padx=14, pady=(10, 4))
        self._row(tci, 0, "TCI Host", app.tci_host, hint="e.g. 127.0.0.1 or 192.168.x.x")
        self._row(tci, 1, "TCI Port", app.tci_port, width=8, hint="Default 50001")
        ttk.Button(tci, text="Reconnect", command=app.reconnect_tci).grid(
            row=2, column=0, columnspan=3, sticky="w", padx=8, pady=6)

        # Commander WaterfallBandmap spot feed
        wbm = ttk.LabelFrame(p, text="Commander WaterfallBandmap  (real-time spots  UDP :13063)")
        wbm.pack(fill="x", padx=14, pady=4)
        ttk.Label(wbm,
            text="Commander sends real-time spots on UDP :13063 (WaterfallBandmap plugin).\n"
                 "Bridge paints them on the Thetis panadapter with LoTW/eQSL background colors.",
            font=("Segoe UI", 8), foreground="#004400", justify="left").grid(
            row=0, column=0, columnspan=5, padx=10, pady=(6, 4), sticky="w")
        self._iprow(wbm, 1, "Listen Address", app.cdr_spots_ip, app.cdr_spots_port,
            "Default 127.0.0.1 : 13063")
        def _apply_cdr():
            app.save_config(); app._start_cdr_spots()
        ttk.Button(wbm, text="Apply & Restart", command=_apply_cdr).grid(
            row=2, column=0, columnspan=5, sticky="w", padx=8, pady=6)

        # Flex SmartSDR server — hidden until fully working
        # To re-enable: change flx.pack_forget() to flx.pack(fill="x", padx=14, pady=4)
        flx = ttk.LabelFrame(p, text="Flex SmartSDR  (Stage 1 — fake Flex radio  TCP :4992)")
        ttk.Label(flx,
            text="Configure Commander as a Flex Signature radio pointing to Bridge.\n"
                 "Bridge intercepts spot add/remove directives and paints them on Thetis.\n"
                 "All other SmartSDR commands are ACK'd and ignored.",
            font=("Segoe UI", 8), foreground="#440044", justify="left").grid(
            row=0, column=0, columnspan=5, padx=10, pady=(6, 4), sticky="w")
        ttk.Checkbutton(flx, text="Enable Flex SmartSDR server",
            variable=app.flex_enable).grid(row=1, column=0, columnspan=5, sticky="w", padx=8, pady=(2, 2))
        ttk.Label(flx, text="TCP Port", width=24, anchor="w").grid(
            row=2, column=0, padx=8, pady=3, sticky="w")
        ttk.Entry(flx, textvariable=app.flex_port, width=8).grid(
            row=2, column=1, padx=2, sticky="w")
        ttk.Label(flx, text="Default 4992", font=("Segoe UI", 8),
            foreground="#666").grid(row=2, column=2, padx=8, sticky="w")
        def _apply_flex():
            app.save_config(); app._start_flex_server()
        ttk.Button(flx, text="Apply & Restart", command=_apply_flex).grid(
            row=3, column=0, columnspan=5, sticky="w", padx=8, pady=6)

    def _build_colors(self):
        p = self._color_tab
        ttk.Label(p,
            text="Spot foreground (text) colors are sent by Commander directly\n"
                 "(resolved from SpotCollector NeedCategory via DDE in real-time).\n\n"
                 "Background colors below are read from SpotCollector registry\n"
                 "and follow your SpotCollector color settings automatically.",
            font=("Segoe UI", 9), foreground="#0066cc").pack(padx=14, pady=(10, 6), anchor="w")
        f = ttk.LabelFrame(p, text="LoTW / eQSL Background Colors  (from SpotCollector registry)")
        f.pack(fill="x", padx=14, pady=6)
        for i, (key, label) in enumerate([
            ("bg_normal",    "Normal (not in LoTW/eQSL)"),
            ("bg_lotw",      "LoTW member"),
            ("bg_eqsl",      "eQSL AG member"),
            ("bg_lotw_eqsl", "LoTW + eQSL member"),
        ]):
            color = self.app.spot_colors.get(key, "#FFFFFF")
            ttk.Label(f, text=label, width=34, anchor="w").grid(row=i, column=0, padx=8, pady=3, sticky="w")
            tk.Label(f, bg=color, width=4, relief="solid", borderwidth=1).grid(row=i, column=1, padx=4)
            ttk.Label(f, text=color, width=9, font=("Consolas", 9)).grid(row=i, column=2, padx=4)

    def _build_misc(self):
        app = self.app; p = self._misc_tab

        tcif = ttk.LabelFrame(p, text="TCI Spot Format")
        tcif.pack(fill="x", padx=14, pady=(10, 4))
        ttk.Checkbutton(tcif, text="Extended [json] format (Thetis 2.10.3.9+)",
            variable=app.tci_extended).pack(padx=8, pady=6, anchor="w")
        ttk.Checkbutton(tcif, text="Band filter — paint only spots on current VFO band",
            variable=app.band_filter).pack(padx=8, pady=(0, 6), anchor="w")

        bmf = ttk.LabelFrame(p, text="BandModes.txt  (SpotCollector mode-by-frequency)")
        bmf.pack(fill="x", padx=14, pady=6)
        bm = app._band_modes
        if bm and bm.loaded:
            bm_text = "Loaded: {} ranges  |  {}".format(bm.entry_count, bm.path)
            bm_color = "#004400"
        else:
            bm_text = "Not found — mode guessed from frequency (FT8/CW heuristics)"
            bm_color = "#996600"
        ttk.Label(bmf, text=bm_text, font=("Segoe UI", 8),
                  foreground=bm_color, wraplength=580, justify="left").pack(
            padx=10, pady=6, anchor="w")

# ─────────────────────────────────────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    CONFIG_FILE = "bridge_config.json"

    def __init__(self):
        super().__init__()
        self.title("Bridge to Thetis  v7.0  CT2IRY  —  DXLab Edition")
        self.geometry("780x160"); self.resizable(True, True)

        # ── Tkinter vars ──────────────────────────────────────────────────────
        self.tci_host       = tk.StringVar(value="127.0.0.1")
        self.tci_port       = tk.StringVar(value="50001")
        self.tci_extended   = tk.BooleanVar(value=True)
        self.cdr_spots_ip   = tk.StringVar(value="127.0.0.1")
        self.cdr_spots_port = tk.StringVar(value="13063")
        self.band_filter    = tk.BooleanVar(value=False)  # paint only current VFO band
        self.flex_enable    = tk.BooleanVar(value=False)  # Stage 1 Flex SmartSDR server
        self.flex_port      = tk.StringVar(value="4992")

        # ── Background colors (from SC registry, read once at startup) ────────
        self.spot_colors: "dict[str, str]" = load_sc_bg_colors()

        # ── State ─────────────────────────────────────────────────────────────
        self._spot_count  = 0
        self._start_time  = time.time()
        self.config_win   = None
        self.debug_win    = None
        self._cdr_spots      = None   # CommanderSpotsListener
        self._dxview_cache   = None   # DXViewCache
        self._flex_server    = None   # FlexServer (Stage 1)
        self._painted_spots: "dict[str, dict[str, Any]]" = {}

        # ── Operator QTH (from SC registry, for beam heading) ─────────────────
        self._qth_lat, self._qth_lon, self._qth_grid = _read_sc_qth()

        # ── BigCTY database ───────────────────────────────────────────────────
        self._cty = None
        if CTYDatabase is not None:
            try:
                self._cty = CTYDatabase.from_default_path()
            except Exception as e:
                print("[CTY] Load failed: {}".format(e))

        # ── BandModes map (SpotCollector BandModes.txt) ───────────────────────
        self._band_modes = None
        if BandModesMap is not None:
            try:
                self._band_modes = BandModesMap.from_default_path()
            except Exception as e:
                print("[BandModes] Load failed: {}".format(e))

        # ── TCI client ────────────────────────────────────────────────────────
        self.tci = TCIClient("127.0.0.1", 50001,
                             on_message=self._on_tci_msg,
                             on_status=self._on_tci_status, label="R1")

        self._build_ui()
        self.load_config()
        self.tci.start()
        self._start_cdr_spots()
        self._start_dxview_cache()
        self._start_flex_server()
        self.after(2000, self._heartbeat)
        self.after(3000, self._check_apps)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        mb = tk.Menu(self); self.config(menu=mb)
        mb.add_command(label="Configuration", command=self._open_config)
        mb.add_command(label="Debug",         command=self._open_debug)
        mb.add_command(label="Help",          command=self._show_help)
        mb.add_command(label="About",         command=self._show_about)

        self._main_frame = ttk.Frame(self)
        self._main_frame.pack(fill="both", expand=True, padx=16, pady=(10, 8))

        self.status_bar = ttk.Label(self._main_frame, text="Starting...",
                                    relief="sunken", anchor="w", font=("Segoe UI", 11))
        self.status_bar.pack(fill="x", ipady=8)

        bg = self.cget("bg")
        ind = ttk.Frame(self._main_frame); ind.pack(fill="x", pady=(6, 0))

        self.lbl_tci    = tk.Label(ind, text="TCI: connecting...",
                                   font=("Segoe UI", 10), fg="#996600", bg=bg, anchor="w")
        self.lbl_cdr    = tk.Label(ind, text="CDR: off",
                                   font=("Segoe UI", 10), fg="#888888", bg=bg, anchor="w")
        self.lbl_dxview  = tk.Label(ind, text="DXV: off",
                                    font=("Segoe UI", 10), fg="#888888", bg=bg, anchor="w")
        self.lbl_flex    = tk.Label(ind, text="FLEX: disabled",
                                    font=("Segoe UI", 10), fg="#888888", bg=bg, anchor="w")
        self.lbl_tci.pack(fill="x", padx=2, pady=1)
        self.lbl_cdr.pack(fill="x", padx=2, pady=1)
        self.lbl_dxview.pack(fill="x", padx=2, pady=1)
        # lbl_flex hidden until Flex SmartSDR server is fully working
        # self.lbl_flex.pack(fill="x", padx=2, pady=1)

    def _heartbeat(self):
        elapsed = max(1, (time.time() - self._start_time) / 60)
        rate = self._spot_count / elapsed
        self.status_bar.config(text="Spots: {}   Rate: {:.1f}/min".format(
            self._spot_count, rate))
        self._update_indicators()
        self._resize_main_window()
        # Refresh BandModes every ~5 min (re-reads only if date header changed)
        if self._band_modes is not None and int(elapsed) % 5 == 0:
            self._band_modes.refresh_if_changed()
        self.after(2000, self._heartbeat)

    def _resize_main_window(self):
        self.update_idletasks()
        req_h = self._main_frame.winfo_reqheight() + 60
        if req_h > self.winfo_height():
            self.geometry("{}x{}".format(self.winfo_width(), req_h))

    def _update_indicators(self):
        # TCI
        _COLOR = {
            TCIClient.STATE_READY:      "#007700",
            TCIClient.STATE_CONNECTING: "#CC8800",
            TCIClient.STATE_ERROR:      "#CC0000",
            TCIClient.STATE_CLOSED:     "#CC0000",
        }
        color = _COLOR.get(self.tci.state, "#888888")
        lat = " {}ms".format(self.tci.latency_ms) if self.tci.latency_ms >= 0 else ""
        self.lbl_tci.config(
            text="TCI: {}:{} {}{}".format(
                self.tci.host, self.tci.port, self.tci.state.capitalize(), lat),
            fg=color)

        # CDR spots listener
        cdr = self._cdr_spots
        if cdr is None:
            self.lbl_cdr.config(text="CDR: off", fg="#888888")
        else:
            st = cdr.status; lo = st.lower()
            dot = "●"
            ago = cdr.seconds_since_last_packet()
            # Stale feed: amber warning >3 min, red >10 min
            if any(k in lo for k in ("error", "fail")):
                color = "#cc0000"
            elif ago > 600:
                color = "#cc0000"   # red — feed dead >10 min
            elif ago > 180:
                color = "#cc6600"   # amber — no packets >3 min
            elif any(k in lo for k in ("ok", "listening")):
                color = "#007700"   # green — active
            else:
                color = "#996600"   # amber — connecting
            ago_str = (" (no pkts yet)" if ago < 0 else
                       " ({}s ago)".format(int(ago)) if ago < 60 else
                       " ({}m ago)".format(int(ago // 60)))
            self.lbl_cdr.config(text="{} CDR: {}{}".format(dot, st, ago_str), fg=color)

        # DXView cache
        dxv = self._dxview_cache
        if dxv is None:
            self.lbl_dxview.config(text="DXV: off", fg="#888888")
        else:
            st = dxv.status; lo = st.lower()
            dot = "●"
            color = "#007700" if "ok" in lo else ("#cc0000" if "error" in lo else "#996600")
            self.lbl_dxview.config(text="{} DXV: {}".format(dot, st), fg=color)

        # Flex SmartSDR server
        flx = self._flex_server
        if flx is None:
            txt = "FLEX: disabled" if not self.flex_enable.get() else "FLEX: off"
            self.lbl_flex.config(text=txt, fg="#888888")
        else:
            st = flx.status; lo = st.lower()
            dot = "●"
            color = ("#007700" if any(k in lo for k in ("ok", "connected", "listening")) else
                     "#cc0000" if "error" in lo else "#996600")
            self.lbl_flex.config(text="{} FLEX: {}".format(dot, st), fg=color)

    # ── Commander spot listener (UDP :13063) ──────────────────────────────────

    def _start_cdr_spots(self):
        if CommanderSpotsListener is None:
            self.log_debug("commander_spots module not available", tag="warn"); return
        self._stop_cdr_spots()
        try:    port = int(self.cdr_spots_port.get())
        except: port = 13063
        ip = self.cdr_spots_ip.get().strip() or "127.0.0.1"
        self._cdr_spots = CommanderSpotsListener(
            on_spot_add=self._on_spot_add,
            on_spot_delete=self._on_spot_delete,
            on_spot_clearall=self._on_spot_clearall,
            port=port, bind_ip=ip,
        )
        self._cdr_spots.start()
        self.log_debug("CDR spots listener started on UDP {}:{}".format(ip, port), tag="dxlab")

    def _stop_cdr_spots(self):
        if self._cdr_spots:
            self._cdr_spots.stop(); self._cdr_spots = None
            self.log_debug("CDR spots listener stopped", tag="dxlab")

    def _on_spot_add(self, spot: "dict[str, Any]"):
        """Handle real-time spot from Commander UDP :13063."""
        call    = spot.get("callsign", "").upper()
        freq_hz = spot.get("freq_hz", 0)
        mode_sc = spot.get("mode", "USB").upper()
        spotter = spot.get("spotter", "")
        status  = spot.get("status", "")   # "single mult", "new", "dupe", etc.
        comment = spot.get("comment", "") or status
        if not call or not freq_hz:
            return

        # Band filter: skip spots not on the same band as the current VFO
        if self.band_filter.get() and not _same_band(freq_hz, self.tci.vfo_hz):
            return

        # If Commander didn't supply a mode (Format B):
        # 1st choice: BandModes.txt (exact SC band plan)
        # 2nd choice: frequency heuristics (FT8 spot freqs + CW segments)
        if mode_sc == "USB":
            if self._band_modes is not None and self._band_modes.loaded:
                mode_sc = self._band_modes.lookup(freq_hz)
            else:
                from cluster_client import guess_mode_from_comment
                mode_sc = guess_mode_from_comment(comment, freq_hz / 1000.0)

        mode_tci = DXLAB_MODE_MAP.get(mode_sc, "USB")
        if mode_sc in ("CW", "CW-R", "CWR"):
            mode_tci = resolve_cw_mode(freq_hz)

        # Foreground: Commander sends the need-category color (DDE-resolved by SC)
        fg_hex = spot.get("fontcolor") or "#0000FF"

        # Background: backcolor from Commander if present, else DXView LoTW/eQSL cache
        cdr_bg = spot.get("backcolor")
        if cdr_bg:
            bg_hex = cdr_bg
            bg_src = "CDR"
        elif self._dxview_cache is not None:
            bg_key = self._dxview_cache.bg_key(call)
            bg_hex = self.spot_colors.get(bg_key, "#FFFFFF")
            bg_src = "DXV"
        else:
            bg_hex = self.spot_colors.get("bg_normal", "#FFFFFF")
            bg_src = "default"

        # CTY lookup: country, continent, beam heading
        country = ""; continent = ""; heading = 0
        spot_comment = comment
        if self._cty is not None:
            entity = self._cty.lookup(call)
            if entity:
                country   = entity.name
                continent = entity.continent
                if self._qth_lat or self._qth_lon:
                    heading, dist = _bearing_distance(
                        self._qth_lat, self._qth_lon, entity.lat, entity.lon)
                    dist_str = "{}km".format(dist)
                    spot_comment = "{} · {}".format(comment, dist_str).strip() if comment else dist_str

        ok = self.tci.send_spot(
            call, mode_tci, freq_hz, bg_hex,
            spotter=spotter, comment=spot_comment,
            extended=self.tci_extended.get(),
            text_color_hex=fg_hex,
            country=country, continent=continent, heading=heading,
        )
        self._painted_spots[call] = {
            "freq": freq_hz, "mode": mode_tci, "fg": fg_hex, "bg": bg_hex,
            "spotter": spotter, "comment": spot_comment,
            "country": country, "continent": continent, "heading": heading,
        }
        self._spot_count += 1
        self.log_debug(
            "Spot: {} {:.3f}MHz {} bg=[{}] fg={} {}".format(
                call, freq_hz / 1e6, mode_tci, bg_src, fg_hex,
                "painted" if ok else "TCI not ready"),
            tag="spot")

    def _on_spot_delete(self, callsign: str, freq_hz: int):
        call = callsign.upper()
        info = self._painted_spots.pop(call, None)
        fhz  = info["freq"] if info else freq_hz
        # Thetis spot_delete takes callsign only — no frequency argument
        self.tci.send("spot_delete:{};".format(call))
        self.log_debug("CDR delete: {} {:.3f}MHz".format(call, fhz / 1e6), tag="spot")

    def _on_spot_clearall(self):
        self.tci.send("spot_clear;")
        count = len(self._painted_spots)
        self._painted_spots.clear()
        self.log_debug("CDR clearall — cleared {} spot(s)".format(count), tag="spot")

    # ── DXView cache ──────────────────────────────────────────────────────────

    def _start_dxview_cache(self):
        if DXViewCache is None:
            self.log_debug("dxview_db not available — bg colors use bg_normal", tag="warn"); return
        if self._dxview_cache is not None:
            return
        self._dxview_cache = DXViewCache(refresh_interval=3600.0)
        self._dxview_cache.start()
        self.log_debug("DXView LoTW/eQSL cache started", tag="dxlab")

    def _stop_dxview_cache(self):
        if self._dxview_cache:
            self._dxview_cache.stop(); self._dxview_cache = None

    # ── Flex SmartSDR server (Stage 1) ────────────────────────────────────────

    def _start_flex_server(self):
        if FlexServer is None:
            self.log_debug("flex_server module not available", tag="warn"); return
        if not self.flex_enable.get():
            return
        self._stop_flex_server()
        try:    port = int(self.flex_port.get())
        except: port = 4992
        self._flex_server = FlexServer(
            on_spot_add=self._on_flex_spot_add,
            on_spot_delete=self._on_spot_delete,
            on_spot_clearall=self._on_spot_clearall,
            port=port, bind_ip="0.0.0.0",
            on_log=lambda msg: self.log_debug(msg, tag="spot"),
        )
        self._flex_server.start()
        self.log_debug("Flex SmartSDR server started on TCP :{}".format(port), tag="dxlab")

    def _stop_flex_server(self):
        if self._flex_server:
            self._flex_server.stop(); self._flex_server = None
            self.log_debug("Flex SmartSDR server stopped", tag="dxlab")

    def _on_flex_spot_add(self, spot: "dict[str, Any]"):
        """Handle spot from Flex SmartSDR server — colors already decoded to #RRGGBB."""
        call    = spot.get("callsign", "").upper()
        freq_hz = spot.get("freq_hz", 0)
        mode_tci = spot.get("mode", "USB")
        fg_hex  = spot.get("fg_hex", "#0000FF")
        bg_hex  = spot.get("bg_hex", "#FFFFFF")
        comment = spot.get("comment", "")
        spotter = spot.get("spotter", "")
        if not call or not freq_hz:
            return

        # Band filter
        if self.band_filter.get() and not _same_band(freq_hz, self.tci.vfo_hz):
            return

        # CW sideband
        if mode_tci == "CW":
            mode_tci = resolve_cw_mode(freq_hz)

        # CTY lookup
        country = ""; continent = ""; heading = 0
        spot_comment = comment
        if self._cty is not None:
            entity = self._cty.lookup(call)
            if entity:
                country   = entity.name
                continent = entity.continent
                if self._qth_lat or self._qth_lon:
                    heading, dist = _bearing_distance(
                        self._qth_lat, self._qth_lon, entity.lat, entity.lon)
                    dist_str = "{}km".format(dist)
                    spot_comment = "{} · {}".format(comment, dist_str).strip() if comment else dist_str

        ok = self.tci.send_spot(
            call, mode_tci, freq_hz, bg_hex,
            spotter=spotter, comment=spot_comment,
            extended=self.tci_extended.get(),
            text_color_hex=fg_hex,
            country=country, continent=continent, heading=heading,
        )
        self._painted_spots[call] = {
            "freq": freq_hz, "mode": mode_tci, "fg": fg_hex, "bg": bg_hex,
            "spotter": spotter, "comment": spot_comment,
            "country": country, "continent": continent, "heading": heading,
        }
        self._spot_count += 1
        self.log_debug(
            "Flex spot: {} {:.3f}MHz {} fg={} bg={} {}".format(
                call, freq_hz / 1e6, mode_tci, fg_hex, bg_hex,
                "painted" if ok else "TCI not ready"),
            tag="spot")

    # ── TCI callbacks ─────────────────────────────────────────────────────────

    def _on_tci_status(self, label, status):
        self.log_debug("TCI {} -> {}".format(label, status), tag="tci")
        if status == "ready":
            self.after(1000, self._repaint_spots)

    def _on_tci_msg(self, label: str, msg: str):
        # clicked_on_spot:CALLSIGN,FREQ_HZ;
        m = re.match(r"clicked_on_spot:([A-Z0-9/]+),(\d+);", msg, re.I)
        if m:
            call = m.group(1).upper()
            freq_hz = int(m.group(2))
            self.log_debug("TCI click: {} {:.3f}MHz".format(call, freq_hz / 1e6), tag="tci")
            return
        # rx_clicked_on_spot:RX,CHAN,CALLSIGN,FREQ_HZ;
        m2 = re.match(r"rx_clicked_on_spot:(\d+),(\d+),([A-Z0-9/]+),(\d+);", msg, re.I)
        if m2:
            call = m2.group(3).upper()
            freq_hz = int(m2.group(4))
            self.log_debug("TCI rx click: {} {:.3f}MHz (rx={} ch={})".format(
                call, freq_hz / 1e6, m2.group(1), m2.group(2)), tag="tci")

    def _repaint_spots(self):
        """Clear stale Thetis spots then repaint all cached spots with full data."""
        self.tci.send("spot_clear;")
        count = 0
        for call, info in self._painted_spots.items():
            self.tci.send_spot(
                call, info["mode"], info["freq"],
                info.get("bg", "#FFFFFF"),
                spotter=info.get("spotter", ""),
                comment=info.get("comment", ""),
                extended=self.tci_extended.get(),
                text_color_hex=info.get("fg"),
                country=info.get("country", ""),
                continent=info.get("continent", ""),
                heading=info.get("heading", 0),
            )
            count += 1
        self.log_debug("TCI reconnect — cleared + repainted {} spot(s)".format(count), tag="tci")

    # ── TCI actions ───────────────────────────────────────────────────────────

    def reconnect_tci(self):
        self.tci.stop()
        self.tci = TCIClient(self.tci_host.get(), int(self.tci_port.get()),
                             on_message=self._on_tci_msg,
                             on_status=self._on_tci_status, label="R1")
        self.tci.start()
        self.log_debug("TCI reconnecting -> {}:{}".format(
            self.tci.host, self.tci.port), tag="tci")

    def send_test_spot(self):
        ok = self.tci.send_spot("CT2IRY", "CW", self.tci.vfo_hz,
                                self.spot_colors.get("bg_normal", "#FFFFFF"),
                                spotter="CT2IRY", comment="Bridge v7.0 TEST",
                                extended=self.tci_extended.get())
        self.log_debug("Test spot @ {:.3f}MHz  {}".format(
            self.tci.vfo_hz / 1e6, "ok" if ok else "TCI not ready"), tag="spot")

    def clear_all_spots(self):
        self.tci.send("spot_clear;")
        self._painted_spots.clear()
        self.log_debug("spot_clear sent", tag="tci")

    # ── Startup check ─────────────────────────────────────────────────────────

    def _check_apps(self):
        """Warn if Commander or SpotCollector not running."""
        import subprocess
        def _running(exe):
            try:
                out = subprocess.check_output(
                    ["tasklist", "/FI", "IMAGENAME eq {}".format(exe), "/NH"],
                    stderr=subprocess.DEVNULL).decode(errors="ignore")
                return exe.lower() in out.lower()
            except Exception:
                return True
        missing = [a for a in ("Commander.exe", "SpotCollector.exe") if not _running(a)]
        if missing:
            names = " and ".join(m.replace(".exe", "") for m in missing)
            messagebox.showwarning("DXLab apps not running",
                "{} not running.\n\nPlease start {} before using Bridge.\n"
                "Commander sends real-time spots on UDP :13063 (WaterfallBandmap).\n"
                "SpotCollector must be running for Commander to resolve spot colors.".format(
                    names, names))

    # ── Windows ───────────────────────────────────────────────────────────────

    def _open_config(self):
        if self.config_win and self.config_win.winfo_exists(): self.config_win.lift()
        else: self.config_win = ConfigWindow(self)

    def _open_debug(self):
        if self.debug_win and self.debug_win.winfo_exists(): self.debug_win.lift()
        else: self.debug_win = DebugWindow(self)

    def log_debug(self, msg, tag=""):
        ts = time.strftime("%H:%M:%S"); full = "[{}] {}".format(ts, msg); print(full)
        if self.debug_win and self.debug_win.winfo_exists():
            self.debug_win.append(full, tag=tag)

    def _show_help(self):
        messagebox.showinfo("Help",
            "Bridge to Thetis  v7.0  —  DXLab Edition\n\n"
            "1. Start Commander + SpotCollector\n"
            "2. In Thetis: Setup > Network > TCI Server Running\n"
            "3. Commander: Enable WaterfallBandmap plugin (UDP :13063)\n"
            "4. Configuration > Network > set TCI host:port if needed\n"
            "5. Debug > Send Test Spot to verify\n\n"
            "Commander ↔ Thetis radio control (VFO/mode/split/click) is\n"
            "handled natively via Commander TCP :13013 — no config needed.\n\n"
            "pip install websocket-client")

    def _show_about(self):
        messagebox.showinfo("About Bridge to Thetis",
            "Bridge to Thetis\n"
            "Version 7.0\n\n"
            "Developed by Nuno Lopes — CT2IRY\n\n"
            "─────────────────────────────────\n\n"
            "Special thanks:\n\n"
            "Dave Bernstein AA6YQ\n"
            "  DXLab Suite — WaterfallBandmap protocol,\n"
            "  integration support and listing on\n"
            "  the DXLab download page.\n\n"
            "Richie MW0LGE\n"
            "  Thetis SDR — TCI protocol guidance\n"
            "  and spot painting implementation.\n\n"
            "─────────────────────────────────\n\n"
            "https://github.com/ct2iry-dot/Bridge-to-Thetis")

    # ── Config persistence ────────────────────────────────────────────────────

    @property
    def _config_path(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), self.CONFIG_FILE)

    def save_config(self):
        g = lambda v: v.get()
        cfg = {
            "tci_host":          g(self.tci_host),
            "tci_port":          g(self.tci_port),
            "tci_extended":      g(self.tci_extended),
            "cdr_spots_ip":      g(self.cdr_spots_ip),
            "cdr_spots_port":    g(self.cdr_spots_port),
            "band_filter":       g(self.band_filter),
            "flex_enable":       g(self.flex_enable),
            "flex_port":         g(self.flex_port),
        }
        try:
            with open(self._config_path, "w") as fh: json.dump(cfg, fh, indent=2)
        except Exception as e:
            self.log_debug("Config save error: {}".format(e), tag="error")

    def load_config(self):
        if not os.path.exists(self._config_path): return
        try:
            with open(self._config_path) as fh: cfg = json.load(fh)
        except: return
        for var, key in [
            (self.tci_host,         "tci_host"),
            (self.tci_port,         "tci_port"),
            (self.tci_extended,     "tci_extended"),
            (self.cdr_spots_ip,     "cdr_spots_ip"),
            (self.cdr_spots_port,   "cdr_spots_port"),
            (self.band_filter,      "band_filter"),
            (self.flex_enable,      "flex_enable"),
            (self.flex_port,        "flex_port"),
        ]:
            if key in cfg: var.set(cfg[key])
        self.log_debug("Config loaded")

    # ── Shutdown ──────────────────────────────────────────────────────────────

    def destroy(self):
        self.save_config()
        self.tci.stop()
        self._stop_cdr_spots()
        self._stop_dxview_cache()
        self._stop_flex_server()
        super().destroy()

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not HAS_WEBSOCKET:
        print("ERROR: pip install websocket-client")
    App().mainloop()
