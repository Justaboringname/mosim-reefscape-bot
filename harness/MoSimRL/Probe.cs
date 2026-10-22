// MoSimRL entry point for the REEFSCAPE bot (release copy, trimmed to the Reefscape bridge only).
//
// Loaded by a bootstrap that tools/install-hook.sh inserts into a locally installed mod DLL. Inert unless
// <run dir>/ENABLE exists. A bot instance (env MOSIMRL_REEF=1, set by py/reefbot/gamectl.py) gets the ReefBridge
// (Python control bridge) and the no-upload guard; a normal human session is left untouched.

using System;
using System.IO;
using MoSimCore.BaseClasses.GameManagement;
using UnityEngine;
using UnitySceneManager = UnityEngine.SceneManagement.SceneManager;
using Scene = UnityEngine.SceneManagement.Scene;
using LoadSceneMode = UnityEngine.SceneManagement.LoadSceneMode;

namespace MoSimRL
{
    public static class Entry
    {
        public static readonly string RunDir = ResolveRunDir();

        // Where the flag files (ENABLE, ...) and logs live: env MOSIMRL_RUN_DIR (bot instances: gamectl sets it), else
        // the path tools/install-hook.sh wrote to ~/.mosimrl_run, else ~/Projects/mosim-reefscape-bot/run. The harness
        // is loaded from bytes, so it cannot use its own file location.
        private static string ResolveRunDir()
        {
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            try
            {
                string env = Environment.GetEnvironmentVariable("MOSIMRL_RUN_DIR");
                if (!string.IsNullOrEmpty(env)) return env;
                string ptr = Path.Combine(home, ".mosimrl_run");
                if (File.Exists(ptr)) { string p = File.ReadAllText(ptr).Trim(); if (p.Length > 0) return p; }
            }
            catch (Exception) { }
            return Path.Combine(home, "Projects", "mosim-reefscape-bot", "run");
        }

        private static bool _inited;

        public static bool Flag(string name) => File.Exists(Path.Combine(RunDir, name));

        // Called from a MonoBehaviour constructor during AssetBundle deserialization:
        // pure managed work only, no Unity object APIs here.
        public static void Init()
        {
            if (_inited) return;
            _inited = true;
            bool enabled = Flag("ENABLE");
            Log.Write($"Init from host ctor (enabled={enabled})");
            if (!enabled) return;
            UnitySceneManager.sceneLoaded += OnSceneLoaded;
        }

        private static void OnSceneLoaded(Scene scene, LoadSceneMode mode)
        {
            try
            {
                if (Host.Instance == null)
                {
                    var go = new GameObject("MoSimRL.Host");
                    UnityEngine.Object.DontDestroyOnLoad(go);
                    go.AddComponent<Host>();
                }
            }
            catch (Exception e)
            {
                Log.Write("OnSceneLoaded failed: " + e);
            }
        }
    }

    public static class Log
    {
        private static readonly object Gate = new object();

        public static void Write(string msg)
        {
            string line = $"[MoSimRL] {DateTime.Now:HH:mm:ss.fff} {msg}";
            try { Debug.Log(line); } catch { }
            try
            {
                lock (Gate)
                {
                    Directory.CreateDirectory(Entry.RunDir);
                    File.AppendAllText(Path.Combine(Entry.RunDir, "probe.log"), line + "\n");
                }
            }
            catch { }
        }
    }

    public class Host : MonoBehaviour
    {
        public static Host Instance { get; private set; }

        private bool _botSession;

        private void Awake()
        {
            Instance = this;
            Log.Write($"host awake: app={Application.version} unity={Application.unityVersion} fixedDeltaTime={Time.fixedDeltaTime:F6}");
            if (ReefBridge.Requested)
            {
                _botSession = true;
                Log.Write("bot session: ReefBridge + no-upload guard");
                gameObject.AddComponent<ReefBridge>();
            }
        }

        // No-upload guard: a bot-driven match is always labelled as cheated, so the game never submits it to the Steam
        // leaderboard (the game checks this flag when a match ends). Match start clears the flag, hence it is
        // re-asserted every frame and every physics step for the whole bot session.
        private void AssertNoUpload()
        {
            if (!_botSession) return;
            var gm = BaseGameManager.Instance;
            if (gm != null && !gm.wasCheated) gm.wasCheated = true;
        }

        private void Update() => AssertNoUpload();
        private void FixedUpdate() => AssertNoUpload();
        private void LateUpdate() => AssertNoUpload();
    }
}
