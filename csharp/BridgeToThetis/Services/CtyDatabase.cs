using System.Globalization;
using System.IO;

namespace BridgeToThetis.Services;

public class DxccEntity
{
    public string Name { get; set; } = string.Empty;
    public int Dxcc { get; set; }
    public string Continent { get; set; } = string.Empty;
    public int Itu { get; set; }
    public int Cq { get; set; }
    public double Lat { get; set; }
    public double Lon { get; set; }
}

public class CtyDatabase
{
    private readonly Dictionary<string, DxccEntity> _exactMatches = new(StringComparer.OrdinalIgnoreCase);
    private readonly List<(string Prefix, DxccEntity Entity)> _prefixList = new();
    private bool _loaded;

    public event Action<string>? OnLogMessage;

    public bool Load(string? csvPath)
    {
        _exactMatches.Clear();
        _prefixList.Clear();
        _loaded = false;

        if (string.IsNullOrEmpty(csvPath) || !File.Exists(csvPath))
        {
            OnLogMessage?.Invoke($"[CTY] BigCTY.csv not found: {csvPath}");
            return false;
        }

        try
        {
            foreach (string line in File.ReadLines(csvPath))
            {
                if (string.IsNullOrWhiteSpace(line) || line.StartsWith('#')) continue;

                // CSV: prefix,entity_name,dxcc_num,continent,itu_zone,cq_zone,lat,lon,tz_offset,prefixes_list
                string[] cols = SplitCsvLine(line);
                if (cols.Length < 10) continue;

                string mainPrefix = cols[0].Trim();
                string entityName = cols[1].Trim();
                int dxcc = int.TryParse(cols[2].Trim(), out int d) ? d : 0;
                string continent = cols[3].Trim();
                int itu = int.TryParse(cols[4].Trim(), out int i) ? i : 0;
                int cq = int.TryParse(cols[5].Trim(), out int c) ? c : 0;
                double lat = double.TryParse(cols[6].Trim(), NumberStyles.Float,
                    CultureInfo.InvariantCulture, out double la) ? la : 0;
                // BigCTY lon is stored negated — negate when loading
                double lon = double.TryParse(cols[7].Trim(), NumberStyles.Float,
                    CultureInfo.InvariantCulture, out double lo) ? -lo : 0;

                var entity = new DxccEntity
                {
                    Name = entityName,
                    Dxcc = dxcc,
                    Continent = continent,
                    Itu = itu,
                    Cq = cq,
                    Lat = lat,
                    Lon = lon,
                };

                // Add main prefix
                AddPrefix(mainPrefix, entity);

                // Parse the prefixes_list field (space-separated)
                string prefixList = cols[9].Trim();
                foreach (string token in prefixList.Split(' ', StringSplitOptions.RemoveEmptyEntries))
                {
                    // Strip modifiers like /AM, /MM, etc. from the token itself, but we track them as-is
                    // Tokens starting with = are exact matches
                    AddPrefix(token, entity);
                }
            }

            // Sort prefix list by length descending for longest-match-first
            _prefixList.Sort((a, b) => b.Prefix.Length.CompareTo(a.Prefix.Length));
            _loaded = true;
            OnLogMessage?.Invoke($"[CTY] Loaded {_exactMatches.Count} exact + {_prefixList.Count} prefix entries");
            return true;
        }
        catch (Exception ex)
        {
            OnLogMessage?.Invoke($"[CTY] Load error: {ex.Message}");
            return false;
        }
    }

    private void AddPrefix(string token, DxccEntity entity)
    {
        string t = token.Trim();
        if (string.IsNullOrEmpty(t)) return;

        if (t.StartsWith('='))
        {
            // Exact match
            string exact = t[1..];
            _exactMatches[exact] = entity;
        }
        else
        {
            // Prefix match
            _prefixList.Add((t, entity));
        }
    }

    public DxccEntity? Lookup(string callSign)
    {
        if (!_loaded || string.IsNullOrEmpty(callSign)) return null;

        string call = callSign.ToUpperInvariant().Trim();

        // 1. Exact match on full callsign
        if (_exactMatches.TryGetValue(call, out var exact)) return exact;

        // 2. Longest prefix match on full callsign
        var match = LongestPrefixMatch(call);
        if (match != null) return match;

        // 3. Split on '/' and try each part
        string[] parts = call.Split('/');
        foreach (string part in parts)
        {
            if (string.IsNullOrEmpty(part)) continue;
            if (_exactMatches.TryGetValue(part, out var pe)) return pe;
            var pm = LongestPrefixMatch(part);
            if (pm != null) return pm;
        }

        return null;
    }

    private DxccEntity? LongestPrefixMatch(string call)
    {
        // Prefix list is sorted longest first
        foreach (var (prefix, entity) in _prefixList)
        {
            if (call.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
                return entity;
        }
        return null;
    }

    private static string[] SplitCsvLine(string line)
    {
        // Simple CSV split respecting quoted fields
        var result = new List<string>();
        bool inQuotes = false;
        var current = new System.Text.StringBuilder();

        foreach (char ch in line)
        {
            if (ch == '"')
            {
                inQuotes = !inQuotes;
            }
            else if (ch == ',' && !inQuotes)
            {
                result.Add(current.ToString());
                current.Clear();
            }
            else
            {
                current.Append(ch);
            }
        }
        result.Add(current.ToString());
        return result.ToArray();
    }
}
