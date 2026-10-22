// Lockstep TCP bridge for REEFSCAPE (FRC 2025): Python drives the blue robot (default Madtown 1323).
//
// Started only in bot instances: Host adds it when the environment has MOSIMRL_REEF=1 (the Steam stub drops
// command-line args, but the environment reaches the real player). A Steam-launched human session never has it.
//   MOSIMRL_PORT   listen port (default 47600)
//   MOSIMRL_OWNER  pid; the instance quits ~2 s after that process is gone (orphan guard)
//   MOSIMRL_TEAM   robot team number to select in memory (default 1323); MatchSettings.json is never written
//   MOSIMRL_LITE   1 = disable every camera and canvas (no rendering work; there is no -nographics through the stub)
//
// Protocol: newline-delimited JSON on 127.0.0.1:<port>.
//   {"cmd":"hello"} / {"cmd":"config","stepsPerFrame":2,"decisionSteps":10,"timeScale":1}
//   {"cmd":"reset"}  -> first state of a fresh match (selects REEFSCAPE + the team's robot and loads the scene if needed)
//   {"cmd":"field"}  -> static field geometry read from the live scene (align nodes, branch triggers, zones, ...)
//   {"cmd":"act","v":[vx,vz],"w":deg_per_s,"hold":[13 x 0/1],"tap":[button indices],"n":steps}
//                    -> state after n (default decisionSteps) physics steps. v is world m/s (x, z), w is the
//                       DriveController omega (deg/s). Taps are pressed for 3 rendered frames, then released.
//   {"cmd":"release"}

using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using Games.Reefscape.Components;
using Games.Reefscape.FieldScripts;
using Games.Reefscape.GameManagement;
using Games.Reefscape.GamePieceSystem;
using Games.Reefscape.Robots;
using Games.Reefscape.Scoring;
using Games.Reefscape.Scoring.Scorers;
using GameSystems.Cameras;
using GameSystems.Management;
using GameSystems.Settings;
using MoSimCore.BaseClasses.GameManagement;
using MoSimCore.Enums;
using MoSimCore.GameSelection;
using RobotFramework;
using RobotFramework.Controllers.Drivetrain;
using UnityEngine;
using MoSimSceneManager = MoSimCore.SceneTransitions.SceneManager;

namespace MoSimRL
{
    [Serializable]
    public class ReefMsg
    {
        public string cmd = "";
        public float[] v;
        public float w;
        public int[] hold;
        public int[] tap;
        public int n = -1;
        public int free = 0;           // 1: the bridge does not call OverrideInput (lets the game's own AutoAlign drive)
        public float timeScale = -1f;
        public int decisionSteps = -1;
        public int stepsPerFrame = -1;
    }

    public class ReefBridge : MonoBehaviour
    {
        public static bool Requested => Environment.GetEnvironmentVariable("MOSIMRL_REEF") == "1";

        private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;
        private const BindingFlags NP = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags ANY = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;

        private TcpListener _listener;
        private TcpClient _client;
        private System.Net.Sockets.NetworkStream _stream;
        private readonly List<byte> _rx = new List<byte>();
        private readonly byte[] _buf = new byte[65536];

        private int _decisionSteps = 10;
        private int _stepsThisDecision = 10;
        private bool _lite;
        private int _team = 1323;
        private int _port = 47600;

        private bool _controlling, _awaitingAction, _resetting;
        private int _stepCounter, _episode;
        private float _episodeStartTime = -100f;
        private float _endSince = -1f;

        private GameObject _robot;
        private ReefscapeAutoAlign _al;
        private GameObject _alOwner;
        private static FieldInfo _alHas;
        private static PropertyInfo _alActive;
        private Rigidbody _rb;
        private DriveController _drive;
        private ReefscapeRobotBase _base;
        private ReefscapeRobotGamePieceController _rgpc;
        private ClimbScorer _climb;
        private ReefButtons _buttons;
        private readonly bool[] _hold = new bool[ReefButtons.Count];
        private readonly bool[] _tap = new bool[ReefButtons.Count];
        private int _tapFrames;
        private Vector2 _cmdV;
        private float _cmdW;
        private bool _drive_on;
        private int _tapsSent;

        // Madtown private state (reflection; null for other robots)
        private FieldInfo _fElevator, _fStation, _fFloor, _fHand, _fBuf;

        private ReefscapeScoreHandler _score;
        private Reef _blueReef, _redReef;
        private Component[] _troughs = new Component[0];
        private FieldInfo _fTroughObjs;

        // ------------------------------------------------------------ orphan guard
        [DllImport("/usr/lib/libSystem.B.dylib", SetLastError = true)]
        private static extern int kill(int pid, int sig);
        private const int ESRCH = 3;
        private int _ownerPid = -1;
        private float _lastOwnerCheck, _quitAt = -1f, _lastMute = -10f;

        private static int EnvInt(string name, int dflt)
        {
            var s = Environment.GetEnvironmentVariable(name);
            return int.TryParse(s, NumberStyles.Integer, Inv, out int v) ? v : dflt;
        }

        private IEnumerator PinWindow()
        {
            yield return new WaitForSecondsRealtime(1.0f);
            WindowPin.ToCorner(Screen.width, Screen.height);
            yield return new WaitForSecondsRealtime(0.5f);
            Log.Write($"window: floated {WindowPin.Float()} window(s) size={Screen.width}x{Screen.height}");
        }

        private void Awake()
        {
            _port = EnvInt("MOSIMRL_PORT", 47600);
            _ownerPid = EnvInt("MOSIMRL_OWNER", -1);
            _team = EnvInt("MOSIMRL_TEAM", 1323);
            _lite = EnvInt("MOSIMRL_LITE", 1) == 1;
            AudioListener.volume = 0f;
            AudioListener.pause = true;
            // MoSim's quality levels are "High Fidelity/Balanced/Performant": the LAST index is the cheapest (bots)
            if (_lite) { QualitySettings.SetQualityLevel(QualitySettings.names.Length - 1, true); Screen.SetResolution(320, 200, FullScreenMode.Windowed); }
            else
            {
                // demo matches: a rendered window of the requested size (MOSIMRL_RES=WxH); MOSIMRL_FULLSCREEN=1 makes it
                // a borderless full-screen window (e.g. a demo at the display's native size)
                var res = Environment.GetEnvironmentVariable("MOSIMRL_RES");
                var parts = string.IsNullOrEmpty(res) ? null : res.Split('x');
                var mode = Environment.GetEnvironmentVariable("MOSIMRL_FULLSCREEN") == "1" ? FullScreenMode.FullScreenWindow : FullScreenMode.Windowed;
                if (parts != null && parts.Length == 2 && int.TryParse(parts[0], out int rw) && int.TryParse(parts[1], out int rh))
                    Screen.SetResolution(rw, rh, mode);
                // demo: highest quality level and a third-person camera, whatever the user's own settings
                // say (the settings file itself is untouched; the guard restores it after the run anyway)
                ApplyDemoQuality();
            }
            // Bot instances only: keep processing the virtual buttons when the window loses focus. With the default
            // (ResetAndDisableNonBackgroundDevices) a click on the bot window and back disabled the device and every
            // button press was dropped while driving still worked (drill-cap1, 10-18).
            try
            {
                Application.runInBackground = true;
                UnityEngine.InputSystem.InputSystem.settings.backgroundBehavior =
                    UnityEngine.InputSystem.InputSettings.BackgroundBehavior.IgnoreFocus;
                Log.Write("input: backgroundBehavior=IgnoreFocus");
            }
            catch (Exception e) { Log.Write("input: IgnoreFocus failed: " + e.Message); }
            Log.Write($"process activity (no App Nap): {WindowPin.NoNap()}");
            if (EnvInt("MOSIMRL_FLOAT", 0) == 1) StartCoroutine(PinWindow());
            try
            {
                _listener = new TcpListener(IPAddress.Loopback, _port);
                _listener.Start();
                Log.Write($"reef bridge listening on 127.0.0.1:{_port} pid={System.Diagnostics.Process.GetCurrentProcess().Id} owner={_ownerPid} team={_team} lite={_lite}");
            }
            catch (Exception e) { Log.Write($"reef bridge listen failed on {_port}: {e.Message}"); }
            try
            {
                var dir = System.IO.Path.Combine(Entry.RunDir, "reef");
                System.IO.Directory.CreateDirectory(dir);
                System.IO.File.WriteAllText(System.IO.Path.Combine(dir, $"inst-{_port}.json"),
                    "{\"port\":" + _port + ",\"pid\":" + System.Diagnostics.Process.GetCurrentProcess().Id + ",\"owner\":" + _ownerPid + "}");
            }
            catch { }
            // Loading the match scene creates a fresh MatchManager that re-reads MatchSettings.json from disk, and the
            // spawn controller reads it one frame later: re-apply the selection in sceneLoaded, before that read, so
            // the user's robot never spawns and PlayerSettings.SaveSpawnpoint never rewrites the file.
            UnityEngine.SceneManagement.SceneManager.sceneLoaded += OnSceneLoadedPin;
        }

        private void ApplyDemoQuality()
        {
            if (_lite) return;
            try
            {
                // MoSim's levels are "High Fidelity/Balanced/Performant": index 0 is the highest.
                // MOSIMRL_QUALITY overrides it (e.g. Performant on a slower machine).
                var want = Environment.GetEnvironmentVariable("MOSIMRL_QUALITY");
                if (string.IsNullOrEmpty(want)) want = "High Fidelity";
                int top = Array.IndexOf(QualitySettings.names, want);
                if (top < 0) top = 0;
                QualitySettings.SetQualityLevel(top, true);
                Log.Write($"demo quality: {QualitySettings.names[top]} ({top} of {string.Join("/", QualitySettings.names)})");
            }
            catch (Exception e) { Log.Write("demo quality failed: " + e.Message); }
        }

        // demo camera: MOSIMRL_CAMERA = ThirdPerson (default) | FirstPerson | DriverStationDynamic | DriverStationStatic
        private void ApplyDemoCamera(PlayerSettings ps)
        {
            if (_lite || ps == null) return;
            var name = Environment.GetEnvironmentVariable("MOSIMRL_CAMERA");
            if (string.IsNullOrEmpty(name)) name = "ThirdPerson";
            try { ps.cameraMode = (CameraMode)Enum.Parse(typeof(CameraMode), name, true); }
            catch (Exception e) { Log.Write($"demo camera '{name}' unknown: {e.Message}"); }
        }

        private void OnSceneLoadedPin(UnityEngine.SceneManagement.Scene scene, UnityEngine.SceneManagement.LoadSceneMode mode)
        {
            try { if (_robotMeta != null && BaseGameManager.Instance is ReefscapeGameManager) { PinRobotOnSpawnController(); ApplyDemoQuality(); } }
            catch (Exception e) { Log.Write("sceneLoaded pin failed: " + e.Message); }
        }

        private void OnDestroy()
        {
            UnityEngine.SceneManagement.SceneManager.sceneLoaded -= OnSceneLoadedPin;
            ReleaseControl();
            try { _client?.Close(); } catch { }
            try { _listener?.Stop(); } catch { }
        }

        private void CheckOwner()
        {
            if (_quitAt >= 0f)
            {
                if (Time.realtimeSinceStartup - _quitAt > 10f) kill(System.Diagnostics.Process.GetCurrentProcess().Id, 9);
                return;
            }
            if (_ownerPid <= 0 || Time.realtimeSinceStartup - _lastOwnerCheck < 2f) return;
            _lastOwnerCheck = Time.realtimeSinceStartup;
            if (kill(_ownerPid, 0) == 0 || Marshal.GetLastWin32Error() != ESRCH) return;
            Log.Write($"owner process {_ownerPid} is gone; quitting");
            _quitAt = Time.realtimeSinceStartup;
            ReleaseControl();
            Application.Quit();
        }

        // ------------------------------------------------------------ socket plumbing
        private void Send(string json)
        {
            if (_stream == null) return;
            var bytes = Encoding.UTF8.GetBytes(json + "\n");
            try { _stream.Write(bytes, 0, bytes.Length); }
            catch (Exception e) { Log.Write("send failed: " + e.Message); DropClient(); }
        }

        private void DropClient()
        {
            ReleaseControl();
            try { _client?.Close(); } catch { }
            _client = null; _stream = null; _rx.Clear();
        }

        private string TryPopLine()
        {
            int nl = _rx.IndexOf((byte)'\n');
            if (nl < 0) return null;
            string line = Encoding.UTF8.GetString(_rx.GetRange(0, nl).ToArray());
            _rx.RemoveRange(0, nl + 1);
            return line;
        }

        private string ReadLineNonBlocking()
        {
            if (_stream == null) return null;
            try
            {
                while (_client.Available > 0)
                {
                    int n = _stream.Read(_buf, 0, Math.Min(_buf.Length, _client.Available));
                    if (n <= 0) break;
                    for (int i = 0; i < n; i++) _rx.Add(_buf[i]);
                }
            }
            catch (Exception e) { Log.Write("read failed: " + e.Message); DropClient(); return null; }
            return TryPopLine();
        }

        private string ReadLineBlocking(int timeoutMs)
        {
            string line = TryPopLine();
            if (line != null || _stream == null) return line;
            _stream.ReadTimeout = timeoutMs;
            try
            {
                while (true)
                {
                    int n = _stream.Read(_buf, 0, _buf.Length);
                    if (n <= 0) { DropClient(); return null; }
                    for (int i = 0; i < n; i++) _rx.Add(_buf[i]);
                    line = TryPopLine();
                    if (line != null) return line;
                }
            }
            catch (Exception e) { Log.Write("blocking read ended: " + e.Message); DropClient(); return null; }
        }

        // ------------------------------------------------------------ main thread
        // The game pauses itself (GameUI.PauseMenu: pauseOnFocusLost -> Time.timeScale = 0) when its window loses
        // focus; the lockstep bridge then waits forever (drill-cap2/cap3, 10-18, while the user watched a full-screen
        // video). Bot instances only: switch that off on every PauseMenu and resume if it already paused.
        private static Type _pauseType;
        private static FieldInfo _pauseOnFocus;
        private static PropertyInfo _pauseInst, _pauseIsPaused;
        private static MethodInfo _pauseResume;
        private UnityEngine.Object _pauseSeen;

        private void KeepUnpaused()
        {
            try
            {
                if (_pauseType == null)
                {
                    foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
                    {
                        _pauseType = a.GetType("GameUI.PauseMenu", false);
                        if (_pauseType != null) break;
                    }
                    if (_pauseType == null) return;
                    _pauseOnFocus = _pauseType.GetField("pauseOnFocusLost", BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public);
                    _pauseInst = _pauseType.GetProperty("Instance", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                    _pauseIsPaused = _pauseType.GetProperty("IsPaused", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                    _pauseResume = _pauseType.GetMethod("Resume", BindingFlags.Instance | BindingFlags.Public);
                }
                var inst = _pauseInst?.GetValue(null) as UnityEngine.Object;
                if (inst == null) return;
                if (!ReferenceEquals(inst, _pauseSeen))
                {
                    _pauseOnFocus?.SetValue(inst, false);
                    _pauseSeen = inst;
                    Log.Write("pause menu: pauseOnFocusLost=false");
                }
                if (_pauseIsPaused != null && (bool)_pauseIsPaused.GetValue(null))
                {
                    _pauseResume?.Invoke(inst, null);
                    Log.Write("pause menu: was paused (focus lost?) -> resumed");
                }
            }
            catch (Exception e) { Log.Write("pause guard failed: " + e.Message); _pauseType = typeof(void); }
        }

        private void Update()
        {
            if (_pauseType != typeof(void)) KeepUnpaused();
            CheckOwner();
            if (_listener != null && _listener.Pending())
            {
                if (_client != null) { Log.Write("new client connecting; dropping previous connection"); DropClient(); }
                _client = _listener.AcceptTcpClient();
                _client.NoDelay = true;
                _stream = _client.GetStream();
                Log.Write("reef bridge client connected");
            }
            AudioListener.volume = 0f;
            AudioListener.pause = true;
            if (Time.unscaledTime - _lastMute > 1f)
            {
                _lastMute = Time.unscaledTime;
                foreach (var src in FindObjectsByType<AudioSource>(FindObjectsInactive.Include, FindObjectsSortMode.None)) src.mute = true;
                if (_lite)
                {
                    foreach (var cam in Camera.allCameras) cam.enabled = false;
                    foreach (var c in FindObjectsByType<Canvas>(FindObjectsInactive.Exclude, FindObjectsSortMode.None)) c.enabled = false;
                }
            }
            if (_lite) foreach (var cam in Camera.allCameras) cam.enabled = false;

            // tap release: taps stay down for 3 rendered frames, then only the held buttons remain
            if (_tapFrames > 0 && --_tapFrames == 0 && _buttons != null)
            {
                for (int i = 0; i < _tap.Length; i++) _tap[i] = false;
                _buttons.SetAll(_hold);
            }

            if (_controlling || _resetting) return;
            string line = ReadLineNonBlocking();
            if (line != null) Handle(line);
        }

        private void Handle(string line)
        {
            ReefMsg m;
            try { m = JsonUtility.FromJson<ReefMsg>(line); }
            catch (Exception e) { Send(Err("bad json: " + e.Message)); return; }
            switch (m.cmd)
            {
                case "hello": Send(Info()); break;
                case "config": ApplyConfig(m); Send(Info()); break;
                case "reset": StartCoroutine(ResetEpisode()); break;
                case "field": Send(FieldJson()); break;
                case "release": ReleaseControl(); Send("{\"ok\":true}"); break;
                case "act": Send(Err("no episode running; send reset first")); break;
                default: Send(Err("unknown cmd " + m.cmd)); break;
            }
        }

        private void ApplyConfig(ReefMsg m)
        {
            if (m.timeScale > 0f) Time.timeScale = m.timeScale;
            if (m.decisionSteps > 0) _decisionSteps = m.decisionSteps;
            if (m.stepsPerFrame > 0) { Time.captureDeltaTime = Time.fixedDeltaTime * m.stepsPerFrame; QualitySettings.vSyncCount = 0; Application.targetFrameRate = -1; }
            if (m.stepsPerFrame == 0) { Time.captureDeltaTime = 0f; }
            Log.Write($"reef config timeScale={Time.timeScale} decisionSteps={_decisionSteps} captureDeltaTime={Time.captureDeltaTime}");
        }

        private string Info()
        {
            return "{\"ok\":true,\"version\":\"" + Application.version + "\",\"port\":" + _port +
                   ",\"pid\":" + System.Diagnostics.Process.GetCurrentProcess().Id +
                   ",\"fixedDeltaTime\":" + F(Time.fixedDeltaTime) + ",\"timeScale\":" + F(Time.timeScale) +
                   ",\"captureDeltaTime\":" + Time.captureDeltaTime.ToString("0.#######", Inv) +
                   ",\"decisionSteps\":" + _decisionSteps + ",\"lite\":" + (_lite ? "true" : "false") +
                   ",\"inMatch\":" + (BaseGameManager.Instance is ReefscapeGameManager ? "true" : "false") + "}";
        }

        private static string Err(string msg) => "{\"ok\":false,\"error\":\"" + msg.Replace("\"", "'").Replace("\n", " ") + "\"}";

        // ------------------------------------------------------------ game + robot selection (in memory only)
        private bool SelectReefscapeAndRobot(out string why)
        {
            why = null;
            var sgm = SelectedGameManager.Instance;
            if (sgm == null || sgm.GameOptions == null) { why = "no SelectedGameManager"; return false; }
            var game = sgm.GameOptions.FirstOrDefault(g => g != null && g.GameYear == 2025);
            if (game == null) { why = "no 2025 game option"; return false; }
            sgm.SelectedGame = game;
            var rl = RobotLoader.Instance;
            if (rl == null || !rl.AllRobotsLoaded) { why = "robots not loaded"; return false; }
            var robot = rl.LoadedRobots.FirstOrDefault(r => r != null && r.TeamNumber == _team && r.GameYear == 2025);
            if (robot == null) { why = $"team {_team} 2025 robot not found"; return false; }
            var ps = MatchManager.Instance?.GetOrCreatePlayerSettings(0);
            if (ps == null) { why = "no player settings"; return false; }
            ps.robotGuid = robot.GetGuid();
            ps.useAlternateRobot = false;
            ps.alliance = Alliance.Blue;
            ApplyDemoCamera(ps);
            var mpName = rl.LoadedModpacks.FirstOrDefault(mp => mp != null && mp.name != null && mp.name.IndexOf("Reefscape", StringComparison.OrdinalIgnoreCase) >= 0)?.name;
            if (!string.IsNullOrEmpty(mpName)) ps.modpackName = mpName;
            if (MatchManager.Instance.MatchSettings != null) MatchManager.Instance.MatchSettings.gameMode = GameMode.Singleplayer;
            try { MatchRuntimeService.Instance?.CurrentMatchData?.SetSelectedRobot(Alliance.Blue, robot); } catch (Exception e) { Log.Write("SetSelectedRobot: " + e.Message); }
            _robotMeta = robot;
            Log.Write($"selected game '{game.name}' ({game.GameYear}) robot team={robot.TeamNumber} '{robot.TeamName}' year={robot.GameYear} guid={ps.robotGuid} modpack='{ps.modpackName}' " + PsDiag());
            return true;
        }

        private RobotMetadataSO _robotMeta;

        private static string PsDiag()
        {
            var mm = MatchManager.Instance;
            var ps = mm?.GetOrCreatePlayerSettings(0);
            return $"[mm={(mm != null ? mm.GetInstanceID() : 0)} ms={(mm?.MatchSettings != null ? System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(mm.MatchSettings) : 0)} " +
                   $"ps={(ps != null ? System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(ps) : 0)} guid={ps?.robotGuid} alt={ps?.useAlternateRobot} " +
                   $"mode={mm?.MatchSettings?.gameMode} n={mm?.MatchSettings?.playerSettings?.Count}]";
        }

        // Belt and braces after the scene is up: re-apply the selection and pin the spawn controller's override.
        private void PinRobotOnSpawnController()
        {
            if (_robotMeta == null) return;
            var ps = MatchManager.Instance?.GetOrCreatePlayerSettings(0);
            // perspectiveAutoAlign=false: AutoAlign picks the literal Left/Right node instead of needing a camera
            if (ps != null) { ps.robotGuid = _robotMeta.GetGuid(); ps.useAlternateRobot = false; ps.alliance = Alliance.Blue; ps.perspectiveAutoAlign = false; ApplyDemoCamera(ps); }
            var rsc = FindFirstObjectByType<RobotSpawnController>();
            var f = typeof(RobotSpawnController).GetField("robotOverride", NP);
            var gy = typeof(RobotSpawnController).GetField("gameYear", NP);
            if (rsc != null && f != null) f.SetValue(rsc, _robotMeta);
            Log.Write($"pinned spawn override: rsc={(rsc != null)} gameYear={(rsc != null && gy != null ? gy.GetValue(rsc) : "?")} " + PsDiag());
        }

        private IEnumerator ResetEpisode()
        {
            _resetting = true;
            ReleaseControl();

            if (!(BaseGameManager.Instance is ReefscapeGameManager))
            {
                float t0 = Time.realtimeSinceStartup;
                string why = null;
                while (Time.realtimeSinceStartup - t0 < 120f && !SelectReefscapeAndRobot(out why)) yield return null;
                if (why != null && !(SelectedGameManager.Instance?.SelectedGame?.GameYear == 2025))
                { _resetting = false; Send(Err("selection failed: " + why)); yield break; }
                if (MoSimSceneManager.Instance == null) { _resetting = false; Send(Err("no scene manager")); yield break; }
                var game = SelectedGameManager.Instance.SelectedGame;
                Log.Write($"reef reset: loading scene '{game.GameScene?.Name}'");
                MoSimSceneManager.Instance.LoadScene(game.GameScene, "CrossFade");
                t0 = Time.realtimeSinceStartup;
                while ((!(BaseGameManager.Instance is ReefscapeGameManager) || FindBlueRobot() == null) && Time.realtimeSinceStartup - t0 < 120f)
                    yield return null;
                if (!(BaseGameManager.Instance is ReefscapeGameManager)) { _resetting = false; Send(Err("reefscape scene never loaded")); yield break; }
                yield return new WaitForSecondsRealtime(1f);
            }

            var gm = BaseGameManager.Instance;
            while (gm.IsResetting) yield return null;
            // two ResetMatch calls in quick succession re-instantiate the field over the previous one and the reef
            // algae fly off: leave >= 1.5 s (game time) after the previous episode's start
            while (Time.time - _episodeStartTime < 1.5f) yield return null;
            PinRobotOnSpawnController();
            gm.StartCoroutine(gm.ResetMatch());
            yield return null;
            while (gm.IsResetting) yield return null;
            float w0 = Time.realtimeSinceStartup;
            while (Time.realtimeSinceStartup - w0 < 60f &&
                   (FindBlueRobot() == null || gm.GameState != GameState.Auto || gm.RobotState != RobotState.Enabled))
                yield return null;

            _robot = FindBlueRobot();
            if (_robot == null) { _resetting = false; Send(Err("no blue robot after reset")); yield break; }
            _rb = _robot.GetComponent<Rigidbody>();
            _drive = _robot.GetComponent<DriveController>();
            _base = _robot.GetComponent<ReefscapeRobotBase>();
            _rgpc = _robot.GetComponent<ReefscapeRobotGamePieceController>();
            _climb = _robot.GetComponentInChildren<ClimbScorer>(true);
            var rt = _base != null ? _base.GetType() : null;
            _fElevator = rt?.GetField("elevator", NP); _fStation = rt?.GetField("stationCoral", NP);
            _fFloor = rt?.GetField("floorCoral", NP); _fHand = rt?.GetField("handoffing", NP); _fBuf = rt?.GetField("bufferedSetpoint", NP);
            if (_base != null) _base.PerspectiveAutoAlign = false;
            try { _buttons = ReefButtons.Install(_base); }
            catch (Exception e) { Log.Write("reef button install failed: " + e); _buttons = null; }

            _score = FindFirstObjectByType<ReefscapeScoreHandler>();
            _blueReef = null; _redReef = null;
            foreach (var r in FindObjectsByType<Reef>(FindObjectsInactive.Exclude, FindObjectsSortMode.None))
                if (r.Alliance == Alliance.Blue) _blueReef = r; else _redReef = r;
            var troughType = Type.GetType("FieldScripts.Trough, Assembly-CSharp");
            _troughs = troughType != null ? FindObjectsByType(troughType, FindObjectsInactive.Exclude, FindObjectsSortMode.None).Cast<Component>().ToArray() : new Component[0];
            _fTroughObjs = troughType?.GetField("troughObjects", NP);

            _cmdV = Vector2.zero; _cmdW = 0f; _drive_on = true;
            for (int i = 0; i < _hold.Length; i++) { _hold[i] = false; _tap[i] = false; }
            _tapFrames = 0; _tapsSent = 0;
            _endSince = -1f;
            _stepCounter = 0;
            _stepsThisDecision = _decisionSteps;
            _episode++;
            _episodeStartTime = Time.time;
            _controlling = true;
            _resetting = false;
            Log.Write($"reef episode {_episode} start: robot={_robot.name} type={_base?.GetType().Name} team={_base?.TeamNumber} " +
                      $"pos=({_robot.transform.position.x:F3},{_robot.transform.position.z:F3}) yaw={_robot.transform.eulerAngles.y:F1} buttons={(_buttons != null)} troughs={_troughs.Length}");
            Send(State(false));
            _awaitingAction = true;
        }

        private void ReleaseControl()
        {
            _controlling = false;
            _awaitingAction = false;
            if (_buttons != null) { try { _buttons.Dispose(); } catch { } _buttons = null; }
        }

        private static GameObject FindBlueRobot()
        {
            var rsc = FindFirstObjectByType<RobotSpawnController>();
            return rsc?.BlueSpawnedRobots?.FirstOrDefault(r => r != null);
        }

        // ------------------------------------------------------------ physics-step loop
        private void FixedUpdate()
        {
            if (!_controlling) return;
            if (_robot == null) { Log.Write("robot vanished mid-episode"); ReleaseControl(); Send(Err("robot vanished")); return; }

            while (_awaitingAction)
            {
                string line = ReadLineBlocking(300000);
                if (line == null) { Log.Write("no action within 300 s; releasing control"); ReleaseControl(); return; }
                ReefMsg m;
                try { m = JsonUtility.FromJson<ReefMsg>(line); } catch { m = null; }
                if (m != null && m.cmd == "field") { Send(FieldJson()); continue; }
                if (m == null || m.cmd != "act")
                {
                    ReleaseControl();
                    if (m != null) Handle(line);
                    return;
                }
                ApplyAction(m);
                _awaitingAction = false;
                _stepCounter = 0;
            }

            if (_drive_on && _drive != null)
                _drive.OverrideInput(_cmdV.x, _cmdV.y, _cmdW, DriveController.DrivePerspective.FieldOriented);

            var gm = BaseGameManager.Instance;
            if (gm != null && gm.GameState == GameState.End && _endSince < 0f) _endSince = Time.time;
            // the final totals settle ~3 s after End (climb/park keep re-scoring while the robots are disabled)
            bool done = _endSince >= 0f && Time.time - _endSince >= 3.4f;

            if (++_stepCounter >= _stepsThisDecision || done)
            {
                Send(State(done));
                if (done) { ReleaseControl(); return; }
                _awaitingAction = true;
            }
        }

        private void ApplyAction(ReefMsg m)
        {
            _cmdV = (m.v != null && m.v.Length >= 2) ? new Vector2(m.v[0], m.v[1]) : Vector2.zero;
            _cmdW = m.w;
            _drive_on = m.free != 1;
            _stepsThisDecision = m.n > 0 ? m.n : _decisionSteps;
            if (_buttons == null) return;
            for (int i = 0; i < _hold.Length; i++) _hold[i] = m.hold != null && i < m.hold.Length && m.hold[i] != 0;
            bool anyTap = false;
            if (m.tap != null)
                foreach (int b in m.tap)
                    if (b >= 0 && b < _tap.Length) { _tap[b] = true; anyTap = true; _tapsSent++; }
            var down = new bool[_hold.Length];
            for (int i = 0; i < down.Length; i++) down[i] = _hold[i] || _tap[i];
            // full pad state every decision: self-heals a dropped event
            _buttons.SetAll(down);
            if (anyTap) _tapFrames = 3;
        }

        // ------------------------------------------------------------ serialisation helpers
        private static string F(float f) => float.IsNaN(f) || float.IsInfinity(f) ? "0" : f.ToString("0.####", Inv);
        private static void V3(StringBuilder sb, Vector3 v) => sb.Append('[').Append(F(v.x)).Append(',').Append(F(v.y)).Append(',').Append(F(v.z)).Append(']');

        private static void Box(StringBuilder sb, Collider c)
        {
            sb.Append("{\"c\":"); V3(sb, c.bounds.center);
            sb.Append(",\"aabb\":"); V3(sb, c.bounds.size);
            sb.Append(",\"yaw\":").Append(F(c.transform.eulerAngles.y));
            if (c is BoxCollider bc)
            {
                sb.Append(",\"size\":"); V3(sb, Vector3.Scale(bc.size, c.transform.lossyScale));
                sb.Append(",\"ctr\":"); V3(sb, c.transform.TransformPoint(bc.center));
            }
            sb.Append(",\"name\":\"").Append(c.gameObject.name.Replace("\"", "'")).Append("\"}");
        }

        private static float SF(object secureFloat)
        {
            if (secureFloat == null) return 0f;
            var p = secureFloat.GetType().GetProperty("Value");
            return p != null ? Convert.ToSingle(p.GetValue(secureFloat)) : 0f;
        }

        private static void ScoreData(StringBuilder sb, ReefscapeScoreData d)
        {
            sb.Append("{\"coral\":").Append(F(d.CoralPoints.Value)).Append(",\"net\":").Append(F(d.NetPoints.Value))
              .Append(",\"proc\":").Append(F(d.ProcessorPoints.Value)).Append(",\"climb\":").Append(F(d.ClimbPoints.Value))
              .Append(",\"park\":").Append(F(d.ParkPoints.Value)).Append(",\"leave\":").Append(F(d.LeavePoints.Value))
              .Append(",\"nCoral\":").Append(F(d.CoralScored.Value)).Append(",\"nAlgae\":").Append(F(d.AlgaeScored.Value))
              .Append(",\"pen\":").Append(F(d.PenaltyEarned.Value)).Append(",\"self\":").Append(F(d.SelfPenaltyEarned.Value)).Append('}');
        }

        public static int CoralAxisIndex = -1;   // 0 = right, 1 = up, 2 = forward; -1 = unknown (reports up)
        private static Vector3 CoralAxis(Transform t)
        {
            if (CoralAxisIndex < 0)
            {
                // the coral's long axis: the capsule direction or the largest box/mesh dimension of its first collider
                var col = t.GetComponentInChildren<Collider>(true);
                Vector3 size = Vector3.zero;
                if (col is CapsuleCollider cc) { size = Vector3.zero; size[cc.direction] = 1f; }
                else if (col is BoxCollider bc) size = bc.size;
                else if (col is MeshCollider mc && mc.sharedMesh != null) size = mc.sharedMesh.bounds.size;
                if (col != null && col.transform != t)
                {
                    // express in the piece root's frame
                    var d0 = t.InverseTransformDirection(col.transform.TransformDirection(Vector3.right * size.x));
                    var d1 = t.InverseTransformDirection(col.transform.TransformDirection(Vector3.up * size.y));
                    var d2 = t.InverseTransformDirection(col.transform.TransformDirection(Vector3.forward * size.z));
                    size = new Vector3(Mathf.Abs(d0.x) + Mathf.Abs(d1.x) + Mathf.Abs(d2.x), Mathf.Abs(d0.y) + Mathf.Abs(d1.y) + Mathf.Abs(d2.y), Mathf.Abs(d0.z) + Mathf.Abs(d1.z) + Mathf.Abs(d2.z));
                }
                CoralAxisIndex = size.x >= size.y && size.x >= size.z ? 0 : (size.y >= size.z ? 1 : 2);
                Log.Write($"coral long axis index={CoralAxisIndex} size={size}");
            }
            return CoralAxisIndex == 0 ? t.right : (CoralAxisIndex == 1 ? t.up : t.forward);
        }

        private static string ReefBits(Reef r)
        {
            if (r == null) return "";
            try
            {
                var s = r.Scored();
                var sb = new StringBuilder(36);
                for (int i = 0; i < s.Length; i++) for (int j = 0; j < 3; j++) sb.Append(s[i][j] ? '1' : '0');
                return sb.ToString();
            }
            catch { return ""; }
        }

        private static object Node(ReefscapeRobotGamePieceController rgpc, string name)
        {
            try { return rgpc?.GetPieceByName(name); } catch { return null; }
        }

        private static void NodeJson(StringBuilder sb, object node)
        {
            if (node == null) { sb.Append("null"); return; }
            var t = node.GetType();
            int st = (int)t.GetField("currentStateNum").GetValue(node);
            bool at = (bool)t.GetField("atTarget").GetValue(node);
            bool has = Compat.Bool(t.GetMethod("HasPiece").Invoke(node, null));
            sb.Append("{\"st\":").Append(st).Append(",\"at\":").Append(at ? 1 : 0).Append(",\"has\":").Append(has ? 1 : 0).Append('}');
        }

        private string State(bool done)
        {
            var gm = BaseGameManager.Instance;
            var sb = new StringBuilder(8192);
            sb.Append("{\"ok\":true,\"ep\":").Append(_episode).Append(",\"done\":").Append(done ? "true" : "false");
            sb.Append(",\"t\":").Append(F(gm != null ? gm.Timer : -1f));
            sb.Append(",\"gs\":").Append(gm != null ? (int)gm.GameState : -1).Append(",\"rs\":").Append(gm != null ? (int)gm.RobotState : -1);
            sb.Append(",\"blue\":").Append(F(ReefscapeScoreUI.TotalBlueScore.Value)).Append(",\"red\":").Append(F(ReefscapeScoreUI.TotalRedScore.Value));
            sb.Append(",\"gameTime\":").Append(F(Time.time)).Append(",\"frame\":").Append(Time.frameCount);
            if (_score != null)
            {
                sb.Append(",\"bd\":"); ScoreData(sb, _score.GetScoreData(Alliance.Blue));
                sb.Append(",\"rd\":"); ScoreData(sb, _score.GetScoreData(Alliance.Red));
            }
            sb.Append(",\"reefB\":\"").Append(ReefBits(_blueReef)).Append("\",\"reefR\":\"").Append(ReefBits(_redReef)).Append('"');
            if (_fTroughObjs != null && _troughs.Length > 0)
            {
                sb.Append(",\"trough\":[");
                for (int i = 0; i < _troughs.Length; i++)
                {
                    if (i > 0) sb.Append(',');
                    var list = _troughs[i] != null ? _fTroughObjs.GetValue(_troughs[i]) as System.Collections.ICollection : null;
                    sb.Append(list?.Count ?? -1);
                }
                sb.Append(']');
            }

            if (_robot != null)
            {
                var tr = _robot.transform;
                Vector3 p = tr.position, e = tr.eulerAngles;
                Vector3 v = _rb != null ? _rb.velocity : Vector3.zero, w = _rb != null ? _rb.angularVelocity : Vector3.zero;
                sb.Append(",\"robot\":{\"x\":").Append(F(p.x)).Append(",\"y\":").Append(F(p.y)).Append(",\"z\":").Append(F(p.z));
                sb.Append(",\"yaw\":").Append(F(e.y)).Append(",\"pitch\":").Append(F(e.x)).Append(",\"roll\":").Append(F(e.z));
                sb.Append(",\"vx\":").Append(F(v.x)).Append(",\"vz\":").Append(F(v.z)).Append(",\"wy\":").Append(F(w.y * Mathf.Rad2Deg));
                sb.Append(",\"ground\":").Append(_drive != null && _drive.IsTouchingGround ? 1 : 0);
                sb.Append('}');
                if (_base != null)
                {
                    sb.Append(",\"mech\":{\"sp\":").Append((int)_base.CurrentSetpoint).Append(",\"lsp\":").Append((int)_base.LastSetpoint)
                      .Append(",\"mode\":").Append((int)_base.CurrentRobotMode).Append(",\"imode\":").Append((int)_base.CurrentIntakeMode)
                      .Append(",\"facing\":").Append(_base.FacingReef ? 1 : 0).Append(",\"fbarge\":").Append(_base.FacingBarge ? 1 : 0);
                    sb.Append(",\"coral\":"); NodeJson(sb, Node(_rgpc, "Coral"));
                    sb.Append(",\"algae\":"); NodeJson(sb, Node(_rgpc, "Algae"));
                    try
                    {
                        var el = _fElevator?.GetValue(_base);
                        if (el != null) sb.Append(",\"elev\":").Append(F(Compat.Float(el.GetType().GetMethod("GetElevatorHeight").Invoke(el, null))));
                        if (_fStation != null) sb.Append(",\"station\":").Append((bool)_fStation.GetValue(_base) ? 1 : 0);
                        if (_fFloor != null) sb.Append(",\"floor\":").Append((bool)_fFloor.GetValue(_base) ? 1 : 0);
                        if (_fHand != null) sb.Append(",\"hand\":").Append((bool)_fHand.GetValue(_base) ? 1 : 0);
                        if (_fBuf != null) { var bv = _fBuf.GetValue(_base); sb.Append(",\"buf\":").Append(bv == null ? -1 : Convert.ToInt32(bv)); }
                    }
                    catch (Exception) { }
                    // ClimbScorer.ScoringTriggered is side-effect free (AutoClimbTriggered advances a debounce timer: never read it)
                    try { if (_climb != null) sb.Append(",\"cage\":").Append(_climb.ScoringTriggered ? 1 : 0); } catch { }
                    sb.Append('}');
                }
                // built-in AutoAlign: active, has a latched target this physics step, latched position target (x, z)
                try
                {
                    if (_robot != null && (_al == null || _alOwner != _robot))
                    {
                        _al = _robot.GetComponentInChildren<ReefscapeAutoAlign>(true);
                        _alOwner = _robot;
                        _alHas = typeof(ReefscapeAutoAlign).GetField("_hasTarget", BindingFlags.Instance | BindingFlags.NonPublic);
                        _alActive = typeof(ReefscapeAutoAlign).GetProperty("Active", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                    }
                    if (_al != null)
                    {
                        var tg = _al.GetPositionTarget();
                        bool act = _alActive != null && (bool)_alActive.GetValue(_al);
                        bool has = _alHas != null && (bool)_alHas.GetValue(_al);
                        sb.Append(",\"al\":{\"act\":").Append(act ? 1 : 0).Append(",\"has\":").Append(has ? 1 : 0)
                          .Append(",\"tgt\":[").Append(F(tg.x)).Append(',').Append(F(tg.y)).Append("]}");
                    }
                }
                catch (Exception) { }
                if (_buttons != null)
                {
                    sb.Append(",\"bIn\":\"");
                    for (int i = 0; i < ReefButtons.Count; i++) sb.Append(_buttons.ReadBack(i) ? '1' : '0');
                    sb.Append('"');
                }
                // loose / held pieces: [x, z, y, held, scored]
                var coral = new StringBuilder(2048); var algae = new StringBuilder(1024);
                bool fc = true, fa = true;
                foreach (var pc in FindObjectsByType<ReefscapeGamePieceController>(FindObjectsInactive.Exclude, FindObjectsSortMode.None))
                {
                    if (pc == null) continue;
                    bool isCoral = pc.CompareTag("Coral") || pc.GetComponentInChildren<Collider>()?.CompareTag("Coral") == true;
                    bool isAlgae = !isCoral && (pc.CompareTag("Algae") || pc.GetComponentInChildren<Collider>()?.CompareTag("Algae") == true);
                    if (!isCoral && !isAlgae) continue;
                    var q = pc.transform.position;
                    bool held = pc.GetComponentInParent<RobotBase>() != null;
                    var dst = isCoral ? coral : algae;
                    if (isCoral) { if (!fc) dst.Append(','); fc = false; } else { if (!fa) dst.Append(','); fa = false; }
                    dst.Append('[').Append(F(q.x)).Append(',').Append(F(q.z)).Append(',').Append(F(q.y)).Append(',').Append(held ? 1 : 0)
                       .Append(',').Append(pc.IsScored ? 1 : 0);
                    if (isCoral)
                    {
                        // long-axis direction (world) for coral: transform.up/right/forward, picked by CoralAxis
                        var ax = CoralAxis(pc.transform);
                        dst.Append(',').Append(F(ax.x)).Append(',').Append(F(ax.z)).Append(',').Append(F(ax.y));
                    }
                    else
                    {
                        // release impulse direction (world): GamePieceController.ReleaseV1 adds
                        // transform.TransformDirection(force) with Madtown's force (0,-0.25,0), i.e. the piece's own -y
                        var rd = pc.transform.TransformDirection(Vector3.down);
                        dst.Append(',').Append(F(rd.x)).Append(',').Append(F(rd.z)).Append(',').Append(F(rd.y));
                    }
                    // stable identity (field 8): FindObjectsByType(FindObjectsSortMode.None) is not index-stable, every
                    // station ground drop inserted the new coral mid-list and the bot's index tracking jumped targets
                    dst.Append(',').Append(pc.GetInstanceID());
                    dst.Append(']');
                }
                sb.Append(",\"coral\":[").Append(coral).Append("],\"algae\":[").Append(algae).Append(']');
            }
            sb.Append('}');
            return sb.ToString();
        }

        // ------------------------------------------------------------ static field geometry (live scene)
        private static Transform FindDeep(Transform t, string name)
        {
            if (t.name == name) return t;
            foreach (Transform c in t) { var r = FindDeep(c, name); if (r != null) return r; }
            return null;
        }

        private string FieldJson()
        {
            var sb = new StringBuilder(65536);
            sb.Append("{\"ok\":true");
            try
            {
                var br = GameObject.Find("BlueReef"); var rr = GameObject.Find("RedReef");
                sb.Append(",\"blueReef\":"); V3(sb, br != null ? br.transform.position : Vector3.zero);
                sb.Append(",\"redReef\":"); V3(sb, rr != null ? rr.transform.position : Vector3.zero);

                sb.Append(",\"faces\":[");
                bool first = true;
                foreach (var go in GameObject.FindGameObjectsWithTag("ReefFace"))
                {
                    if (!go.TryGetComponent<AlignNode>(out var an) || an.LeftNode == null || an.RightNode == null) continue;
                    if (!first) sb.Append(','); first = false;
                    sb.Append("{\"p\":"); V3(sb, go.transform.position);
                    sb.Append(",\"yaw\":").Append(F(go.transform.eulerAngles.y));
                    sb.Append(",\"L\":"); V3(sb, an.LeftNode.transform.position);
                    sb.Append(",\"Lyaw\":").Append(F(an.LeftNode.transform.eulerAngles.y));
                    sb.Append(",\"R\":"); V3(sb, an.RightNode.transform.position);
                    sb.Append(",\"Ryaw\":").Append(F(an.RightNode.transform.eulerAngles.y));
                    sb.Append(",\"lowAlgae\":").Append(an.LowAlgae ? 1 : 0).Append(",\"name\":\"").Append(go.name).Append("\"}");
                }
                sb.Append(']');

                // branch triggers in Reef.Scored() order: per reef, 12 poles x [L4, L3, L2]
                var fScorers = typeof(Reef).GetField("_scorers", NP);
                sb.Append(",\"poles\":{");
                bool fr = true;
                foreach (var reef in FindObjectsByType<Reef>(FindObjectsInactive.Exclude, FindObjectsSortMode.None))
                {
                    if (!fr) sb.Append(','); fr = false;
                    sb.Append('"').Append(reef.Alliance == Alliance.Blue ? "blue" : "red").Append("\":[");
                    var scorers = fScorers?.GetValue(reef) as ReefPoleScorer[];
                    for (int i = 0; scorers != null && i < scorers.Length; i++)
                    {
                        if (i > 0) sb.Append(',');
                        sb.Append("{\"p\":"); V3(sb, scorers[i].transform.position);
                        sb.Append(",\"name\":\"").Append(scorers[i].gameObject.name).Append("\",\"trig\":[");
                        var trig = FindDeep(scorers[i].transform, "Triggers");
                        string[] lv = { "L4", "L3", "L2" };
                        for (int j = 0; j < 3; j++)
                        {
                            if (j > 0) sb.Append(',');
                            var c = trig != null ? FindDeep(trig, lv[j])?.GetComponent<BoxCollider>() : null;
                            if (c != null) Box(sb, c); else sb.Append("null");
                        }
                        sb.Append("]}");
                    }
                    sb.Append(']');
                }
                sb.Append('}');

                var troughType = Type.GetType("FieldScripts.Trough, Assembly-CSharp");
                var fTT = troughType?.GetField("troughTriggers", NP);
                sb.Append(",\"troughs\":[");
                first = true;
                if (troughType != null)
                    foreach (var tco in FindObjectsByType(troughType, FindObjectsInactive.Exclude, FindObjectsSortMode.None))
                    {
                        var comp = (Component)tco;
                        var trigs = fTT?.GetValue(comp) as BoxCollider[];
                        if (trigs == null) continue;
                        foreach (var c in trigs) { if (c == null) continue; if (!first) sb.Append(','); first = false; Box(sb, c); }
                    }
                sb.Append(']');

                sb.Append(",\"zones\":{");
                string[] zones = { "BlueReefZone", "RedReefZone", "BlueBargeZone", "RedBargeZone", "BlueStartLine", "RedStartLine", "BlueStartLineWide", "RedStartLineWide" };
                bool fz = true;
                foreach (var z in zones)
                {
                    GameObject go = null;
                    try { go = GameObject.FindGameObjectWithTag(z); } catch { }
                    var c = go != null ? go.GetComponent<Collider>() : null;
                    if (c == null) continue;
                    if (!fz) sb.Append(','); fz = false;
                    sb.Append('"').Append(z).Append("\":"); Box(sb, c);
                }
                sb.Append('}');

                sb.Append(",\"cages\":[");
                first = true;
                foreach (var go in GameObject.FindGameObjectsWithTag("Cage"))
                {
                    var c = go.GetComponent<Collider>();
                    if (!first) sb.Append(','); first = false;
                    if (c != null) Box(sb, c); else { sb.Append("{\"c\":"); V3(sb, go.transform.position); sb.Append('}'); }
                }
                sb.Append(']');

                sb.Append(",\"stations\":[");
                first = true;
                foreach (var cs in FindObjectsByType<CoralStation>(FindObjectsInactive.Exclude, FindObjectsSortMode.None))
                {
                    if (!first) sb.Append(','); first = false;
                    sb.Append("{\"alliance\":").Append((int)cs.Alliance).Append(",\"p\":"); V3(sb, cs.transform.position);
                    sb.Append(",\"yaw\":").Append(F(cs.transform.eulerAngles.y));
                    var sp = typeof(CoralStation).GetField("_initialSpawnpoint", NP)?.GetValue(cs);
                    if (sp is Vector3 spv) { sb.Append(",\"spawn\":"); V3(sb, spv); }
                    var spr = typeof(CoralStation).GetField("_initialSpawnpointRotation", NP)?.GetValue(cs);
                    if (spr is Quaternion q) { sb.Append(",\"spawnFwd\":"); V3(sb, q * Vector3.forward); }
                    var trig = cs.GetComponent<BoxCollider>();
                    if (trig != null) { sb.Append(",\"trig\":"); Box(sb, trig); }
                    sb.Append('}');
                }
                sb.Append(']');

                sb.Append(",\"processors\":[");
                first = true;
                foreach (var ps in FindObjectsByType<ProcessorScorer>(FindObjectsInactive.Exclude, FindObjectsSortMode.None))
                {
                    if (!first) sb.Append(','); first = false;
                    sb.Append("{\"alliance\":").Append((int)ps.Alliance);
                    var st = typeof(ProcessorScorer).GetField("scoreTrigger", NP)?.GetValue(ps) as BoxCollider;
                    if (st != null) { sb.Append(",\"trig\":"); Box(sb, st); }
                    foreach (var tn in new[] { "launchTarget1", "launchTarget2", "launchTarget3" })
                    {
                        var lt = typeof(ProcessorScorer).GetField(tn, NP)?.GetValue(ps) as Transform;
                        if (lt != null) { sb.Append(",\"").Append(tn).Append("\":"); V3(sb, lt.position); }
                    }
                    sb.Append('}');
                }
                sb.Append(']');

                sb.Append(",\"nets\":[");
                first = true;
                foreach (var bs in FindObjectsByType<BargeScorer>(FindObjectsInactive.Exclude, FindObjectsSortMode.None))
                {
                    if (!first) sb.Append(','); first = false;
                    sb.Append("{\"alliance\":").Append((int)bs.Alliance);
                    var c = bs.GetComponent<BoxCollider>();
                    if (c != null) { sb.Append(",\"box\":"); Box(sb, c); }
                    sb.Append('}');
                }
                sb.Append(']');

                var anyCoral = FindObjectsByType<ReefscapeGamePieceController>(FindObjectsInactive.Exclude, FindObjectsSortMode.None)
                    .FirstOrDefault(pc => pc.CompareTag("Coral") || pc.GetComponentInChildren<Collider>()?.CompareTag("Coral") == true);
                if (anyCoral != null)
                {
                    sb.Append(",\"coralColliders\":[");
                    bool fcc = true;
                    foreach (var col in anyCoral.GetComponentsInChildren<Collider>(true))
                    {
                        if (!fcc) sb.Append(','); fcc = false;
                        sb.Append("{\"type\":\"").Append(col.GetType().Name).Append("\",\"go\":\"").Append(col.gameObject.name).Append("\",\"layer\":\"").Append(LayerMask.LayerToName(col.gameObject.layer))
                          .Append("\",\"tag\":\"").Append(col.tag).Append("\",\"trig\":").Append(col.isTrigger ? 1 : 0);
                        sb.Append(",\"localRot\":"); V3(sb, (Quaternion.Inverse(anyCoral.transform.rotation) * col.transform.rotation).eulerAngles);
                        if (col is CapsuleCollider cc) sb.Append(",\"dir\":").Append(cc.direction).Append(",\"h\":").Append(F(cc.height)).Append(",\"r\":").Append(F(cc.radius));
                        if (col is BoxCollider bc2) { sb.Append(",\"size\":"); V3(sb, bc2.size); }
                        if (col is MeshCollider mc && mc.sharedMesh != null) { sb.Append(",\"mesh\":"); V3(sb, mc.sharedMesh.bounds.size); }
                        sb.Append(",\"scale\":"); V3(sb, col.transform.lossyScale);
                        sb.Append('}');
                    }
                    sb.Append(']');
                }
                if (_robot != null)
                {
                    sb.Append(",\"robot\":{\"type\":\"").Append(_base?.GetType().FullName).Append('"');
                    if (_drive != null) sb.Append(",\"vmax\":").Append(F(_drive.GetMaxVelocity())).Append(",\"wmax\":").Append(F(_drive.GetMaxOmega()));
                    var bump = _base?.bumperCollider as BoxCollider;
                    if (bump != null) { sb.Append(",\"bumper\":"); V3(sb, Vector3.Scale(bump.size, bump.transform.lossyScale)); sb.Append(",\"bumperCtr\":"); V3(sb, _robot.transform.InverseTransformPoint(bump.transform.TransformPoint(bump.center))); }
                    sb.Append(",\"autoAlign\":").Append(_robot.GetComponentInChildren<ReefscapeAutoAlign>(true) != null ? 1 : 0);
                    if (_climb != null)
                    {
                        sb.Append(",\"climbTrig\":");
                        var ct = typeof(ClimbScorer).GetField("cageScoringTrigger", NP)?.GetValue(_climb) as BoxCollider;
                        if (ct != null) Box(sb, ct); else sb.Append("null");
                    }
                    var csm = _base?.CurrentCoralStationMode;
                    if (csm != null) sb.Append(",\"stationMode\":{\"drop\":").Append((int)csm.DropType).Append(",\"dist\":").Append(F(csm.DropDistance)).Append(",\"req\":").Append(csm.RequireIntaking ? 1 : 0).Append('}');
                    // every game-piece intake on the robot: robot-local position and trigger sizes
                    sb.Append(",\"intakes\":{");
                    bool fi = true;
                    if (_base != null)
                        foreach (var f in _base.GetType().GetFields(ANY))
                        {
                            if (!typeof(Component).IsAssignableFrom(f.FieldType) || !f.FieldType.Name.Contains("Intake")) continue;
                            var comp = f.GetValue(_base) as Component;
                            if (comp == null) continue;
                            if (!fi) sb.Append(','); fi = false;
                            sb.Append('"').Append(f.Name).Append("\":{\"local\":"); V3(sb, _robot.transform.InverseTransformPoint(comp.transform.position));
                            bool fc2 = true;
                            FieldInfo icf = null;
                            for (var bt = comp.GetType(); bt != null && icf == null; bt = bt.BaseType) icf = bt.GetField("intakeCollider", NP);
                            var icols = icf?.GetValue(comp) as BoxCollider[] ?? new BoxCollider[0];
                            sb.Append(',');
                            // pull parameters: target (robot-local), maxDistance/accuracy (inches), planar box
                            Func<string, object> gf = name => { for (var bt = comp.GetType(); bt != null; bt = bt.BaseType) { var fi2 = bt.GetField(name, NP | BindingFlags.Public); if (fi2 != null) return fi2.GetValue(comp); } return null; };
                            var tgt = gf("target") as Transform;
                            sb.Append("\"tgt\":"); if (tgt != null) V3(sb, _robot.transform.InverseTransformPoint(tgt.position)); else sb.Append("null");
                            sb.Append(",\"maxDist\":").Append(F(Convert.ToSingle(gf("maxDistance") ?? 0f))).Append(",\"acc\":").Append(F(Convert.ToSingle(gf("accuracy") ?? 0f)))
                              .Append(",\"planar\":").Append((bool)(gf("usePlanarTolerancing") ?? false) ? 1 : 0).Append(",\"speed\":").Append(F(Convert.ToSingle(gf("intakeSpeed") ?? 0f)))
                              .Append(",\"force\":").Append(F(Convert.ToSingle(gf("intakeForce") ?? 0f))).Append(",\"req\":").Append((bool)(gf("requestIntake") ?? false) ? 1 : 0)
                              .Append(",\"hasGP\":").Append((bool)(gf("hasGamePiece") ?? false) ? 1 : 0);
                            var bx = gf("boxExtents"); if (bx is Vector3 bxv) { sb.Append(",\"box\":"); V3(sb, bxv); }
                            sb.Append(",\"cols\":[");
                            foreach (var col in icols)
                            {
                                if (col == null) continue;
                                if (!fc2) sb.Append(','); fc2 = false;
                                // GamePieceIntake.FixedUpdate: OverlapBox(collider.transform.position, size*lossyScale/2, intake.transform.rotation)
                                sb.Append("{\"local\":"); V3(sb, _robot.transform.InverseTransformPoint(col.transform.position));
                                sb.Append(",\"half\":"); V3(sb, Vector3.Scale(col.size, col.transform.lossyScale) / 2f);
                                sb.Append(",\"rotLocal\":"); V3(sb, (Quaternion.Inverse(_robot.transform.rotation) * comp.transform.rotation).eulerAngles);
                                sb.Append(",\"aabb\":"); V3(sb, col.bounds.size);
                                sb.Append(",\"trig\":").Append(col.isTrigger ? 1 : 0).Append(",\"name\":\"").Append(col.gameObject.name.Replace("\"", "'")).Append("\"}");
                            }
                            sb.Append("]}");
                        }
                    sb.Append('}');
                    // every RobotSetpoint1323 (or other *Setpoint) field, flattened with its public float/bool fields
                    sb.Append(",\"setpoints\":{");
                    bool fs = true;
                    if (_base != null)
                        foreach (var f in _base.GetType().GetFields(ANY))
                        {
                            if (!f.FieldType.Name.Contains("Setpoint") || f.FieldType.IsEnum) continue;
                            object sv; try { sv = f.GetValue(_base); } catch { continue; }
                            if (sv == null) continue;
                            if (!fs) sb.Append(','); fs = false;
                            sb.Append('"').Append(f.Name).Append("\":{");
                            bool ff = true;
                            foreach (var g in sv.GetType().GetFields(BindingFlags.Instance | BindingFlags.Public))
                            {
                                object gv = g.GetValue(sv);
                                if (!(gv is float || gv is bool)) continue;
                                if (!ff) sb.Append(','); ff = false;
                                sb.Append('"').Append(g.Name).Append("\":").Append(gv is float gf ? F(gf) : ((bool)gv ? "1" : "0"));
                            }
                            sb.Append('}');
                        }
                    sb.Append("}}");
                }
            }
            catch (Exception e) { sb.Append(",\"error\":\"").Append(e.Message.Replace("\"", "'")).Append('"'); Log.Write("field json: " + e); }
            sb.Append('}');
            return sb.ToString();
        }
    }
}
