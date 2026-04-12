# Getting Started with Bridge to Thetis

**Bridge to Thetis** is a companion application for DXLab Suite that paints DX cluster spots onto the [Thetis](https://github.com/ramdor/Thetis) SDR panadapter in real time, using the same colour-coded need-status information that SpotCollector resolves for your station.

Developed by CT2IRY. Listed on the [DXLab download page](https://www.dxlabsuite.com/download.htm#Bridges,%20Gateways,%20%20and%20Extenders).

---

## What you will need

- DXLab Suite with **Commander**, **SpotCollector**, and **DXView** installed and working
- **Thetis** SDR software with your SDR hardware connected and receiving
- **Bridge to Thetis** — download from [ct2iry GitHub](https://github.com/ct2iry-dot)

---

## How it works

SpotCollector receives DX spots and resolves each callsign against your DXKeeper log — determining whether the station is needed, worked, confirmed, or a new multiplier — and assigns a foreground colour accordingly. Bridge to Thetis receives that colour-coded spot stream from Commander and forwards each spot to Thetis via the TCI protocol, where it appears as a labelled, colour-coded marker on the panadapter.

Additionally, Bridge to Thetis enriches each spot with:

- **Background colour** indicating LoTW / eQSL membership (from DXView)
- **Country name and continent** (from DXView's BigCTY database)
- **Beam heading and distance** from your QTH to the DX entity
- **Spotter callsign and comment** from the original cluster spot

Clicking a spot on the Thetis panadapter tunes Commander's VFO to that frequency — this is handled by the existing Commander ↔ Thetis connection and requires no additional configuration.

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

Commander broadcasts spots to Bridge to Thetis over UDP.

1. In Commander, open **Settings → Network Services**.
2. Find the **Waterfall Bandmap and Thetis Bridge Service** panel.
3. Enable the service (leave the port at the default **13063**).
4. Click **OK**.

---

## Step 4 — Configure Bridge to Thetis

1. Install and launch **Bridge to Thetis**.
2. The TCI Host should be **127.0.0.1** and TCI Port **50001** (defaults). Change the host only if Thetis runs on a different PC.
3. Optionally enter your station **latitude and longitude** — this enables beam heading and distance to appear in spot tooltips.
4. Optionally enable **Band filter** to show only spots on the same band as your current VFO.

---

## Step 5 — Verify operation

With all four applications running (SpotCollector, Commander, Thetis, Bridge to Thetis):

1. The Bridge to Thetis status indicator should show **Ready**.
2. Within seconds of spots appearing in SpotCollector, matching coloured spots should appear on the Thetis panadapter.
3. Hovering over a spot in Thetis shows a tooltip with callsign, country, spotter, comment, heading, and age.
4. Clicking a spot on the panadapter tunes Commander's VFO.

If spots do not appear, use the **Debug Log** in Bridge to Thetis to diagnose the issue.

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

| Port | Purpose |
|------|---------|
| 13063 UDP | Commander → Bridge to Thetis (spot stream) |
| 50001 TCP/WS | Bridge to Thetis → Thetis (TCI spot commands) |
| 13013 TCP | Commander ↔ Thetis (VFO/CAT — not Bridge) |

---

## Further information

- Full configuration guide: [CONFIGURATION.md](https://github.com/ct2iry-dot/bridge-to-thetis/blob/main/CONFIGURATION.md)
- Download and releases: [GitHub Releases](https://github.com/ct2iry-dot/bridge-to-thetis/releases)
- Issues and support: [GitHub Issues](https://github.com/ct2iry-dot/bridge-to-thetis/issues)
