import math
import yostlabs.math.vector as _vec

def quat_mul(a: list[float], b: list[float]):
    out = [0, 0, 0, 0]
    x = 0; y = 1; z = 2; w = 3
    out[w] = a[w]*b[w] - a[x]*b[x] - a[y]*b[y] - a[z]*b[z]
    out[x] = a[w]*b[x] + a[x]*b[w] + a[y]*b[z] - a[z]*b[y]
    out[y] = a[w]*b[y] + a[y]*b[w] + a[z]*b[x] - a[x]*b[z]
    out[z] = a[w]*b[z] + a[z]*b[w] + a[x]*b[y] - a[y]*b[x]
    return out

#Rotates quaternion b by quaternion a, it does not combine them
def quat_rotate(a: list[float], b: list[float]):
    inv = a.copy()
    inv[0] *= -1
    inv[1] *= -1
    inv[2] *= -1
    axis = [b[0], b[1], b[2], 0]
    halfway = quat_mul(a, axis)
    final = quat_mul(halfway, inv)
    return [*final[:3], b[3]]

def quat_inverse(quat: list[float]):
    return [-quat[0], -quat[1], -quat[2], quat[3]]

def quat_rotate_vec(quat: list[float], vec: list[float]):
    inv = quat_inverse(quat)
    tmp = [vec[0], vec[1], vec[2], 0]
    halfway = quat_mul(quat, tmp)
    final = quat_mul(halfway, inv)
    return [final[0], final[1], final[2]]

def angles_to_quaternion(angles: list[float], order: str, degrees=True):
    quat = [0, 0, 0, 1]
    for i in range(len(angles)):
        axis = order[i]
        angle = angles[i]
        if degrees:
            angle = math.radians(angle)
        unit_vec = _vec.axis_to_unit_vector(axis)
        w = math.cos(angle / 2)
        imaginary = math.sin(angle / 2)
        unit_vec = [v * imaginary for v in unit_vec]
        angle_quat = [*unit_vec, w]
        #print(f"{axis} {angle} {angle_quat}")
        quat = quat_mul(quat, angle_quat)
    return quat

def quat_from_axis_angle(axis: list[float], angle: float):
    imaginary = math.sin(angle / 2)
    quat = [imaginary * v for v in axis]
    quat.append(math.cos(angle / 2))
    return quat

def quat_from_two_vectors(forward: list[float], down: list[float]):
    """
    This function requires two orthogonal vectors to work
    """
    forward_reference = [0, 0, 1]
    down_reference = [0, -1, 0]

    forward = _vec.vec_normalize(forward)
    down = _vec.vec_normalize(down)

    #Create the first rotation to align the forward axis
    axis_of_rotation = _vec.vec_cross(forward_reference, forward)
    axis_of_rotation = _vec.vec_normalize(axis_of_rotation)
    if not any(abs(v) > 0 for v in axis_of_rotation):
        axis_of_rotation = down_reference #This is just a direct 180 degree rotation around any orthogonal axis, so just use the down_ref
    dot = min(1, max(-1, _vec.vec_dot(forward_reference, forward)))
    angle1 = math.acos(dot)
    imaginary = math.sin(angle1/2)
    quat = [v * imaginary for v in axis_of_rotation] #XYZ
    quat.append(math.cos(angle1/2)) #W

    #Update the reference to figure out where it is after the turn
    down_reference = quat_rotate_vec(quat, down_reference)

    #find the rotation to make the remaining reference align with its given value
    axis_of_rotation = _vec.vec_cross(down_reference, down)
    axis_of_rotation = _vec.vec_normalize(axis_of_rotation)
    if not any(abs(v) > 0 for v in axis_of_rotation):
        axis_of_rotation = forward #Rotate along the final forward vector until up is aligned
    dot = min(1, max(-1, _vec.vec_dot(down_reference, down)))
    angle2 = math.acos(dot)
    imaginary = math.sin(angle2/2)
    rotation = [v * imaginary for v in axis_of_rotation] #XYZ
    rotation.append(math.cos(angle2/2)) #W

    quat = quat_mul(rotation, quat)
    return quat

def quaternion_to_3x3_rotation_matrix(quat):
    """
    Convert a quaternion in form x, y, z, w to a rotation matrix
    """
    x, y, z, w = quat

    fTx = 2.0 * x
    fTy = 2.0 * y
    fTz = 2.0 * z
    fTwx = fTx * w
    fTwy = fTy * w
    fTwz = fTz * w
    fTxx = fTx * x
    fTxy = fTy * x
    fTxz = fTz * x
    fTyy = fTy * y
    fTyz = fTz * y
    fTzz = fTz * z

    out = [0] * 9
    out[0] = 1.0-(fTyy+fTzz)
    out[1] = fTxy-fTwz
    out[2] = fTxz+fTwy
    out[3] = fTxy+fTwz
    out[4] = 1.0-(fTxx+fTzz)
    out[5] = fTyz-fTwx
    out[6] = fTxz-fTwy
    out[7] = fTyz + fTwx
    out[8] = 1.0 - (fTxx+fTyy)

    return [
        [out[0], out[1], out[2]],
        [out[3], out[4], out[5]],
        [out[6], out[7], out[8]],
    ]

def quaternion_global_to_local(quat, vec):
    inverse = quat_inverse(quat)
    return quat_rotate_vec(inverse, vec)

def quaternion_local_to_global(quat, vec):
    return quat_rotate_vec(quat, vec)

#https://splines.readthedocs.io/en/latest/rotation/slerp.html
def slerp(a, b, t):
    dot = _vec.vec_dot(a, b)
    if dot < 0: #To force it to be the shortest route
        b = [-v for v in b]

    theta = math.acos(dot)
    sin_theta = math.sin(theta)
    r1 = math.sin(1 - t) * theta / sin_theta
    r2 = math.sin(t * theta) / sin_theta
    a = [r1 * v for v in a]
    b = [r2 * v for v in b]
    return _vec.vec_normalize([v + w for v, w in zip(a, b)])