using System.Net.WebSockets;
using System.Text;
using BridgeToThetis.Core;
using BridgeToThetis.Models;

namespace BridgeToThetis.Services;

public class TciClient : IDisposable
{
    public event Action? OnReady;
    public event Action<string>? OnStatusChanged;
    public event Action<string>? OnLogMessage;
    public event Action<long>? OnVfoFrequency;

    public string Status { get; private set; } = "Disconnected";
    public bool IsReady { get; private set; }
    public int LatencyMs { get; private set; } = -1;

    private ClientWebSocket? _ws;
    private CancellationTokenSource? _cts;
    private readonly SemaphoreSlim _sendLock = new(1, 1);
    private DateTime _pingSentAt;
    private bool _pingPending;
    private bool _disposed;

    private string _host = "127.0.0.1";
    private int _port = 50001;
    private readonly object _wsLock = new();

    public void Connect(string host, int port)
    {
        _host = host;
        _port = port;
        Disconnect();
        _cts = new CancellationTokenSource();
        _ = RunLoopAsync(_cts.Token);
    }

    public void Disconnect()
    {
        _cts?.Cancel();
        _cts?.Dispose();
        _cts = null;
        lock (_wsLock)
        {
            try { _ws?.Abort(); } catch { }
            _ws?.Dispose();
            _ws = null;
        }
        IsReady = false;
        LatencyMs = -1;
    }

    private async Task RunLoopAsync(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            SetStatus("Connecting");
            IsReady = false;
            LatencyMs = -1;

            var ws = new ClientWebSocket();
            lock (_wsLock) { _ws = ws; }

            try
            {
                var uri = new Uri($"ws://{_host}:{_port}");
                await ws.ConnectAsync(uri, ct);
                SetStatus("Connected");

                // Start ping timer and receive loop concurrently
                using var linkedCts = CancellationTokenSource.CreateLinkedTokenSource(ct);
                var receiveTask = ReceiveLoopAsync(ws, linkedCts.Token);
                var pingTask = PingLoopAsync(ws, linkedCts.Token);

                await Task.WhenAny(receiveTask, pingTask);
                linkedCts.Cancel();
                try { await Task.WhenAll(receiveTask, pingTask); } catch { }
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex)
            {
                OnLogMessage?.Invoke($"[TCI] Connect error: {ex.Message}");
                SetStatus($"Error");
            }
            finally
            {
                IsReady = false;
                LatencyMs = -1;
                lock (_wsLock) { ws.Dispose(); if (_ws == ws) _ws = null; }
            }

            if (ct.IsCancellationRequested) break;

            SetStatus("Disconnected");
            OnLogMessage?.Invoke("[TCI] Reconnecting in 5 s...");
            try { await Task.Delay(5000, ct); } catch (OperationCanceledException) { break; }
        }

        SetStatus("Disconnected");
    }

    private async Task ReceiveLoopAsync(ClientWebSocket ws, CancellationToken ct)
    {
        var buffer = new byte[65536];
        var sb = new StringBuilder();

        while (!ct.IsCancellationRequested && ws.State == WebSocketState.Open)
        {
            WebSocketReceiveResult result;
            try
            {
                result = await ws.ReceiveAsync(new ArraySegment<byte>(buffer), ct);
            }
            catch (OperationCanceledException) { break; }
            catch { break; }

            if (result.MessageType == WebSocketMessageType.Close)
            {
                OnLogMessage?.Invoke("[TCI] Server closed connection");
                break;
            }

            sb.Append(Encoding.UTF8.GetString(buffer, 0, result.Count));

            if (result.EndOfMessage)
            {
                string msg = sb.ToString();
                sb.Clear();
                ProcessInbound(msg);
            }
        }
    }

    private void ProcessInbound(string msg)
    {
        OnLogMessage?.Invoke($"[TCI] RX: {msg[..Math.Min(msg.Length, 200)]}");

        // Handle multiple semicolon-delimited messages
        foreach (string frame in msg.Split(';', StringSplitOptions.RemoveEmptyEntries))
        {
            string f = frame.Trim();
            if (string.IsNullOrEmpty(f)) continue;

            if (f == "ready")
            {
                IsReady = true;
                SetStatus("Ready");
                OnLogMessage?.Invoke("[TCI] Ready received — firing OnReady");
                OnReady?.Invoke();
                continue;
            }

            // Ping response: audio_samplerate:TRX,RATE
            if (_pingPending && f.StartsWith("audio_samplerate:", StringComparison.OrdinalIgnoreCase))
            {
                _pingPending = false;
                LatencyMs = (int)(DateTime.UtcNow - _pingSentAt).TotalMilliseconds;
                OnLogMessage?.Invoke($"[TCI] Latency: {LatencyMs} ms");
                continue;
            }

            // vfo_frequency:TRX,CHAN,FREQ_HZ
            if (f.StartsWith("vfo_frequency:", StringComparison.OrdinalIgnoreCase))
            {
                string args = f["vfo_frequency:".Length..];
                var parts = args.Split(',');
                if (parts.Length >= 3 && long.TryParse(parts[2], out long vfo))
                {
                    OnVfoFrequency?.Invoke(vfo);
                }
                continue;
            }

            // clicked_on_spot:CALL,FREQ
            if (f.StartsWith("clicked_on_spot:", StringComparison.OrdinalIgnoreCase))
            {
                OnLogMessage?.Invoke($"[TCI] Spot click: {f["clicked_on_spot:".Length..]}");
                continue;
            }

            // rx_clicked_on_spot:RX,CHAN,CALL,FREQ
            if (f.StartsWith("rx_clicked_on_spot:", StringComparison.OrdinalIgnoreCase))
            {
                OnLogMessage?.Invoke($"[TCI] RX spot click: {f["rx_clicked_on_spot:".Length..]}");
                continue;
            }
        }
    }

    private async Task PingLoopAsync(ClientWebSocket ws, CancellationToken ct)
    {
        while (!ct.IsCancellationRequested && ws.State == WebSocketState.Open)
        {
            try { await Task.Delay(3000, ct); } catch (OperationCanceledException) { break; }

            if (!IsReady || ws.State != WebSocketState.Open) continue;

            try
            {
                _pingSentAt = DateTime.UtcNow;
                _pingPending = true;
                await SendRawAsync("audio_samplerate:0;", ct);
            }
            catch { break; }
        }
    }

    public async Task SendSpotAsync(SpotInfo spot, bool extended)
    {
        if (!IsReady) return;

        string tciMode = BandHelpers.MapMode(spot.Mode, spot.FreqHz);
        uint argb = ColorHelpers.HexToArgb(spot.BackColor);

        string cmd;
        if (extended)
        {
            string utc = spot.UtcTime.ToString("yyyy-MM-ddTHH:mm:ssZ");
            var _camel = new System.Text.Json.JsonSerializerOptions
            {
                PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.CamelCase
            };
            string jsonPayload = System.Text.Json.JsonSerializer.Serialize(new
            {
                Spotter = spot.Spotter,
                Comment = spot.Comment,
                Heading = spot.Heading,
                Country = spot.Country,
                UtcTime = utc,
                TextColor = spot.FontColor,
                IsSWL = false,
                SWLSecondsToLive = 0,
            }, _camel);
            cmd = $"spot:{spot.CallSign},{tciMode},{spot.FreqHz},{argb},[json]{jsonPayload};";
        }
        else
        {
            cmd = $"spot:{spot.CallSign},{tciMode},{spot.FreqHz},{argb};";
        }

        OnLogMessage?.Invoke($"[TCI] TX spot: {cmd[..Math.Min(cmd.Length, 200)]}");
        await SendRawAsync(cmd, CancellationToken.None);
    }

    public async Task SendDeleteAsync(string callSign)
    {
        if (!IsReady) return;
        string cmd = $"spot_delete:{callSign};";
        OnLogMessage?.Invoke($"[TCI] TX delete: {cmd}");
        await SendRawAsync(cmd, CancellationToken.None);
    }

    public async Task SendClearAsync()
    {
        if (!IsReady) return;
        const string cmd = "spot_clear;";
        OnLogMessage?.Invoke("[TCI] TX clear");
        await SendRawAsync(cmd, CancellationToken.None);
    }

    private async Task SendRawAsync(string text, CancellationToken ct)
    {
        ClientWebSocket? ws;
        lock (_wsLock) { ws = _ws; }
        if (ws == null || ws.State != WebSocketState.Open) return;

        byte[] bytes = Encoding.UTF8.GetBytes(text);
        await _sendLock.WaitAsync(ct);
        try
        {
            await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, ct);
        }
        catch (Exception ex)
        {
            OnLogMessage?.Invoke($"[TCI] Send error: {ex.Message}");
        }
        finally
        {
            _sendLock.Release();
        }
    }

    private void SetStatus(string s)
    {
        Status = s;
        OnStatusChanged?.Invoke(s);
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        Disconnect();
        _sendLock.Dispose();
    }
}
