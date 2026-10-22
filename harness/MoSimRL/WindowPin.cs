// Bot instances only: keep the (tiny, Lite) MoSim window on top in the bottom-right corner of its display so the
// user's own windows can never fully cover it. A fully occluded Unity window stops rendering and the lockstep game
// stalls (drill-cap2, 10-18: the bridge stopped answering ~40 s into a match while the user was working).
using System;
using System.Runtime.InteropServices;
using UnityEngine;

namespace MoSimRL
{
    internal static class WindowPin
    {
        const string ObjC = "/usr/lib/libobjc.A.dylib";
        [DllImport(ObjC)] static extern IntPtr objc_getClass(string name);
        [DllImport(ObjC)] static extern IntPtr sel_registerName(string name);
        [DllImport(ObjC, EntryPoint = "objc_msgSend")] static extern IntPtr Send(IntPtr recv, IntPtr sel);
        [DllImport(ObjC, EntryPoint = "objc_msgSend")] static extern IntPtr SendIdx(IntPtr recv, IntPtr sel, ulong i);
        [DllImport(ObjC, EntryPoint = "objc_msgSend")] static extern ulong SendU(IntPtr recv, IntPtr sel);
        [DllImport(ObjC, EntryPoint = "objc_msgSend")] static extern void SendL(IntPtr recv, IntPtr sel, long v);
        [DllImport(ObjC, EntryPoint = "objc_msgSend")] static extern void SendB(IntPtr recv, IntPtr sel, bool v);

        const long NSFloatingWindowLevel = 3;
        const long CanJoinAllSpaces = 1 << 0, Stationary = 1 << 4, FullScreenAuxiliary = 1 << 8;

        /// <summary>Raise every NSWindow of this process to the floating level; returns how many.</summary>
        public static int Float()
        {
            if (Application.platform != RuntimePlatform.OSXPlayer) return 0;
            try
            {
                IntPtr app = Send(objc_getClass("NSApplication"), sel_registerName("sharedApplication"));
                IntPtr wins = Send(app, sel_registerName("windows"));
                ulong n = SendU(wins, sel_registerName("count"));
                for (ulong i = 0; i < n; i++)
                {
                    IntPtr w = SendIdx(wins, sel_registerName("objectAtIndex:"), i);
                    SendL(w, sel_registerName("setLevel:"), NSFloatingWindowLevel);
                    SendL(w, sel_registerName("setCollectionBehavior:"), CanJoinAllSpaces | Stationary | FullScreenAuxiliary);
                    SendB(w, sel_registerName("setHidesOnDeactivate:"), false);
                }
                return (int)n;
            }
            catch (Exception e) { Log.Write("window float failed: " + e.Message); return -1; }
        }

        [DllImport(ObjC, EntryPoint = "objc_msgSend")] static extern IntPtr SendStr(IntPtr recv, IntPtr sel, string utf8);
        [DllImport(ObjC, EntryPoint = "objc_msgSend")] static extern IntPtr SendActivity(IntPtr recv, IntPtr sel, ulong opts, IntPtr reason);
        static IntPtr _activity;

        /// <summary>Opt the bot instance out of App Nap / timer throttling (NSActivityUserInitiatedAllowingIdleSystemSleep
        /// | NSActivityLatencyCritical): macOS naps apps whose windows are not visible, e.g. while the user watches a
        /// full-screen video in another Space.</summary>
        public static bool NoNap()
        {
            if (Application.platform != RuntimePlatform.OSXPlayer || _activity != IntPtr.Zero) return _activity != IntPtr.Zero;
            try
            {
                const ulong UserInitiated = 0x00FFFFFFUL | (1UL << 20), IdleSystemSleepDisabled = 1UL << 20, LatencyCritical = 0xFF00000000UL;
                ulong opts = (UserInitiated & ~IdleSystemSleepDisabled) | LatencyCritical;
                IntPtr info = Send(objc_getClass("NSProcessInfo"), sel_registerName("processInfo"));
                IntPtr reason = SendStr(objc_getClass("NSString"), sel_registerName("stringWithUTF8String:"), "MoSimRL lockstep bot");
                IntPtr act = SendActivity(info, sel_registerName("beginActivityWithOptions:reason:"), opts, reason);
                _activity = Send(act, sel_registerName("retain"));
                return _activity != IntPtr.Zero;
            }
            catch (Exception e) { Log.Write("no-nap failed: " + e.Message); return false; }
        }

        /// <summary>Move the main window to the bottom-right of its display's work area.</summary>
        public static void ToCorner(int w, int h)
        {
            try
            {
                var di = Screen.mainWindowDisplayInfo;
                var wa = di.workArea;
                var pos = new Vector2Int(wa.x + wa.width - w - 16, wa.y + wa.height - h - 16);
                Screen.MoveMainWindowTo(di, pos);
                Log.Write($"window: display {di.width}x{di.height} workArea=({wa.x},{wa.y},{wa.width},{wa.height}) -> {pos}");
            }
            catch (Exception e) { Log.Write("window move failed: " + e.Message); }
        }
    }
}
