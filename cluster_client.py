# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeArgument=false
"""
cluster_client.py — Lightweight telnet DX cluster client for Bridge to Thetis.

Connects to a DX cluster via telnet, parses standard DX spot lines,
and delivers them as dicts to a callback.

Usage:
    def on_spot(spot):
        print(spot)  # {"callsign":"JA1ABC", "frequency":14025000, "spotter":"W3LPL", ...}

    client = ClusterClient("dxc.ve7cc.net", 23, "CT2IRY", on_spot=on_spot)
    client.start()
    ...
    client.stop()
"""

import re
import socket
import threading
import time
from typing import Callable, Optional, Dict, Any

# ── Standard DX spot line parser ────────────────────────────────────────────
# Format: "DX de SPOTTER:     FREQ  CALLSIGN  comment           TIMEZ"
#   - Fixed 80-char format from AK1A PacketCluster, used by all clusters
_SPOT_RE = re.compile(
    r"^DX\s+de\s+"              # "DX de "
    r"([A-Z0-9/]+)[-#]?:\s+"   # spotter callsign (strip -# for RBN skimmers)
    r"([0-9.]+)\s+"             # frequency in kHz (e.g. 14025.0)
    r"([A-Z0-9/]+)\s+"         # DX callsign
    r"(.*?)\s+"                 # comment (mode, signal, etc.)
    r"(\d{4})Z",                # UTC time (e.g. 1234Z)
    re.IGNORECASE
)

# ── Mode extraction from comment field ──────────────────────────────────────
_MODE_PATTERNS = [
    (re.compile(r"\bFT8\b",  re.I), "DIGU"),
    (re.compile(r"\bFT4\b",  re.I), "DIGU"),
    (re.compile(r"\bRTTY\b", re.I), "RTTY"),
    (re.compile(r"\bPSK\d*", re.I), "DIGU"),
    (re.compile(r"\bCW\b",   re.I), "CW"),
    (re.compile(r"\bSSB\b",  re.I), "USB"),
    (re.compile(r"\bUSB\b",  re.I), "USB"),
    (re.compile(r"\bLSB\b",  re.I), "LSB"),
    (re.compile(r"\bAM\b",   re.I), "AM"),
    (re.compile(r"\bFM\b",   re.I), "FM"),
    (re.compile(r"\bJS8\b",  re.I), "DIGU"),
    (re.compile(r"\bQ65\b",  re.I), "DIGU"),
    (re.compile(r"\bMSK144\b", re.I), "DIGU"),
    (re.compile(r"\bSSTv\b", re.I), "USB"),
]

def guess_mode_from_comment(comment: str, freq_khz: float) -> str:
    """Extract mode from the comment field, or guess from frequency."""
    for pat, mode in _MODE_PATTERNS:
        if pat.search(comment):
            return mode
    # Heuristic: common FT8 frequencies
    _ft8_freqs = {1840, 3573, 5357, 7074, 10136, 14074, 18100, 21074, 24915, 28074, 50313}
    freq_rounded = round(freq_khz)
    if freq_rounded in _ft8_freqs:
        return "DIGU"
    # Default: CW below 10 MHz typical CW segments, else USB
    if freq_khz < 10000 and (freq_khz % 1000) < 100:
        return "CW"
    return "USB"

def parse_spot_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a standard DX cluster spot line into a dict.

    Returns None if the line is not a spot.
    """
    m = _SPOT_RE.match(line.strip())
    if not m:
        return None
    spotter  = m.group(1).upper()
    freq_khz = float(m.group(2))
    callsign = m.group(3).upper()
    comment  = m.group(4).strip()
    utc_time = m.group(5)
    freq_hz  = int(freq_khz * 1000)
    mode     = _guess_mode_from_comment(comment, freq_khz)

    return {
        "callsign":  callsign,
        "frequency": freq_hz,
        "freq_khz":  freq_khz,
        "spotter":   spotter,
        "comment":   comment,
        "mode":      mode,
        "utc_time":  utc_time,
        "source":    "Cluster",
    }


# ── Telnet cluster client ───────────────────────────────────────────────────

class ClusterClient:
    """Telnet DX cluster connection with auto-reconnect."""

    RECONNECT_DELAY = 15   # seconds between reconnect attempts
    RECV_TIMEOUT    = 60   # socket recv timeout (also serves as keepalive)

    def __init__(self, host: str, port: int, callsign: str,
                 on_spot: Optional[Callable] = None,
                 on_status: Optional[Callable] = None):
        self.host = host
        self.port = port
        self.callsign = callsign.upper()
        self.on_spot = on_spot
        self.on_status = on_status
        self._running = False
        self._sock: Optional[socket.socket] = None
        self._spot_count = 0
        self.status = "off"

    def start(self):
        if self._running:
            return
        self._running = True
        threading.Thread(target=self._loop, daemon=True, name="cluster").start()

    def stop(self):
        self._running = False
        self._close_socket()
        self.status = "off"

    @property
    def spot_count(self) -> int:
        return self._spot_count

    def _close_socket(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _set_status(self, st: str):
        self.status = st
        if self.on_status:
            try:
                self.on_status(st)
            except Exception:
                pass

    def _loop(self):
        while self._running:
            try:
                self._connect_and_read()
            except Exception as e:
                self._set_status("Error: {}".format(str(e)[:60]))
            finally:
                self._close_socket()
            if self._running:
                self._set_status("Reconnecting in {}s...".format(self.RECONNECT_DELAY))
                time.sleep(self.RECONNECT_DELAY)

    def _connect_and_read(self):
        self._set_status("Connecting {}:{}".format(self.host, self.port))
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect((self.host, self.port))
        s.settimeout(self.RECV_TIMEOUT)
        self._sock = s

        # Wait for login prompt and send callsign
        self._set_status("Logging in as {}".format(self.callsign))
        buf = ""
        login_sent = False
        while self._running:
            try:
                data = s.recv(4096)
            except socket.timeout:
                if not login_sent:
                    # Some clusters don't send a prompt, just send callsign
                    s.sendall((self.callsign + "\r\n").encode("ascii", errors="replace"))
                    login_sent = True
                continue
            if not data:
                break  # connection closed

            text = data.decode("ascii", errors="replace")
            buf += text

            # Send callsign when we see a login/call prompt
            if not login_sent:
                lower = buf.lower()
                if any(p in lower for p in ("login:", "call:", "callsign:", "enter your call",
                                             "your callsign", "please enter")):
                    s.sendall((self.callsign + "\r\n").encode("ascii", errors="replace"))
                    login_sent = True
                    buf = ""
                    continue
                # Some clusters just say "Welcome" or similar without explicit prompt
                if len(buf) > 200 and not login_sent:
                    s.sendall((self.callsign + "\r\n").encode("ascii", errors="replace"))
                    login_sent = True

            # Process complete lines
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                if login_sent and "DX de" not in line.upper() and self.status.startswith(("Connecting", "Logging")):
                    self._set_status("OK — {} spots".format(self._spot_count))

                spot = parse_spot_line(line)
                if spot:
                    self._spot_count += 1
                    self._set_status("OK — {} spots".format(self._spot_count))
                    if self.on_spot:
                        try:
                            self.on_spot(spot)
                        except Exception:
                            pass


# ── CLI self-test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Test parser
    test_lines = [
        "DX de W3LPL:     14025.0  JA1ABC       CW UP 5                        1234Z",
        "DX de CT3FW:     21004.8  HC2AO        599 TKS(CW)QSL READ,QRZ.COM    2132Z",
        "DX de EA5WU-#:    7018.3  RW1M         CW 19 dB 18 WPM CQ             2259Z",
        "DX de KM3T:      14074.0  PY2ABC       FT8 -12dB from FN42            0345Z",
        "DX de VE3NE:     28074.0  ZL1ABC       FT8                            1200Z",
        "DX de K1TTT:      3573.0  5B4ALJ       FT8 Sent: -10 Rcvd: -15       0800Z",
        "This is not a spot line",
        "DX de N6ACA:     18100.0  3Y0J         UP 1-3 RARE!                   1500Z",
    ]
    print("=== Spot Parser Test ===\n")
    for line in test_lines:
        result = parse_spot_line(line)
        if result:
            print("  {} {:.1f} kHz {} mode={} spotter={} comment='{}'".format(
                result["callsign"], result["freq_khz"], result["utc_time"],
                result["mode"], result["spotter"], result["comment"]))
        else:
            print("  [not a spot] {}".format(line[:60]))

    print("\n=== Live Cluster Test (10 seconds) ===")
    print("Connecting to dxc.ve7cc.net:23 ...")

    def show_spot(spot):
        print("  SPOT: {} {:.1f} kHz {} [{}] by {}".format(
            spot["callsign"], spot["freq_khz"], spot["mode"],
            spot["comment"][:30], spot["spotter"]))

    def show_status(st):
        print("  STATUS:", st)

    client = ClusterClient("dxc.ve7cc.net", 23, "CT2IRY",
                           on_spot=show_spot, on_status=show_status)
    client.start()
    time.sleep(15)
    client.stop()
    print("\nDone. {} spots received.".format(client.spot_count))
