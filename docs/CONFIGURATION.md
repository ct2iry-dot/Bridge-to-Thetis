# DXLab - Thetis Bridge — Configuration Guide (v7.1.0)

This guide walks through every step needed to get DXLab - Thetis Bridge running with DXLab Suite and Thetis. Complete each section in order.

---

## 1. Prerequisites

Make sure you have all of the following installed and working before configuring DXLab - Thetis Bridge:

- **DXLab Suite** — specifically Commander, SpotCollector, and DXView
- **Thetis** — with your SDR hardware connected and receiving
- **DXLab - Thetis Bridge** — installed via `BridgeToThetis-Setup.msi`

Both DXLab Suite and Thetis should be running and operational (spots appearing in SpotCollector, VFO working in Commander) before you start DXLab - Thetis Bridge.

---

## 2. Thetis setup

### 2.1 Enable the TCI server

1. In Thetis, open **Setup** (the wrench icon or menu).
2. Go to the **TCI** tab (in some builds it is under **General → TCI**).
3. Check **Enable TCI Server**.
4. Set the port to **50001** (this is the default; DXLab - Thetis Bridge uses this port by default too).
5. Click **Apply** or **OK**.

### 2.2 Enable spot display on the panadapter

1. In Thetis, right-click on a panadapter.
2. Enable **Display calls on panadapter** (or equivalent option).
3. Spots will appear as labelled markers on the panadapter once DXLab - Thetis Bridge is running.

### 2.3 Connect Commander to Thetis (CAT / VFO control)

This step is separate from DXLab - Thetis Bridge and handles radio control (VFO tuning, mode, split). DXLab - Thetis Bridge only paints spots — it does not control the VFO.

1. In Commander, go to **Settings → Hardware → Radio**.
2. Select your Thetis connection (typically CAT port, or direct Thetis TCP on port 13013).
3. Verify Commander can tune the VFO. Once this works, clicking a spot in Thetis will QSY Commander automatically.

---

## 3. DXLab Suite setup

### 3.1 SpotCollector — DX cluster connection

SpotCollector must be connected to a DX cluster and receiving spots. If spots are already appearing in SpotCollector's spot pane, this is already working — skip to 3.2.

### 3.2 SpotCollector — background colours

DXLab - Thetis Bridge reads your SpotCollector background colour settings directly from the Windows registry and applies them to spot backgrounds in Thetis:

| SpotCollector setting | Meaning | Typical colour |
|---|---|---|
| PaneColor3 | No LoTW / no eQSL | White |
| PaneColor4 | LoTW member | Yellow |
| PaneColor9 | eQSL member | Cyan |
| PaneColor8 | Both LoTW and eQSL | Silver |

These are read automatically — no action needed. If you customise these colours in SpotCollector, DXLab - Thetis Bridge will use your custom colours.

### 3.3 Commander — Waterfall Bandmap and Thetis Bridge Service

Commander sends spots to DXLab - Thetis Bridge over UDP on port 13063.

1. In Commander, open **Settings → Network Services**.
2. Locate the **Waterfall Bandmap and Thetis Bridge Service** panel.
3. Enable the service.
4. The default port is **13063** — leave this unchanged.
5. Click **OK**.

Commander will now send every spot (with its resolved colour) to DXLab - Thetis Bridge via UDP.

### 3.4 DXView — LoTW and eQSL background colours

DXView maintains a database (.mdb file) of LoTW and eQSL membership. DXLab - Thetis Bridge reads this database to determine background colour per callsign.

- DXView must be installed and its database populated (this happens automatically as you use DXLab Suite).
- The path to DXView's database is found automatically via the Windows registry — no configuration needed.
- If DXView is not installed, spots will use a white background for all callsigns.

### 3.5 DXView — BigCTY country database

DXView ships with the BigCTY country database (`BigCTY.csv`). DXLab - Thetis Bridge uses this to determine:

- Country name (shown in Thetis spot tooltip)
- Continent
- Beam heading and distance from your QTH

This file is located and loaded automatically. Country and heading information appears in the Thetis spot tooltip when you hover over a spot on the panadapter.

---

## 4. DXLab - Thetis Bridge settings

When you first start DXLab - Thetis Bridge, the main window shows a small set of configurable options.

### 4.1 TCI connection

| Setting | Default | Notes |
|---|---|---|
| TCI Host | 127.0.0.1 | Change if Thetis runs on a different PC |
| TCI Port | 50001 | Must match the port set in Thetis Setup → TCI |

The status indicator shows **Ready** when DXLab - Thetis Bridge is connected to Thetis and has received the `ready` signal from the TCI server. Spots will not be sent before this point.

### 4.2 Band filter

When enabled, only spots on the same amateur band as the current Thetis VFO are painted on the panadapter. Spots on other bands are silently ignored.

This is useful during a contest or when you only want to see spots relevant to your current operating position.

### 4.3 Extended spot data

When enabled (default), DXLab - Thetis Bridge sends a full JSON payload with each spot containing: spotter, comment, country, heading, and UTC time. This data appears in the Thetis spot tooltip.

When disabled, only the minimal spot command is sent (callsign, mode, frequency, colour). Disable this only if you experience compatibility issues with older Thetis builds.

### 4.4 QTH coordinates

Enter your station latitude and longitude to enable beam heading and distance calculation. Headings appear in the spot tooltip as "Heading: NNN" and distance as "NNNNkm" in the comment field.

If left blank, country and continent are still shown but no heading or distance is calculated.

---

## 5. Verifying operation

Once all components are running:

1. Spots should appear on the Thetis panadapter within seconds of appearing in SpotCollector.
2. Spot colours should match SpotCollector (foreground = need status, background = LoTW/eQSL).
3. Hovering over a spot in Thetis should show a tooltip with: callsign, country, spotter, comment, heading, and age.
4. Clicking a spot on the Thetis panadapter should tune Commander's VFO to that frequency.

### Debug log

DXLab - Thetis Bridge includes a debug log window (menu or toolbar button). Each spot sent to Thetis is logged with: callsign, frequency, mode, background source, and foreground colour. Use this to diagnose any issue.

The **Send Test Spot** button (in the debug window) sends a test spot to Thetis so you can verify the TCI connection independently of the spot pipeline.

---

## 6. Troubleshooting

**No spots appear on the panadapter**
- Check the TCI status in DXLab - Thetis Bridge — it must show "Ready"
- Verify Thetis TCI server is enabled (Setup → TCI → Enable TCI Server)
- Verify Commander's Waterfall Bandmap and Thetis Bridge Service is enabled
- Check the Bridge debug log for error messages

**Status shows "Connecting" and never changes to "Ready"**
- Thetis is not running, or TCI is not enabled
- Check that port 50001 is not blocked by a firewall
- Verify the TCI Host setting matches the machine running Thetis

**Spots appear but all are the same colour**
- SpotCollector is not connected to your DXKeeper log — check SpotCollector's DXKeeper connection
- DXView database not found — check DXView is installed

**Spots appear but no country/heading in tooltip**
- BigCTY.csv not found — verify DXView is installed and has run at least once
- QTH coordinates not set — enter lat/lon in Bridge settings for headings

**Clicking spots in Thetis does not tune Commander**
- This is handled by the Commander ↔ Thetis CAT link (port 13013), not by DXLab - Thetis Bridge
- Verify Commander is connected to Thetis as a radio controller

---

## 7. Port summary

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 13063 | UDP | Commander → Bridge | Spot stream (Waterfall Bandmap) |
| 50001 | WebSocket (TCP) | Bridge → Thetis | TCI spot commands |
| 13013 | TCP | Commander ↔ Thetis | CAT / VFO control (not Bridge) |

All ports are on localhost (127.0.0.1) unless Thetis runs on a separate PC.
