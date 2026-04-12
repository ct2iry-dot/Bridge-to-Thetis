# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportReturnType=false, reportAttributeAccessIssue=false
# dxview_db.py  —  DXView LoTW / eQSL background-color cache
#
# DXView stores LoTW and eQSL AG member databases as Microsoft Access .mdb files.
# Confirmed column names (from direct inspection):
#   CallSign   — callsign
#   LastUpload — date of last upload (LoTW: "M/D/YYYY", eQSL: "YYYY-MM-DD")
#
# SpotCollector applies a "Maximum age of last LoTW/eQSL upload (months)" filter
# (SpotCollector\Spot\LotWUploadConstraint / eQSLUploadConstraint) to decide
# whether a callsign gets bg_lotw / bg_eqsl coloring.  We mirror that filter.
#
# bg_key returned by bg_key(callsign):
#   "bg_lotw_eqsl" — active in both LoTW and eQSL
#   "bg_lotw"      — LoTW only
#   "bg_eqsl"      — eQSL only
#   "bg_normal"    — neither, or upload too old
#
# Requires: pip install pyodbc   + Microsoft Access Database Engine 64-bit

from __future__ import annotations
import os, threading, winreg
from datetime import datetime, timedelta
from typing import Optional

try:
    import pyodbc
except ImportError:
    pyodbc = None  # type: ignore

_DXLAB_ROOT = r"Software\VB and VBA Program Settings\DXView"
_SC_ROOT    = r"Software\VB and VBA Program Settings\SpotCollector"

# Confirmed registry value names (from user's regedit inspection)
_LOTW_REG_SECTION = "LotWDatabase"
_LOTW_REG_KEY     = "lotWDatabasePathname"
_EQSL_REG_SECTION = "eQSLDatabase"
_EQSL_REG_KEY     = "eQSLDatabasePathname"

# Confirmed column names (direct pyodbc inspection of DXView databases)
_CALL_CANDIDATES:   list[str] = ["CallSign", "Call", "CALL", "Callsign", "callsign"]
_UPLOAD_CANDIDATES: list[str] = ["LastUpload", "lastupload", "LastUploadDate", "UploadDate"]

# Confirmed SC registry keys for max-age settings
_SC_LOTW_SECTION = "Spot"
_SC_LOTW_KEY     = "LotWUploadConstraint"
_SC_EQSL_SECTION = "Spot"
_SC_EQSL_KEY     = "eQSLUploadConstraint"


# ─────────────────────────────────────────────────────────────────────────────
# Registry helpers
# ─────────────────────────────────────────────────────────────────────────────

def _read_reg(root: str, section: str, key: str) -> Optional[str]:
    """Read a single string value from HKCU\\{root}\\{section}\\{key}."""
    try:
        path = root + ("\\" + section if section else "")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
            val, _ = winreg.QueryValueEx(k, key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    except OSError:
        pass
    return None


def get_lotw_db_path() -> Optional[str]:
    path = _read_reg(_DXLAB_ROOT, _LOTW_REG_SECTION, _LOTW_REG_KEY)
    if path and os.path.isfile(path):
        return path
    if path:
        print("[DXViewDB] LoTW path in registry but not on disk: {}".format(path))
    return None


def get_eqsl_db_path() -> Optional[str]:
    path = _read_reg(_DXLAB_ROOT, _EQSL_REG_SECTION, _EQSL_REG_KEY)
    if path and os.path.isfile(path):
        return path
    if path:
        print("[DXViewDB] eQSL path in registry but not on disk: {}".format(path))
    return None


def get_sc_lotw_max_age() -> Optional[int]:
    """SC 'Max age of last LoTW upload (months)'. None = no limit."""
    val = _read_reg(_SC_ROOT, _SC_LOTW_SECTION, _SC_LOTW_KEY)
    if val:
        try:
            months = int(val)
            if months > 0:
                return months
        except ValueError:
            pass
    return None


def get_sc_eqsl_max_age() -> Optional[int]:
    """SC 'Max age of last eQSL upload (months)'. None = no limit."""
    val = _read_reg(_SC_ROOT, _SC_EQSL_SECTION, _SC_EQSL_KEY)
    if val:
        try:
            months = int(val)
            if months > 0:
                return months
        except ValueError:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Database loading
# ─────────────────────────────────────────────────────────────────────────────

def _open_access_db(db_path: str):  # returns pyodbc.Connection
    if pyodbc is None:
        raise RuntimeError("pyodbc not installed — run: pip install pyodbc")
    conn_str = (
        "DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};"
        "DBQ={};ReadOnly=1;".format(db_path)
    )
    return pyodbc.connect(conn_str, autocommit=True)


def _parse_date(value: object) -> Optional[datetime]:
    """Parse a date value from DXView — handles datetime, M/D/YYYY, YYYY-MM-DD."""
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    s = str(value).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def _load_callsigns_from_db(db_path: str,
                             max_age_months: Optional[int] = None) -> set[str]:
    """
    Load callsigns from a DXView Access database, applying the SC max-age filter.
    Returns a set of uppercase callsigns whose LastUpload is within max_age_months.
    """
    calls: set[str] = set()
    conn = _open_access_db(db_path)
    try:
        cursor = conn.cursor()
        tables: list[str] = [t.table_name for t in cursor.tables(tableType="TABLE")]
        if not tables:
            return calls
        table = tables[0]

        cols_lower: dict[str, str] = {
            c.column_name.lower(): c.column_name
            for c in cursor.columns(table=table)
        }

        call_col: Optional[str] = next(
            (cols_lower[c.lower()] for c in _CALL_CANDIDATES if c.lower() in cols_lower),
            None)
        upload_col: Optional[str] = next(
            (cols_lower[c.lower()] for c in _UPLOAD_CANDIDATES if c.lower() in cols_lower),
            None)

        if call_col is None:
            print("[DXViewDB] No callsign column in '{}' — cols: {}".format(
                table, list(cols_lower.values())))
            return calls

        print("[DXViewDB] Table='{}' call='{}' upload='{}'  max_age={}mo".format(
            table, call_col, upload_col or "n/a",
            max_age_months if max_age_months else "none"))

        cutoff: Optional[datetime] = (
            datetime.now() - timedelta(days=max_age_months * 30.44)
            if max_age_months and upload_col else None
        )

        if upload_col:
            cursor.execute("SELECT [{}], [{}] FROM [{}]".format(
                call_col, upload_col, table))
            for row in cursor.fetchall():
                call_val, upload_val = row[0], row[1]
                if not call_val or not isinstance(call_val, str):
                    continue
                if cutoff is not None:
                    upload_dt = _parse_date(upload_val)
                    if upload_dt is not None and upload_dt < cutoff:
                        continue
                calls.add(call_val.strip().upper())
        else:
            cursor.execute("SELECT [{}] FROM [{}]".format(call_col, table))
            for row in cursor.fetchall():
                if row[0] and isinstance(row[0], str):
                    calls.add(row[0].strip().upper())
    finally:
        conn.close()
    return calls


# ─────────────────────────────────────────────────────────────────────────────
# Cache class
# ─────────────────────────────────────────────────────────────────────────────

class DXViewCache:
    """
    Background cache of LoTW / eQSL callsigns, filtered by SpotCollector's
    max-age-of-last-upload setting so bg colors match exactly what SC shows.

    bg_key(callsign) -> "bg_lotw_eqsl" | "bg_lotw" | "bg_eqsl" | "bg_normal"

    Usage:
        cache = DXViewCache()
        cache.start()          # loads immediately, refreshes every hour
        bg = cache.bg_key("CT2IRY")
        cache.stop()
    """

    def __init__(self, refresh_interval: float = 3600.0) -> None:
        self._interval:  float      = refresh_interval
        self._lotw:      set[str]   = set()
        self._eqsl:      set[str]   = set()
        self._lock:      threading.Lock = threading.Lock()
        self._stop_evt:  threading.Event = threading.Event()
        self._thread:    threading.Thread = threading.Thread(
            target=self._run, name="DXViewCache", daemon=True)
        self.status:     str        = "Idle"
        self.lotw_count: int        = 0
        self.eqsl_count: int        = 0
        self.lotw_max_age: Optional[int] = None
        self.eqsl_max_age: Optional[int] = None

    def start(self) -> None: self._thread.start()
    def stop(self)  -> None: self._stop_evt.set()
    def is_running(self) -> bool: return self._thread.is_alive()
    def refresh(self) -> None: self._refresh()  # public sync refresh (for testing)

    def bg_key(self, callsign: str) -> str:
        """Return background color key for callsign (thread-safe)."""
        call = callsign.strip().upper()
        with self._lock:
            in_lotw = call in self._lotw
            in_eqsl = call in self._eqsl
        if in_lotw and in_eqsl:
            return "bg_lotw_eqsl"
        if in_lotw:
            return "bg_lotw"
        if in_eqsl:
            return "bg_eqsl"
        return "bg_normal"

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            self._refresh()
            self._stop_evt.wait(self._interval)
        print("[DXViewCache] Stopped.")

    def _refresh(self) -> None:
        lotw_max = get_sc_lotw_max_age()
        eqsl_max = get_sc_eqsl_max_age()
        self.lotw_max_age = lotw_max
        self.eqsl_max_age = eqsl_max

        lotw_path = get_lotw_db_path()
        eqsl_path = get_eqsl_db_path()

        lotw: set[str] = set()
        eqsl: set[str] = set()

        if lotw_path:
            try:
                lotw = _load_callsigns_from_db(lotw_path, lotw_max)
                print("[DXViewCache] LoTW: {:,} active calls (max_age={}mo) from {}".format(
                    len(lotw), lotw_max or "none", os.path.basename(lotw_path)))
            except Exception as e:
                print("[DXViewCache] LoTW load error: {}".format(e))
        else:
            print("[DXViewCache] LoTW database not found in registry")

        if eqsl_path:
            try:
                eqsl = _load_callsigns_from_db(eqsl_path, eqsl_max)
                print("[DXViewCache] eQSL: {:,} active calls (max_age={}mo) from {}".format(
                    len(eqsl), eqsl_max or "none", os.path.basename(eqsl_path)))
            except Exception as e:
                print("[DXViewCache] eQSL load error: {}".format(e))
        else:
            print("[DXViewCache] eQSL database not found in registry")

        with self._lock:
            self._lotw      = lotw
            self._eqsl      = eqsl
            self.lotw_count = len(lotw)
            self.eqsl_count = len(eqsl)

        if lotw or eqsl:
            self.status = "OK — LoTW:{:,} eQSL:{:,} (age<={}/{}mo)".format(
                len(lotw), len(eqsl),
                lotw_max or "any", eqsl_max or "any")
        else:
            self.status = "No DB found"


# ─── Self-test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("LoTW DB  :", get_lotw_db_path())
    print("eQSL DB  :", get_eqsl_db_path())
    print("LoTW age :", get_sc_lotw_max_age(), "months")
    print("eQSL age :", get_sc_eqsl_max_age(), "months")
    cache = DXViewCache()
    cache.refresh()
    print("Status   :", cache.status)
    for test_call in ["CT2IRY", "DX0P", "OH2BH", "VK9XG", "CT1BOH"]:
        print("  bg_key({:10s}) = {}".format(test_call, cache.bg_key(test_call)))
