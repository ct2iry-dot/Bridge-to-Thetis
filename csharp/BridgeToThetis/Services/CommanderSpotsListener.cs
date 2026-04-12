using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Xml;
using BridgeToThetis.Core;
using BridgeToThetis.Models;

namespace BridgeToThetis.Services;

public class CommanderSpotsListener : IDisposable
{
    public Action<SpotInfo>? OnSpotAdd;
    public Action<string, long>? OnSpotDelete;
    public Action? OnSpotClearAll;
    public Action<string>? OnStatusChanged;
    public Action<string>? OnLogMessage;

    public string Status { get; private set; } = "Stopped";
    public DateTime? LastPacketTime { get; private set; }

    private UdpClient? _udp;
    private CancellationTokenSource? _cts;
    private int _packetCount;
    private readonly object _lock = new();

    public void Start(string ip, int port)
    {
        Stop();
        _cts = new CancellationTokenSource();
        _ = ListenAsync(ip, port, _cts.Token);
    }

    public void Stop()
    {
        _cts?.Cancel();
        _cts?.Dispose();
        _cts = null;
        lock (_lock) { _udp?.Close(); _udp = null; }
        SetStatus("Stopped");
    }

    private async Task ListenAsync(string ip, int port, CancellationToken ct)
    {
        try
        {
            lock (_lock)
            {
                _udp = new UdpClient(new IPEndPoint(IPAddress.Parse(ip), port));
            }
            SetStatus($"Listening :{port}");
            _packetCount = 0;

            while (!ct.IsCancellationRequested)
            {
                UdpReceiveResult result;
                try
                {
                    result = await _udp!.ReceiveAsync(ct);
                }
                catch (OperationCanceledException) { break; }
                catch (Exception ex)
                {
                    SetStatus($"Error: {ex.Message}");
                    break;
                }

                _packetCount++;
                LastPacketTime = DateTime.UtcNow;

                try
                {
                    string xml = Encoding.UTF8.GetString(result.Buffer);
                    OnLogMessage?.Invoke($"[CDR] RX {result.Buffer.Length}b: {xml[..Math.Min(xml.Length, 400)]}");
                    ParsePacket(xml);
                    SetStatus($"OK — {_packetCount} pkts");
                }
                catch (Exception ex)
                {
                    OnLogMessage?.Invoke($"[CDR] Parse error: {ex.Message}");
                }
            }
        }
        catch (Exception ex)
        {
            SetStatus($"Error: {ex.Message}");
        }
        finally
        {
            lock (_lock) { _udp?.Close(); _udp = null; }
        }
    }

    private void ParsePacket(string xml)
    {
        // Try to determine format
        xml = xml.Trim();
        if (!xml.StartsWith('<')) return;

        var doc = new XmlDocument();
        doc.LoadXml(xml);
        XmlElement root = doc.DocumentElement!;

        string rootName = root.Name.ToLowerInvariant();

        // Format B: root=<spot> with <action> child
        if (rootName == "spot")
        {
            ParseFormatB(root);
            return;
        }

        // Format A: root = action element
        ParseFormatA(root);
    }

    private void ParseFormatA(XmlElement root)
    {
        string action = root.Name.ToLowerInvariant();

        if (action is "clearall" or "spotclearall")
        {
            OnSpotClearAll?.Invoke();
            return;
        }

        if (action is "delete" or "spotdelete")
        {
            string call = GetChild(root, "dxcall") ?? GetChild(root, "call") ?? string.Empty;
            string freqRaw = GetChild(root, "rxfreq") ?? "0";
            long freqHz = long.TryParse(freqRaw, out long fv) ? fv / 10 : 0;
            if (!string.IsNullOrEmpty(call))
                OnSpotDelete?.Invoke(call.ToUpperInvariant(), freqHz);
            return;
        }

        if (action is "add" or "spotadd")
        {
            string? call = GetChild(root, "dxcall") ?? GetChild(root, "call");
            if (string.IsNullOrEmpty(call)) return;

            string freqRaw = GetChild(root, "rxfreq") ?? "0";
            long freqHz = long.TryParse(freqRaw, out long fv) ? fv / 10 : 0;

            string mode = GetChild(root, "mode") ?? "USB";
            string spotter = GetChild(root, "spotter") ?? string.Empty;
            string comment = GetChild(root, "comment") ?? string.Empty;

            string fontColorRaw = GetChild(root, "fontcolor") ?? GetChild(root, "needcolor") ?? "0";
            string backColorRaw = GetChild(root, "backcolor") ?? "0";

            string fontHex = "#FFFFFF";
            string backHex = "#000000";
            if (int.TryParse(fontColorRaw, out int fc)) fontHex = ColorHelpers.Vb6ToHex(fc);
            if (int.TryParse(backColorRaw, out int bc)) backHex = ColorHelpers.Vb6ToHex(bc);

            var spot = new SpotInfo
            {
                CallSign = call.ToUpperInvariant(),
                FreqHz = freqHz,
                Mode = mode,
                Spotter = spotter,
                Comment = comment,
                FontColor = fontHex,
                BackColor = backHex,
                UtcTime = DateTime.UtcNow,
            };
            OnSpotAdd?.Invoke(spot);
        }
    }

    private void ParseFormatB(XmlElement root)
    {
        string action = (GetChild(root, "action") ?? string.Empty).ToLowerInvariant();

        if (action == "clearall")
        {
            OnSpotClearAll?.Invoke();
            return;
        }

        string? call = GetChild(root, "dxcall") ?? GetChild(root, "call");
        string freqRaw = GetChild(root, "frequency") ?? "0";
        long freqHz = double.TryParse(freqRaw,
            System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture,
            out double fkHz) ? (long)(fkHz * 1000) : 0;

        if (action == "delete")
        {
            if (!string.IsNullOrEmpty(call))
                OnSpotDelete?.Invoke(call!.ToUpperInvariant(), freqHz);
            return;
        }

        if (action is "add" or "")
        {
            if (string.IsNullOrEmpty(call)) return;

            string mode = GetChild(root, "mode") ?? "USB";
            string spotter = GetChild(root, "spotter") ?? string.Empty;
            string comment = GetChild(root, "comment") ?? string.Empty;

            string fontColorRaw = GetChild(root, "fontcolor") ?? "0";
            string backColorRaw = GetChild(root, "backcolor") ?? "0";

            string fontHex = "#FFFFFF";
            string backHex = "#000000";
            if (int.TryParse(fontColorRaw, out int fc)) fontHex = ColorHelpers.Vb6ToHex(fc);
            if (int.TryParse(backColorRaw, out int bc)) backHex = ColorHelpers.Vb6ToHex(bc);

            var spot = new SpotInfo
            {
                CallSign = call!.ToUpperInvariant(),
                FreqHz = freqHz,
                Mode = mode,
                Spotter = spotter,
                Comment = comment,
                FontColor = fontHex,
                BackColor = backHex,
                UtcTime = DateTime.UtcNow,
            };
            OnSpotAdd?.Invoke(spot);
        }
    }

    private static string? GetChild(XmlElement parent, string tagName)
    {
        // Try direct child match (case-insensitive)
        foreach (XmlNode node in parent.ChildNodes)
        {
            if (node is XmlElement el &&
                string.Equals(el.Name, tagName, StringComparison.OrdinalIgnoreCase))
            {
                return el.InnerText.Trim();
            }
        }
        return null;
    }

    private void SetStatus(string s)
    {
        Status = s;
        OnStatusChanged?.Invoke(s);
    }

    public void Dispose() => Stop();
}
