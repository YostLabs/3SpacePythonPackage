import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from yostlabs.math.quaternion import slerp  # noqa: E402


def _quat_z(deg):
    half = math.radians(deg) / 2
    return [0.0, 0.0, math.sin(half), math.cos(half)]


def _angle_z(q):
    return math.degrees(2 * math.atan2(q[2], q[3]))


def test_slerp_interpolates_angle_linearly():
    for total in (45, 90, 170):
        for t in (0.25, 0.5, 0.75):
            q = slerp(_quat_z(0), _quat_z(total), t)
            assert math.isclose(_angle_z(q), total * t, abs_tol=1e-6)


def test_slerp_takes_short_arc_when_dot_is_negative():
    b = [-v for v in _quat_z(90)]  # same rotation, opposite sign
    q = slerp(_quat_z(0), b, 0.5)
    assert math.isclose(_angle_z(q), 45.0, abs_tol=1e-6)


def test_slerp_identical_quaternions():
    q = slerp(_quat_z(30), _quat_z(30), 0.5)
    assert math.isclose(_angle_z(q), 30.0, abs_tol=1e-6)
