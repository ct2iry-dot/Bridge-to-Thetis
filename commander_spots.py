# commander_spots.py  —  Real-time Commander WaterfallBandmap UDP listener
#
# Commander sends XML spot events on UDP 127.0.0.1:13063 in the
# WaterfallBandmap (N2IC / AA6YQ) protocol.
#
# Packet types:
#   <add>    — new spot painted by Commander/SpotCollector
#   <delete> — spot removed
#   <clearall> — all spots cleared
#
# Confirmed XML fields (from N2IC WaterfallBandmap wiki + AA6YQ notes):
#   <dxcall>CT2IRY</dxcall>
#   <rxfreq>14025700</rxfreq>   ← in Hz * 10  (divide by 10 to get Hz)
#   <mode>CW</mode>
#   <spotter>OH2BH</spotter>
#   <comment>up 3</comment>
#   <time>1456</time>           ← HHMM UTC
#   <fontcolor>255</fontcolor>  ← VB6 OLE_COLOR signed long (BGR)
#   <backcolor>16777215</backcolor> (optional background color)
#   <needcolor>255</needcolor>  (optional, same as fontcolor in newer builds)
#
# Frequency units: Commander WaterfallBandmap uses Hz×10 (same scale as
# Commander's RadioInfo <Freq> field which is also ×10). Divide by 10 to get Hz.

from __future__ import annotations
import re, socket, threading, time
from typing import Callable, Optional

_DEFAULT_PORT = 13063
_DEFAULT_BIND = "127.0.0.1"


def _tag(xml: str, name: str) -> str:
    """Extract first occurrence of <name>value</name> (case-insensitive)."""
    m = re.search(r"<{0}[^>]*>([^<]*)</{0}>".format(name), xml, re.I)
    return m.group(1).strip() if m else ""


def vb6_color_to_hex(raw: str) -> Optional[str]:
    """
    Convert a VB6 OLE_COLOR integer string to '#RRGGBB'.
    Commander uses two different encodings:
      Positive (registry PaneColors): VB6 BGR — R=low byte, B=high byte
      Negative (XML fontcolor):       24-bit two's complement RGB — R=high byte, B=low byte
    Returns None if raw is empty or not parseable.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        v = int(raw)
        if v < 0:
            u = v & 0xFFFFFF   # 24-bit two's complement, RGB byte order
            r = (u >> 16) & 0xFF
            g = (u >> 8) & 0xFF
            b = u & 0xFF
        else:
            r = v & 0xFF       # VB6 OLE_COLOR BGR byte order
            g = (v >> 8) & 0xFF
            b = (v >> 16) & 0xFF
        return "#{:02X}{:02X}{:02X}".format(r, g, b)
    except (ValueError, TypeError):
        return None


def _parse_freq_hz(freq_s: str, is_khz: bool) -> int:
    """Convert frequency string to Hz. is_khz=True for kHz input, False for Hz×10 input."""
    if not freq_s:
        return 0
    try:
        v = float(freq_s)
        if is_khz:
            return int(round(v * 1000))   # kHz → Hz
        else:
            return int(round(v / 10))     # Hz×10 → Hz
    except ValueError:
        return 0


def parse_wbm_packet(xml: str) -> Optional[dict]:
    """
    Parse one Commander WaterfallBandmap UDP packet.

    Handles two formats:

    Format A — classic WaterfallBandmap (root element = action):
        <add><dxcall>CT2IRY</dxcall><rxfreq>14025700</rxfreq>...  (rxfreq in Hz×10)

    Format B — SpotCollector XML (root element = <spot>, action in child):
        <spot><dxcall>CT2IRY</dxcall><frequency>14025</frequency>
              <action>add</action>...                              (frequency in kHz)

    Returns dict with keys:
        action    : "add" | "delete" | "clearall"
        callsign  : str (uppercase)
        freq_hz   : int (Hz)
        mode      : str (uppercase, e.g. "CW", "USB", "DIGU")
        spotter   : str
        comment   : str
        time_utc  : str (HHMM)
        fontcolor : str ('#RRGGBB') or None
        backcolor : str ('#RRGGBB') or None

    Returns None if the packet cannot be parsed.
    """
    if not xml:
        return None

    lo = xml.lower()

    # ── Format A: root element is the action ─────────────────────────────────

    if "<clearall>" in lo or "<spotclearall>" in lo:
        return {"action": "clearall"}

    if "<delete>" in lo or "<spotdelete>" in lo:
        call   = (_tag(xml, "dxcall") or _tag(xml, "call")).upper()
        freq_s = _tag(xml, "rxfreq") or _tag(xml, "frequency")
        # rxfreq = Hz×10; frequency = kHz
        is_khz = bool(_tag(xml, "frequency")) and not bool(_tag(xml, "rxfreq"))
        return {"action": "delete", "callsign": call,
                "freq_hz": _parse_freq_hz(freq_s, is_khz)}

    if "<add>" in lo or "<spotadd>" in lo or "<spotupdate>" in lo:
        call    = (_tag(xml, "dxcall") or _tag(xml, "call")).upper()
        freq_s  = _tag(xml, "rxfreq") or _tag(xml, "frequency")
        is_khz  = bool(_tag(xml, "frequency")) and not bool(_tag(xml, "rxfreq"))
        mode    = (_tag(xml, "mode") or "USB").upper()
        spotter = _tag(xml, "spotter")
        comment = _tag(xml, "comment")
        time_u  = _tag(xml, "time")
        fc_s    = _tag(xml, "fontcolor") or _tag(xml, "needcolor")
        bc_s    = _tag(xml, "backcolor")
        if not call:
            return None
        return {
            "action":    "add",
            "callsign":  call,
            "freq_hz":   _parse_freq_hz(freq_s, is_khz),
            "mode":      mode,
            "spotter":   spotter,
            "comment":   comment,
            "time_utc":  time_u,
            "fontcolor": vb6_color_to_hex(fc_s),
            "backcolor": vb6_color_to_hex(bc_s),
        }

    # ── Format B: <spot> root with <action> child ────────────────────────────
    # Schema (confirmed):
    #   <dxcall>     string
    #   <frequency>  float  kHz
    #   <fontcolor>  signed integer  VB6 OLE_COLOR (BGR)
    #   <action>     add | delete | clearall
    #   <status>     busy | bust | cq | dupe | single mult | double mult | new qso

    if "<spot>" in lo:
        action_s = _tag(xml, "action").lower()
        call     = _tag(xml, "dxcall").upper()
        freq_s   = _tag(xml, "frequency")   # kHz
        status   = _tag(xml, "status")      # spot category
        fc_s     = _tag(xml, "fontcolor")   # VB6 OLE_COLOR signed int

        if action_s == "clearall":
            return {"action": "clearall"}

        if action_s == "delete":
            return {"action": "delete", "callsign": call,
                    "freq_hz": _parse_freq_hz(freq_s, is_khz=True)}

        if action_s == "add":
            if not call:
                return None
            return {
                "action":    "add",
                "callsign":  call,
                "freq_hz":   _parse_freq_hz(freq_s, is_khz=True),
                "mode":      "USB",   # not in schema — inferred by Commander/SC
                "spotter":   "",
                "comment":   "",
                "status":    status,
                "time_utc":  "",
                "fontcolor": vb6_color_to_hex(fc_s),
                "backcolor": None,
            }

    # Unknown — ignore silently
    return None


class CommanderSpotsListener:
    """
    Listens on UDP port 13063 for Commander WaterfallBandmap spot events.

    Callbacks (all optional, called from the listener thread):
        on_spot_add(spot_dict)     — new or updated spot
        on_spot_delete(callsign, freq_hz)
        on_spot_clearall()

    Usage:
        listener = CommanderSpotsListener(
            on_spot_add=handler_add,
            on_spot_delete=handler_del,
            on_spot_clearall=handler_clear,
        )
        listener.start()
        ...
        listener.stop()
    """

    def __init__(
        self,
        on_spot_add:    Optional[Callable] = None,
        on_spot_delete: Optional[Callable] = None,
        on_spot_clearall: Optional[Callable] = None,
        port: int = _DEFAULT_PORT,
        bind_ip: str = _DEFAULT_BIND,
    ):
        self._on_add     = on_spot_add
        self._on_delete  = on_spot_delete
        self._on_clear   = on_spot_clearall
        self._port       = port
        self._bind_ip    = bind_ip
        self._stop_evt   = threading.Event()
        self._thread     = threading.Thread(
            target=self._run, name="CdrSpots", daemon=True)
        self.status        = "Idle"
        self._packet_count = 0
        self._last_rx      = 0.0   # time.time() of last received packet

    def start(self): self._thread.start()
    def stop(self):  self._stop_evt.set()
    def is_running(self): return self._thread.is_alive()

    def seconds_since_last_packet(self) -> float:
        """Seconds since the last UDP packet was received. -1 if never received."""
        if self._last_rx == 0.0:
            return -1.0
        return time.time() - self._last_rx

    def _run(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._bind_ip, self._port))
            sock.settimeout(1.0)
            self.status = "Listening :{}".format(self._port)
            print("[CdrSpots] Listening on {}:{}".format(self._bind_ip, self._port))
        except OSError as e:
            self.status = "Error: {}".format(e)
            print("[CdrSpots] Bind error: {}".format(e))
            return

        while not self._stop_evt.is_set():
            try:
                data, _addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break

            xml = data.decode("utf-8", errors="ignore").strip()
            self._packet_count += 1
            self._last_rx = time.time()

            parsed = parse_wbm_packet(xml)
            if parsed is None:
                continue

            action = parsed["action"]
            try:
                if action == "add" and self._on_add:
                    self._on_add(parsed)
                elif action == "delete" and self._on_delete:
                    self._on_delete(parsed["callsign"], parsed.get("freq_hz", 0))
                elif action == "clearall" and self._on_clear:
                    self._on_clear()
                self.status = "OK — {} pkts".format(self._packet_count)
            except Exception as e:
                print("[CdrSpots] Callback error: {}".format(e))

        sock.close()
        self.status = "Stopped"
        print("[CdrSpots] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
# CommanderRadioClient — persistent TCP connection to Commander's main port
# ─────────────────────────────────────────────────────────────────────────────

class CommanderRadioClient:
    """
    Persistent TCP client for Commander's main TCP server (default port 13013).

    Commander streams <RadioInfo> XML on this connection (VFO, mode, split, RadioNr).
    Bridge sends CmdSetFreqMode / CmdQSXSplit back via short-lived connections to
    the same port (same as the current one-shot TCP send used for 52002).

    Callbacks:
        on_radio_xml(xml_str)  — called for each complete <RadioInfo> block received

    Usage:
        client = CommanderRadioClient(host="127.0.0.1", port=13013,
                                      on_radio_xml=handler)
        client.start()
        ...
        client.stop()
    """

    RECONNECT_DELAY = 5.0

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 13013,
        on_radio_xml: Optional[Callable] = None,
    ):
        self.host        = host
        self.port        = port
        self._on_xml     = on_radio_xml
        self._stop_evt   = threading.Event()
        self._thread     = threading.Thread(
            target=self._run, name="CdrTCP", daemon=True)
        self.status      = "Idle"
        self._pkt_count  = 0

    def start(self):      self._thread.start()
    def stop(self):       self._stop_evt.set()
    def is_running(self): return self._thread.is_alive()

    def _run(self):
        while not self._stop_evt.is_set():
            sock = None
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                self.status = "Connecting {}:{}...".format(self.host, self.port)
                print("[CdrTCP] Connecting to {}:{}".format(self.host, self.port))
                sock.connect((self.host, self.port))
                sock.settimeout(2.0)
                self.status = "Connected"
                print("[CdrTCP] Connected to {}:{}".format(self.host, self.port))
                buf = ""

                while not self._stop_evt.is_set():
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    buf += data.decode("utf-8", errors="ignore")

                    # Extract complete <RadioInfo>...</RadioInfo> blocks
                    while True:
                        start = buf.find("<RadioInfo>")
                        if start == -1:
                            if len(buf) > 2048:
                                buf = buf[-256:]  # keep tail in case tag split across chunks
                            break
                        end = buf.find("</RadioInfo>", start)
                        if end == -1:
                            break
                        end += len("</RadioInfo>")
                        xml = buf[start:end]
                        buf = buf[end:]
                        self._pkt_count += 1
                        self.status = "OK — {} RadioInfo".format(self._pkt_count)
                        if self._on_xml:
                            try:
                                self._on_xml(xml)
                            except Exception as e:
                                print("[CdrTCP] Callback error: {}".format(e))

            except (OSError, ConnectionRefusedError) as e:
                self.status = "Error: {}".format(e)
                print("[CdrTCP] Error: {}".format(e))
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass

            if not self._stop_evt.is_set():
                self.status = "Reconnecting in {}s...".format(int(self.RECONNECT_DELAY))
                self._stop_evt.wait(self.RECONNECT_DELAY)

        self.status = "Stopped"
        print("[CdrTCP] Stopped.")


# ─── Self-test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import time, json

    def on_add(s):
        print("ADD:", json.dumps(s, indent=2))

    def on_del(call, freq):
        print("DEL:", call, freq)

    def on_clear():
        print("CLEARALL")

    print("Listening on UDP 127.0.0.1:13063 — press Ctrl-C to stop")
    lst = CommanderSpotsListener(on_add, on_del, on_clear)
    lst.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        lst.stop()
