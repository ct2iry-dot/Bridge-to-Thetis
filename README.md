# Bridge to Thetis

**Author:** Nuno Lopes CT2IRY  
**Version:** 7.1.0  
**Status:** Active Development  

---

## What is this?

**Bridge to Thetis** connects [DXLab Suite](https://www.dxlabsuite.com) to the [Thetis](https://github.com/ramdor/Thetis) SDR software, painting DX cluster spots live onto the Thetis panadapter with full colour-coding, country information, and beam headings.

Developed by **CT2IRY**. Endorsed and listed by [DXLab Suite](https://www.dxlabsuite.com).

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

---

## Requirements

| Component | Notes |
|-----------|-------|
| Windows 10 / 11 | 64-bit |
| [DXLab Suite](https://www.dxlabsuite.com) | Commander, SpotCollector, DXView |
| [Thetis](https://github.com/ramdor/Thetis) | Any recent build with TCI server |
| An SDR connected to Thetis | Any hardware supported by Thetis |

---

## Installation

1. Download the latest `BridgeToThetis-Setup.msi` from the [Releases](../../releases) page.
2. Run the installer — no additional runtime or dependencies required.
3. Launch **Bridge to Thetis** from the Start menu or desktop shortcut.
4. Follow the [Configuration Guide](docs/CONFIGURATION.md).

---

## Documentation

- [Configuration Guide](docs/CONFIGURATION.md) — full step-by-step setup
- [DXLab Getting Started](docs/DXLAB_WIKI_GettingStarted.md) — quick-start for DXLab users

---

## Changelog

| Version | Date | Changes |
|---------|------|---------|
| 7.1.0 | 2026-04-14 | Correct Commander UDP colour parsing (Dave AA6YQ ConvertColor formula); improve spot reliability |
| 7.0.5 | 2026 | Band filter, BandModes.txt mode detection, SpotCollector PaneColor registry read |
| 7.0.1 | 2026 | Installer upgrade fix, assembly version in About dialog |
| 7.0.0 | 2026 | C# WPF rewrite, TCI WebSocket, DXLab UDP XML spot parsing |

---

## Acknowledgements

- **Dave Bernstein AA6YQ** — DXLab Suite author, for the Waterfall Bandmap UDP protocol and ConvertColor colour encoding documentation
- **Richie Samphire MW0LGE** — Thetis development
- **ExpertSDR** — TCI protocol
- The DXLab Suite community

---

## License

MIT License — see [LICENSE](LICENSE).
