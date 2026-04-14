# dxkeeper_db.py  —  Stage 4: DXKeeper log DB polling
#
# Polls DXKeeper's Access .mdb for new QSO entries.
# On new QSO: Bridge repaints the spot from its current color to
# dx_unneeded (black = already worked).
#
# Registry discovery:
#   DXLab apps use VB6 SaveSetting which writes to:
#   HKCU\Software\VB and VBA Program Settings\{AppName}\{Section}\{Key}
#   We enumerate ALL subkeys under DXKeeper and scan for DatabasePathname.
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
# Registry discovery  (robust: enumerates all DXKeeper subkeys)
# ─────────────────────────────────────────────────────────────────────────────

_DXLAB_ROOT = r"Software\VB and VBA Program Settings"
_DK_APP     = "DXKeeper"

def _all_reg_values(hkey, path: str) -> list[tuple[str, str, str]]:
    """
    Recursively enumerate every value under hkey\\path.
    Returns list of (full_key_path, value_name, value_data).
    """
    results = []
    try:
        with winreg.OpenKey(hkey, path) as k:
            # Enumerate values at this level
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(k, i)
                    if isinstance(data, str):
                        results.append((path, name, data))
                    i += 1
                except OSError:
                    break
            # Recurse into subkeys
            j = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, j)
                    results.extend(_all_reg_values(hkey, path + "\\" + sub))
                    j += 1
                except OSError:
                    break
    except OSError:
        pass
    return results

def get_dxkeeper_db_path() -> str:
    """
    Find DXKeeper's log DB path from the Windows registry.

    Strategy (learned from error: value name is NOT 'DatabasePathname'):
    1. Recursively enumerate ALL string values under HKCU\\...\\DXKeeper
    2. Return the first value whose data points to an existing .mdb/.accdb file
    3. Prefer values whose name contains 'log', 'database', or 'path'
    4. Fall back to any .mdb/.accdb path found
    """
    dk_root = f"{_DXLAB_ROOT}\\{_DK_APP}"

    all_values = _all_reg_values(winreg.HKEY_CURRENT_USER, dk_root)

    if not all_values:
        raise RuntimeError(
            f"DXKeeper registry key not found or empty: HKCU\\{dk_root}\n"
            "Is DXKeeper installed and has it been run at least once?")

    # Phase 1: look for values whose name hints at log/database and data = .mdb/.accdb
    preferred_keywords = ("log", "database", "path", "file", "mdb", "accdb")
    candidates = []
    for (key_path, val_name, val_data) in all_values:
        lower_data = val_data.lower()
        if lower_data.endswith((".mdb", ".accdb")):
            name_lower = val_name.lower()
            priority = any(kw in name_lower for kw in preferred_keywords)
            candidates.append((priority, key_path, val_name, val_data))

    # Sort: preferred names first, then existence
    candidates.sort(key=lambda x: (not x[0],))

    for (_, key_path, val_name, val_data) in candidates:
        if os.path.exists(val_data):
            print(f"[DXKeeperDB] Found: HKCU\\{key_path}\\{val_name!r} = {val_data}")
            return val_data
        else:
            print(f"[DXKeeperDB] Path in registry but file missing: {val_data}")

    # Phase 2: if no .mdb/.accdb value found, show all values for diagnosis
    found_paths = [f"  HKCU\\{kp}\\{vn!r} = {vd!r}"
                   for kp, vn, vd in all_values
                   if vd.lower().endswith((".mdb", ".accdb"))]
    all_keys_seen = sorted(set(kp for kp, _, _ in all_values))

    if found_paths:
        detail = "Found .mdb paths but files don't exist:\n" + "\n".join(found_paths)
    else:
        detail = ("No .mdb/.accdb value found in any DXKeeper subkey.\n"
                  "Subkeys searched:\n" +
                  "\n".join(f"  HKCU\\{k}" for k in all_keys_seen))

    raise RuntimeError(
        f"{detail}\n\n"
        "Set the path manually:\n"
        "  Bridge Configuration → Network → DK DB Path")

def open_dxkeeper_db(db_path: str):
    if pyodbc is None:
        raise RuntimeError("pyodbc not installed — run: pip install pyodbc")
    if not os.path.isfile(db_path):
        raise RuntimeError(f"DXKeeper DB not found: {db_path}")
    conn_str = (
        f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};"
        f"DBQ={db_path};ReadOnly=1;"
    )
    return pyodbc.connect(conn_str, autocommit=True)

# ─────────────────────────────────────────────────────────────────────────────
# Schema discovery  (DXKeeper table/column names vary by version)
# ─────────────────────────────────────────────────────────────────────────────

_LOG_TABLE_CANDIDATES = ["QSOs", "Log", "DXDB", "QSOLog", "log", "ADIF"]
_PK_CANDIDATES   = ["APP_DXKeeper_QSO_NUMBER", "PrimaryKey", "ADIF_RECNO", "ID", "RowID", "RecordID", "AutoKey"]
_CALL_CANDIDATES = ["Call", "CALL", "Callsign", "DX_Call", "Dx_Call"]
_BAND_CANDIDATES = ["Band", "BAND", "QSOBand", "BandUsed"]
_MODE_CANDIDATES = ["Mode", "MODE", "QSOMode", "ModeUsed"]
_TIME_CANDIDATES = ["QSO_Begin", "QSO_End", "QSO_Date", "QSODate", "Date", "QSO_DATE", "TimeOn", "Time_On", "Timestamp"]

def _discover_log_schema(conn) -> dict:
    cursor = conn.cursor()
    tables = [t.table_name for t in cursor.tables(tableType="TABLE")]

    table = None
    for c in _LOG_TABLE_CANDIDATES:
        if c in tables:
            table = c
            break
    if table is None:
        table = tables[0] if tables else "QSOs"
    print(f"[DXKeeperDB] Using table '{table}' (available: {tables})")

    cols = {c.column_name.lower(): c.column_name
            for c in cursor.columns(table=table)}
    print(f"[DXKeeperDB] Columns: {list(cols.values())}")

    def pick(*candidates):
        for c in candidates:
            if c.lower() in cols:
                return cols[c.lower()]
        return None

    # Time candidates — DXKeeper ADIF uses QSO_Date + Time_On separately
    time_col  = pick("QSO_Date", "QSODate", "Date", "QSO_DATE", "Timestamp")
    timeon_col = pick("Time_On", "TimeOn", "Time", "QSO_Time")

    schema = {
        "_table":    table,
        "_all_cols": list(cols.values()),
        "pk":        pick(*_PK_CANDIDATES),
        "call":      pick(*_CALL_CANDIDATES),
        "band":      pick(*_BAND_CANDIDATES),
        "mode":      pick(*_MODE_CANDIDATES),
        "qso_date":  time_col,
        "time_on":   timeon_col,
        "comment":   pick("Comment", "comment", "Note", "Remarks", "Notes"),
    }
    print(f"[DXKeeperDB] Schema: " +
          ", ".join(f"{k}={v}" for k, v in schema.items()
                    if not k.startswith("_") and v))

    if not schema["pk"] and not schema["qso_date"]:
        print("[DXKeeperDB] Warning: no PK and no time column — cannot poll incrementally")
    elif not schema["pk"]:
        print(f"[DXKeeperDB] No PK column — using time-based watermark "
              f"({schema['qso_date']}/{schema['time_on']})")
    return schema

# ─────────────────────────────────────────────────────────────────────────────
# Row → QSO dict
# ─────────────────────────────────────────────────────────────────────────────

def _row_to_qso(row, schema: dict) -> Optional[dict]:
    def col(key):
        c = schema.get(key)
        return getattr(row, c, None) if c else None

    call = col("call")
    if not call:
        return None
    return {
        "callsign": call.strip().upper(),
        "band":     (col("band")     or "").strip(),
        "mode":     (col("mode")     or "").strip().upper(),
        "qso_date": str(col("qso_date") or ""),
        "time_on":  str(col("time_on")  or ""),
        "comment":  (col("comment")  or "").strip(),
        "source":   "DXKeeper",
    }

# ─────────────────────────────────────────────────────────────────────────────
# Poller thread
# ─────────────────────────────────────────────────────────────────────────────

class DXKeeperPoller:
    """
    Polls DXKeeper's log DB every `interval` seconds for new QSOs.
    Calls on_qso(qso_dict) for each new entry.

    qso_dict keys: callsign, band, mode, qso_date, comment, source

        poller = DXKeeperPoller(on_qso=handler, interval=15)
        poller.start()
        poller.stop()
    """

    def __init__(self, on_qso: Callable, interval: float = 15.0,
                 db_path: Optional[str] = None):
        self._on_qso   = on_qso
        self._interval = interval
        self._db_path  = db_path   # None = read from registry
        self._stop_evt = threading.Event()
        self._thread   = threading.Thread(
            target=self._run, name="DKPoller", daemon=True)
        self.status    = "Idle"

    def start(self): self._thread.start()
    def stop(self):  self._stop_evt.set()
    def is_running(self): return self._thread.is_alive()

    def _run(self):
        last_pk       = -1
        last_time_key = None   # fallback: (qso_date_str, time_on_str)
        conn   = None
        schema = None

        while not self._stop_evt.is_set():
            try:
                if conn is None:
                    db_path = self._db_path or get_dxkeeper_db_path()
                    conn    = open_dxkeeper_db(db_path)
                    schema  = _discover_log_schema(conn)
                    self.status = "Connected"
                    print(f"[DXKeeperDB] Connected: {db_path}")

                table   = schema["_table"]
                pk_col  = schema.get("pk")
                dt_col  = schema.get("qso_date")
                ton_col = schema.get("time_on")
                cursor  = conn.cursor()

                # ── Strategy A: PK watermark ──────────────────────────────
                if pk_col:
                    if last_pk < 0:
                        cursor.execute(f"SELECT MAX([{pk_col}]) FROM [{table}]")
                        row = cursor.fetchone()
                        last_pk = int(row[0]) if (row and row[0] is not None) else 0
                        print(f"[DXKeeperDB] Bootstrap PK={last_pk}")
                        self.status = f"OK — PK>{last_pk}"
                        self._stop_evt.wait(self._interval)
                        continue

                    cursor.execute(
                        f"SELECT * FROM [{table}] "
                        f"WHERE [{pk_col}] > ? "
                        f"ORDER BY [{pk_col}] ASC",
                        (last_pk,))
                    rows = cursor.fetchall()
                    new  = 0
                    for row in rows:
                        pk = getattr(row, pk_col, None)
                        if pk is not None:
                            last_pk = max(last_pk, int(pk))
                        qso = _row_to_qso(row, schema)
                        if qso:
                            self._on_qso(qso)
                            new += 1
                    if new:
                        print(f"[DXKeeperDB] {new} new QSO(s) via PK")
                    self.status = f"OK — PK>{last_pk}"

                # ── Strategy B: time-based watermark (no PK) ─────────────
                elif dt_col:
                    if last_time_key is None:
                        # Bootstrap: record the latest QSO timestamp
                        order = f"[{dt_col}] DESC"
                        if ton_col:
                            order += f", [{ton_col}] DESC"
                        cursor.execute(
                            f"SELECT TOP 1 [{dt_col}]"
                            + (f", [{ton_col}]" if ton_col else "") +
                            f" FROM [{table}] ORDER BY {order}")
                        row = cursor.fetchone()
                        if row:
                            last_time_key = (str(row[0]), str(row[1]) if ton_col else "")
                        else:
                            last_time_key = ("", "")
                        print(f"[DXKeeperDB] Bootstrap time={last_time_key}")
                        self.status = f"OK — time watermark set"
                        self._stop_evt.wait(self._interval)
                        continue

                    # Fetch all rows, filter client-side (Access has poor datetime parameterisation)
                    order = f"[{dt_col}] ASC"
                    if ton_col:
                        order += f", [{ton_col}] ASC"
                    cursor.execute(f"SELECT * FROM [{table}] ORDER BY {order}")
                    rows = cursor.fetchall()
                    new  = 0
                    for row in rows:
                        dt  = str(getattr(row, dt_col, "") or "")
                        ton = str(getattr(row, ton_col, "") or "") if ton_col else ""
                        key = (dt, ton)
                        if key <= last_time_key:
                            continue
                        last_time_key = key
                        qso = _row_to_qso(row, schema)
                        if qso:
                            self._on_qso(qso)
                            new += 1
                    if new:
                        print(f"[DXKeeperDB] {new} new QSO(s) via time watermark")
                    self.status = f"OK — time>{last_time_key[0]}"

                else:
                    self.status = "Error: no PK or time column in QSOs table"
                    print("[DXKeeperDB] Cannot poll — no usable watermark column")
                    self._stop_evt.wait(self._interval)
                    continue

            except Exception as e:
                print(f"[DXKeeperDB] Error: {e}")
                self.status = f"Error: {e}"
                try:
                    if conn: conn.close()
                except Exception: pass
                conn          = None
                schema        = None
                last_pk       = -1
                last_time_key = None

            self._stop_evt.wait(self._interval)

        try:
            if conn: conn.close()
        except Exception: pass
        print("[DXKeeperDB] Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
# DXKeeperCache — bulk worked/confirmed lookup for spot annotation
# ─────────────────────────────────────────────────────────────────────────────

class DXKeeperCache:
    """
    Background cache of worked and confirmed callsigns from the DXKeeper log.

    worked_status(callsign) -> "confirmed" | "worked" | None

    Refreshes every `refresh_interval` seconds (default 5 min).
    "confirmed" means LoTW or eQSL QSL received.

    Usage:
        cache = DXKeeperCache()
        cache.start()
        s = cache.worked_status("JA1ABC")   # "confirmed", "worked", or None
        cache.stop()
    """

    def __init__(self, refresh_interval: float = 300.0,
                 db_path: Optional[str] = None) -> None:
        self._interval   = refresh_interval
        self._db_path    = db_path
        self._worked:    set[str] = set()
        self._confirmed: set[str] = set()
        self._lock       = threading.Lock()
        self._stop_evt   = threading.Event()
        self._thread     = threading.Thread(
            target=self._run, name="DXKeeperCache", daemon=True)
        self.status_str: str = "Idle"
        self.worked_count    = 0
        self.confirmed_count = 0
        self.path: str = ""
        self.loaded: bool = False

    def start(self) -> None: self._thread.start()
    def stop(self)  -> None: self._stop_evt.set()
    def is_running(self) -> bool: return self._thread.is_alive()

    def worked_status(self, callsign: str) -> Optional[str]:
        """Return 'confirmed', 'worked', or None (thread-safe)."""
        call = callsign.strip().upper()
        with self._lock:
            if call in self._confirmed:
                return "confirmed"
            if call in self._worked:
                return "worked"
        return None

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            self._refresh()
            self._stop_evt.wait(self._interval)
        print("[DXKeeperCache] Stopped.")

    def _refresh(self) -> None:
        try:
            path = self._db_path or get_dxkeeper_db_path()
        except RuntimeError as e:
            self.status_str = "Log not found: {}".format(str(e)[:60])
            print("[DXKeeperCache] {}".format(self.status_str))
            return
        self.path = path

        worked:    set[str] = set()
        confirmed: set[str] = set()
        try:
            conn = open_dxkeeper_db(path)
            try:
                schema = _discover_log_schema(conn)
                table    = schema["_table"]
                call_col = schema.get("call")
                if not call_col:
                    self.status_str = "No CALL column in log table"
                    return

                # LoTW / eQSL confirmed columns
                cols_lower = {c.lower(): c for c in schema["_all_cols"]}
                lotw_col = next(
                    (cols_lower[c] for c in ("app_lotw_qsl_rcvd", "lotw_qsl_rcvd",
                                             "lotw_rcvd") if c in cols_lower), None)
                eqsl_col = next(
                    (cols_lower[c] for c in ("eqsl_qsl_rcvd", "eqsl_rcvd") if c in cols_lower),
                    None)

                sel = "[{}]".format(call_col)
                if lotw_col: sel += ", [{}]".format(lotw_col)
                if eqsl_col: sel += ", [{}]".format(eqsl_col)

                cursor = conn.cursor()
                cursor.execute("SELECT {} FROM [{}]".format(sel, table))
                for row in cursor.fetchall():
                    call_val = row[0]
                    if not call_val or not isinstance(call_val, str):
                        continue
                    call_u = call_val.strip().upper()
                    lotw_v = str(row[1]).upper() if lotw_col and len(row) > 1 else ""
                    eqsl_v = str(row[2]).upper() if eqsl_col and len(row) > 2 else ""
                    worked.add(call_u)
                    if "Y" in (lotw_v, eqsl_v):
                        confirmed.add(call_u)
            finally:
                conn.close()

        except Exception as e:
            self.status_str = "Error: {}".format(str(e)[:80])
            print("[DXKeeperCache] Load error: {}".format(e))
            return

        with self._lock:
            self._worked    = worked
            self._confirmed = confirmed
            self.worked_count    = len(worked)
            self.confirmed_count = len(confirmed)
        self.loaded = True
        self.status_str = "OK — {:,} worked, {:,} confirmed".format(
            len(worked), len(confirmed))
        print("[DXKeeperCache] {}".format(self.status_str))


if __name__ == "__main__":
    import json
    def show(q): print("QSO:", json.dumps(q, indent=2, default=str))
    try:
        path = get_dxkeeper_db_path()
        print(f"DB: {path}")
    except RuntimeError as e:
        path = input(f"{e}\nEnter path manually: ").strip()
    p = DXKeeperPoller(on_qso=show, interval=10, db_path=path)
    p.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        p.stop()
