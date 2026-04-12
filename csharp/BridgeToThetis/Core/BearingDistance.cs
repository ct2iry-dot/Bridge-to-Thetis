using static System.Math;

namespace BridgeToThetis.Core;

public static class BearingDistance
{
    public static (int bearing, int distanceKm) Calculate(
        double lat1, double lon1, double lat2, double lon2)
    {
        double R = 6371;
        double la1 = lat1 * PI / 180;
        double lo1 = lon1 * PI / 180;
        double la2 = lat2 * PI / 180;
        double lo2 = lon2 * PI / 180;
        double dlo = lo2 - lo1;

        double x = Sin(dlo) * Cos(la2);
        double y = Cos(la1) * Sin(la2) - Sin(la1) * Cos(la2) * Cos(dlo);
        double brg = (Atan2(x, y) * 180 / PI + 360) % 360;

        double a = Pow(Sin((la2 - la1) / 2), 2)
                 + Cos(la1) * Cos(la2) * Pow(Sin(dlo / 2), 2);
        double dist = R * 2 * Asin(Sqrt(a));

        return ((int)Round(brg), (int)Round(dist));
    }
}
