using System.Data;
using System.Data.OleDb;
using System.Globalization;
using System.IO;

namespace BridgeToThetis.Services;

public class DXViewCache : IDisposable
{
    public event Action<string>? OnStatusChanged;
    public event Action<string>? OnLogMessage;

    public string Status { get; private set; } = "Not initialized";

    private HashSet<string> _loTwCalls = new(StringComparer.OrdinalIgnoreCase);
    private HashSet<string> _eQslCalls = new(StringComparer.OrdinalIgnoreCase);
    private readonly object _lock = new();

    private string? _loTwPath;
    private string? _eQslPath;
    private int _loTwMaxMonths;
    private int _eQslMaxMonths;

    private Timer? _refreshTimer;
    private bool _disposed;

    public void Initialize(string? loTwPath, string? eQslPath, int loTwMaxMonths, int eQslMaxMonths)
    {
        _loTwPath = loTwPath;
        _eQslPath = eQslPath;
        _loTwMaxMonths = loTwMaxMonths;
        _eQslMaxMonths = eQslMaxMonths;

        if (string.IsNullOrEmpty(loTwPath) && string.IsNullOrEmpty(eQslPath))
        {
            SetStatus("No DB found");
            return;
        }

        // Load immediately then schedule hourly refresh
        _ = Task.Run(RefreshAsync);
        _refreshTimer = new Timer(_ => _ = Task.Run(RefreshAsync), null,
            TimeSpan.FromHours(1), TimeSpan.FromHours(1));
    }

    private async Task RefreshAsync()
    {
        var newLoTw = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var newEQsl = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        string? errorMsg = null;

        try
        {
            if (!string.IsNullOrEmpty(_loTwPath) && File.Exists(_loTwPath))
                await LoadMdbAsync(_loTwPath, newLoTw, _loTwMaxMonths);
        }
        catch (Exception ex)
        {
            errorMsg = $"LoTW: {ex.Message}";
            OnLogMessage?.Invoke($"[DXV] LoTW load error: {ex.Message}");
        }

        try
        {
            if (!string.IsNullOrEmpty(_eQslPath) && File.Exists(_eQslPath))
                await LoadMdbAsync(_eQslPath, newEQsl, _eQslMaxMonths);
        }
        catch (Exception ex)
        {
            errorMsg = (errorMsg != null ? errorMsg + "; " : "") + $"eQSL: {ex.Message}";
            OnLogMessage?.Invoke($"[DXV] eQSL load error: {ex.Message}");
        }

        lock (_lock)
        {
            _loTwCalls = newLoTw;
            _eQslCalls = newEQsl;
        }

        if (errorMsg != null)
            SetStatus($"Error: {errorMsg}");
        else
            SetStatus($"OK — LoTW:{newLoTw.Count} eQSL:{newEQsl.Count}");

        OnLogMessage?.Invoke($"[DXV] Refreshed: {Status}");
    }

    private Task LoadMdbAsync(string path, HashSet<string> target, int maxMonths)
    {
        return Task.Run(() =>
        {
            string connStr = $"Provider=Microsoft.ACE.OLEDB.12.0;Data Source={path};Mode=Read;";
            using var conn = new OleDbConnection(connStr);
            conn.Open();

            // Get first table name from schema
            DataTable schema = conn.GetOleDbSchemaTable(OleDbSchemaGuid.Tables,
                new object?[] { null, null, null, "TABLE" })!;

            if (schema.Rows.Count == 0)
            {
                OnLogMessage?.Invoke($"[DXV] No tables in {path}");
                return;
            }

            string tableName = schema.Rows[0]["TABLE_NAME"].ToString()!;
            OnLogMessage?.Invoke($"[DXV] Using table '{tableName}' from {Path.GetFileName(path)}");

            DateTime cutoff = maxMonths > 0
                ? DateTime.UtcNow.AddMonths(-maxMonths)
                : DateTime.MinValue;

            string sql = $"SELECT CallSign, LastUpload FROM [{tableName}]";
            using var cmd = new OleDbCommand(sql, conn);
            using var reader = cmd.ExecuteReader();

            while (reader.Read())
            {
                string? call = reader["CallSign"]?.ToString()?.Trim();
                if (string.IsNullOrEmpty(call)) continue;

                if (maxMonths > 0)
                {
                    object rawDate = reader["LastUpload"];
                    if (!TryParseDate(rawDate, out DateTime uploaded)) continue;
                    if (uploaded < cutoff) continue;
                }

                target.Add(call);
            }
        });
    }

    private static bool TryParseDate(object raw, out DateTime result)
    {
        result = DateTime.MinValue;
        if (raw == null || raw == DBNull.Value) return false;

        if (raw is DateTime dt) { result = dt; return true; }

        string s = raw.ToString()!.Trim();
        if (DateTime.TryParse(s, out result)) return true;
        if (DateTime.TryParseExact(s, "M/d/yyyy", CultureInfo.InvariantCulture,
            DateTimeStyles.None, out result)) return true;
        if (DateTime.TryParseExact(s, "yyyy-MM-dd", CultureInfo.InvariantCulture,
            DateTimeStyles.None, out result)) return true;
        return false;
    }

    /// <summary>
    /// Returns background key: "bg_lotw_eqsl" | "bg_lotw" | "bg_eqsl" | "bg_normal"
    /// </summary>
    public string GetBgKey(string callSign)
    {
        bool inLoTw, inEQsl;
        lock (_lock)
        {
            inLoTw = _loTwCalls.Contains(callSign);
            inEQsl = _eQslCalls.Contains(callSign);
        }

        if (inLoTw && inEQsl) return "bg_lotw_eqsl";
        if (inLoTw) return "bg_lotw";
        if (inEQsl) return "bg_eqsl";
        return "bg_normal";
    }

    public bool HasLoTW(string callSign)
    {
        lock (_lock) return _loTwCalls.Contains(callSign);
    }

    public bool HasEQsl(string callSign)
    {
        lock (_lock) return _eQslCalls.Contains(callSign);
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
        _refreshTimer?.Dispose();
    }
}
