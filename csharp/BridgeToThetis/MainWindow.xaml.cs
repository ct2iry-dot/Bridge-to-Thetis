using System.Windows;
using BridgeToThetis.ViewModels;

namespace BridgeToThetis;

public partial class MainWindow : Window
{
    private readonly MainViewModel _vm;
    private DebugWindow? _debugWindow;
    private ConfigWindow? _configWindow;

    public MainWindow()
    {
        InitializeComponent();

        _vm = new MainViewModel(Dispatcher);
        _vm.OnLogMessage += msg => _debugWindow?.AppendLog(msg);
        DataContext = _vm;

        string ver = System.Reflection.Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "7";
        Title = $"Bridge to Thetis  v{ver}  CT2IRY  —  DXLab Edition";
        FooterText.Text = $"Bridge to Thetis v{ver} — CT2IRY — DXLab Edition";
    }

    private void MenuConfiguration_Click(object sender, RoutedEventArgs e)
    {
        if (_configWindow == null || !_configWindow.IsVisible)
        {
            _configWindow = new ConfigWindow(_vm);
            _configWindow.Owner = this;
            _configWindow.Show();
        }
        else
        {
            _configWindow.Activate();
        }
    }

    private void MenuDebug_Click(object sender, RoutedEventArgs e)
    {
        if (_debugWindow == null || !_debugWindow.IsVisible)
        {
            _debugWindow = new DebugWindow(_vm);
            _debugWindow.Owner = this;
            _debugWindow.Show();
        }
        else
        {
            _debugWindow.Activate();
        }
    }

    private void MenuAbout_Click(object sender, RoutedEventArgs e)
    {
        MessageBox.Show(
            $"Bridge to Thetis\nVersion {System.Reflection.Assembly.GetExecutingAssembly().GetName().Version?.ToString(3)}\n\nDeveloped by Nuno Lopes — CT2IRY\n\n" +
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n" +
            "Special thanks:\n\n" +
            "Dave Bernstein AA6YQ\n" +
            "  DXLab Suite — WaterfallBandmap protocol,\n" +
            "  integration support and listing on\n" +
            "  the DXLab download page.\n\n" +
            "Richie Samphire MW0LGE\n" +
            "  Thetis SDR — TCI protocol guidance\n" +
            "  and spot painting implementation.\n\n" +
            "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n" +
            "https://github.com/ct2iry-dot/Bridge-to-Thetis",
            "About Bridge to Thetis",
            MessageBoxButton.OK,
            MessageBoxImage.Information);
    }

    protected override void OnClosed(EventArgs e)
    {
        base.OnClosed(e);
        _vm.TciClient.Dispose();
        _vm.CdrListener.Dispose();
        _vm.DxvCache.Dispose();
    }
}
