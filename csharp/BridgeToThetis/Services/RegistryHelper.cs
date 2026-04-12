using System.IO;
using BridgeToThetis.Core;
using Microsoft.Win32;

namespace BridgeToThetis.Services;

public class QthInfo
{
    public double Lat { get; set; }
    public double Lon { get; set; }
    public string Grid { get; set; } = string.Empty;
}

public class ScColors
{
    public string BgNormal { get; set; } = "#FFFFFF";
    public string BgLoTW { get; set; } = "#FFFF00";
    public string BgLoTWEQsl { get; set; } = "#C0C0C0";
    public string BgEQsl { get; set; } = "#D7FFFF";
}

public static class RegistryHelper
{
    private const string VbRoot = @"Software\VB and VBA Program Settings";

    private static string? ReadValue(string subKey, string valueName)
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey($@"{VbRoot}\{subKey}");
            return key?.GetValue(valueName)?.ToString();
        }
        catch
        {
            return null;
        }
    }

    public static ScColors GetSpotColors()
    {
        string sub = @"SpotCollector\Spot";
        return new ScColors
        {
            BgNormal    = ColorHelpers.OleColorStringToHex(ReadValue(sub, "PaneColor3"), "#FFFFFF"),
            BgLoTW      = ColorHelpers.OleColorStringToHex(ReadValue(sub, "PaneColor4"), "#FFFF00"),
            BgLoTWEQsl  = ColorHelpers.OleColorStringToHex(ReadValue(sub, "PaneColor8"), "#C0C0C0"),
            BgEQsl      = ColorHelpers.OleColorStringToHex(ReadValue(sub, "PaneColor9"), "#D7FFFF"),
        };
    }

    private static double ParseDms(string app, string prefix)
    {
        double deg = double.TryParse(ReadValue(app, $"{prefix}Deg"), out double d) ? d : 0;
        double min = double.TryParse(ReadValue(app, $"{prefix}Min"), out double m) ? m : 0;
        double sec = double.TryParse(ReadValue(app, $"{prefix}Sec"), out double s) ? s : 0;
        return deg + min / 60.0 + sec / 3600.0;
    }

    public static QthInfo GetQth()
    {
        // Try SpotCollector, DXView, DXKeeper in order
        string[] apps = { @"SpotCollector\QTH", @"DXView\QTH", @"DXKeeper\QTH" };

        foreach (string app in apps)
        {
            string? latSign = ReadValue(app, "LatSign");
            if (latSign == null) continue;

            double latSignVal = double.TryParse(latSign, out double ls) ? ls : 0;
            double lonSign = double.TryParse(ReadValue(app, "LonSign"), out double lons) ? lons : 0;

            double latMag = ParseDms(app, "Lat");
            double lonMag = ParseDms(app, "Lon");

            // sign=-1 → North → positive lat; sign=1 → South → negative lat
            double lat = -latSignVal * latMag;
            // sign=-1 → West → negative lon; sign=1 → East → positive lon
            double lon = lonSign * lonMag;

            string grid = ReadValue(@"WinWarbler\Position", "MyGrid") ?? string.Empty;

            return new QthInfo { Lat = lat, Lon = lon, Grid = grid };
        }

        return new QthInfo();
    }

    public static string? GetLoTWDatabasePath()
        => ReadValue(@"DXView\LotWDatabase", "lotWDatabasePathname");

    public static string? GetEQslDatabasePath()
        => ReadValue(@"DXView\eQSLDatabase", "eQSLDatabasePathname");

    public static int GetLoTWUploadConstraint()
    {
        string? v = ReadValue(@"SpotCollector\Spot", "LotWUploadConstraint");
        return int.TryParse(v, out int m) ? m : 0;
    }

    public static int GetEQslUploadConstraint()
    {
        string? v = ReadValue(@"SpotCollector\Spot", "eQSLUploadConstraint");
        return int.TryParse(v, out int m) ? m : 0;
    }

    public static string? GetBandModesTxtPath()
    {
        // Try DataDirectory first, then ProgramDirectory
        string? dir = ReadValue(@"SpotCollector\General", "DataDirectory")
                   ?? ReadValue(@"SpotCollector\General", "ProgramDirectory");

        if (!string.IsNullOrWhiteSpace(dir))
        {
            string p = Path.Combine(dir, "BandModes.txt");
            if (File.Exists(p)) return p;
        }

        // Fallback paths
        string[] fallbacks =
        {
            @"D:\DXLab\SpotCollector\BandModes.txt",
            @"C:\DXLab\SpotCollector\BandModes.txt",
        };
        foreach (string f in fallbacks)
            if (File.Exists(f)) return f;

        return null;
    }

    public static string? GetBigCtyCsvPath()
    {
        string? dbFolder = ReadValue(@"DXView\General", "DatabaseFolder");
        if (!string.IsNullOrWhiteSpace(dbFolder))
        {
            string p = Path.Combine(dbFolder, "BigCTY.csv");
            if (File.Exists(p)) return p;
        }

        string[] fallbacks =
        {
            @"D:\DXLab\DXView\Databases\BigCTY.csv",
            @"C:\DXLab\DXView\Databases\BigCTY.csv",
        };
        foreach (string f in fallbacks)
            if (File.Exists(f)) return f;

        return null;
    }
}
