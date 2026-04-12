namespace BridgeToThetis.Models;

public class SpotInfo
{
    public string CallSign { get; set; } = string.Empty;
    public long FreqHz { get; set; }
    public string Mode { get; set; } = string.Empty;
    public string Spotter { get; set; } = string.Empty;
    public string Comment { get; set; } = string.Empty;
    public string FontColor { get; set; } = "#FFFFFF";
    public string BackColor { get; set; } = "#000000";
    public string Country { get; set; } = string.Empty;
    public int Heading { get; set; } = -1;
    public int DistanceKm { get; set; } = -1;
    public bool HasLoTW { get; set; }
    public bool HasEQsl { get; set; }
    public DateTime UtcTime { get; set; } = DateTime.UtcNow;
}
