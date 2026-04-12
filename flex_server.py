# flex_server.py  —  Stage 1 Flex SmartSDR TCP server emulation
#
# Pretends to be a FlexRadio Signature series radio on TCP port 4992.
# Commander (configured as Flex radio) connects, gets handshake, sends
# spot add/remove directives which Bridge translates to Thetis TCI spots.
#
# All non-spot commands are ACK'd and discarded.
# No radio status is sent back to Commander (spots-only Stage 1).
#
# Protocol reference:
#   https://github.com/flexradio/smartsdr-api-docs/wiki/SmartSDR-TCPIP-API
#   https://github.com/flexradio/smartsdr-api-docs/wiki/TCPIP-spot
#
# Handshake (sent by Bridge on connect):
#   V1.8.0.0\n          ← firmware version
#   H<hex_handle>\n     ← 32-bit client handle
#
# Command format from Commander:
#   C<seq>|command [params]\n
#
# Response format to Commander:
#   R<seq>|0|\n  ← ACK (success)
#   R<seq>|0|<spot_index>\n  ← ACK spot add with index
#
# Spot add fields we care about:
#   rx_freq=<MHz>           → freq_hz = int(rx_freq * 1e6)
#   callsign=<call>         → callsign
#   mode=<mode>             → mode (lsb/usb/cw/ft8/rtty/psk etc.)
#   color=<#AARRGGBB>       → text color ARGB
#   background_color=<...>  → background color ARGB
#   comment=<text>          → comment (spaces encoded as 0x7F)
#   spotter_callsign=<...>  → spotter
#   lifetime_seconds=<n>    → ignored (Thetis manages lifetime)
#   tx_freq=<MHz>           → ignored (CAT handles split)

from __future__ import annotations
import os, random, re, socket, threading
from typing import Callable, Optional

_DEFAULT_PORT = 4992
_DEFAULT_BIND = "0.0.0.0"   # listen on all interfaces so Commander can connect

# Flex firmware version we pretend to be
# Spots were introduced in SmartSDR v2.x; use a recent v3.x to satisfy any version gate
_FLEX_VERSION = "3.3.26.0"

# SmartSDR mode → TCI mode
_FLEX_MODE_MAP = {
    "lsb":  "LSB",
    "usb":  "USB",
    "cw":   "CW",
    "cwl":  "CWL",
    "am":   "AM",
    "fm":   "FM",
    "rtty": "DIGU",
    "psk":  "DIGU",
    "ft8":  "DIGU",
    "ft4":  "DIGU",
    "jt65": "DIGU",
    "jt9":  "DIGU",
    "wspr": "DIGU",
    "js8":  "DIGU",
    "digu": "DIGU",
    "digl": "DIGL",
}

# Regex for C<seq>|command (case-insensitive; D flag is optional)
_CMD_RE = re.compile(r"^[Cc][Dd]?(\d+)\|(.*)$")


def _parse_kv(params: str) -> dict[str, str]:
    """Parse 'key=value key2=value2' into a dict. Handles quoted and unquoted values."""
    result: dict[str, str] = {}
    # Split on spaces but respect that values may contain 0x7F (space substitute)
    for token in params.split():
        if "=" in token:
            k, _, v = token.partition("=")
            result[k.strip().lower()] = v.strip()
    return result


def _decode_comment(s: str) -> str:
    """Decode Flex comment: 0x7F → space."""
    return s.replace("\x7f", " ").replace("0x7F", " ").replace("0x7f", " ")


def _argb_hex_to_rgb_hex(argb: str) -> Optional[str]:
    """
    Convert Flex '#AARRGGBB' hex string to '#RRGGBB'.
    Returns None if unparseable.
    """
    s = argb.lstrip("#")
    if len(s) == 8:
        # AARRGGBB — drop alpha
        return "#{}".format(s[2:])
    if len(s) == 6:
        return "#{}".format(s)
    return None


class FlexClientHandler:
    """Handles one Commander TCP connection."""

    def __init__(self, conn: socket.socket, addr, handler_id: int,
                 on_spot_add: Callable,
                 on_spot_remove: Callable,
                 on_log: Optional[Callable] = None):
        self._conn        = conn
        self._addr        = addr
        self._id          = handler_id
        self._on_spot_add = on_spot_add
        self._on_remove   = on_spot_remove
        self._log         = on_log
        self._spot_index  = 1      # monotonic spot index returned to Commander
        self._spot_map:  dict[int, str] = {}   # index → callsign (for remove)
        self._handle:    str = ""              # set after handshake, used for S echo
        self._stop_evt   = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True,
                         name="flex-client-{}".format(self._id)).start()

    def stop(self):
        self._stop_evt.set()
        try: self._conn.close()
        except: pass

    def _send(self, line: str):
        try:
            self._conn.sendall((line + "\n").encode("ascii", errors="replace"))
        except OSError:
            pass

    def _info(self, msg: str):
        full = "[FlexSrv] client#{} {} — {}".format(self._id, self._addr[0], msg)
        print(full)
        if self._log:
            try: self._log(full)
            except: pass

    def _run(self):
        # Radio sends V (version) then H (handle) — client sends nothing during handshake
        # Per API Primer p.4: "the API sends two parameters to the client: V then H"
        handle = "{:08X}".format(random.randint(0x10000000, 0xFFFFFFFF))
        self._handle = handle
        self._send("V{}".format(_FLEX_VERSION))
        self._send("H{}".format(handle))
        self._info("connected — handle H{}".format(handle))

        # Send full connection burst exactly as a real Flex does (API Primer p.11)
        # Commander waits for S<h>|client 0x<handle> connected before sending spots
        h = handle
        self._send("M10000001|Client connected from IP {}".format(self._addr[0]))
        self._send("S{}|radio slices=1 panadapters=1 lineout_gain=50 lineout_mute=0"
                   " headphone_gain=50 headphone_mute=0 remote_on_enabled=0 pll_done=0"
                   " freq_error_ppb=0 cal_freq=15.000000 tnf_enabled=0"
                   " snap_tune_enabled=1 nickname=FlexBridge callsign=BRIDGE"
                   " binaural_rx=0 full_duplex_enabled=0 band_persistence_enabled=1"
                   " rtty_mark_default=2125 enforce_private_ip_connections=0"
                   " version={}".format(h, _FLEX_VERSION))
        self._send("S{}|radio filter_sharpness VOICE level=2 auto_level=1".format(h))
        self._send("S{}|radio filter_sharpness CW level=2 auto_level=1".format(h))
        self._send("S{}|radio filter_sharpness DIGITAL level=2 auto_level=1".format(h))
        self._send("S{}|radio static_net_params ip= gateway= netmask=".format(h))
        self._send("S{}|interlock timeout=0 acc_txreq_enable=0 rca_txreq_enable=0"
                   " acc_txreq_polarity=0 rca_txreq_polarity=0 tx1_enabled=1 tx1_delay=0"
                   " tx2_enabled=1 tx2_delay=0 tx3_enabled=1 tx3_delay=0"
                   " acc_tx_enabled=1 acc_tx_delay=0 tx_delay=0".format(h))
        self._send("S{}|panadapter 0x00000001 center=14.100000 bandwidth=0.200000"
                   " min_dbm=-125.00 max_dbm=0.00 fps=25 average=23"
                   " weighted_average=0 rfgain=0 rxant=ANT1 wide=0 loopa=0 loopb=0"
                   " band=20 daxiq=0 daxiq_rate=0 daxiq_clients=0 waterfall=0x42000001"
                   " min_bw=0.004991 max_bw=14.745601 xvtr= pre= ant_list=ANT1,ANT2".format(h))
        self._send("S{}|slice 0 RF_frequency=14.100 mode=USB rx_ant=ANT1"
                   " in_use=1 active=1 pan=0x00000001 wide=0"
                   " lock=0 step=100 step_list=1,10,50,100,500,1000,2000,3000"
                   " agc_mode=med agc_threshold=65 agc_off_level=10"
                   " nr=0 nr_level=50 nb=0 nb_level=50 anf=0 anf_level=50"
                   " filter_lo=-2400 filter_hi=2400 rit_on=0 rit_freq=0"
                   " xit_on=0 xit_freq=0 dax=0 dax_clients=0"
                   " mute=0 rxgain=0 audio_pan=0.5 audio_level=50"
                   " record=0 play=0 diversityEnabled=0 ant_list=ANT1,ANT2".format(h))
        # Critical: this line signals Commander that the session is fully established
        self._send("S{}|client 0x{} connected".format(h, h))
        self._info("sent full connection burst — waiting for spot add commands")

        buf = ""
        self._conn.settimeout(2.0)

        while not self._stop_evt.is_set():
            try:
                data = self._conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break

            buf += data.decode("ascii", errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line:
                    self._handle_command(line)

        self._info("disconnected")

    def _handle_command(self, line: str):
        m = _CMD_RE.match(line)
        if not m:
            return
        seq     = m.group(1)
        command = m.group(2).strip()

        cmd_lower = command.lower()

        # ── spot add ─────────────────────────────────────────────────────────
        if cmd_lower.startswith("spot add"):
            params_str = command[len("spot add"):].strip()
            kv = _parse_kv(params_str)

            callsign       = kv.get("callsign", "").upper()
            rx_freq        = kv.get("rx_freq", "0")
            tx_freq        = kv.get("tx_freq", "")
            mode_raw       = kv.get("mode", "usb").lower()
            color          = kv.get("color", "")
            bg_color       = kv.get("background_color", "")
            comment        = _decode_comment(kv.get("comment", ""))
            spotter        = _decode_comment(kv.get("spotter_callsign", ""))
            source         = _decode_comment(kv.get("source", "Flex"))
            priority       = kv.get("priority", "0")
            trigger_action = kv.get("trigger_action", "none")
            timestamp      = kv.get("timestamp", "")

            try:
                freq_hz = int(round(float(rx_freq) * 1_000_000))
            except ValueError:
                freq_hz = 0

            try:
                tx_freq_hz = int(round(float(tx_freq) * 1_000_000)) if tx_freq else 0
            except ValueError:
                tx_freq_hz = 0

            mode_tci  = _FLEX_MODE_MAP.get(mode_raw, "USB")
            fg_hex    = _argb_hex_to_rgb_hex(color) or "#0000FF"
            bg_hex    = _argb_hex_to_rgb_hex(bg_color) or "#FFFFFF"

            idx = self._spot_index
            self._spot_index += 1
            self._spot_map[idx] = callsign

            # ACK with spot index
            self._send("R{}|0|{}".format(seq, idx))

            if callsign and freq_hz:
                self._info("spot add #{} {} {:.3f}MHz {} src={} fg={} bg={}".format(
                    idx, callsign, freq_hz / 1e6, mode_tci, source, fg_hex, bg_hex))
                # Echo status message so FlexLib fires SpotAdded event on Commander side
                self._send(
                    "S{}|spot {} callsign={} rx_freq={:.6f} mode={}"
                    " color={} background_color={} comment={}"
                    " spotter_callsign={} source={} priority={}"
                    " trigger_action={} lifetime_seconds=300".format(
                        self._handle, idx, callsign, freq_hz / 1e6, mode_raw,
                        color or "#FF0000FF", bg_color or "#FFFFFFFF",
                        kv.get("comment", ""),
                        kv.get("spotter_callsign", ""), kv.get("source", ""),
                        priority, trigger_action))
                try:
                    self._on_spot_add({
                        "callsign":       callsign,
                        "freq_hz":        freq_hz,
                        "tx_freq_hz":     tx_freq_hz,
                        "mode":           mode_tci,
                        "fg_hex":         fg_hex,
                        "bg_hex":         bg_hex,
                        "comment":        comment,
                        "spotter":        spotter,
                        "source":         source,
                        "priority":       priority,
                        "trigger_action": trigger_action,
                        "timestamp":      timestamp,
                    })
                except Exception as e:
                    self._info("spot_add callback error: {}".format(e))

        # ── spot remove ───────────────────────────────────────────────────────
        elif cmd_lower.startswith("spot remove"):
            # Two formats: "spot remove <index>" or "spot remove callsign=X rx_freq=Y"
            params_str = command[len("spot remove"):].strip()
            kv_r = _parse_kv(params_str)
            if kv_r:
                # callsign= / rx_freq= format (RemoveSpot API)
                callsign = kv_r.get("callsign", "").upper()
                rx_freq_r = kv_r.get("rx_freq", "0")
                try:
                    freq_hz_r = int(round(float(rx_freq_r) * 1_000_000))
                except ValueError:
                    freq_hz_r = 0
                # find index by callsign
                idx = next((i for i, c in self._spot_map.items() if c == callsign), -1)
                if idx >= 0:
                    self._spot_map.pop(idx, None)
            else:
                # bare index format
                parts = params_str.split()
                idx = int(parts[0]) if parts and parts[0].isdigit() else -1
                callsign = self._spot_map.pop(idx, "")
                freq_hz_r = 0
            self._send("R{}|0|".format(seq))
            if callsign:
                self._info("spot remove #{} {}".format(idx, callsign))
                try:
                    self._on_remove(callsign, freq_hz_r)
                except Exception as e:
                    self._info("spot_remove callback error: {}".format(e))

        # ── spot clear_all ────────────────────────────────────────────────────
        elif cmd_lower in ("spot clear_all", "spot clearall", "spot clear"):
            self._spot_map.clear()
            self._send("R{}|0|".format(seq))
            self._info("spot clear_all")
            if self._on_remove:
                try:
                    self._on_remove("", 0)   # Bridge.py interprets empty callsign as clear-all
                except Exception:
                    pass

        # ── info → return fake radio description ─────────────────────────────
        elif cmd_lower == "info":
            self._info("CMD seq={} : info (returning fake radio info)".format(seq))
            self._send(
                "R{}|0|"
                "model=FLEX-6600 "
                "serial=FLEX-6600-0000001 "
                "version={} "
                "name=FlexBridge "
                "callsign=BRIDGE "
                "ip=127.0.0.1 "
                "port=4992 "
                "location= "
                "antenna_list=ANT1,ANT2 "
                "mic_list=MIC "
                "num_slices=1 "
                "num_panadapters=1 "
                "chassis_serial=FLEX-6600-0000001 "
                "region=EUR "
                "screensaver=model "
                "gateway= "
                "subnet= "
                "dns=".format(seq, _FLEX_VERSION))

        # ── meter list → return minimal meter list ────────────────────────────
        elif cmd_lower == "meter list":
            self._info("CMD seq={} : meter list (returning minimal)".format(seq))
            # Minimal meter list — real Flex returns many meters; Commander needs at least one
            self._send("R{}|0|"
                       "1.src=slc.0 1.num=1 1.nam=SIGL 1.low=-200.0 1.hi=20.0 1.desc=Signal_Level "
                       "2.src=slc.0 2.num=2 2.nam=SIGR 2.low=-200.0 2.hi=20.0 2.desc=Signal_Right "
                       "3.src=TX.0001 3.num=3 3.nam=FWDPWR 3.low=-150.0 3.hi=10.0 3.desc=Fwd_Power "
                       "4.src=TX.0001 4.num=4 4.nam=REFPWR 4.low=-150.0 4.hi=10.0 4.desc=Ref_Power".format(seq))

        # ── everything else → ACK and ignore ─────────────────────────────────
        else:
            self._info("CMD seq={} : {}".format(seq, command[:120]))
            self._send("R{}|0|".format(seq))


class FlexServer:
    """
    TCP server that listens on port 4992 and pretends to be a FlexRadio.
    Commander (configured as Flex Signature) connects here and sends spot directives.

    Callbacks (same signature as CommanderSpotsListener):
        on_spot_add(spot_dict)          — spot_dict keys: callsign, freq_hz, mode,
                                          fg_hex, bg_hex, comment, spotter, source
        on_spot_delete(callsign, freq)
        on_spot_clearall()              — called when client disconnects

    Usage:
        srv = FlexServer(
            on_spot_add=handler_add,
            on_spot_delete=handler_del,
            on_spot_clearall=handler_clear,
            port=4992,
        )
        srv.start()
        ...
        srv.stop()
    """

    def __init__(
        self,
        on_spot_add:      Optional[Callable] = None,
        on_spot_delete:   Optional[Callable] = None,
        on_spot_clearall: Optional[Callable] = None,
        port:    int = _DEFAULT_PORT,
        bind_ip: str = _DEFAULT_BIND,
        on_log:  Optional[Callable] = None,
    ):
        self._on_add     = on_spot_add
        self._on_delete  = on_spot_delete
        self._on_clear   = on_spot_clearall
        self._port       = port
        self._bind_ip    = bind_ip
        self._on_log     = on_log
        self._stop_evt   = threading.Event()
        self._thread     = threading.Thread(
            target=self._run, name="FlexSrv", daemon=True)
        self._clients:   list[FlexClientHandler] = []
        self._client_id  = 0
        self._lock       = threading.Lock()
        self.status      = "Idle"
        self.spot_count  = 0

    def start(self): self._thread.start()

    def stop(self):
        self._stop_evt.set()
        with self._lock:
            for c in self._clients:
                c.stop()
        self.status = "Stopped"

    def is_running(self): return self._thread.is_alive()

    def _run(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((self._bind_ip, self._port))
            srv.listen(4)
            srv.settimeout(1.0)
            self.status = "Listening :{}".format(self._port)
            print("[FlexSrv] Listening on {}:{}".format(self._bind_ip, self._port))
        except OSError as e:
            self.status = "Error: {}".format(e)
            print("[FlexSrv] Bind error: {}".format(e))
            return

        while not self._stop_evt.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            self._client_id += 1
            cid = self._client_id
            print("[FlexSrv] Commander connected from {}:{}".format(addr[0], addr[1]))
            self.status = "Connected — {}".format(addr[0])

            handler = FlexClientHandler(
                conn, addr, cid,
                on_spot_add=self._on_spot_add_wrap,
                on_spot_remove=self._on_remove_wrap,
                on_log=self._on_log,
            )
            with self._lock:
                self._clients.append(handler)
            handler.start()

            # Watch for dead clients in background
            threading.Thread(target=self._watch_client,
                             args=(handler,), daemon=True).start()

        srv.close()
        self.status = "Stopped"
        print("[FlexSrv] Stopped.")

    def _watch_client(self, handler: FlexClientHandler):
        """Wait for client thread to finish, then clean up."""
        # Poll until handler thread ends
        import time
        while handler._stop_evt.is_set() is False:
            time.sleep(0.5)
            # Check if thread died naturally
            for t in threading.enumerate():
                if t.name == "flex-client-{}".format(handler._id):
                    break
            else:
                break
        with self._lock:
            if handler in self._clients:
                self._clients.remove(handler)
        if self._on_clear:
            try: self._on_clear()
            except: pass
        if not self._clients:
            self.status = "Listening :{}".format(self._port)

    def _on_spot_add_wrap(self, spot: dict):
        self.spot_count += 1
        self.status = "OK — {} spots".format(self.spot_count)
        if self._on_add:
            self._on_add(spot)

    def _on_remove_wrap(self, callsign: str, freq: int):
        if self._on_delete:
            self._on_delete(callsign, freq)


# ── Self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import time, json

    def on_add(s):
        print("ADD:", json.dumps(s, indent=2))

    def on_del(call, freq):
        print("DEL:", call, freq)

    def on_clear():
        print("CLEARALL")

    print("Flex SmartSDR fake radio — listening TCP :4992")
    print("Configure Commander: radio type = Flex Signature, host = 127.0.0.1")
    srv = FlexServer(on_add, on_del, on_clear)
    srv.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()
