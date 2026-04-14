using System.IO;
using System.Text.Json;

namespace BridgeToThetis.Models;

public class AppConfig
{
    public string TciHost { get; set; } = "127.0.0.1";
    public int TciPort { get; set; } = 50001;
    public bool TciExtended { get; set; } = true;
    public string CdrSpotsIp { get; set; } = "127.0.0.1";
    public int CdrSpotsPort { get; set; } = 13063;
    public bool BandFilter { get; set; } = false;
    public bool FlexEnabled { get; set; } = false;
    public int FlexPort { get; set; } = 4992;

    private static readonly string ConfigPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "BridgeToThetis",
        "config.json");

    public static AppConfig Load()
    {
        try
        {
            if (File.Exists(ConfigPath))
            {
                string json = File.ReadAllText(ConfigPath);
                return JsonSerializer.Deserialize<AppConfig>(json) ?? new AppConfig();
            }
        }
        catch
        {
            // Fall through to default
        }
        return new AppConfig();
    }

    public void Save()
    {
        try
        {
            string? dir = Path.GetDirectoryName(ConfigPath);
            if (dir != null) Directory.CreateDirectory(dir);
            string json = JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
            File.WriteAllText(ConfigPath, json);
        }
        catch
        {
            // Ignore save errors
        }
    }
}
