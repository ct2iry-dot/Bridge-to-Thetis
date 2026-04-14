# dxkeeper_progress.py  —  DXKeeper award Progress lookup
#
# Reads DXKeeper's [Progress] table (DXCC award) directly to answer:
#   "for this (prefix, band, mode) slot, what is my award status?"
#
# ──────────────────────────────────────────────────────────────────────────
#  BANDMODES encoding (reverse-engineered from DXKeeper DB):
#
#  A 44-char positional matrix — 4 modes × 11 bands, mode-major:
#
#     positions  0..10 → PHONE  (bands: 160,80,40,30,20,17,15,12,10,6,2)
#     positions 11..21 → CW     (same band order)
#     positions 22..32 → RTTY   (same band order)
#     positions 33..43 → PSK    (same band order)
#
#  Alphabet per slot:
#     ' '  = unworked OR not-an-objective slot
#     'W'  = Worked (QSO logged, no QSL received yet)
#     'R'  = QSL Received (not yet submitted for DXCC credit)
#     'F'  = Final (DXCC credit awarded)
#
#  Aggregation priority (for the single-char M*/PHONE/CW/RTTY/PSK summaries):
#     F  >  R  >  W  >  ' '      (most-credited wins)
# ──────────────────────────────────────────────────────────────────────────
#
# Usage:
#     from dxkeeper_progress import DXKeeperProgress
#     dkp = DXKeeperProgress()            # auto-discovers DB path
#     dkp.open()
#     status = dkp.slot_status("OE", "20M", "CW")      # → 'F'
#     nc = dkp.need_key("OE", "20M", "CW", lotw=False) # → 'unneeded_dx'

from __future__ import annotations
import os, threading, time
from typing import Optional, Dict, List, Tuple

try:
    import pyodbc
except ImportError:
    pyodbc = None  # type: ignore

from dxkeeper_db import get_dxkeeper_db_path, open_dxkeeper_db


# ─────────────────────────────────────────────────────────────────────────────
# BANDMODES layout constants
# ─────────────────────────────────────────────────────────────────────────────

# Band order INSIDE each 11-char mode block
_BANDS: Tuple[str, ...] = ("160M", "80M", "40M", "30M", "20M", "17M",
                           "15M", "12M", "10M", "6M", "2M")

# Mode order in BANDMODES (each gets 11 consecutive chars)
_MODES: Tuple[str, ...] = ("PHONE", "CW", "RTTY", "PSK")

_N_BANDS = len(_BANDS)       # 11
_N_MODES = len(_MODES)       # 4
_BANDMODES_LEN = _N_MODES * _N_BANDS   # 44

_BAND_IDX: Dict[str, int] = {b: i for i, b in enumerate(_BANDS)}
_MODE_IDX: Dict[str, int] = {m: i for i, m in enumerate(_MODES)}


# ─────────────────────────────────────────────────────────────────────────────
# Band / mode normalisation (spot inputs → Progress-table terms)
# ─────────────────────────────────────────────────────────────────────────────

# Frequency (MHz) → band name. Edges are inclusive-lower / exclusive-upper.
# These match ARRL/DXCC band definitions that DXKeeper uses.
_BAND_RANGES_MHZ: List[Tuple[float, float, str]] = [
    (1.800,   2.000,   "160M"),
    (3.500,   4.000,   "80M"),
    (7.000,   7.300,   "40M"),
    (10.100,  10.150,  "30M"),
    (14.000,  14.350,  "20M"),
    (18.068,  18.168,  "17M"),
    (21.000,  21.450,  "15M"),
    (24.890,  24.990,  "12M"),
    (28.000,  29.700,  "10M"),
    (50.000,  54.000,  "6M"),
    (144.000, 148.000, "2M"),
]

def band_from_freq_mhz(freq_mhz: float) -> Optional[str]:
    """Map a frequency (MHz) to a DXKeeper Progress band name."""
    for lo, hi, name in _BAND_RANGES_MHZ:
        if lo <= freq_mhz < hi:
            return name
    return None

def normalize_band(band: str) -> Optional[str]:
    """Accept '20M', '20m', '20', 'M20', etc. Return canonical '20M'."""
    if not band:
        return None
    b = band.strip().upper().replace("M", "")
    if not b:
        return None
    cand = f"{b}M"
    return cand if cand in _BAND_IDX else None

# Wide TCI/DXLab mode → Progress table 4-mode class
# Progress table tracks: PHONE, CW, RTTY, PSK
# Everything digital non-PSK/non-RTTY we bucket into RTTY (matches DXKeeper's
# DXCC-Digital-Mode default for FT8/FT4 etc.)
_MODE_TO_CLASS: Dict[str, str] = {
    # Phone
    "SSB": "PHONE", "USB": "PHONE", "LSB": "PHONE", "AM": "PHONE",
    "FM": "PHONE", "PHONE": "PHONE", "PH": "PHONE",
    # CW
    "CW": "CW", "CWR": "CW", "CW_U": "CW", "CW_L": "CW",
    # RTTY / digital (FT8/FT4 default bucket per DXKeeper DXCC-Digital-Mode)
    "RTTY": "RTTY", "DIGI": "RTTY", "DIGI_U": "RTTY", "DIGI_L": "RTTY",
    "DATA": "RTTY", "DATA-U": "RTTY", "DATA-L": "RTTY", "FSK": "RTTY",
    "FT8": "RTTY", "FT4": "RTTY", "JT65": "RTTY", "JT9": "RTTY",
    "MFSK": "RTTY", "OLIVIA": "RTTY", "RTTYM": "RTTY", "MSK144": "RTTY",
    "PACKET": "RTTY",
    # PSK
    "PSK": "PSK", "PSK31": "PSK", "PSK63": "PSK", "PSK125": "PSK",
    "PSKR": "PSK", "QPSK": "PSK",
}

def normalize_mode_class(mode: str) -> Optional[str]:
    """Map any TCI/ADIF mode name to one of PHONE/CW/RTTY/PSK."""
    if not mode:
        return None
    m = mode.strip().upper().replace("-", "_")
    if m in _MODE_TO_CLASS:
        return _MODE_TO_CLASS[m]
    # Fallback heuristics
    if m.startswith(("FT", "JT", "MFSK", "OLIVIA")):
        return "RTTY"
    if "PSK" in m:
        return "PSK"
    if "CW" in m:
        return "CW"
    if m in _MODE_IDX:
        return m
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Prefix extraction from callsign
# ─────────────────────────────────────────────────────────────────────────────

def extract_wpx_prefix(call: str) -> str:
    """
    Extract the CQ WPX prefix from a callsign (= everything up to & including
    the LAST digit in the base call).
        "OE7MAH"  → "OE7"
        "Z34CMF"  → "Z34"
        "K1AR"    → "K1"
        "3Y0J"    → "3Y0"
        "6W0X"    → "6W0"
        "VP8LP"   → "VP8"
    For portable calls, uses the shorter / country-style part.
        "K1AR/VE3" → "VE3"
        "VP9/G3ZAY" → "VP9"
    """
    if not call:
        return ""
    c = call.strip().upper()
    if "/" in c:
        parts = [p for p in c.split("/") if p and p not in
                 ("P", "M", "MM", "AM", "A", "QRP", "QRPP", "LH")]
        if not parts:
            return ""
        # Prefer shorter part (typical WPX rule for portable)
        c = min(parts, key=lambda p: (not any(x.isdigit() for x in p), len(p)))
    # Return substring up to & including last digit
    last_digit = -1
    for i, ch in enumerate(c):
        if ch.isdigit():
            last_digit = i
    return c[:last_digit + 1] if last_digit >= 0 else c


def extract_dxcc_prefix(call: str) -> str:
    """
    Best-effort DXCC-prefix extraction from a callsign.
    DXKeeper's Progress table is keyed by the *canonical* DXCC prefix
    (OE, DL, Z3, A92, K, JA, VK, 3Y, FT, etc.), NOT by WPX prefix.
    We try a few heuristics; real DXCC identification requires a country file.

    Returns upper-case candidate prefix. Caller should match against the
    Progress table and fall back if no match.
    """
    if not call:
        return ""
    c = call.strip().upper()
    # Strip /P /M /MM /AM etc. suffixes
    if "/" in c:
        parts = c.split("/")
        # Prefer the non-trivial part (longest, or country-prefix looking one)
        parts = [p for p in parts if p and p not in
                 ("P", "M", "MM", "AM", "A", "QRP", "QRPP", "LH")]
        if not parts:
            return ""
        # If one part has digits-only or looks like a country prefix, prefer it
        # e.g. "ZK3/K1AR" → prefer "ZK3"; "K1AR/VE3" → prefer "VE3"
        # Heuristic: country prefix usually has ≤ 3 chars or ends in a digit.
        candidates = sorted(parts, key=lambda p: (len(p), p))
        c = candidates[0]
    # Strip trailing digits+letters until we hit something that looks like a prefix
    # Progress table prefixes are 1-6 chars. We'll try progressively shorter slices.
    # Examples: OE7MAH → OE; K1AR → K; DL1ABC → DL; A92GE → A9; Z34CMF → Z3;
    #           3Y0J → 3Y; FT8WW → FT
    # Extract leading letters + digit-run
    # Standard call format: <letters><digits><letters>
    head = ""
    i = 0
    # letters
    while i < len(c) and c[i].isalpha():
        head += c[i]; i += 1
    # digit(s)
    while i < len(c) and c[i].isdigit():
        head += c[i]; i += 1
        # Standard DXCC prefix is letters + ONE digit, but some (3Y, 4U, etc.)
        # use 2 leading chars + 1 digit. Keep only ONE digit to match canonical.
        break
    return head


# ─────────────────────────────────────────────────────────────────────────────
# DXKeeperProgress — main reader
# ─────────────────────────────────────────────────────────────────────────────

class DXKeeperProgress:
    """
    Reads DXKeeper's [Progress] table (DXCC) and answers per-slot queries.

    Typical use:
        dkp = DXKeeperProgress()
        dkp.open()
        status = dkp.slot_status("OE", "20M", "CW")      # 'F' / 'W' / 'R' / ' '
        need_key = dkp.need_key("OE", "20M", "CW")       # bridge category
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path   # None = auto-discover via registry
        self._rows: Dict[str, dict] = {}   # prefix → row dict
        self._lock = threading.Lock()
        self._last_load: float = 0.0

    # ── lifecycle ──────────────────────────────────────────────────────

    def open(self) -> None:
        """Connect, read all Progress rows into memory, close."""
        if pyodbc is None:
            raise RuntimeError("pyodbc not installed — pip install pyodbc")
        path = self._db_path or get_dxkeeper_db_path()
        conn = open_dxkeeper_db(path)
        try:
            cursor = conn.cursor()
            # Check that the table exists
            tables = {t.table_name for t in cursor.tables(tableType="TABLE")}
            if "Progress" not in tables:
                raise RuntimeError(
                    "Progress table not found in DXKeeper DB. "
                    f"Available: {sorted(tables)}")

            cursor.execute(
                "SELECT [CountryCode], [Prefix], [Valid], "
                "[BANDMODES], [CountryStatus] "
                "FROM [Progress]")
            rows = cursor.fetchall()
            loaded: Dict[str, dict] = {}
            for r in rows:
                prefix = (r.Prefix or "").strip().upper()
                if not prefix:
                    continue
                loaded[prefix] = {
                    "country_code":   (r.CountryCode or "").strip(),
                    "prefix":         prefix,
                    "valid":          bool(r.Valid),
                    "bandmodes":      (r.BANDMODES or "").ljust(
                                         _BANDMODES_LEN, " ")[:_BANDMODES_LEN],
                    "country_status": (r.CountryStatus or " "),
                }
            with self._lock:
                self._rows = loaded
                self._last_load = time.time()
            print(f"[DXKeeperProgress] Loaded {len(loaded)} Progress rows "
                  f"from {path}")
        finally:
            conn.close()

    def refresh(self) -> None:
        """Reload Progress rows (call after QSOs logged / awards updated)."""
        self.open()

    def row_count(self) -> int:
        with self._lock:
            return len(self._rows)

    def last_load_time(self) -> float:
        with self._lock:
            return self._last_load

    # ── prefix lookup ──────────────────────────────────────────────────

    def lookup_prefix(self, prefix: str) -> Optional[dict]:
        """Return Progress row dict for an exact prefix match, else None."""
        if not prefix:
            return None
        p = prefix.strip().upper()
        with self._lock:
            return self._rows.get(p)

    def find_best_prefix(self, call_or_prefix: str) -> Optional[str]:
        """
        Given a callsign or raw prefix, find the best-matching Progress
        prefix by LONGEST prefix match. DXKeeper Progress prefixes are
        1–6 chars, canonical DXCC prefixes.

            "OE7MAH"  → "OE"
            "Z34CMF"  → "Z3"
            "A92GE"   → "A9" (if A9 row exists; else falls back)
            "3Y0J"    → "3Y" or longer
            "K1AR/VE3"→ "VE" (via extract_dxcc_prefix)
        """
        if not call_or_prefix:
            return None
        raw = extract_dxcc_prefix(call_or_prefix)
        if not raw:
            return None
        with self._lock:
            prefixes = self._rows
            # Longest prefix that is a prefix-of `raw`
            # e.g. raw='OE7', try 'OE7','OE' etc.
            for n in range(min(len(raw), 6), 0, -1):
                cand = raw[:n]
                if cand in prefixes:
                    return cand
            return None

    # ── slot status (the core lookup) ─────────────────────────────────

    def slot_status(self, prefix_or_call: str, band: str,
                    mode: str) -> Optional[str]:
        """
        Return the BANDMODES char for the (prefix, band, mode_class) slot.

        Args:
            prefix_or_call: callsign ('OE7MAH') or raw prefix ('OE')
            band: '20M', '80M', or with/without 'M' suffix
            mode: TCI/ADIF mode name ('CW', 'USB', 'FT8', 'DIGI_U'…)

        Returns: 'F' / 'W' / 'R' / ' ' on match, or None if the prefix/
                 band/mode could not be resolved.
        """
        match_prefix = self.find_best_prefix(prefix_or_call)
        if not match_prefix:
            return None
        row = self.lookup_prefix(match_prefix)
        if not row:
            return None

        band_n = normalize_band(band)
        mode_cls = normalize_mode_class(mode)
        if band_n is None or mode_cls is None:
            return None

        band_i = _BAND_IDX[band_n]
        mode_i = _MODE_IDX[mode_cls]
        pos = mode_i * _N_BANDS + band_i
        bm: str = row["bandmodes"]
        if pos >= len(bm):
            return None
        return bm[pos]

    def country_status(self, prefix_or_call: str) -> Optional[str]:
        """
        Return DXCC CountryStatus for the entity: 'F' / 'W' / 'R' / ' ' / None.
        'F' = DXCC credit awarded for the entity (all-band counter).
        ' ' = never worked (new one!)
        """
        match_prefix = self.find_best_prefix(prefix_or_call)
        if not match_prefix:
            return None
        row = self.lookup_prefix(match_prefix)
        return row["country_status"] if row else None

    # ── Bridge category key ─────────────────────────────────────────────

    def need_key(self, prefix_or_call: str, band: str, mode: str,
                 lotw: bool = False, eqsl: bool = False) -> Optional[str]:
        """
        Map a (callsign, band, mode) spot to one of Bridge's category keys:
            unneeded_dx          — DXCC credit already on this slot ('F')
            verified_dx          — QSL received for this slot ('R')
            unconfirmed_dx       — Worked but no QSL ('W')
            unworked_dx_band_mode— have country, need this band+mode slot
            unworked_dx_counter  — brand-new DXCC entity (never worked)
            special_callsign     — (not derivable from Progress alone)

        Returns None if the prefix couldn't be resolved in the DB.
        """
        slot = self.slot_status(prefix_or_call, band, mode)
        country = self.country_status(prefix_or_call)

        if slot is None or country is None:
            return None

        # Slot-level classification wins first
        if slot == "F":
            return "unneeded_dx"
        if slot == "R":
            return "verified_dx"
        if slot == "W":
            return "unconfirmed_dx"

        # slot == ' ' → unworked on this band+mode
        # Is this a brand-new country, or already-credited elsewhere?
        if country == " " or country == "":
            return "unworked_dx_counter"        # never worked the entity
        # We have the entity on some slot — this one is just a new band/mode
        return "unworked_dx_band_mode"

    # ── debug helper ───────────────────────────────────────────────────

    def dump_entity(self, prefix_or_call: str) -> str:
        """Return a pretty-printed per-slot matrix for an entity."""
        mp = self.find_best_prefix(prefix_or_call)
        if not mp:
            return f"[DXKeeperProgress] No match for {prefix_or_call!r}"
        row = self.lookup_prefix(mp)
        if not row:
            return f"[DXKeeperProgress] No row for {mp!r}"
        bm = row["bandmodes"]
        lines = [
            f"Progress[{mp}]  country_code={row['country_code']}  "
            f"valid={row['valid']}  country_status={row['country_status']!r}",
            "           " + "  ".join(f"{b:>4}" for b in _BANDS),
        ]
        for m_i, m in enumerate(_MODES):
            chunk = bm[m_i * _N_BANDS:(m_i + 1) * _N_BANDS]
            cells = "  ".join(f" {c!r} " for c in chunk)
            lines.append(f"  {m:<6}  " + cells)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# WPXProgress — CQ WPX prefix award reader
# ─────────────────────────────────────────────────────────────────────────────
#
# Schema (flat — no packed matrix):
#   PREFIX (VARCHAR 8)        — WPX prefix key (e.g. '6W0', 'OE7', 'Z34')
#   MIXED                     — overall aggregate
#   NOBAND                    — unspecified-band marker
#   M160..M6                  — per-band status (11 bands incl. M60)
#   NOMODE                    — unspecified-mode marker
#   SSB, CW, DIGITAL          — per-mode status
#
# Alphabet: 'C' (confirmed) / 'W' (worked) / 'R' (requested) / ' ' (empty)
# No per-band-per-mode matrix — endorsements are by band OR by mode.

# WPX tracks these bands (M60 is new vs DXCC Progress, no M30 in mode tracking)
_WPX_BANDS: Tuple[str, ...] = ("160M", "80M", "60M", "40M", "30M", "20M",
                               "17M", "15M", "12M", "10M", "6M")
_WPX_BAND_COL: Dict[str, str] = {b: f"M{b[:-1]}" for b in _WPX_BANDS}
# → {'160M':'M160', '80M':'M80', ... '6M':'M6'}

# WPX mode classes
_WPX_MODE_COL: Dict[str, str] = {
    "PHONE": "SSB", "SSB": "SSB", "USB": "SSB", "LSB": "SSB",
    "AM": "SSB", "FM": "SSB",
    "CW": "CW",
    "RTTY": "DIGITAL", "DIGI": "DIGITAL", "DIGITAL": "DIGITAL",
    "PSK": "DIGITAL", "DATA": "DIGITAL",
    "FT8": "DIGITAL", "FT4": "DIGITAL",
}

def _wpx_mode_col_for(mode: str) -> Optional[str]:
    if not mode:
        return None
    m = mode.strip().upper().replace("-", "_").replace("_U", "").replace("_L", "")
    return _WPX_MODE_COL.get(m) or ("DIGITAL"
        if m.startswith(("FT", "JT", "MFSK", "OLIVIA", "PSK")) else None)


class WPXProgress:
    """Reader for DXKeeper's [WPXProgress] table."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._rows: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def open(self) -> None:
        if pyodbc is None:
            raise RuntimeError("pyodbc not installed — pip install pyodbc")
        path = self._db_path or get_dxkeeper_db_path()
        conn = open_dxkeeper_db(path)
        try:
            cur = conn.cursor()
            tables = {t.table_name for t in cur.tables(tableType="TABLE")}
            if "WPXProgress" not in tables:
                print("[WPXProgress] table missing — skipping WPX award")
                return
            cur.execute(
                "SELECT [PREFIX],[MIXED],[M160],[M80],[M60],[M40],[M30],"
                "[M20],[M17],[M15],[M12],[M10],[M6],[SSB],[CW],[DIGITAL] "
                "FROM [WPXProgress]")
            loaded: Dict[str, dict] = {}
            for r in cur.fetchall():
                pfx = (r.PREFIX or "").strip().upper()
                if not pfx:
                    continue
                loaded[pfx] = {
                    "prefix":  pfx,
                    "MIXED":   r.MIXED or " ",
                    "M160":    r.M160 or " ", "M80":  r.M80 or " ",
                    "M60":     r.M60 or " ",  "M40":  r.M40 or " ",
                    "M30":     r.M30 or " ",  "M20":  r.M20 or " ",
                    "M17":     r.M17 or " ",  "M15":  r.M15 or " ",
                    "M12":     r.M12 or " ",  "M10":  r.M10 or " ",
                    "M6":      r.M6 or " ",
                    "SSB":     r.SSB or " ",  "CW":   r.CW or " ",
                    "DIGITAL": r.DIGITAL or " ",
                }
            with self._lock:
                self._rows = loaded
            print(f"[WPXProgress] Loaded {len(loaded)} WPX prefixes")
        finally:
            conn.close()

    def row_count(self) -> int:
        with self._lock:
            return len(self._rows)

    def lookup(self, wpx_prefix: str) -> Optional[dict]:
        if not wpx_prefix:
            return None
        with self._lock:
            return self._rows.get(wpx_prefix.strip().upper())

    def slot_status(self, call_or_prefix: str, band: str,
                    mode: str) -> Optional[str]:
        """
        Return the weakest-state char across (band status, mode status)
        for this WPX prefix. 'worst' = least credited.

        Returns ' '/'W'/'R'/'C' when prefix is known, or 'new' if the
        prefix has no entry in WPXProgress at all (= new WPX prefix!).
        Returns None if the band/mode couldn't be normalised.
        """
        wpx = extract_wpx_prefix(call_or_prefix)
        if not wpx:
            return None
        row = self.lookup(wpx)
        if row is None:
            return "new"        # prefix never seen — brand-new WPX slot!

        band_n = normalize_band(band)
        mode_col = _wpx_mode_col_for(mode)
        if band_n is None or mode_col is None:
            return None
        band_col = _WPX_BAND_COL.get(band_n)
        if band_col is None or band_col not in row:
            # 30m & 60m aren't in all schemas — try just modes
            band_status = " "
        else:
            band_status = row.get(band_col, " ")
        mode_status = row.get(mode_col, " ")

        # Weakest = least-credited → priority for 'need' decision.
        # Order: ' ' < 'W' < 'R' < 'C'
        order = {" ": 0, "W": 1, "R": 2, "C": 3}
        return min((band_status, mode_status),
                   key=lambda s: order.get(s, 0))

    def need_key(self, call_or_prefix: str, band: str,
                 mode: str) -> Optional[str]:
        """Map WPX slot status to Bridge category."""
        s = self.slot_status(call_or_prefix, band, mode)
        if s is None:
            return None
        if s == "new":
            return "unworked_dx_counter"    # never worked this prefix
        if s == " ":
            return "unworked_dx_band_mode"  # have prefix, need this slot
        if s == "W":
            return "unconfirmed_dx"
        if s == "R":
            return "verified_dx"
        if s == "C":
            return "unneeded_dx"
        return None


# ─────────────────────────────────────────────────────────────────────────────
# WAZProgress — CQ WAZ (Worked All Zones) reader
# ─────────────────────────────────────────────────────────────────────────────
#
# Schema:
#   ZONE (SMALLINT)  — zone number 1-40
#   MIXED            — overall aggregate
#   M160..M2         — per-band summary (11 bands)
#   SSB,CW,RTTY,AM,SSTV,DIGITAL,SAT,EME — per-mode summary (8 modes)
#   BandModes (VARCHAR 88) — packed 8 modes × 11 bands = 88 chars
#
# BandModes layout (mode-major, 11 bands per mode):
#   positions  0..10  → SSB
#   positions 11..21  → CW
#   positions 22..32  → RTTY
#   positions 33..43  → AM
#   positions 44..54  → SSTV
#   positions 55..65  → DIGITAL
#   positions 66..76  → SAT
#   positions 77..87  → EME
#
# Band order within each mode: 160,80,40,30,20,17,15,12,10,6,2
#
# Alphabet: 'V' (verified/credit), 'C' (confirmed QSL), 'R' (requested),
#           'W' (worked), ' ' (empty)

_WAZ_MODES: Tuple[str, ...] = ("SSB", "CW", "RTTY", "AM", "SSTV",
                                "DIGITAL", "SAT", "EME")
_WAZ_N_MODES = len(_WAZ_MODES)   # 8
_WAZ_BANDMODES_LEN = _WAZ_N_MODES * _N_BANDS  # 88
_WAZ_MODE_IDX: Dict[str, int] = {m: i for i, m in enumerate(_WAZ_MODES)}

# Map TCI/ADIF mode → WAZ mode class
_WAZ_MODE_MAP: Dict[str, str] = {
    "SSB": "SSB", "USB": "SSB", "LSB": "SSB", "AM": "AM", "FM": "SSB",
    "PHONE": "SSB", "PH": "SSB",
    "CW": "CW", "CWR": "CW", "CW_U": "CW", "CW_L": "CW",
    "RTTY": "RTTY", "FSK": "RTTY",
    "PSK": "DIGITAL", "PSK31": "DIGITAL", "PSK63": "DIGITAL",
    "DIGI": "DIGITAL", "DIGI_U": "DIGITAL", "DIGI_L": "DIGITAL",
    "DATA": "DIGITAL", "DATA-U": "DIGITAL", "DATA-L": "DIGITAL",
    "FT8": "DIGITAL", "FT4": "DIGITAL", "JT65": "DIGITAL",
    "JT9": "DIGITAL", "MFSK": "DIGITAL", "OLIVIA": "DIGITAL",
    "MSK144": "DIGITAL", "DIGITAL": "DIGITAL", "PACKET": "DIGITAL",
    "SSTV": "SSTV", "SAT": "SAT", "EME": "EME",
}

def _waz_mode_for(mode: str) -> Optional[str]:
    if not mode:
        return None
    m = mode.strip().upper().replace("-", "_").replace("_U", "").replace("_L", "")
    if m in _WAZ_MODE_MAP:
        return _WAZ_MODE_MAP[m]
    if m.startswith(("FT", "JT", "MFSK", "PSK", "OLIVIA")):
        return "DIGITAL"
    if "CW" in m:
        return "CW"
    return None


class WAZProgress:
    """Reader for DXKeeper's [WAZProgress] table (40 CQ zones)."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._rows: Dict[int, dict] = {}   # zone_number → row dict
        self._lock = threading.Lock()

    def open(self) -> None:
        if pyodbc is None:
            raise RuntimeError("pyodbc not installed")
        path = self._db_path or get_dxkeeper_db_path()
        conn = open_dxkeeper_db(path)
        try:
            cur = conn.cursor()
            tables = {t.table_name for t in cur.tables(tableType="TABLE")}
            if "WAZProgress" not in tables:
                print("[WAZProgress] table missing — skipping WAZ award")
                return
            cur.execute(
                "SELECT [ZONE],[MIXED],[BandModes] FROM [WAZProgress]")
            loaded: Dict[int, dict] = {}
            for r in cur.fetchall():
                zone = r.ZONE
                if zone is None:
                    continue
                loaded[int(zone)] = {
                    "zone":      int(zone),
                    "mixed":     (r.MIXED or " "),
                    "bandmodes": (r.BandModes or "").ljust(
                                     _WAZ_BANDMODES_LEN, " ")[:_WAZ_BANDMODES_LEN],
                }
            with self._lock:
                self._rows = loaded
            print(f"[WAZProgress] Loaded {len(loaded)} zones")
        finally:
            conn.close()

    def row_count(self) -> int:
        with self._lock:
            return len(self._rows)

    def slot_status(self, zone: int, band: str, mode: str) -> Optional[str]:
        """Return BandModes char for (zone, band, mode_class) slot."""
        if zone <= 0 or zone > 40:
            return None
        with self._lock:
            row = self._rows.get(zone)
        if row is None:
            return "new"     # zone not in table → never worked!

        band_n = normalize_band(band)
        mode_cls = _waz_mode_for(mode)
        if band_n is None or mode_cls is None:
            return None
        if mode_cls not in _WAZ_MODE_IDX:
            return None

        band_i = _BAND_IDX[band_n]
        mode_i = _WAZ_MODE_IDX[mode_cls]
        pos = mode_i * _N_BANDS + band_i
        bm: str = row["bandmodes"]
        if pos >= len(bm):
            return None
        return bm[pos]

    def need_key(self, zone: int, band: str, mode: str) -> Optional[str]:
        """Map WAZ slot status to Bridge category."""
        if zone <= 0:
            return None
        with self._lock:
            row = self._rows.get(zone)
        if row is None:
            return "unworked_dx_counter"    # never worked this zone!

        s = self.slot_status(zone, band, mode)
        if s is None:
            return None
        mixed = row.get("mixed", " ")
        if s in ("V", "C"):
            return "unneeded_dx"
        if s == "R":
            return "verified_dx"
        if s == "W":
            return "unconfirmed_dx"
        # s == ' ' → unworked slot
        if mixed == " " or mixed == "":
            return "unworked_dx_counter"    # never worked zone at all
        return "unworked_dx_band_mode"      # have zone, need this slot


# ─────────────────────────────────────────────────────────────────────────────
# WASProgress — Worked All States reader
# ─────────────────────────────────────────────────────────────────────────────
#
# Schema (flat, no packed BandModes):
#   STATE (VARCHAR 2)   — US state code (AK, AL, AR, …)
#   MIXED               — overall aggregate
#   M160..M2, CM125, CM70, SAT, EME  — per-band
#   PHONE, CW, RTTY, DIGITAL, SSTV, FT8, FT4  — per-mode
#
# Alphabet: 'C' (confirmed), 'W' (worked), 'R' (requested), ' ' (empty)

_WAS_BAND_COL: Dict[str, str] = {
    "160M": "M160", "80M": "M80", "40M": "M40", "30M": "M30",
    "20M": "M20", "17M": "M17", "15M": "M15", "12M": "M12",
    "10M": "M10", "6M": "M6", "2M": "M2",
}

_WAS_MODE_COL: Dict[str, str] = {
    "PHONE": "PHONE", "SSB": "PHONE", "USB": "PHONE", "LSB": "PHONE",
    "AM": "PHONE", "FM": "PHONE",
    "CW": "CW",
    "RTTY": "RTTY",
    "DIGITAL": "DIGITAL", "DIGI": "DIGITAL", "DIGI_U": "DIGITAL",
    "DIGI_L": "DIGITAL", "DATA": "DIGITAL",
    "FT8": "DIGITAL", "FT4": "DIGITAL",
    "PSK": "DIGITAL", "PSK31": "DIGITAL",
}

def _was_mode_col_for(mode: str) -> Optional[str]:
    if not mode:
        return None
    m = mode.strip().upper().replace("-", "_").replace("_U", "").replace("_L", "")
    return _WAS_MODE_COL.get(m) or (
        "DIGITAL" if m.startswith(("FT", "JT", "PSK", "MFSK")) else None)


class WASProgress:
    """Reader for DXKeeper's [WASProgress] table (50 US states)."""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path
        self._rows: Dict[str, dict] = {}   # state_code → row dict
        self._lock = threading.Lock()

    def open(self) -> None:
        if pyodbc is None:
            raise RuntimeError("pyodbc not installed")
        path = self._db_path or get_dxkeeper_db_path()
        conn = open_dxkeeper_db(path)
        try:
            cur = conn.cursor()
            tables = {t.table_name for t in cur.tables(tableType="TABLE")}
            if "WASProgress" not in tables:
                print("[WASProgress] table missing — skipping WAS award")
                return
            cur.execute("SELECT * FROM [WASProgress]")
            cols = [desc[0] for desc in cur.description]
            loaded: Dict[str, dict] = {}
            for r in cur.fetchall():
                state = (r.STATE or "").strip().upper()
                if not state:
                    continue
                row_dict: dict = {"state": state}
                for col in cols:
                    val = getattr(r, col, None)
                    if isinstance(val, str):
                        row_dict[col] = val.strip() or " "
                    elif val is not None:
                        row_dict[col] = val
                loaded[state] = row_dict
            with self._lock:
                self._rows = loaded
            print(f"[WASProgress] Loaded {len(loaded)} states")
        finally:
            conn.close()

    def row_count(self) -> int:
        with self._lock:
            return len(self._rows)

    def slot_status(self, state: str, band: str, mode: str) -> Optional[str]:
        """Return weakest status char across (band, mode) for this state."""
        if not state or len(state) != 2:
            return None
        with self._lock:
            row = self._rows.get(state.upper())
        if row is None:
            return "new"     # state not in table → never worked!

        band_n = normalize_band(band)
        mode_col = _was_mode_col_for(mode)
        if band_n is None or mode_col is None:
            return None
        band_col = _WAS_BAND_COL.get(band_n)
        band_status = row.get(band_col, " ") if band_col else " "
        mode_status = row.get(mode_col, " ")
        # Return weakest
        order = {" ": 0, "W": 1, "R": 2, "C": 3}
        return min((band_status, mode_status),
                   key=lambda s: order.get(s, 0))

    def need_key(self, state: str, band: str, mode: str) -> Optional[str]:
        """Map WAS slot status to Bridge category."""
        if not state or len(state) != 2:
            return None
        with self._lock:
            row = self._rows.get(state.upper())
        if row is None:
            return "unworked_dx_counter"    # never worked this state!
        s = self.slot_status(state, band, mode)
        if s is None:
            return None
        if s == "new":
            return "unworked_dx_counter"
        mixed = row.get("MIXED", " ")
        if s == "C":
            return "unneeded_dx"
        if s == "R":
            return "verified_dx"
        if s == "W":
            return "unconfirmed_dx"
        # s == ' ' → unworked slot
        if mixed == " " or mixed == "":
            return "unworked_dx_counter"
        return "unworked_dx_band_mode"


# ─────────────────────────────────────────────────────────────────────────────
# DXKeeperAwards — multi-award combiner
# ─────────────────────────────────────────────────────────────────────────────

# Relative priority for Bridge's category keys (higher = more urgent need).
# This is what we use to combine results across awards: MAX urgency wins.
CATEGORY_URGENCY: Dict[str, int] = {
    "special_callsign":      6,    # (reserved — DXKeeper can't infer)
    "unworked_dx_counter":   5,    # new prefix / entity
    "unworked_dx_band_mode": 4,    # need this band+mode slot
    "unconfirmed_dx":        3,    # worked, no QSL
    "verified_dx":           2,    # QSL received, not submitted
    "unneeded_dx":           1,    # credited
}


class DXKeeperAwards:
    """
    Coordinates multiple DXKeeper award readers and returns the MOST-URGENT
    need across all enabled awards for a given spot.

    Enabled awards are auto-detected by which tables exist + have rows.
    You can also pass `enabled=['dxcc','wpx','waz','was']` to limit.

    Example:
        dk = DXKeeperAwards()
        dk.open()
        need = dk.combined_need_key("Z34CMF", "20M", "DIGI_U")
        need = dk.combined_need_key("9Y4C", "40M", "DIGI_U", cq_zone=9)
    """

    def __init__(self, db_path: Optional[str] = None,
                 enabled: Optional[List[str]] = None):
        self._db_path = db_path
        self._enabled = set(enabled) if enabled else None  # None = all
        self.dxcc: Optional[DXKeeperProgress] = None
        self.wpx: Optional[WPXProgress] = None
        self.waz: Optional[WAZProgress] = None
        self.was: Optional[WASProgress] = None

    def open(self) -> None:
        """Open all enabled award readers. Failures are logged & non-fatal."""
        if self._enabled is None or "dxcc" in self._enabled:
            try:
                self.dxcc = DXKeeperProgress(self._db_path)
                self.dxcc.open()
            except Exception as e:
                print(f"[DXKeeperAwards] DXCC open failed: {e}")
                self.dxcc = None
        if self._enabled is None or "wpx" in self._enabled:
            try:
                self.wpx = WPXProgress(self._db_path)
                self.wpx.open()
            except Exception as e:
                print(f"[DXKeeperAwards] WPX open failed: {e}")
                self.wpx = None
        if self._enabled is None or "waz" in self._enabled:
            try:
                self.waz = WAZProgress(self._db_path)
                self.waz.open()
            except Exception as e:
                print(f"[DXKeeperAwards] WAZ open failed: {e}")
                self.waz = None
        if self._enabled is None or "was" in self._enabled:
            try:
                self.was = WASProgress(self._db_path)
                self.was.open()
            except Exception as e:
                print(f"[DXKeeperAwards] WAS open failed: {e}")
                self.was = None

    def refresh(self) -> None:
        if self.dxcc: self.dxcc.refresh()
        if self.wpx:  self.wpx.open()
        if self.waz:  self.waz.open()
        if self.was:  self.was.open()

    def per_award_need_keys(self, call: str, band: str, mode: str,
                            cq_zone: int = 0,
                            state: str = "") -> Dict[str, Optional[str]]:
        """Return each award's need_key for this spot (None if unresolved)."""
        out: Dict[str, Optional[str]] = {}
        if self.dxcc:
            out["dxcc"] = self.dxcc.need_key(call, band, mode)
        if self.wpx:
            out["wpx"] = self.wpx.need_key(call, band, mode)
        if self.waz and cq_zone > 0:
            out["waz"] = self.waz.need_key(cq_zone, band, mode)
        if self.was and state:
            out["was"] = self.was.need_key(state, band, mode)
        return out

    def combined_need_key(self, call: str, band: str, mode: str,
                          cq_zone: int = 0,
                          state: str = "") -> Optional[str]:
        """Return the MOST-URGENT category across all awards. None if none."""
        keys = [v for v in self.per_award_need_keys(
                    call, band, mode, cq_zone, state).values()
                if v is not None]
        if not keys:
            return None
        return max(keys, key=lambda k: CATEGORY_URGENCY.get(k, 0))

    def explain(self, call: str, band: str, mode: str,
                cq_zone: int = 0, state: str = "") -> dict:
        """Diagnostic: per-award results + combined."""
        per = self.per_award_need_keys(call, band, mode, cq_zone, state)
        combined = None
        keys = [v for v in per.values() if v is not None]
        if keys:
            combined = max(keys, key=lambda k: CATEGORY_URGENCY.get(k, 0))
        return {**per, "combined": combined,
                "dxcc_prefix": (self.dxcc.find_best_prefix(call)
                                if self.dxcc else None),
                "wpx_prefix": extract_wpx_prefix(call),
                "cq_zone": cq_zone, "state": state}


# ─────────────────────────────────────────────────────────────────────────────
# CLI self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    dk = DXKeeperAwards()
    dk.open()
    dcount = dk.dxcc.row_count() if dk.dxcc else 0
    wcount = dk.wpx.row_count()  if dk.wpx  else 0
    zcount = dk.waz.row_count()  if dk.waz  else 0
    scount = dk.was.row_count()  if dk.was  else 0
    print(f"\nLoaded DXCC={dcount}, WPX={wcount}, WAZ={zcount}, WAS={scount}\n")

    # Test spots: (call, freq_mhz, mode, cq_zone, state)
    test_spots = [
        ("OE7MAH",  14.080, "DIGI_U", 15, ""),
        ("Z34CMF",  14.074, "DIGI_U", 15, ""),
        ("9Y4C",     7.074, "DIGI_U",  9, ""),
        ("XE2V",     7.074, "DIGI_U",  6, ""),
        ("N6ACA",   21.074, "DIGI_U",  3, "CA"),
        ("K1AR",    14.025, "CW",      5, "NH"),
        ("IK0BAL",  14.074, "DIGI_U", 15, ""),
        ("DL3QL",   28.500, "USB",    14, ""),
        ("PD8MAX",  14.074, "DIGI_U", 14, ""),
    ]
    if len(sys.argv) > 1:
        test_spots = [(
            sys.argv[1],
            float(sys.argv[2]) if len(sys.argv) > 2 else 14.074,
            sys.argv[3] if len(sys.argv) > 3 else "CW",
            int(sys.argv[4]) if len(sys.argv) > 4 else 0,
            sys.argv[5] if len(sys.argv) > 5 else "",
        )]

    print(f"{'call':<8} {'band':<5} {'mode':<8} {'dxcc':<20} "
          f"{'wpx':<20} {'waz':<20} {'was':<20} {'→ COMBINED':<20}")
    print("-" * 136)
    for call, freq_mhz, mode, cqz, state in test_spots:
        band = band_from_freq_mhz(freq_mhz) or "?"
        ex   = dk.explain(call, band, mode, cq_zone=cqz, state=state)
        print(f"{call:<8} {band:<5} {mode:<8} "
              f"{str(ex.get('dxcc')):<20} "
              f"{str(ex.get('wpx')):<20} "
              f"{str(ex.get('waz')):<20} "
              f"{str(ex.get('was')):<20} "
              f"{str(ex.get('combined')):<20}")

    if len(sys.argv) > 1 and dk.dxcc:
        print("\n" + dk.dxcc.dump_entity(sys.argv[1]))
