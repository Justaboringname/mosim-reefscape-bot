// 26.5.x API shims.
// - Most RobotFramework methods now return MoSimResult<T>; reflection callers must unwrap before casting.
// - DriveController.overideInput(normalised input, normalised rotation, DriveMode) became
//   OverrideInput(vx m/s, vy m/s, omega rad/s, DrivePerspective). The override is cleared after every
//   drivetrain step, so callers keep re-issuing it each FixedUpdate as before.

using System;
using RobotFramework.Controllers.Drivetrain;
using UnityEngine;

namespace MoSimRL
{
    internal static class Compat
    {
        public static object Unwrap(object o)
        {
            if (o == null) return null;
            var t = o.GetType();
            if (t.IsGenericType && t.Name.StartsWith("MoSimResult", StringComparison.Ordinal))
                return t.GetProperty("Value").GetValue(o);
            return o;
        }

        public static float Float(object o) => Convert.ToSingle(Unwrap(o));

        public static bool Bool(object o) => (bool)Unwrap(o);

        // Old semantics: input in [-1, 1] (x = field +x / robot forward, y = field +z / robot strafe),
        // rotation in [-1, 1] with the old sign (applied as -rotation).
        public static void Drive(DriveController d, Vector2 input, float rotation, bool robotRelative = false)
        {
            if (d == null) return;
            float v = d.GetMaxVelocity(), w = d.GetMaxOmega();
            d.OverrideInput(input.x * v, input.y * v, -rotation * w,
                robotRelative ? DriveController.DrivePerspective.RobotRelative : DriveController.DrivePerspective.FieldOriented);
        }
    }
}
