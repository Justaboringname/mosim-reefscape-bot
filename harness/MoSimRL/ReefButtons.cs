// Presses Madtown's (any ReefscapeRobotBase's) 13 driver buttons from a private virtual gamepad.
//
// ReefscapeRobotBase keeps its actions in auto-property backing fields (`<L4Action>k__BackingField`, ...), filled
// once in Awake from the robot's InputActionMap. We swap each for an InputAction bound to one control of our own
// pad, so the robot's state machine reads our presses through its normal `.triggered` / `.IsPressed()` calls and
// the human's keyboard/controller never reaches the bot robot.
//
// Edge semantics (ReefscapeRobotBase.Update): one button is handled per rendered frame through an if/else chain,
// and while Intake is held every L1-L4 / Place / Climb press is swallowed. Callers tap (press, release a couple of
// frames later) and never tap while holding Intake.

using System;
using System.Collections.Generic;
using System.Reflection;
using Games.Reefscape.Robots;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;

namespace MoSimRL
{
    public enum ReefButton
    {
        Intake, Place, L1, L2, L3, L4, Climb, Stow, RobotSpecial, RobotModeToggle, IntakeModeToggle, AutoAlignLeft, AutoAlignRight
    }

    // A private 16-button device. It is deliberately NOT a Gamepad: the game's global UI/menu actions bind to
    // generic <Gamepad>/start etc., and a virtual Gamepad pressing Start reset the match. No global binding can
    // match this custom layout.
    public sealed class ReefButtons : IDisposable
    {
        public const int Count = 13;
        private const string LayoutName = "MoSimRLButtons";

        private static readonly string[] Props =
        {
            nameof(ReefscapeRobotBase.IntakeAction), nameof(ReefscapeRobotBase.OuttakeAction), nameof(ReefscapeRobotBase.L1Action),
            nameof(ReefscapeRobotBase.L2Action), nameof(ReefscapeRobotBase.L3Action), nameof(ReefscapeRobotBase.L4Action),
            nameof(ReefscapeRobotBase.ClimbAction), nameof(ReefscapeRobotBase.StowAction), nameof(ReefscapeRobotBase.RobotSpecialAction),
            nameof(ReefscapeRobotBase.RobotModeToggleAction), nameof(ReefscapeRobotBase.IntakeModeToggleAction),
            nameof(ReefscapeRobotBase.AutoAlignLeftAction), nameof(ReefscapeRobotBase.AutoAlignRightAction),
        };

        private static bool _layoutRegistered;

        private readonly InputDevice _dev;
        private readonly ReefscapeRobotBase _robot;
        private readonly List<(FieldInfo field, InputAction original, InputAction injected)> _swaps =
            new List<(FieldInfo, InputAction, InputAction)>();
        private readonly InputAction[] _actions = new InputAction[Count];

        private ReefButtons(InputDevice dev, ReefscapeRobotBase robot) { _dev = dev; _robot = robot; }

        private static void EnsureLayout()
        {
            if (_layoutRegistered) return;
            var sb = new System.Text.StringBuilder();
            sb.Append("{\"name\":\"").Append(LayoutName).Append("\",\"format\":\"MRLB\",\"controls\":[");
            for (int i = 0; i < 16; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append("{\"name\":\"b").Append(i).Append("\",\"layout\":\"Button\",\"offset\":0,\"bit\":").Append(i).Append(",\"format\":\"BIT\"}");
            }
            sb.Append("]}");
            InputSystem.RegisterLayout(sb.ToString());
            _layoutRegistered = true;
        }

        public static ReefButtons Install(ReefscapeRobotBase robot)
        {
            EnsureLayout();
            var dev = InputSystem.AddDevice(LayoutName);
            var inj = new ReefButtons(dev, robot);
            for (int i = 0; i < Count; i++)
            {
                var field = typeof(ReefscapeRobotBase).GetField($"<{Props[i]}>k__BackingField",
                    BindingFlags.Instance | BindingFlags.NonPublic);
                if (field == null) throw new MissingFieldException($"ReefscapeRobotBase.<{Props[i]}>k__BackingField");
                var original = field.GetValue(robot) as InputAction;
                var injected = new InputAction("MoSimRL." + (ReefButton)i, InputActionType.Button, dev["b" + i].path);
                injected.Enable();
                field.SetValue(robot, injected);
                inj._swaps.Add((field, original, injected));
                inj._actions[i] = injected;
            }
            return inj;
        }

        public void SetAll(bool[] down)
        {
            using (StateEvent.From(_dev, out var ev))
            {
                for (int i = 0; i < 16; i++)
                    ((UnityEngine.InputSystem.Controls.ButtonControl)_dev["b" + i]).WriteValueIntoEvent(i < down.Length && down[i] ? 1f : 0f, ev);
                InputSystem.QueueEvent(ev);
            }
        }

        public bool ReadBack(int i) => _actions[i] != null && _actions[i].IsPressed();

        public void Dispose()
        {
            foreach (var (field, original, injected) in _swaps)
            {
                try { if (_robot != null) field.SetValue(_robot, original); } catch { }
                injected.Disable();
                injected.Dispose();
            }
            _swaps.Clear();
            if (_dev != null && _dev.added) InputSystem.RemoveDevice(_dev);
        }
    }
}
