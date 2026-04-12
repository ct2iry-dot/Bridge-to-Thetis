#!/usr/bin/env python3
"""
shared.py — Common anchor layer between Bridge.py and Propagation.py
Author: Nuno Lopes CT2IRY

This module is imported by BOTH Bridge.py and Propagation.py.
It defines:
  - Constants (ports, defaults)
  - SpotEvent dataclass
  - SpotPublisher  → Bridge uses this to broadcast spots
  - SpotSubscriber → Propagation uses this to receive spots from Bridge
  - Shared config defaults

Architecture:
  Bridge.py  ──[TCP 127.0.0.1:9877]──►  Propagation.py
  (publisher)                            (subscriber)

  Propagation can also run STANDALONE — it falls back to its own
  cluster/PSK Reporter/WSPR feeds when Bridge is not running.
"""

import socket
import threading
import json
import time
from dataclasses import dataclass, asdict
from typing import Optional, Callable

# ── Anchor port (local only, Bridge → Propagation) ──────────────────────────
BRIDGE_SPOT_PORT   = 9877          # TCP, localhost only
BRIDGE_HOST        = "127.0.0.1"

# ── Default network values ───────────────────────────────────────────────────
DEFAULT_LOGGER_IP    = "0.0.0.0"
DEFAULT_SPOT_PORT    = "12060"
DEFAULT_COMMAND_PORT = "12060"
DEFAULT_SCORE_PORT   = "12060"
DEFAULT_TCI_HOST     = "127.0.0.1"
DEFAULT_TCI_PORT     = "50001"

# ── Propagation defaults ─────────────────────────────────────────────────────
DEFAULT_GRID         = "IN51"
DEFAULT_REGION       = "EU"
DEFAULT_TX_POWER     = "100"
DEFAULT_TTL          = "10"


# ── Spot dataclass ────────────────────────────────────────────────────────────
@dataclass
class SpotEvent:
    """A single DX spot, normalised from any logger or cluster source."""
    freq:       float           # kHz
    callsign:   str
    mode:       str             # CW / SSB / FT8 / etc.
    snr:        Optional[int]   # dB, None if unknown
    spotter:    str             # who spotted it
    comment:    str
    timestamp:  float           # Unix epoch
    source:     str             # "DXLog" | "N1MM+" | "DXLab" | "Cluster" | "PSKReporter" | "WSPR"
    color_key:  str             # "single_mult" | "double_mult" | "qso" | "new" | "dupe" | "cq" | "busy" | "qtc"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(data: str) -> "SpotEvent":
        return SpotEvent(**json.loads(data))


# ── Publisher (used by Bridge.py) ─────────────────────────────────────────────
class SpotPublisher:
    """
    Listens on BRIDGE_SPOT_PORT and pushes SpotEvents (newline-delimited JSON)
    to all connected Propagation clients.
    """
    def __init__(self, on_status: Optional[Callable[[str], None]] = None):
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._on_status = on_status or print
        self._server: Optional[socket.socket] = None
        self._running = False

    def start(self):
        self._running = True
        t = threading.Thread(target=self._serve, daemon=True)
        t.start()

    def _serve(self):
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._server.bind((BRIDGE_HOST, BRIDGE_SPOT_PORT))
            self._server.listen(5)
            self._on_status(f"SpotPublisher listening on {BRIDGE_HOST}:{BRIDGE_SPOT_PORT}")
            while self._running:
                try:
                    self._server.settimeout(1.0)
                    conn, addr = self._server.accept()
                    with self._lock:
                        self._clients.append(conn)
                    self._on_status(f"Propagation connected from {addr}")
                except socket.timeout:
                    pass
        except Exception as e:
            if self._running:
                self._on_status(f"SpotPublisher error: {e}")

    def publish(self, spot: SpotEvent):
        payload = (spot.to_json() + "\n").encode()
        dead = []
        with self._lock:
            for c in self._clients:
                try:
                    c.sendall(payload)
                except Exception:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)
                try: c.close()
                except: pass

    def stop(self):
        self._running = False
        if self._server:
            try: self._server.close()
            except: pass


# ── Subscriber (used by Propagation.py) ──────────────────────────────────────
class SpotSubscriber:
    """
    Connects to Bridge's SpotPublisher and fires on_spot(SpotEvent) for each
    incoming spot.  Reconnects automatically if Bridge is restarted.
    """
    def __init__(self,
                 on_spot: Callable[[SpotEvent], None],
                 on_status: Optional[Callable[[str], None]] = None):
        self._on_spot   = on_spot
        self._on_status = on_status or print
        self._running   = False
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def start(self):
        self._running = True
        t = threading.Thread(target=self._connect_loop, daemon=True)
        t.start()

    def _connect_loop(self):
        while self._running:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                s.connect((BRIDGE_HOST, BRIDGE_SPOT_PORT))
                self._connected = True
                self._on_status("Connected to Bridge spot feed ✅")
                buf = ""
                while self._running:
                    chunk = s.recv(4096).decode(errors="ignore")
                    if not chunk:
                        break
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        if line.strip():
                            try:
                                spot = SpotEvent.from_json(line)
                                self._on_spot(spot)
                            except Exception as e:
                                self._on_status(f"Spot parse error: {e}")
            except Exception:
                pass
            finally:
                self._connected = False
                self._on_status("Bridge spot feed disconnected — retrying in 5 s…")
            time.sleep(5)

    def stop(self):
        self._running = False
