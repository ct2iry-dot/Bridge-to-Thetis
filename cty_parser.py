# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# cty_parser.py  —  BigCTY.csv callsign → DXCC entity lookup
#
# DXView ships BigCTY.csv at D:\DXLab\DXView\Databases\BigCTY.csv
# Format (comma-separated, one entity per line):
#   prefix, entity_name, dxcc_num, continent, itu_zone, cq_zone, lat, lon, tz_offset, prefixes_list
#
# prefixes_list is space-separated tokens where:
#   plain token  = prefix match (e.g. "CT2" matches CT2IRY)
#   =TOKEN       = exact callsign match (highest priority)
#
# Resolution order (standard cty.dat rules):
#   1. Exact match (=CALLSIGN in any entity's list)
#   2. Longest prefix match
#   3. Strip suffixes (/P /M /MM /QRP /LH etc) and retry
#   4. Strip prefix (K/CT2IRY → CT2IRY) and retry

from __future__ import annotations
import os, re, winreg
from typing import Optional

_DXVIEW_ROOT = r"Software\VB and VBA Program Settings\DXView"

# ─────────────────────────────────────────────────────────────────────────────

class DXCCEntity:
    __slots__ = ("name", "dxcc", "continent", "itu", "cq", "lat", "lon")

    def __init__(self, name: str, dxcc: int, continent: str,
                 itu: int, cq: int, lat: float, lon: float) -> None:
        self.name      = name
        self.dxcc      = dxcc
        self.continent = continent
        self.itu       = itu
        self.cq        = cq
        self.lat       = lat
        self.lon       = lon

    def __repr__(self) -> str:
        return "DXCCEntity({}, {}, {}, CQ{})".format(
            self.name, self.dxcc, self.continent, self.cq)


def _find_bigcty_path() -> Optional[str]:
    """Locate BigCTY.csv — first try DXView registry path, then common defaults."""
    # Try to get DXView database folder from registry
    try:
        reg_path = _DXVIEW_ROOT + r"\General"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as k:
            folder, _ = winreg.QueryValueEx(k, "DatabaseFolder")
            if isinstance(folder, str):
                p = os.path.join(folder.strip(), "BigCTY.csv")
                if os.path.isfile(p):
                    return p
    except OSError:
        pass
    # Fallback: known default location
    default = r"D:\DXLab\DXView\Databases\BigCTY.csv"
    if os.path.isfile(default):
        return default
    return None


class CTYDatabase:
    """
    Parses BigCTY.csv and resolves callsigns to DXCCEntity.

    Usage:
        db = CTYDatabase()
        db.load()                        # or CTYDatabase.from_default_path()
        entity = db.lookup("CT2IRY")     # DXCCEntity or None
        entity = db.lookup("K/CT2IRY")   # resolves prefix stripping
    """

    def __init__(self) -> None:
        self._exact:    dict[str, DXCCEntity] = {}   # =CALLSIGN → entity
        self._prefixes: dict[str, DXCCEntity] = {}   # PREFIX    → entity
        self.path: str = ""
        self.entity_count: int = 0

    @classmethod
    def from_default_path(cls) -> "CTYDatabase":
        db = cls()
        path = _find_bigcty_path()
        if path:
            db.load(path)
        else:
            print("[CTY] BigCTY.csv not found")
        return db

    def load(self, path: Optional[str] = None) -> None:
        if path is None:
            path = _find_bigcty_path()
        if not path or not os.path.isfile(path):
            print("[CTY] File not found: {}".format(path))
            return
        self.path = path
        exact:    dict[str, DXCCEntity] = {}
        prefixes: dict[str, DXCCEntity] = {}
        count = 0

        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",")
                if len(parts) < 9:
                    continue
                try:
                    name      = parts[1].strip()
                    dxcc      = int(parts[2].strip())
                    continent = parts[3].strip()
                    itu       = int(parts[4].strip())
                    cq        = int(parts[5].strip())
                    lat       = float(parts[6].strip())
                    # BigCTY uses negative=East convention for lon — invert
                    lon       = -float(parts[7].strip())
                except (ValueError, IndexError):
                    continue

                entity = DXCCEntity(name, dxcc, continent, itu, cq, lat, lon)
                count += 1

                # Main prefix (col 0) — may have /suffix like "3D2/c"
                main_pfx = parts[0].strip().upper()
                prefixes[main_pfx] = entity

                # Additional prefixes/exceptions in col 9+
                tokens_raw = ",".join(parts[9:]) if len(parts) > 9 else ""
                for token in tokens_raw.split():
                    token = token.rstrip(";").strip()
                    if not token:
                        continue
                    if token.startswith("="):
                        exact[token[1:].upper()] = entity
                    else:
                        prefixes[token.upper()] = entity

        self._exact    = exact
        self._prefixes = prefixes
        self.entity_count = count
        print("[CTY] Loaded {} entities, {} exact, {} prefixes from {}".format(
            count, len(exact), len(prefixes), os.path.basename(path)))

    # ── Lookup ────────────────────────────────────────────────────────────────

    def lookup(self, callsign: str) -> Optional[DXCCEntity]:
        """Resolve callsign to DXCCEntity using standard cty.dat rules."""
        call = callsign.strip().upper()
        if not call:
            return None

        # Handle compound calls like K/CT2IRY or CT2IRY/P
        # Split on / — keep the longest part as the 'main' call for prefix matching
        parts = call.split("/")
        # Remove known portable suffixes to find the base
        _SUFFIX_RE = re.compile(
            r"^(P|M|MM|QRP|LH|LGT|JOTA|AM|A|B|R|0|1|2|3|4|5|6|7|8|9)$")
        # Check exact match on full call first
        result = self._exact.get(call)
        if result:
            return result

        # Try each part — exact match
        for part in parts:
            result = self._exact.get(part)
            if result:
                return result

        # Prefix match on full call, then each part, longest first
        candidates = [call] + sorted(parts, key=len, reverse=True)
        for candidate in candidates:
            result = self._prefix_match(candidate)
            if result:
                return result

        return None

    def _prefix_match(self, call: str) -> Optional[DXCCEntity]:
        """Longest-prefix match in the prefix table."""
        for length in range(len(call), 0, -1):
            pfx = call[:length]
            entity = self._prefixes.get(pfx)
            if entity:
                return entity
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Self-test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    db = CTYDatabase.from_default_path()
    test_calls = [
        "CT2IRY",    # Portugal
        "CT1BOH",    # Portugal
        "OH2BH",     # Finland
        "DX0P",      # Spratly
        "VK9XG",     # Christmas Island or similar
        "K/CT2IRY",  # compound — should resolve Portugal
        "CT2IRY/P",  # portable — should resolve Portugal
        "ZL7",       # Chatham Islands
        "3Y0X",      # Peter 1 (exact match)
        "9M6/N1UR",  # exact match in Spratly
        "PY0F",      # Fernando de Noronha
        "XX9",       # Macao
    ]
    for call in test_calls:
        e = db.lookup(call)
        if e:
            print("  {:12s} = {:30s}  CQ{:2d}  {}  {:.1f}/{:.1f}".format(
                call, e.name, e.cq, e.continent, e.lat, e.lon))
        else:
            print("  {:12s} = NOT FOUND".format(call))
