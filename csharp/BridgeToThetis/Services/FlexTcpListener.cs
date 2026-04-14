using System.Net;
using System.Net.Sockets;
using System.Text;
using BridgeToThetis.Models;

namespace BridgeToThetis.Services;

/// <summary>
/// Pretends to be a FlexRadio Signature series radio on TCP port 4992.
/// Commander (configured as Flex Signature radio) connects here and sends
/// spot add/remove directives which are translated to SpotInfo objects.
///
/// Protocol reference:
///   https://github.com/flexradio/smartsdr-api-docs/wiki/SmartSDR-TCPIP-API
///   https://github.com/flexradio/smartsdr-api-docs/wiki/TCPIP-spot
///
/// Handshake (sent by us on connect):
///   V3.3.26.0\n          ← firmware version
///   H<HEX_HANDLE>\n      ← 32-bit handle, NO 0x prefix
///   S<handle>|radio ...\n
///   S<handle>|client <handle> connected\n  ← triggers Commander to start sending spots
///
/// Command format from Commander:
///   C<seq>|spot add rx_freq=<MHz> callsign=<call> [mode=...] [color=...] ...\n
///   C<seq>|spot remove <index>\n
///
/// Response format to Commander:
///   R<seq>|0|<spot_index>    ← ACK spot add
///   R<seq>|0|                ← ACK everything else
/// </summary>
public class FlexTcpListener : IDisposable
{
    public Action<SpotInfo>? OnSpotAdd;
    public Action<string, long>? OnSpotDelete;
    public Action? OnSpotClearAll;
    public Action<string>? OnStatusChanged;
    public Action<string>? OnLogMessage;

    public string Status { get; private set; } = "Stopped";

    private TcpListener? _listener;
    private CancellationTokenSource? _cts;
    private readonly object _lock = new();
    private int _clientCount;

    // Protocol version sent in the V handshake line — must be in range 1.0–1.4 (FlexLib API contract).
    private const string FlexProtocolVersion = "1.4.0.0";

    // Firmware version reported in the info response.
    // DXLab docs: "WaterfallBandmap requires SmartSDR 2.4.9+".
    // Commander's check is major=2, minor>=4, so 3.x fails. Use 2.x.
    private const string FlexFirmwareVersion = "2.9.0.0";

    // SmartSDR mode → TCI mode
    private static readonly Dictionary<string, string> ModeMap = new(StringComparer.OrdinalIgnoreCase)
    {
        ["lsb"]  = "LSB",
        ["usb"]  = "USB",
        ["cw"]   = "CW",
        ["cwl"]  = "CWL",
        ["cwu"]  = "CWU",
        ["am"]   = "AM",
        ["fm"]   = "FM",
        ["rtty"] = "DIGU",
        ["psk"]  = "DIGU",
        ["ft8"]  = "DIGU",
        ["ft4"]  = "DIGU",
        ["jt65"] = "DIGU",
        ["jt9"]  = "DIGU",
        ["wspr"] = "DIGU",
        ["js8"]  = "DIGU",
        ["digu"] = "DIGU",
        ["digl"] = "DIGL",
    };

    public void Start(int port)
    {
        Stop();
        _cts = new CancellationTokenSource();
        _ = ListenAsync(port, _cts.Token);
    }

    public void Stop()
    {
        _cts?.Cancel();
        _cts?.Dispose();
        _cts = null;
        lock (_lock) { _listener?.Stop(); _listener = null; }
        SetStatus("Stopped");
    }

    private async Task ListenAsync(int port, CancellationToken ct)
    {
        try
        {
            var listener = new TcpListener(IPAddress.Any, port);
            lock (_lock) { _listener = listener; }
            listener.Start();
            SetStatus($"Listening :{port}");
            Log($"[FLEX] Listening on TCP :{port}");

            while (!ct.IsCancellationRequested)
            {
                TcpClient client;
                try
                {
                    client = await listener.AcceptTcpClientAsync(ct);
                }
                catch (OperationCanceledException) { break; }
                catch (Exception ex)
                {
                    Log($"[FLEX] Accept error: {ex.Message}");
                    break;
                }

                int id = Interlocked.Increment(ref _clientCount);
                Log($"[FLEX] Commander connected from {((IPEndPoint?)client.Client.RemoteEndPoint)?.Address}");
                SetStatus($"Connected — {((IPEndPoint?)client.Client.RemoteEndPoint)?.Address}");
                _ = HandleClientAsync(client, id, ct);
            }
        }
        catch (Exception ex)
        {
            SetStatus($"Error: {ex.Message}");
            Log($"[FLEX] Listen error: {ex.Message}");
        }
        finally
        {
            lock (_lock) { _listener?.Stop(); _listener = null; }
        }
    }

    private async Task HandleClientAsync(TcpClient tcp, int id, CancellationToken ct)
    {
        // Generate a random 32-bit handle — NO 0x prefix per API spec
        string handle = Random.Shared.Next(0x10000000, 0x7FFFFFFF).ToString("X8");

        try
        {
        using (tcp)
        using (var stream = tcp.GetStream())
        {

        async Task SendLine(string line)
        {
            byte[] bytes = Encoding.ASCII.GetBytes(line + "\r\n");
            await stream.WriteAsync(bytes, ct);
        }

        try
        {
            // ── Handshake ──────────────────────────────────────────────────────
            Log($"[FLEX] HS1 sending V{FlexProtocolVersion}");
            await SendLine($"V{FlexProtocolVersion}");
            Log($"[FLEX] HS2 sending H{handle}");
            await SendLine($"H{handle}");
            Log($"[FLEX] HS3 sending M/S burst");

            // Full connection burst — Commander waits for "client <handle> connected"
            await SendLine($"M10000001|Client connected");
            // version= in S radio: Commander's off-by-one skips first char of value.
            // "version=22.9.0.0" → Commander reads "2.9.0.0" → major=2 → FlexSpotsSupported=True
            await SendLine($"S{handle}|radio model=FLEX-6500 serial=1234-5678-9012-3456" +
                           $" version=2{FlexFirmwareVersion} nickname=BridgeToThetis callsign=N0CALL" +
                           $" slices=1 panadapters=1 lineout_gain=50 lineout_mute=0" +
                           $" headphone_gain=50 headphone_mute=0 snap_tune_enabled=1" +
                           $" tnf_enabled=1 spots=1 region=EUR ip=127.0.0.1");
            await SendLine($"S{handle}|radio filter_sharpness VOICE level=2 auto_level=1");
            await SendLine($"S{handle}|radio filter_sharpness CW level=2 auto_level=1");
            await SendLine($"S{handle}|radio filter_sharpness DIGITAL level=2 auto_level=1");
            await SendLine($"S{handle}|radio static_net_params ip= gateway= netmask=");
            await SendLine($"S{handle}|interlock timeout=0 acc_txreq_enable=0 rca_txreq_enable=0" +
                           $" tx1_enabled=1 tx2_enabled=1 tx3_enabled=1 acc_tx_enabled=1 tx_delay=0");
            await SendLine($"S{handle}|panadapter 0x00000001 center=14.100000 bandwidth=0.200000" +
                           $" min_dbm=-125.00 max_dbm=0.00 fps=25 band=20 capacity=16 available=16" +
                           $" rxant=ANT1 ant_list=ANT1,ANT2 xpixels=500 ypixels=200 daxiq_rate=48");
            await SendLine($"S{handle}|slice 0 RF_frequency=14.100 mode=USB rx_ant=ANT1" +
                           $" in_use=1 active=1 pan=0x00000001");
            Log($"[FLEX] HS4 sending client connected");
            // Critical line — triggers Commander to start sending spots
            await SendLine($"S{handle}|client {handle} connected");

            Log($"[FLEX] Handshake complete — handle={handle}");

            // ── Periodic heartbeat ─────────────────────────────────────────────
            // Sends a radio S message every 5 s so FlexLib sees a "live" radio
            var heartbeatSeq = 10000000;
            _ = Task.Run(async () =>
            {
                while (!ct.IsCancellationRequested && tcp.Connected)
                {
                    await Task.Delay(5000, ct).ContinueWith(_ => { }); // swallow cancellation
                    if (!tcp.Connected || ct.IsCancellationRequested) break;
                    try
                    {
                        await SendLine($"S{handle}|radio model=FLEX-6500 serial=1234-5678-9012-3456" +
                                       $" version=2{FlexFirmwareVersion} nickname=BridgeToThetis callsign=N0CALL" +
                                       $" slices=1 panadapters=1 tnf_enabled=1 spots=1" +
                                       $" region=EUR ip=127.0.0.1 ant_list=ANT1,ANT2");
                        await SendLine($"S{handle}|slice 0 RF_frequency=14.100 mode=USB" +
                                       $" rx_ant=ANT1 in_use=1 active=1 pan=0x00000001 dax=1");
                        heartbeatSeq++;
                    }
                    catch { break; }
                }
            }, ct);

            // ── Receive loop ───────────────────────────────────────────────────
            var spotIndex = 1;
            var spotMap = new Dictionary<int, string>(); // index → callsign
            var buf = new StringBuilder();
            var readBuf = new byte[4096];

            while (!ct.IsCancellationRequested && tcp.Connected)
            {
                int n;
                try
                {
                    n = await stream.ReadAsync(readBuf, ct);
                }
                catch (OperationCanceledException) { break; }
                catch { break; }

                if (n == 0) break;

                buf.Append(Encoding.ASCII.GetString(readBuf, 0, n));

                string all = buf.ToString();
                int nl;
                while ((nl = all.IndexOf('\n')) >= 0)
                {
                    string line = all[..nl].Trim();
                    all = all[(nl + 1)..];

                    if (string.IsNullOrEmpty(line)) continue;

                    Log($"[FLEX] RX: {line[..Math.Min(line.Length, 200)]}");

                    // Parse C[D]<seq>|<command>
                    if (line.Length < 2 || (line[0] != 'C' && line[0] != 'c')) continue;
                    int pipeIdx = line.IndexOf('|');
                    if (pipeIdx < 0) continue;

                    string seqPart = line[1..pipeIdx].TrimStart('D', 'd');
                    string command = line[(pipeIdx + 1)..].Trim();
                    string cmdLow  = command.ToLowerInvariant();

                    // ── spot add ──────────────────────────────────────────────
                    if (cmdLow.StartsWith("spot add"))
                    {
                        var kv = ParseKv(command[8..]);

                        string callsign   = (kv.GetValueOrDefault("callsign") ?? "").ToUpperInvariant();
                        string rxFreqStr  = kv.GetValueOrDefault("rx_freq") ?? "0";
                        string modeRaw    = kv.GetValueOrDefault("mode") ?? "usb";
                        string colorStr   = kv.GetValueOrDefault("color") ?? "";
                        string bgColorStr = kv.GetValueOrDefault("background_color") ?? "";
                        string comment    = DecodeComment(kv.GetValueOrDefault("comment") ?? "");
                        string spotter    = DecodeComment(kv.GetValueOrDefault("spotter_callsign") ?? "");

                        long freqHz = double.TryParse(rxFreqStr,
                            System.Globalization.NumberStyles.Float,
                            System.Globalization.CultureInfo.InvariantCulture,
                            out double mhz) ? (long)(mhz * 1_000_000) : 0;

                        string tciMode = ModeMap.GetValueOrDefault(modeRaw.ToLowerInvariant(), "USB");
                        string fontHex = ArgbToHex(colorStr) ?? "#0000FF";
                        string backHex = ArgbToHex(bgColorStr) ?? "#FFFFFF";

                        int idx = spotIndex++;
                        spotMap[idx] = callsign;

                        await SendLine($"R{seqPart}|0|{idx}");
                        // Echo S message so Commander's FlexLib fires SpotAdded event
                        await SendLine($"S{handle}|spot {idx} callsign={callsign}" +
                                       $" rx_freq={mhz:F6} mode={modeRaw.ToLowerInvariant()}" +
                                       $" color={colorStr} background_color={bgColorStr}" +
                                       $" comment={kv.GetValueOrDefault("comment") ?? ""}" +
                                       $" spotter_callsign={kv.GetValueOrDefault("spotter_callsign") ?? ""}" +
                                       $" lifetime_seconds=300");

                        if (!string.IsNullOrEmpty(callsign) && freqHz > 0)
                        {
                            Log($"[FLEX] Spot add #{idx} {callsign} {freqHz}Hz {tciMode} fg={fontHex} bg={backHex}");
                            var spot = new SpotInfo
                            {
                                CallSign  = callsign,
                                FreqHz    = freqHz,
                                Mode      = tciMode,
                                Spotter   = spotter,
                                Comment   = comment,
                                FontColor = fontHex,
                                BackColor = backHex,
                                UtcTime   = DateTime.UtcNow,
                            };
                            OnSpotAdd?.Invoke(spot);
                        }
                    }
                    // ── spot remove ───────────────────────────────────────────
                    else if (cmdLow.StartsWith("spot remove"))
                    {
                        string rest = command[11..].Trim();
                        var kv = ParseKv(rest);
                        string callsign = "";
                        long freqHz = 0;

                        if (kv.ContainsKey("callsign"))
                        {
                            callsign = (kv["callsign"]).ToUpperInvariant();
                            if (double.TryParse(kv.GetValueOrDefault("rx_freq") ?? "0",
                                System.Globalization.NumberStyles.Float,
                                System.Globalization.CultureInfo.InvariantCulture,
                                out double mhz2)) freqHz = (long)(mhz2 * 1_000_000);
                            var entry = spotMap.FirstOrDefault(e => e.Value == callsign);
                            if (entry.Value != null) spotMap.Remove(entry.Key);
                        }
                        else if (int.TryParse(rest, out int idx2))
                        {
                            spotMap.TryGetValue(idx2, out callsign!);
                            spotMap.Remove(idx2);
                        }

                        await SendLine($"R{seqPart}|0|");
                        if (!string.IsNullOrEmpty(callsign))
                        {
                            Log($"[FLEX] Spot remove {callsign}");
                            OnSpotDelete?.Invoke(callsign, freqHz);
                        }
                    }
                    // ── spot clear_all ────────────────────────────────────────
                    else if (cmdLow is "spot clear_all" or "spot clearall" or "spot clear")
                    {
                        spotMap.Clear();
                        await SendLine($"R{seqPart}|0|");
                        Log($"[FLEX] Spot clear all");
                        OnSpotClearAll?.Invoke();
                    }
                    // ── info ──────────────────────────────────────────────────
                    else if (cmdLow == "info")
                    {
                        // Real SmartSDR 'info' response (FlexLib Radio.cs UpdateInfo) uses comma
                        // separators and handles: callsign, name, region, atu_present, gps, options,
                        // screensaver, num_tx, netmask, diversity_allowed.
                        // 'version=' and 'model=' are NOT in the real protocol's info response —
                        // those come from the UDP discovery broadcast, not the TCP info command.
                        //
                        // Commander's VB6 info parser has an off-by-one bug:
                        //   Mid(str, InStr(str,"key=")+Len("key=")+1)  ← +1 skips first char of value
                        // So every value has its first character skipped. Fix: double the first char.
                        //   "model=FFLEX-6500"  → Commander reads "FLEX-6500" ✓
                        //   "version=22.9.0.0"  → Commander off-by-one skips first '2' → reads "2.9.0.0" → major=2 → True ✓
                        //   "version=2.9.0.0"   → Commander off-by-one skips '2' → reads ".9.0.0" → major=0 → False ✗
                        //   The off-by-one affects ALL fields in the info response, including version.
                        //   (Previous tests with 22.9.0.0 failed only because State 35 was crashing and resetting state.)
                        await SendLine($"R{seqPart}|0|" +
                                       $"version=2{FlexFirmwareVersion}," +
                                       $"callsign=N0CALL," +
                                       $"name=BridgeToThetis," +
                                       $"region=EUR," +
                                       $"model=FFLEX-6500");
                    }
                    // ── meter list ────────────────────────────────────────────
                    else if (cmdLow == "meter list")
                    {
                        // Real SmartSDR format (from FlexLib Radio.cs ParseMeterStatus):
                        // Split by '#' → each block is ONE field: "N.key=value" with NO spaces.
                        // N = meter index (1-based), key = field name, value = value.
                        // Fields per meter: num (source index), src (source type), nam (name),
                        //                  low, hi (range), fps (update rate).
                        // src=SLC means Slice, num=0 means slice 0.
                        // Integer values for low/hi/fps avoid Portuguese CDbl locale issues.
                        // Meter 1: S-meter (slice 0 signal strength)
                        // Meter 2: TX forward power
                        await SendLine($"R{seqPart}|0|" +
                                       $"1.num=0#1.src=SLC#1.nam=SIG-S#1.low=-127#1.hi=20#1.fps=40#" +
                                       $"2.num=1#2.src=SLC#2.nam=SIG-Q#2.low=-127#2.hi=20#2.fps=40#" +
                                       $"3.num=0#3.src=TX#3.nam=FORWARD#3.low=0#3.hi=100#3.fps=40#" +
                                       $"4.num=0#4.src=TX#4.nam=SWR#4.low=0#4.hi=100#4.fps=40");
                    }
                    // ── sub pan/slice/spot/tx — ACK + re-broadcast current state ─
                    else if (cmdLow == "sub pan all")
                    {
                        await SendLine($"R{seqPart}|0|");
                        await SendLine($"S{handle}|panadapter 0x00000001 center=14.100000 bandwidth=0.200000" +
                                       $" min_dbm=-125.00 max_dbm=0.00 fps=25 band=20 capacity=16 available=16" +
                                       $" rxant=ANT1 ant_list=ANT1,ANT2 xpixels=500 ypixels=200 daxiq_rate=48");
                    }
                    else if (cmdLow == "sub slice all")
                    {
                        await SendLine($"R{seqPart}|0|");
                        await SendLine($"S{handle}|slice 0 RF_frequency=14.100 mode=USB rx_ant=ANT1" +
                                       $" in_use=1 active=1 pan=0x00000001 dax=0");
                    }
                    else if (cmdLow == "sub spot all")
                    {
                        await SendLine($"R{seqPart}|0|");
                        // no existing spots — nothing else to send
                    }
                    else if (cmdLow == "sub tx all")
                    {
                        await SendLine($"R{seqPart}|0|");
                        await SendLine($"S{handle}|transmit freq=14.100 rfpower=100 tunepower=10" +
                                       $" tx_ant=ANT1 dax=0 sb_monitor=0");
                    }
                    // ── slice set — ACK + echo updated slice state ─────────────
                    else if (cmdLow.StartsWith("slice s ") || cmdLow.StartsWith("slice set "))
                    {
                        await SendLine($"R{seqPart}|0|");
                        // Echo a slice status so FlexLib knows the set was applied
                        await SendLine($"S{handle}|slice 0 RF_frequency=14.100 mode=USB rx_ant=ANT1" +
                                       $" in_use=1 active=1 pan=0x00000001 dax=1");
                    }
                    // ── everything else → ACK ─────────────────────────────────
                    else
                    {
                        await SendLine($"R{seqPart}|0|");
                    }
                }

                buf.Clear();
                buf.Append(all);
            }
        }
        catch (Exception ex)
        {
            Log($"[FLEX] Client error: {ex.Message}");
        }
        finally
        {
            OnSpotClearAll?.Invoke();
            SetStatus("Listening");
            Log($"[FLEX] Commander disconnected (client #{id})");
        }

        } // end using stream
        } // end outer try
        catch (Exception ex)
        {
            Log($"[FLEX] Outer error (client #{id}): {ex.GetType().Name}: {ex.Message}");
            SetStatus("Listening");
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /// <summary>Parse "key=value key2=value2" into a dictionary.</summary>
    private static Dictionary<string, string> ParseKv(string s)
    {
        var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (string token in s.Split(' ', StringSplitOptions.RemoveEmptyEntries))
        {
            int eq = token.IndexOf('=');
            if (eq > 0)
                result[token[..eq].Trim()] = token[(eq + 1)..].Trim();
        }
        return result;
    }

    /// <summary>Decode Flex comment: 0x7F → space.</summary>
    private static string DecodeComment(string s)
        => s.Replace("\x7f", " ").Replace("0x7F", " ").Replace("0x7f", " ");

    /// <summary>
    /// Convert a color value to '#RRGGBB'.
    /// Accepts:
    ///   - Commander decimal ARGB long (e.g. "-16711936") — from ConvertColor:
    ///       base=-16777216, then +Blue +(Green*256) +(Red*65536)
    ///       → equivalent to signed Int32 with 0xFF alpha, RGB in standard order
    ///   - Flex '#AARRGGBB' hex string
    ///   - Flex '#RRGGBB' hex string
    /// Returns null if unparseable.
    /// </summary>
    private static string? ArgbToHex(string argb)
    {
        string s = argb.Trim();
        // Decimal signed long from Commander's ConvertColor
        if (s.Length > 0 && (char.IsDigit(s[0]) || s[0] == '-'))
        {
            if (long.TryParse(s, out long n))
            {
                int r = (int)(n >> 16) & 0xFF;
                int g = (int)(n >> 8)  & 0xFF;
                int b = (int) n        & 0xFF;
                return $"#{r:X2}{g:X2}{b:X2}";
            }
            return null;
        }
        // Hex string from Flex protocol
        s = s.TrimStart('#');
        if (s.Length == 8) return $"#{s[2..]}";   // drop AA
        if (s.Length == 6) return $"#{s}";
        return null;
    }

    private void Log(string msg) => OnLogMessage?.Invoke(msg);

    private void SetStatus(string s)
    {
        Status = s;
        OnStatusChanged?.Invoke(s);
    }

    public void Dispose() => Stop();
}
