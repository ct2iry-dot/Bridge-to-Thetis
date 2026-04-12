# band_modes.py  —  SpotCollector BandModes.txt frequency→mode lookup
#
# SpotCollector ships BandModes.txt in its data directory.
# Format:
#   Line 1 : BandModes YYYY-MM-DD   (date changes when user uploads new file)
#   Line 2+: start_khz,end_khz,mode,band
#
# Example:
#   14000,14070,CW,20M
#   14074,14078,FT8,20M
#   14130,14350,USB,20M
#
# Lookup: given freq_hz, return the mode string (e.g. "FT8", "CW", "USB").
# Returns "USB" if frequency is not covered by any range.
# Re-reads the file only when the date header changes.

from __future__ import annotations
import os
from typing import Optional

try:
    import winreg as _winreg
except ImportError:
    _winreg = None  # type: ignore

_SC_ROOT = r"Software\VB and VBA Program Settings\SpotCollector"

# Known fallback locations (in order of preference)
_FALLBACK_PATHS = [
    r"D:\DXLab\SpotCollector\BandModes.txt",
    r"C:\DXLab\SpotCollector\BandModes.txt",
]


def _find_bandmodes_path() -> Optional[str]:
    """Locate BandModes.txt via SC registry, then known defaults."""
    if _winreg is not None:
        # Try several registry subkeys that SpotCollector uses for its data folder
        for subkey, valname in [
            (r"\General",     "DataDirectory"),
            (r"\General",     "ProgramDirectory"),
            (r"\Directories", "DataDirectory"),
            ("",              "DataDirectory"),
        ]:
            try:
                path = _SC_ROOT + subkey
                with _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, path) as k:
                    folder, _ = _winreg.QueryValueEx(k, valname)
                    if isinstance(folder, str) and folder.strip():
                        p = os.path.join(folder.strip(), "BandModes.txt")
                        if os.path.isfile(p):
                            return p
            except OSError:
                continue

    for p in _FALLBACK_PATHS:
        if os.path.isfile(p):
            return p
    return None


class BandModesMap:
    """
    Parses SpotCollector BandModes.txt and maps freq_hz → mode string.

    Usage:
        bm = BandModesMap.from_default_path()
        mode = bm.lookup(14074000)   # "FT8"
        bm.refresh_if_changed()      # call periodically; re-reads only on date change
    """

    def __init__(self) -> None:
        self._ranges: list[tuple[int, int, str]] = []   # (start_hz, end_hz, mode)
        self._date:   str = ""
        self.path:    str = ""
        self.entry_count: int = 0
        self.loaded:  bool = False

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_default_path(cls) -> "BandModesMap":
        bm = cls()
        path = _find_bandmodes_path()
        if path:
            bm.load(path)
        else:
            print("[BandModes] BandModes.txt not found — mode will be inferred from frequency")
        return bm

    # ── Load ──────────────────────────────────────────────────────────────────

    def load(self, path: Optional[str] = None) -> bool:
        """
        Load (or reload) the file.
        Returns True if the file was (re)loaded, False if unchanged (same date).
        """
        if path is None:
            path = _find_bandmodes_path()
        if not path or not os.path.isfile(path):
            return False
        self.path = path

        ranges: list[tuple[int, int, str]] = []
        new_date = ""
        first_line = True

        with open(path, encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue

                if first_line:
                    first_line = False
                    # Header: "BandModes YYYY-MM-DD"
                    parts = line.split()
                    if len(parts) >= 2:
                        new_date = parts[-1]
                    # Skip re-parse if date unchanged and already loaded
                    if new_date and new_date == self._date and self._ranges:
                        return False
                    continue

                # Data row: start_khz,end_khz,mode,band
                parts = line.split(",")
                if len(parts) < 3:
                    continue
                try:
                    start_hz = int(round(float(parts[0].strip()) * 1000))
                    end_hz   = int(round(float(parts[1].strip()) * 1000))
                    mode     = parts[2].strip().upper()
                    if start_hz < end_hz and mode:
                        ranges.append((start_hz, end_hz, mode))
                except (ValueError, IndexError):
                    continue

        self._ranges     = ranges
        self._date       = new_date
        self.entry_count = len(ranges)
        self.loaded      = True
        print("[BandModes] Loaded {} ranges (date={}) from {}".format(
            len(ranges), new_date or "?", os.path.basename(path)))
        return True

    # ── Lookup ────────────────────────────────────────────────────────────────

    def lookup(self, freq_hz: int) -> str:
        """
        Return mode string for freq_hz.
        Returns "USB" if not covered by any range.
        Mode strings match SpotCollector's naming (CW, FT8, RTTY, USB, etc.).
        """
        for start, end, mode in self._ranges:
            if start <= freq_hz < end:
                return mode
        return "USB"

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh_if_changed(self) -> bool:
        """Re-read file if the date header has changed. Returns True if reloaded."""
        return self.load(self.path or None)


# ── Self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bm = BandModesMap.from_default_path()
    if not bm.loaded:
        print("No BandModes.txt found — check SC install path")
    else:
        test_freqs = [
            (1840000,  "160m CW/SSB boundary"),
            (7074000,  "40m FT8"),
            (14000000, "20m CW start"),
            (14025000, "20m CW"),
            (14074000, "20m FT8"),
            (14080000, "20m JT9/FT4 area"),
            (14130000, "20m RTTY"),
            (14200000, "20m USB"),
            (21074000, "15m FT8"),
            (28074000, "10m FT8"),
            (50313000, "6m FT8"),
        ]
        print("\nFrequency → Mode lookups:")
        for freq, desc in test_freqs:
            mode = bm.lookup(freq)
            print("  {:>11.3f} kHz  {:10s}  ({})".format(freq / 1000, mode, desc))
