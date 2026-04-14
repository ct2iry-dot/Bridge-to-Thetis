namespace BridgeToThetis.Core;

public static class ColorHelpers
{
    /// <summary>
    /// VB6 OLE_COLOR (signed int, BGR) → #RRGGBB string
    /// </summary>
    public static string Vb6ToHex(int v)
    {
        int r, g, b;
        if (v < 0)
        {
            // Commander XML fontcolor: 24-bit two's complement, RGB byte order (R=high byte)
            int u = v & 0xFFFFFF;
            r = (u >> 16) & 0xFF;
            g = (u >> 8) & 0xFF;
            b = u & 0xFF;
        }
        else
        {
            // Registry PaneColors: standard VB6 OLE_COLOR BGR (R=low byte)
            r = v & 0xFF;
            g = (v >> 8) & 0xFF;
            b = (v >> 16) & 0xFF;
        }
        return $"#{r:X2}{g:X2}{b:X2}";
    }

    /// <summary>
    /// #RRGGBB → ARGB uint (alpha=0xFF, opaque) for Thetis TCI
    /// </summary>
    public static uint HexToArgb(string hex)
    {
        hex = hex.TrimStart('#');
        if (hex.Length < 6) hex = hex.PadLeft(6, '0');
        uint r = Convert.ToUInt32(hex[0..2], 16);
        uint g = Convert.ToUInt32(hex[2..4], 16);
        uint b = Convert.ToUInt32(hex[4..6], 16);
        return (0xFFu << 24) | (r << 16) | (g << 8) | b;
    }

    /// <summary>
    /// Parses an OLE_COLOR integer (possibly negative) from a registry string value.
    /// </summary>
    public static string OleColorStringToHex(string? value, string defaultHex)
    {
        if (string.IsNullOrWhiteSpace(value)) return defaultHex;
        if (int.TryParse(value, out int v))
            return Vb6ToHex(v);
        return defaultHex;
    }
}
