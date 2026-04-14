# Getting Started with DXLab - Thetis Bridge

**DXLab - Thetis Bridge** is a companion application for DXLab Suite that paints DX cluster spots onto the [Thetis](https://github.com/ramdor/Thetis) SDR panadapter in real time, using the same colour-coded need-status information that SpotCollector resolves for your station.

Developed by CT2IRY. Listed on the [DXLab download page](https://www.dxlabsuite.com/download.htm#Bridges,%20Gateways,%20%20and%20Extenders).

---

## What you will need

- DXLab Suite with **Commander**, **SpotCollector**, and **DXView** installed and working
- **Thetis** SDR software with your SDR hardware connected and receiving
- **DXLab - Thetis Bridge** — download the latest `BridgeToThetis-Setup.msi` from [GitHub Releases](https://github.com/ct2iry/bridge-to-thetis/releases)

---

## How it works

SpotCollector receives DX spots and resolves each callsign against your DXKeeper log — determining whether the station is needed, worked, confirmed, or a new multiplier — and assigns a foreground colour accordingly. Commander's Waterfall Bandmap and Thetis Bridge Service broadcasts those colour-coded spots over UDP on port 13063.

DXLab - Thetis Bridge receives the spot stream from Commander and forwards each spot to Thetis via the TCI protocol, where it appears as a labelled, colour-coded marker on the panadapter.

Each spot is enriched with:

- **Background colour** indicating LoTW / eQSL membership (from DXView)
- **Country name and continent** (from DXView's BigCTY database)
- **Beam heading and distance** from your QTH to the DX entity
- **Spotter callsign and comment** from the original cluster spot
- **Mode** derived from SpotCollector's BandModes.txt band plan

Clicking a spot on the Thetis panadapter tunes Commander's VFO to that frequency — this is handled by the existing Commander ↔ Thetis connection and requires no additional configuration in DXLab - Thetis Bridge.

---

## Step 1 — Enable the Thetis TCI server

1. Open Thetis and go to **Setup → TCI**.
2. Check **Enable TCI Server**.
3. Set the port to **50001** (default).
4. Click **Apply**.
5. Right-click on a panadapter and enable **Display calls on panadapter**.

---

## Step 2 — Connect Commander to Thetis (if not already done)

Commander connects to Thetis on TCP port 13013 for VFO control. If you are already using Commander to control Thetis (tuning the VFO, changing modes), this step is already complete.

If not, in Commander go to **Settings → Hardware → Radio** and configure the Thetis connection.

---

## Step 3 — Enable Commander's Waterfall Bandmap and Thetis Bridge Service

Commander broadcasts spots to DXLab - Thetis Bridge over UDP.

1. In Commander, open **Settings → Network Services**.
2. Find the **Waterfall Bandmap and Thetis Bridge Service** panel.
3. Enable the service (leave the port at the default **13063**).
4. Click **OK**.

---

## Step 4 — Install and start DXLab - Thetis Bridge

1. Download and run `BridgeToThetis-Setup.msi` from [GitHub Releases](https://github.com/ct2iry/bridge-to-thetis/releases).
2. Launch **DXLab - Thetis Bridge** from the Start menu or desktop shortcut.
3. The TCI Host should be **127.0.0.1** and TCI Port **50001** (defaults). Change the host only if Thetis runs on a different PC.
4. Optionally enter your station **latitude and longitude** — this enables beam heading and distance in spot tooltips.
5. Optionally enable **Band filter** to show only spots on the same band as your current VFO.

No other configuration is required. DXLab - Thetis Bridge auto-discovers all DXLab paths from the Windows registry.

---

## Step 5 — Verify operation

With all four applications running (SpotCollector, Commander, Thetis, DXLab - Thetis Bridge):

1. The DXLab - Thetis Bridge status indicator should show **Ready**.
2. Within seconds of spots appearing in SpotCollector, matching coloured spots should appear on the Thetis panadapter.
3. Hovering over a spot in Thetis shows a tooltip with callsign, country, spotter, comment, heading, and age.
4. Clicking a spot on the panadapter tunes Commander's VFO.

If spots do not appear, use the **Debug Log** in DXLab - Thetis Bridge to diagnose the issue.

---

## Colour coding

Spot colours in Thetis match SpotCollector exactly:

| Foreground colour | Meaning |
|---|---|
| Set by SpotCollector | Needed / worked / confirmed / new multiplier |

| Background colour | Meaning |
|---|---|
| White (default) | No LoTW / no eQSL information |
| Yellow | LoTW member |
| Cyan | eQSL member |
| Silver | Both LoTW and eQSL member |

Background colours follow your SpotCollector colour settings (PaneColor3/4/8/9) and update automatically if you change them.

---

## Port reference

| Port | Protocol | Purpose |
|------|----------|---------|
| 13063 | UDP | Commander → DXLab - Thetis Bridge (spot stream) |
| 50001 | TCP/WebSocket | DXLab - Thetis Bridge → Thetis (TCI spot commands) |
| 13013 | TCP | Commander ↔ Thetis (VFO/CAT — not Bridge) |

All ports are on localhost (127.0.0.1) unless Thetis runs on a separate PC.

---

## Troubleshooting

**No spots appear on the panadapter**
- Check the TCI status in DXLab - Thetis Bridge — it must show "Ready"
- Verify Thetis TCI server is enabled (Setup → TCI → Enable TCI Server)
- Verify Commander's Waterfall Bandmap and Thetis Bridge Service is enabled on port 13063
- Check the Bridge debug log for error messages

**Status shows "Connecting" and never changes to "Ready"**
- Thetis is not running, or TCI is not enabled
- Check that port 50001 is not blocked by a firewall
- Verify the TCI Host setting matches the machine running Thetis

**Spots appear but all are the same colour**
- SpotCollector is not connected to your DXKeeper log
- DXView database not found — check DXView is installed and has run at least once

**Spots appear but no country/heading in tooltip**
- BigCTY.csv not found — verify DXView is installed
- QTH coordinates not set — enter lat/lon in Bridge settings for beam headings

**Clicking spots in Thetis does not tune Commander**
- This is handled by the Commander ↔ Thetis CAT link (port 13013), not by DXLab - Thetis Bridge
- Verify Commander is connected to Thetis as a radio controller

---

## Further information

- Full configuration guide: [CONFIGURATION.md](CONFIGURATION.md)
- Download and releases: [GitHub Releases](https://github.com/ct2iry/bridge-to-thetis/releases)
- Issues and support: [GitHub Issues](https://github.com/ct2iry/bridge-to-thetis/issues)
