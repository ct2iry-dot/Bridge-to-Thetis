# Bridge to Thetis — DXLab Edition

**Bridge to Thetis** connects DXLab Suite's spot pipeline to the [Thetis](https://github.com/ramdor/Thetis) SDR software, painting DX cluster spots live onto the Thetis panadapter with full colour-coding, country information, and beam headings.

Developed by **CT2IRY**. Endorsed and listed by [DXLab Suite](https://www.dxlabsuite.com).

---

## What it does

SpotCollector receives DX cluster spots and resolves each callsign against your logbook: the spot colour already tells you whether a station is needed, worked, confirmed, or a new multiplier. Commander's Waterfall Bandmap displays those colour-coded spots on a frequency axis.

**Bridge to Thetis** takes that same spot stream and paints it onto Thetis's panadapter in real time — with the same colours, the same need-status logic, and enriched tooltip data:

- **Foreground colour** — worked/needed/confirmed/mult status from SpotCollector, resolved against your DXKeeper log
- **Background colour** — LoTW and eQSL status from DXView (yellow = LoTW, cyan = eQSL, silver = both)
- **Country and continent** — from the BigCTY database (auto-located via DXView registry)
- **Beam heading and distance** — great-circle to the DX entity from your QTH coordinates
- **Spotter callsign and comment** — visible in the Thetis spot tooltip
- **Mode** — derived from SpotCollector's BandModes.txt band plan (FT8, CW, USB, RTTY, etc.)

Spots appear and disappear on the panadapter in sync with Commander's Waterfall Bandmap. Clicking a spot in Thetis tunes Commander's VFO via the native Commander ↔ Thetis CAT link — Bridge does not intercept radio control.

---

## Architecture

```
DX Cluster
    │
    ▼
SpotCollector  ──────────────────────────────────────┐
    │  (resolves need-status, applies colours)        │
    ▼                                                 │
Commander (Waterfall Bandmap) ──UDP :13063──▶ Bridge to Thetis
                                                      │
DXView (.mdb) ────────────────────────────────────────┤  (bg colour)
BigCTY (DXView) ──────────────────────────────────────┤  (country/heading)
SpotCollector BandModes.txt ──────────────────────────┤  (mode)
                                                      │
                                              TCI WS :50001
                                                      │
                                                      ▼
                                                   Thetis
                                             (spots on panadapter)

Commander ◀──────────────── TCP :13013 ─────────────▶ Thetis
           (VFO / mode / split / spot-click QSY — native, no Bridge involvement)
```

All data sources are auto-discovered from the Windows registry — no paths to configure.

---

## Requirements

| Component | Notes |
|-----------|-------|
| Windows 10 / 11 | 64-bit |
| [DXLab Suite](https://www.dxlabsuite.com) | Commander, SpotCollector, DXView |
| [Thetis](https://github.com/ramdor/Thetis) | Any recent build with TCI server |
| An SDR connected to Thetis | Any hardware supported by Thetis |

DXLab Suite and Thetis must both be running before Bridge to Thetis is started.

---

## Installation

1. Download the latest `BridgeToThetis-Setup.msi` from the [Releases](../../releases) page.
2. Run the installer — no additional runtime or dependencies required.
3. Launch **Bridge to Thetis** from the Start menu.
4. Follow the [Configuration Guide](CONFIGURATION.md).

---

## Quick start

With DXLab Suite and Thetis already running and configured:

1. In **Commander** → Settings → Network Services → Waterfall Bandmap and Thetis Bridge Service — ensure the service is enabled.
2. In **Thetis** → Setup → TCI — enable the TCI server on port 50001.
3. Start **Bridge to Thetis** — it connects automatically.
4. Open a panadapter in Thetis and watch spots appear.

For full step-by-step instructions see [CONFIGURATION.md](CONFIGURATION.md).

---

## Features at a glance

- Zero configuration — all paths found automatically via Windows registry
- Spot colours match SpotCollector's worked/confirmed/mult colour scheme exactly
- LoTW and eQSL background colours from DXView, following your SpotCollector colour settings
- Country, continent, beam heading and distance in every spot tooltip
- Mode decoded from SpotCollector BandModes.txt (FT8, CW, RTTY, USB, LSB, DIGU, DIGL…)
- Band filter — optional: show only spots on the same band as the current VFO
- CW mode — configurable CWU/CWL selection
- Reconnect-safe — spots are re-painted automatically if Thetis restarts
- Debug log window for troubleshooting

---

## Acknowledgements

- **Dave Bernstein AA6YQ** — DXLab Suite author, for the Waterfall Bandmap UDP protocol documentation and for listing Bridge to Thetis in the DXLab download page
- **Warren Merkel KD5TFD / MW0LGE** — Thetis development, TCI protocol
- The DXLab Suite community

---

## License

MIT License — see [LICENSE](LICENSE).
