# spotcollector_db.py  —  SpotCollector Access DB polling  (confirmed schema)
#
# Schema confirmed by reverse-engineering A92GE's Thetis_SpotViewer.exe:
#   Table : [Spots]
#   Index : [Index]  (auto-increment watermark)
#   Cols  : Callsign, Frequency (kHz float), Band, Mode, SpotTime,
#           LoTW, eQSL, NeedCategory, Source, Notes, DXGrid,
#           Azimuth, Cont, DXCCCountry
#
# Registry (confirmed):
#   HKCU\Software\VB and VBA Program Settings\SpotCollector\Spot
#   value: SpotDatabasePathname
#
# Requires: pip install pyodbc
#           Microsoft Access Database Engine 64-bit

from __future__ import annotations
import os, threading, time, winreg
from typing import Optional, Callable

try:
    import pyodbc
except ImportError:
    pyodbc = None  # type: ignore

# ─────────────────────────────────────────────────────────────────────────────
# Registry / connection
# ─────────────────────────────────────────────────────────────────────────────

_REG_KEY   = r"Software\VB and VBA Program Settings\SpotCollector\Spot"
_REG_VALUE = "SpotDatabasePathname"

def get_spotcollector_db_path() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY) as k:
            val, _ = winreg.QueryValueEx(k, _REG_VALUE)
            return val
    except (FileNotFoundError, OSError) as e:
        raise RuntimeError(
            f"SpotCollector DB not in registry "
            f"(HKCU\\{_REG_KEY}\\{_REG_VALUE}): {e}") from e

def open_spotcollector_db(db_path: str):
    if pyodbc is None:
        raise RuntimeError("pyodbc not installed — run: pip install pyodbc")
    if not os.path.isfile(db_path):
        raise RuntimeError(f"SpotCollector DB not found: {db_path}")
    conn_str = (
        f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};"
        f"DBQ={db_path};ReadOnly=1;"
    )
    return pyodbc.connect(conn_str, autocommit=True)

# ─────────────────────────────────────────────────────────────────────────────
# Confirmed SQL  (exact column names from A92GE binary)
# ─────────────────────────────────────────────────────────────────────────────

# QSX candidate column names (SpotCollector split frequency field)
_QSX_CANDIDATES = ["QSXFrequency", "QSX", "DXQSX", "QSX_Freq", "FreqQSX", "QSXFreq"]

def _probe_qsx_column(conn) -> str:
    """Return the QSX column name if found in Spots table, else empty string."""
    try:
        cols = {c.column_name.lower() for c in conn.cursor().columns(table="Spots")}
        for cand in _QSX_CANDIDATES:
            if cand.lower() in cols:
                print(f"[SpotCollectorDB] QSX column found: {cand}")
                return cand
    except Exception:
        pass
    return ""

def _build_queries(qsx_col: str, needed_col: str = "",
                   cqz_col: str = "", state_col: str = ""):
    """Build SQL queries with optional extra columns."""
    extras = ""
    if qsx_col:
        extras += f",[{qsx_col}] AS qsx_"
    if needed_col:
        extras += f",[{needed_col}] AS needed_str_"
    if cqz_col:
        extras += f",[{cqz_col}] AS cqz_"
    if state_col:
        extras += f",[{state_col}] AS state_"
    spot_q = f"""
    SELECT
        [Index]        AS idx,
        [Callsign]     AS call_,
        [Frequency]    AS freq_,
        [Band]         AS band_,
        [Mode]         AS mode_,
        [SpotTime]     AS stime,
        [LoTW]         AS lotw_,
        [eQSL]         AS eqsl_,
        [NeedCategory] AS needcategory_,
        [Source]       AS source_,
        [Notes]        AS notes_
        {extras}
    FROM [Spots]
    WHERE [Index] > ?
    ORDER BY [Index] ASC
"""
    preload_q = f"""
    SELECT TOP {{n}}
        [Index]        AS idx,
        [Callsign]     AS call_,
        [Frequency]    AS freq_,
        [Band]         AS band_,
        [Mode]         AS mode_,
        [SpotTime]     AS stime,
        [LoTW]         AS lotw_,
        [eQSL]         AS eqsl_,
        [NeedCategory] AS needcategory_,
        [Source]       AS source_,
        [Notes]        AS notes_
        {extras}
    FROM [Spots]
    ORDER BY [Index] DESC
"""
    return spot_q, preload_q


def _probe_needed_column(conn) -> str:
    """Probe for the SpotCollector [Needed] text-flag column (P/PZ/DP/etc.)."""
    try:
        cols = {c.column_name for c in conn.cursor().columns(table="Spots")}
        for cand in ("Needed", "NeededFlags", "NeedFlags", "NeedStr"):
            if cand in cols:
                print(f"[SpotCollectorDB] Needed-flag column found: {cand}")
                return cand
    except Exception:
        pass
    return ""


# CQ-zone and State column probes (for WAZ / WAS award lookups)
_CQZ_CANDIDATES = ["CQ", "CQZ", "CQ_Zone", "CQZone"]
_STATE_CANDIDATES = ["State", "USState", "US_State", "StateCode"]

def _probe_cqz_column(conn) -> str:
    """Probe for the CQ Zone column in Spots table."""
    try:
        cols = {c.column_name for c in conn.cursor().columns(table="Spots")}
        for cand in _CQZ_CANDIDATES:
            if cand in cols:
                print(f"[SpotCollectorDB] CQ Zone column found: {cand}")
                return cand
    except Exception:
        pass
    return ""

def _probe_state_column(conn) -> str:
    """Probe for a US State column in Spots table."""
    try:
        cols = {c.column_name for c in conn.cursor().columns(table="Spots")}
        for cand in _STATE_CANDIDATES:
            if cand in cols:
                print(f"[SpotCollectorDB] State column found: {cand}")
                return cand
    except Exception:
        pass
    return ""

# Default queries (no QSX) — overridden per-connection if column found
_SPOT_QUERY, _PRELOAD_QUERY = _build_queries("")

# ─────────────────────────────────────────────────────────────────────────────
# NeedCategory → need_key
#
# DXLab SpotCollector NeedCategory (numeric):
#   0 = Unneeded  (worked + confirmed)
#   1 = Verified DX  (worked, confirmed LoTW/eQSL)
#   2 = Unconfirmed DX  (worked, not yet confirmed)
#   3 = Unworked — new band or mode
#   4 = Unworked — new DXCC counter
#   5 = Tagged / special callsign
# Inferred from A92GE spot_logic.py NEEDCATEGORY_TO_KEY strings.
# ─────────────────────────────────────────────────────────────────────────────

NEEDCAT_TO_NEED_KEY = {
    0: "unneeded_dx",
    1: "verified_dx",
    2: "unconfirmed_dx",
    3: "unworked_dx_band_mode",
    4: "unworked_dx_counter",
    5: "special_callsign",
}

def _need_key_from_row(row) -> str:
    cat = getattr(row, "needcategory_", None)
    if cat is not None:
        try:
            return NEEDCAT_TO_NEED_KEY.get(int(cat), "unconfirmed_dx")
        except (ValueError, TypeError):
            pass
    # Fallback: infer from LoTW / eQSL columns
    def flag(attr):
        v = getattr(row, attr, None)
        if isinstance(v, bool): return v
        if isinstance(v, int):  return v != 0
        if isinstance(v, str):  return v.strip().upper() in ("Y","YES","1","TRUE","X")
        return False
    return "verified_dx" if (flag("lotw_") or flag("eqsl_")) else "unconfirmed_dx"

def _bg_key_from_row(row) -> str:
    def flag(attr):
        v = getattr(row, attr, None)
        if isinstance(v, bool): return v
        if isinstance(v, int):  return v != 0
        if isinstance(v, str):  return v.strip().upper() in ("Y","YES","1","TRUE","X")
        return False
    l, e = flag("lotw_"), flag("eqsl_")
    if l and e: return "bg_lotw_eqsl"
    if l:       return "bg_lotw"
    if e:       return "bg_eqsl"
    return "bg_normal"

def _row_to_spot(row) -> Optional[dict]:
    call    = getattr(row, "call_",  None)
    freq_raw= getattr(row, "freq_",  None)
    if not call or freq_raw is None:
        return None
    try:
        freq_hz = int(float(freq_raw) * 1000)
    except (ValueError, TypeError):
        return None
    # QSX (split TX frequency) — 0 if column not present
    qsx_raw = getattr(row, "qsx_", None)
    try:
        qsx_hz = int(float(qsx_raw) * 1000) if qsx_raw else 0
    except (ValueError, TypeError):
        qsx_hz = 0
    # need_cat as integer for filter comparisons.
    # SC's NeedCategory ranges from -2..2; use -99 as "missing/unparseable" sentinel.
    cat = getattr(row, "needcategory_", None)
    try:
        need_cat = int(cat) if cat is not None else -99
    except (ValueError, TypeError):
        need_cat = -99
    needed_str = (getattr(row, "needed_str_", None) or "")
    if not isinstance(needed_str, str):
        needed_str = str(needed_str)
    # CQ Zone (for WAZ award lookup)
    cqz_raw = getattr(row, "cqz_", None)
    try:
        cq_zone = int(cqz_raw) if cqz_raw is not None else 0
    except (ValueError, TypeError):
        cq_zone = 0
    # US State (for WAS award lookup)
    state_raw = getattr(row, "state_", None)
    state_str = (state_raw or "").strip().upper() if isinstance(state_raw, str) else ""
    return {
        "callsign":    call.strip().upper(),
        "frequency":   freq_hz,
        "qsx_hz":      qsx_hz,
        "mode":        (getattr(row, "mode_",   None) or "USB").strip().upper(),
        "band":        (getattr(row, "band_",   None) or "").strip().upper(),
        "comment":     (getattr(row, "notes_",  None) or "").strip(),
        "spotter":     (getattr(row, "source_", None) or "").strip(),
        "need_key":    _need_key_from_row(row),
        "need_cat":    need_cat,
        "needed_str":  needed_str.strip(),
        "bg_key":      _bg_key_from_row(row),
        "cq_zone":     cq_zone,
        "state":       state_str,
        "source":      "SpotCollector",
    }

# ─────────────────────────────────────────────────────────────────────────────
# Poller thread
# ─────────────────────────────────────────────────────────────────────────────

class SpotCollectorPoller:
    """
    Polls the SpotCollector Access DB every `interval` seconds.
    Calls on_spot(spot_dict) for each new spot that passes filters.

    filter_band  : str  — band string to match (e.g. "20M"), empty = all bands
    filter_needs : set  — set of NeedCategory ints to include, None = all
                          e.g. {1,2,3,4,5} excludes unneeded (cat 0)

        poller = SpotCollectorPoller(
            on_spot=handler, interval=15,
            filter_band="20M", filter_needs={1,2,3,4,5})
        poller.start()
        ...
        poller.stop()
    """

    def __init__(self, on_spot: Callable, interval: float = 15.0,
                 db_path: Optional[str] = None, preload: int = 50,
                 filter_band: str = "",
                 filter_needs: Optional[set] = None,
                 on_spot_update: Optional[Callable] = None,
                 on_spot_expire: Optional[Callable] = None,
                 rescan_interval: float = 120.0):
        self._on_spot        = on_spot
        self._on_spot_update = on_spot_update   # fires when NeedCategory changes
        self._on_spot_expire = on_spot_expire   # fires when spot leaves SC DB (TTL)
        self._interval       = interval
        self._rescan_interval= rescan_interval
        self._db_path        = db_path
        self._preload        = preload
        self._filter_band    = filter_band.strip().upper()
        self._filter_needs   = filter_needs
        self._known_cats     = {}    # {idx: (callsign, need_cat)} for change detection
        self._last_rescan    = 0.0
        self._stop_evt       = threading.Event()
        self._thread         = threading.Thread(
            target=self._run, name="SCPoller", daemon=True)
        self.status          = "Idle"

    def start(self): self._thread.start()
    def stop(self):  self._stop_evt.set()
    def is_running(self): return self._thread.is_alive()

    def _passes_filter(self, spot: dict) -> bool:
        if self._filter_band:
            if spot.get("band", "").upper() != self._filter_band:
                return False
        if self._filter_needs is not None:
            if spot.get("need_cat", -99) not in self._filter_needs:
                return False
        return True

    def _rescan(self, cursor):
        """
        Re-check NeedCategory for known spots and detect spots that SC has
        expired (TTL). Called every rescan_interval seconds.
        - on_spot_update fired for NeedCategory changes
        - on_spot_expire fired for spots no longer in SC DB
        """
        if not self._known_cats:
            return
        # Limit to 200 most recent indices
        indices = sorted(self._known_cats.keys())[-200:]
        in_clause = ",".join(str(i) for i in indices)
        try:
            cursor.execute(
                f"SELECT [Index] AS idx, [Callsign] AS call_, "
                f"[Frequency] AS freq_, [Band] AS band_, [Mode] AS mode_, "
                f"[LoTW] AS lotw_, [eQSL] AS eqsl_, "
                f"[NeedCategory] AS needcategory_, "
                f"[Source] AS source_, [Notes] AS notes_ "
                f"FROM [Spots] WHERE [Index] IN ({in_clause})")
            rows = cursor.fetchall()
        except Exception as e:
            print(f"[SpotCollectorDB] Rescan error: {e}")
            return

        returned_idx = set()
        changed = 0
        for row in rows:
            idx = getattr(row, "idx", None)
            if idx is None:
                continue
            idx = int(idx)
            returned_idx.add(idx)

            # NeedCategory change detection
            if self._on_spot_update:
                cat_raw = getattr(row, "needcategory_", None)
                try:
                    new_cat = int(cat_raw) if cat_raw is not None else -1
                except Exception:
                    new_cat = -1
                known = self._known_cats.get(idx)
                if known and new_cat != known[1]:
                    call = getattr(row, "call_", None)
                    if call:
                        self._known_cats[idx] = (call.strip().upper(), new_cat)
                    spot = _row_to_spot(row)
                    if spot:
                        self._on_spot_update(spot)
                        changed += 1

        # Expiry detection — indices we know about but SC no longer has
        if self._on_spot_expire:
            expired_idx = set(indices) - returned_idx
            expired = 0
            for idx in expired_idx:
                known = self._known_cats.pop(idx, None)
                if known:
                    callsign = known[0]
                    self._on_spot_expire(callsign)
                    expired += 1
            if expired:
                print(f"[SpotCollectorDB] Rescan: {expired} spot(s) expired (SC TTL)")

        if changed:
            print(f"[SpotCollectorDB] Rescan: {changed} spot(s) changed NeedCategory")

    def _run(self):
        last_idx   = -1
        conn       = None
        spot_q     = _SPOT_QUERY
        preload_q  = _PRELOAD_QUERY
        while not self._stop_evt.is_set():
            try:
                if conn is None:
                    db_path    = self._db_path or get_spotcollector_db_path()
                    conn       = open_spotcollector_db(db_path)
                    qsx_col    = _probe_qsx_column(conn)
                    needed_col = _probe_needed_column(conn)
                    cqz_col    = _probe_cqz_column(conn)
                    state_col  = _probe_state_column(conn)
                    spot_q, preload_q = _build_queries(
                        qsx_col, needed_col, cqz_col, state_col)
                    self.status = "Connected"
                    print(f"[SpotCollectorDB] Connected: {db_path}"
                          + (f" | QSX col: {qsx_col}" if qsx_col else " | no QSX col")
                          + (f" | Needed col: {needed_col}" if needed_col else " | no Needed col")
                          + (f" | CQZ col: {cqz_col}" if cqz_col else "")
                          + (f" | State col: {state_col}" if state_col else ""))

                cursor = conn.cursor()
                if last_idx < 0:
                    cursor.execute(preload_q.format(n=self._preload))
                    rows = list(reversed(cursor.fetchall()))
                else:
                    cursor.execute(spot_q, (last_idx,))
                    rows = cursor.fetchall()

                new = painted = 0
                for row in rows:
                    idx = getattr(row, "idx", None)
                    if idx is not None:
                        idx_int = int(idx)
                        last_idx = max(last_idx, idx_int)
                        # Track for NeedCategory rescan
                        call_r = getattr(row, "call_", "") or ""
                        cat_r  = getattr(row, "needcategory_", None)
                        try: cat_i = int(cat_r) if cat_r is not None else -1
                        except: cat_i = -1
                        if call_r:
                            self._known_cats[idx_int] = (call_r.strip().upper(), cat_i)
                    spot = _row_to_spot(row)
                    if spot:
                        new += 1
                        if self._passes_filter(spot):
                            self._on_spot(spot)
                            painted += 1

                # Prune _known_cats to last 500
                if len(self._known_cats) > 500:
                    keep = sorted(self._known_cats.keys())[-500:]
                    self._known_cats = {k: self._known_cats[k] for k in keep}

                if new:
                    band_info = f" band={self._filter_band}" if self._filter_band else ""
                    print(f"[SpotCollectorDB] {new} new, {painted} painted{band_info}")

                # NeedCategory rescan
                now = time.time()
                if (self._on_spot_update and self._rescan_interval > 0 and
                        now - self._last_rescan >= self._rescan_interval):
                    self._rescan(cursor)
                    self._last_rescan = now

                self.status = f"OK — {painted}/{new} painted"

            except Exception as e:
                print(f"[SpotCollectorDB] Error: {e}")
                self.status = f"Error: {e}"
                try:
                    if conn: conn.close()
                except Exception: pass
                conn = None

            self._stop_evt.wait(self._interval)

        try:
            if conn: conn.close()
        except Exception: pass
        print("[SpotCollectorDB] Stopped.")


if __name__ == "__main__":
    import json
    def show(s): print(json.dumps(s, indent=2, default=str))
    try:
        path = get_spotcollector_db_path()
        print(f"DB: {path}")
    except RuntimeError as e:
        path = input(f"{e}\nEnter path manually: ").strip()
    p = SpotCollectorPoller(on_spot=show, interval=10, db_path=path)
    p.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        p.stop()
