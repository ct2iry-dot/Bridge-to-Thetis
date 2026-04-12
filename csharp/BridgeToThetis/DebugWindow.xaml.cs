using System.Windows;
using System.Windows.Documents;
using System.Windows.Media;
using BridgeToThetis.ViewModels;

namespace BridgeToThetis;

public partial class DebugWindow : Window
{
    private readonly MainViewModel _vm;
    private const int MaxLines = 2000;
    private int _lineCount;

    // Color categories
    private static readonly SolidColorBrush BrushTci    = new(Color.FromRgb(0x00, 0xFF, 0xFF)); // cyan
    private static readonly SolidColorBrush BrushSpot   = new(Color.FromRgb(0x90, 0xEE, 0x90)); // light green
    private static readonly SolidColorBrush BrushDxLab  = new(Color.FromRgb(0xCC, 0xAA, 0xFF)); // light purple
    private static readonly SolidColorBrush BrushWarn   = new(Color.FromRgb(0xFF, 0xFF, 0x00)); // yellow
    private static readonly SolidColorBrush BrushError  = new(Color.FromRgb(0xFF, 0x80, 0x80)); // light red
    private static readonly SolidColorBrush BrushNormal = new(Color.FromRgb(0xCC, 0xCC, 0xCC)); // light gray

    public DebugWindow(MainViewModel vm)
    {
        InitializeComponent();
        _vm = vm;
        // Prevent FlowDocument from collapsing to column-width and rendering text vertically
        LogBox.Document.PageWidth = 10000;
    }

    public void AppendLog(string msg)
    {
        if (!Dispatcher.CheckAccess())
        {
            Dispatcher.BeginInvoke(() => AppendLog(msg));
            return;
        }

        if (_lineCount >= MaxLines)
        {
            // Trim first 200 lines
            var doc = LogBox.Document;
            var para = doc.Blocks.FirstBlock;
            int toRemove = 200;
            while (toRemove-- > 0 && para != null)
            {
                var next = para.NextBlock;
                doc.Blocks.Remove(para);
                para = next;
                _lineCount--;
            }
        }

        SolidColorBrush brush = ClassifyBrush(msg);

        string timestamp = DateTime.Now.ToString("HH:mm:ss.fff");
        string line = $"[{timestamp}] {msg}";

        var paragraph = new Paragraph(new Run(line))
        {
            Foreground = brush,
            Margin = new Thickness(0),
            FontFamily = new FontFamily("Consolas"),
            FontSize = 10,
        };

        LogBox.Document.Blocks.Add(paragraph);
        _lineCount++;

        if (ChkAutoScroll.IsChecked == true)
            LogBox.ScrollToEnd();
    }

    private static SolidColorBrush ClassifyBrush(string msg)
    {
        if (msg.Contains("[TCI]", StringComparison.OrdinalIgnoreCase)) return BrushTci;
        if (msg.Contains("[SPOT]", StringComparison.OrdinalIgnoreCase)) return BrushSpot;
        if (msg.Contains("[DXV]", StringComparison.OrdinalIgnoreCase) ||
            msg.Contains("[CTY]", StringComparison.OrdinalIgnoreCase) ||
            msg.Contains("[BMM]", StringComparison.OrdinalIgnoreCase) ||
            msg.Contains("[REG]", StringComparison.OrdinalIgnoreCase)) return BrushDxLab;
        if (msg.Contains("warn", StringComparison.OrdinalIgnoreCase) ||
            msg.Contains("[WARN]", StringComparison.OrdinalIgnoreCase)) return BrushWarn;
        if (msg.Contains("error", StringComparison.OrdinalIgnoreCase) ||
            msg.Contains("[ERR]", StringComparison.OrdinalIgnoreCase)) return BrushError;
        return BrushNormal;
    }

    private void BtnClear_Click(object sender, RoutedEventArgs e)
    {
        LogBox.Document.Blocks.Clear();
        _lineCount = 0;
    }

    private void BtnClearSpots_Click(object sender, RoutedEventArgs e)
        => _vm.ClearAllSpotsCommand.Execute(null);

    private void BtnTestSpot_Click(object sender, RoutedEventArgs e)
        => _vm.SendTestSpotCommand.Execute(null);

    private void BtnReconnect_Click(object sender, RoutedEventArgs e)
        => _vm.ReconnectTciCommand.Execute(null);
}
