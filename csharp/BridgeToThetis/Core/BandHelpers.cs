namespace BridgeToThetis.Core;

public static class BandHelpers
{
    private static readonly (long Low, long High)[] BandEdges =
    {
        (1_800_000,   2_000_000),
        (3_500_000,   4_000_000),
        (5_330_500,   5_403_500),
        (7_000_000,   7_300_000),
        (10_100_000,  10_150_000),
        (14_000_000,  14_350_000),
        (18_068_000,  18_168_000),
        (21_000_000,  21_450_000),
        (24_890_000,  24_990_000),
        (28_000_000,  29_700_000),
        (50_000_000,  54_000_000),
        (144_000_000, 148_000_000),
    };

    private static readonly Dictionary<string, string> ModeMap = new(StringComparer.OrdinalIgnoreCase)
    {
        { "CW",      "CW"   },
        { "CW-R",    "CWL"  },
        { "CWR",     "CWL"  },
        { "USB",     "USB"  },
        { "LSB",     "LSB"  },
        { "AM",      "AM"   },
        { "FM",      "FM"   },
        { "DIGU",    "DIGU" },
        { "DIGL",    "DIGL" },
        { "RTTY",    "DIGU" },
        { "RTTYR",   "DIGL" },
        { "PSK31",   "DIGU" },
        { "PSK63",   "DIGU" },
        { "PSK",     "DIGU" },
        { "FT8",     "DIGU" },
        { "FT4",     "DIGU" },
        { "FT2",     "DIGU" },
        { "JT65",    "DIGU" },
        { "JT9",     "DIGU" },
        { "WSPR",    "DIGU" },
        { "JS8",     "DIGU" },
        { "Q65",     "DIGU" },
        { "MSK144",  "DIGU" },
        { "PKT",     "DIGU" },
        { "HELL",    "DIGU" },
    };

    private static readonly HashSet<string> ValidTciModes = new(StringComparer.OrdinalIgnoreCase)
    {
        "CW", "CWL", "CWU", "USB", "LSB", "AM", "FM", "DIGU", "DIGL",
        "RTTY", "DSB", "SAM", "DRM", "SPEC"
    };

    /// <summary>
    /// Maps a SpotCollector/Commander mode string to a valid Thetis DSPMode string.
    /// </summary>
    public static string MapMode(string mode, long freqHz)
    {
        if (string.IsNullOrWhiteSpace(mode)) return "USB";

        string upper = mode.Trim().ToUpperInvariant();

        if (upper == "CW")
            return CwSideband(freqHz);

        if (ModeMap.TryGetValue(upper, out string? mapped))
            return mapped;

        // If it's already a valid TCI mode, use it directly
        if (ValidTciModes.Contains(upper))
            return upper;

        return "USB";
    }

    /// <summary>
    /// Determines CW sideband: freq_hz <= 10,000,000 → "CWL", else → "CW"
    /// </summary>
    public static string CwSideband(long freqHz)
        => freqHz <= 10_000_000 ? "CWL" : "CW";

    /// <summary>
    /// Returns true if two frequencies are in the same amateur band.
    /// </summary>
    public static bool IsSameBand(long freqHz1, long freqHz2)
    {
        foreach (var (low, high) in BandEdges)
        {
            bool in1 = freqHz1 >= low && freqHz1 <= high;
            bool in2 = freqHz2 >= low && freqHz2 <= high;
            if (in1 && in2) return true;
            if (in1 || in2) return false;
        }
        return false;
    }

    /// <summary>
    /// Returns the band name for a given frequency in Hz, or null if not found.
    /// </summary>
    public static string? GetBandName(long freqHz)
    {
        string[] names = { "160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m", "12m", "10m", "6m", "2m" };
        for (int i = 0; i < BandEdges.Length; i++)
        {
            if (freqHz >= BandEdges[i].Low && freqHz <= BandEdges[i].High)
                return names[i];
        }
        return null;
    }
}
