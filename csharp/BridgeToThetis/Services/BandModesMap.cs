using System.Globalization;
using System.IO;

namespace BridgeToThetis.Services;

public class BandModesMap
{
    private readonly struct BandRange
    {
        public readonly long StartHz;
        public readonly long EndHz;
        public readonly string Mode;
        public readonly string Band;

        public BandRange(long startHz, long endHz, string mode, string band)
        {
            StartHz = startHz; EndHz = endHz; Mode = mode; Band = band;
        }
    }

    private List<BandRange> _ranges = new();
    private string? _filePath;
    private string _headerDate = string.Empty;
    private readonly object _lock = new();

    public event Action<string>? OnLogMessage;

    public bool Load(string? filePath)
    {
        _filePath = filePath;
        if (string.IsNullOrEmpty(filePath) || !File.Exists(filePath))
        {
            OnLogMessage?.Invoke($"[BMM] BandModes.txt not found: {filePath}");
            return false;
        }

        return ParseFile(filePath);
    }

    private bool ParseFile(string path)
    {
        try
        {
            string[] lines = File.ReadAllLines(path);
            if (lines.Length == 0) return false;

            // Line 1: BandModes YYYY-MM-DD
            string header = lines[0].Trim();
            string newDate = header.StartsWith("BandModes ", StringComparison.OrdinalIgnoreCase)
                ? header["BandModes ".Length..].Trim()
                : header;

            var newRanges = new List<BandRange>();

            for (int i = 1; i < lines.Length; i++)
            {
                string line = lines[i].Trim();
                if (string.IsNullOrEmpty(line) || line.StartsWith('#')) continue;

                // start_khz,end_khz,mode,band
                string[] parts = line.Split(',');
                if (parts.Length < 4) continue;

                if (!double.TryParse(parts[0].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out double startKhz)) continue;
                if (!double.TryParse(parts[1].Trim(), NumberStyles.Float, CultureInfo.InvariantCulture, out double endKhz)) continue;
                string mode = parts[2].Trim();
                string band = parts[3].Trim();

                newRanges.Add(new BandRange(
                    (long)(startKhz * 1000),
                    (long)(endKhz * 1000),
                    mode,
                    band));
            }

            lock (_lock)
            {
                _headerDate = newDate;
                _ranges = newRanges;
            }

            OnLogMessage?.Invoke($"[BMM] Loaded {newRanges.Count} ranges, date={newDate}");
            return true;
        }
        catch (Exception ex)
        {
            OnLogMessage?.Invoke($"[BMM] Load error: {ex.Message}");
            return false;
        }
    }

    /// <summary>
    /// Refresh only if the date header has changed.
    /// </summary>
    public void RefreshIfChanged()
    {
        if (string.IsNullOrEmpty(_filePath) || !File.Exists(_filePath)) return;

        try
        {
            string firstLine = File.ReadLines(_filePath).FirstOrDefault() ?? string.Empty;
            string newDate = firstLine.StartsWith("BandModes ", StringComparison.OrdinalIgnoreCase)
                ? firstLine["BandModes ".Length..].Trim()
                : firstLine.Trim();

            if (newDate != _headerDate)
            {
                OnLogMessage?.Invoke($"[BMM] Date changed ({_headerDate} → {newDate}), reloading...");
                ParseFile(_filePath);
            }
        }
        catch { }
    }

    /// <summary>
    /// Lookup mode for a given frequency in Hz. Returns "USB" if not found.
    /// </summary>
    public string Lookup(long freqHz)
    {
        lock (_lock)
        {
            foreach (var r in _ranges)
            {
                if (freqHz >= r.StartHz && freqHz <= r.EndHz)
                    return r.Mode;
            }
        }
        return "USB";
    }

    public string HeaderDate
    {
        get { lock (_lock) return _headerDate; }
    }

    public int RangeCount
    {
        get { lock (_lock) return _ranges.Count; }
    }
}
