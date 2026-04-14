using System.Windows;
using BridgeToThetis.ViewModels;

namespace BridgeToThetis;

public partial class ConfigWindow : Window
{
    private readonly MainViewModel _vm;

    public ConfigWindow(MainViewModel vm)
    {
        InitializeComponent();
        _vm = vm;
        DataContext = vm;

        // Populate text fields (binding works for checkboxes but TextBox uses direct assign for safety)
        TxtTciHost.Text = vm.Config.TciHost;
        TxtTciPort.Text = vm.Config.TciPort.ToString();
        TxtCdrIp.Text = vm.Config.CdrSpotsIp;
        TxtCdrPort.Text = vm.Config.CdrSpotsPort.ToString();
        TxtFlexPort.Text = vm.Config.FlexPort.ToString();

        // BandModes status
        if (vm.BandModes.RangeCount > 0)
            TxtBandModesStatus.Text = $"Loaded — {vm.BandModes.RangeCount} ranges (date: {vm.BandModes.HeaderDate})";
        else
            TxtBandModesStatus.Text = "Not found — place BandModes.txt in SpotCollector data directory";
    }

    private void BtnReconnectTci_Click(object sender, RoutedEventArgs e)
    {
        ApplyNetworkConfig();
        _vm.ReconnectTciCommand.Execute(null);
    }

    private void BtnApplyCdr_Click(object sender, RoutedEventArgs e)
    {
        ApplyNetworkConfig();
        _vm.ApplyRestartCdrCommand.Execute(null);
    }

    private void BtnSave_Click(object sender, RoutedEventArgs e)
    {
        ApplyNetworkConfig();
        _vm.SaveConfigCommand.Execute(null);
        MessageBox.Show("Configuration saved.", "DXLab - Thetis Bridge", MessageBoxButton.OK, MessageBoxImage.Information);
    }

    private void BtnApplyFlex_Click(object sender, RoutedEventArgs e)
    {
        if (int.TryParse(TxtFlexPort.Text.Trim(), out int fp)) _vm.Config.FlexPort = fp;
        _vm.ApplyRestartFlexCommand.Execute(null);
    }

    private void ApplyNetworkConfig()
    {
        _vm.Config.TciHost = TxtTciHost.Text.Trim();
        if (int.TryParse(TxtTciPort.Text.Trim(), out int tp)) _vm.Config.TciPort = tp;
        _vm.Config.CdrSpotsIp = TxtCdrIp.Text.Trim();
        if (int.TryParse(TxtCdrPort.Text.Trim(), out int cp)) _vm.Config.CdrSpotsPort = cp;
    }
}
