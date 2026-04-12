using System.Collections.Concurrent;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Input;
using System.Windows.Threading;
using BridgeToThetis.Core;
using BridgeToThetis.Models;
using BridgeToThetis.Services;

namespace BridgeToThetis.ViewModels;

public class RelayCommand : ICommand
{
    private readonly Action _execute;
    private readonly Func<bool>? _canExecute;

    public RelayCommand(Action execute, Func<bool>? canExecute = null)
    {
        _execute = execute;
        _canExecute = canExecute;
    }

    public event EventHandler? CanExecuteChanged
    {
        add => CommandManager.RequerySuggested += value;
        remove => CommandManager.RequerySuggested -= value;
    }

    public bool CanExecute(object? parameter) => _canExecute?.Invoke() ?? true;
    public void Execute(object? parameter) => _execute();
}

public class MainViewModel : INotifyPropertyChanged
{
    private readonly Dispatcher _dispatcher;

    // Services
    public readonly TciClient TciClient;
    public readonly CommanderSpotsListener CdrListener;
    public readonly DXViewCache DxvCache;
    public readonly CtyDatabase CtyDb;
    public readonly BandModesMap BandModes;

    // Painted spots cache: callsign → spot (for repaint on reconnect)
    private readonly ConcurrentDictionary<string, SpotInfo> _paintedSpots = new(StringComparer.OrdinalIgnoreCase);

    // Rate tracking
    private readonly Queue<DateTime> _spotTimes = new();
    private int _totalSpotCount;

    // QTH for bearing calculation
    private double _myLat;
    private double _myLon;

    // SC Colors
    private readonly Dictionary<string, string> _bgColors = new()
    {
        ["bg_normal"]     = "#FFFFFF",
        ["bg_lotw"]       = "#FFFF00",
        ["bg_lotw_eqsl"]  = "#C0C0C0",
        ["bg_eqsl"]       = "#D7FFFF",
    };

    public event Action<string>? OnLogMessage;

    public MainViewModel(Dispatcher dispatcher)
    {
        _dispatcher = dispatcher;

        Config = AppConfig.Load();

        TciClient = new TciClient();
        CdrListener = new CommanderSpotsListener();
        DxvCache = new DXViewCache();
        CtyDb = new CtyDatabase();
        BandModes = new BandModesMap();

        SetupServiceCallbacks();
        InitializeFromRegistry();
        SetupCommands();

        // Start services
        TciClient.Connect(Config.TciHost, Config.TciPort);
        CdrListener.Start(Config.CdrSpotsIp, Config.CdrSpotsPort);
    }

    private void SetupServiceCallbacks()
    {
        TciClient.OnStatusChanged += s => _dispatcher.Invoke(() =>
        {
            TciStatus = s;
            TciStatusColor = s switch
            {
                "Ready" => "Green",
                "Connecting" or "Connected" => "#FFA500",
                _ => "Red"
            };
        });

        TciClient.OnLogMessage += msg => LogMessage(msg);

        TciClient.OnReady += async () =>
        {
            LogMessage("[TCI] OnReady fired — clearing and repainting spots");
            await TciClient.SendClearAsync();
            foreach (var spot in _paintedSpots.Values)
                await TciClient.SendSpotAsync(spot, Config.TciExtended);
        };

        TciClient.OnVfoFrequency += freq =>
            LogMessage($"[TCI] VFO: {freq} Hz");

        CdrListener.OnStatusChanged += s => _dispatcher.Invoke(() =>
        {
            CdrStatus = s;
            CdrStatusColor = s switch
            {
                var x when x.StartsWith("OK") => "Green",
                var x when x.StartsWith("Listening") => "#FFA500",
                var x when x.StartsWith("Error") => "Red",
                _ => "Gray"
            };
        });

        CdrListener.OnLogMessage += msg => LogMessage(msg);

        CdrListener.OnSpotAdd += async spot => await HandleSpotAddAsync(spot);
        CdrListener.OnSpotDelete += async (call, freq) => await HandleSpotDeleteAsync(call, freq);
        CdrListener.OnSpotClearAll += async () => await HandleClearAllAsync();

        DxvCache.OnStatusChanged += s => _dispatcher.Invoke(() =>
        {
            DxvStatus = s;
            DxvStatusColor = s switch
            {
                var x when x.StartsWith("OK") => "Green",
                var x when x.StartsWith("Error") => "Red",
                _ => "Gray"
            };
        });

        DxvCache.OnLogMessage += msg => LogMessage(msg);
        CtyDb.OnLogMessage += msg => LogMessage(msg);
        BandModes.OnLogMessage += msg => LogMessage(msg);
    }

    private void InitializeFromRegistry()
    {
        try
        {
            var qth = RegistryHelper.GetQth();
            _myLat = qth.Lat;
            _myLon = qth.Lon;
            LogMessage($"[REG] QTH: lat={_myLat:F4} lon={_myLon:F4} grid={qth.Grid}");

            var colors = RegistryHelper.GetSpotColors();
            _bgColors["bg_normal"]    = colors.BgNormal;
            _bgColors["bg_lotw"]      = colors.BgLoTW;
            _bgColors["bg_lotw_eqsl"] = colors.BgLoTWEQsl;
            _bgColors["bg_eqsl"]      = colors.BgEQsl;
            LogMessage($"[REG] SC colors loaded");

            string? loTwPath = RegistryHelper.GetLoTWDatabasePath();
            string? eQslPath = RegistryHelper.GetEQslDatabasePath();
            int loTwMonths  = RegistryHelper.GetLoTWUploadConstraint();
            int eQslMonths  = RegistryHelper.GetEQslUploadConstraint();
            DxvCache.Initialize(loTwPath, eQslPath, loTwMonths, eQslMonths);

            string? bmPath = RegistryHelper.GetBandModesTxtPath();
            BandModes.Load(bmPath);

            string? ctyPath = RegistryHelper.GetBigCtyCsvPath();
            CtyDb.Load(ctyPath);
        }
        catch (Exception ex)
        {
            LogMessage($"[REG] Init error: {ex.Message}");
        }
    }

    private void SetupCommands()
    {
        ReconnectTciCommand = new RelayCommand(() =>
        {
            LogMessage("[CMD] Reconnect TCI");
            _paintedSpots.Clear();
            TciClient.Connect(Config.TciHost, Config.TciPort);
        });

        ClearAllSpotsCommand = new RelayCommand(async () =>
        {
            LogMessage("[CMD] Clear all spots");
            _paintedSpots.Clear();
            await TciClient.SendClearAsync();
            _totalSpotCount = 0;
            UpdateStatusBar();
        });

        SendTestSpotCommand = new RelayCommand(async () =>
        {
            LogMessage("[CMD] Sending test spot");
            var test = new SpotInfo
            {
                CallSign = "CT2IRY",
                FreqHz = 14_195_000,
                Mode = "USB",
                Spotter = "TEST",
                Comment = "Bridge to Thetis test spot",
                FontColor = "#FFFFFF",
                BackColor = "#000080",
                Country = "Portugal",
                Heading = 0,
                UtcTime = DateTime.UtcNow,
            };
            await TciClient.SendSpotAsync(test, Config.TciExtended);
        });

        SaveConfigCommand = new RelayCommand(() =>
        {
            Config.Save();
            LogMessage("[CFG] Config saved");
        });

        ApplyRestartCdrCommand = new RelayCommand(() =>
        {
            LogMessage($"[CMD] Restarting CDR listener on {Config.CdrSpotsIp}:{Config.CdrSpotsPort}");
            CdrListener.Start(Config.CdrSpotsIp, Config.CdrSpotsPort);
        });
    }

    private async Task HandleSpotAddAsync(SpotInfo spot)
    {
        try
        {
            // Enrich with CTY data
            var entity = CtyDb.Lookup(spot.CallSign);
            if (entity != null)
            {
                spot.Country = entity.Name;
                if (_myLat != 0 || _myLon != 0)
                {
                    var (brg, dist) = BearingDistance.Calculate(_myLat, _myLon, entity.Lat, entity.Lon);
                    spot.Heading = brg;
                    spot.DistanceKm = dist;
                }
            }

            // Enrich with LoTW/eQSL data
            string bgKey = DxvCache.GetBgKey(spot.CallSign);
            spot.HasLoTW = DxvCache.HasLoTW(spot.CallSign);
            spot.HasEQsl = DxvCache.HasEQsl(spot.CallSign);
            if (_bgColors.TryGetValue(bgKey, out string? bgColor))
                spot.BackColor = bgColor;

            // Band filter
            if (Config.BandFilter && TciClient.IsReady)
            {
                // Optionally filter spots not on same band as VFO (simplified: just pass all for now)
            }

            // Map mode using BandModes if mode is empty
            if (string.IsNullOrWhiteSpace(spot.Mode) || spot.Mode == "USB")
            {
                string bmMode = BandModes.Lookup(spot.FreqHz);
                if (!string.IsNullOrEmpty(bmMode)) spot.Mode = bmMode;
            }

            // Cache and paint
            _paintedSpots[spot.CallSign] = spot;

            if (TciClient.IsReady)
                await TciClient.SendSpotAsync(spot, Config.TciExtended);

            // Rate tracking
            _spotTimes.Enqueue(DateTime.UtcNow);
            while (_spotTimes.Count > 0 && (DateTime.UtcNow - _spotTimes.Peek()).TotalMinutes > 1)
                _spotTimes.Dequeue();
            _totalSpotCount++;

            _dispatcher.Invoke(UpdateStatusBar);
            LogMessage($"[SPOT] Add: {spot.CallSign} {spot.FreqHz}Hz {spot.Mode} ({spot.Country}) hdg={spot.Heading}");
        }
        catch (Exception ex)
        {
            LogMessage($"[SPOT] HandleAdd error: {ex.Message}");
        }
    }

    private async Task HandleSpotDeleteAsync(string callSign, long freqHz)
    {
        try
        {
            _paintedSpots.TryRemove(callSign, out _);
            if (TciClient.IsReady)
                await TciClient.SendDeleteAsync(callSign);
            LogMessage($"[SPOT] Delete: {callSign}");
        }
        catch (Exception ex)
        {
            LogMessage($"[SPOT] HandleDelete error: {ex.Message}");
        }
    }

    private async Task HandleClearAllAsync()
    {
        try
        {
            _paintedSpots.Clear();
            _totalSpotCount = 0;
            if (TciClient.IsReady)
                await TciClient.SendClearAsync();
            _dispatcher.Invoke(UpdateStatusBar);
            LogMessage("[SPOT] ClearAll");
        }
        catch (Exception ex)
        {
            LogMessage($"[SPOT] HandleClear error: {ex.Message}");
        }
    }

    private void UpdateStatusBar()
    {
        double rate = _spotTimes.Count; // spots in last 60 seconds
        StatusBar = $"Spots: {_paintedSpots.Count}  Rate: {rate:F1}/min";
    }

    private void LogMessage(string msg)
    {
        OnLogMessage?.Invoke(msg);
    }

    // ---- Properties ----

    private string _tciStatus = "Disconnected";
    public string TciStatus
    {
        get => _tciStatus;
        set { _tciStatus = value; OnPropertyChanged(); }
    }

    private string _tciStatusColor = "Red";
    public string TciStatusColor
    {
        get => _tciStatusColor;
        set { _tciStatusColor = value; OnPropertyChanged(); }
    }

    private string _cdrStatus = "Stopped";
    public string CdrStatus
    {
        get => _cdrStatus;
        set { _cdrStatus = value; OnPropertyChanged(); }
    }

    private string _cdrStatusColor = "Gray";
    public string CdrStatusColor
    {
        get => _cdrStatusColor;
        set { _cdrStatusColor = value; OnPropertyChanged(); }
    }

    private string _dxvStatus = "Not initialized";
    public string DxvStatus
    {
        get => _dxvStatus;
        set { _dxvStatus = value; OnPropertyChanged(); }
    }

    private string _dxvStatusColor = "Gray";
    public string DxvStatusColor
    {
        get => _dxvStatusColor;
        set { _dxvStatusColor = value; OnPropertyChanged(); }
    }

    private string _statusBar = "Spots: 0  Rate: 0.0/min";
    public string StatusBar
    {
        get => _statusBar;
        set { _statusBar = value; OnPropertyChanged(); }
    }

    public AppConfig Config { get; }

    // Commands
    public ICommand ReconnectTciCommand { get; private set; } = null!;
    public ICommand ClearAllSpotsCommand { get; private set; } = null!;
    public ICommand SendTestSpotCommand { get; private set; } = null!;
    public ICommand SaveConfigCommand { get; private set; } = null!;
    public ICommand ApplyRestartCdrCommand { get; private set; } = null!;

    // Background colors exposed for UI
    public string BgNormalColor    => _bgColors["bg_normal"];
    public string BgLoTWColor      => _bgColors["bg_lotw"];
    public string BgLoTWEQslColor  => _bgColors["bg_lotw_eqsl"];
    public string BgEQslColor      => _bgColors["bg_eqsl"];

    public int PaintedSpotCount => _paintedSpots.Count;

    public event PropertyChangedEventHandler? PropertyChanged;
    protected void OnPropertyChanged([CallerMemberName] string? name = null)
        => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
