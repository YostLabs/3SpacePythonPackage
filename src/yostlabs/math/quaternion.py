import math
import yostlabs.math.vector as _vec
from yostlabs.math.axes import AxisOrder

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
    inv = quat_inverse(a)
    axis = [b[0], b[1], b[2], 0]
    halfway = quat_mul(a, axis)
    final = quat_mul(halfway, inv)
    return [*final[:3], b[3]]

def quat_inverse(quat: list[float]):
    #Note: While technically negating just the W is rotationally equivalent, this is not a good idea
    #as it will conflict with rotating vectors, which are not rotations, by quaternions
    return [-quat[0], -quat[1], -quat[2], quat[3]]

def quaternion_global_to_local(quat, vec):
    inverse = quat_inverse(quat)
    return quat_rotate_vec(inverse, vec)

def quaternion_local_to_global(quat, vec):
    return quat_rotate_vec(quat, vec)

def quat_rotate_vec(quat: list[float], vec: list[float]):
    inv = quat_inverse(quat)
    tmp = [vec[0], vec[1], vec[2], 0]
    halfway = quat_mul(quat, tmp)
    final = quat_mul(halfway, inv)
    return [final[0], final[1], final[2]]

def quat_from_axis_angle(axis: list[float], angle: float):
    imaginary = math.sin(angle / 2)
    quat = [imaginary * v for v in axis]
    quat.append(math.cos(angle / 2))

    return quat

#There are multiple valid quats that can be returned by this. The intention of this function
#is to be able to rotate an arrow by the quat such that it points the correct direction. The rotation
#of that arrow along its axis may differ though
def quat_from_one_vector(vec: list[float]):
    vec = _vec.vec_normalize(vec)
    perpendicular = _vec.vec_normalize(_vec.vec_cross([0, 0, 1], vec))
    angle = math.acos(_vec.vec_dot([0, 0, 1], vec))
    return quat_from_axis_angle(perpendicular, angle)

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

#Quat is expected in XYZW order
#https://www.euclideanspace.com/maths/geometry/rotations/conversions/quaternionToEuler/quat_2_euler_paper_ver2-1.pdf
def q2ea(in_quat: list[float], order: list[int]) -> list[float]:
    X, Y, Z, W = 0, 1, 2, 3 #Index order helpers
    if len(order) != 3: raise Exception()
    if len(in_quat) != 4: raise Exception()
    out = [0, 0, 0]

    i1, i2, i3 = order
    i1n = (i1+ 1) % 3
    i1nn = (i1n + 1) % 3

    #Find the direction the final axis ends up pointing after the full rotation
    #Note that this vector does not change when the third rotation is being applied.
    v3_rot = [0, 0, 0]
    v3_rot[i3] = 1 
    v3_rot = quat_rotate_vec(in_quat, v3_rot)

    #NOTE: Whenever using asin/acos, ensure the input is in range of -1 <= x <= 1
    #All this math should result in that, but floating point sometimes causes values like 1.0000002 which can cause NANs
    v3_rot = [max(-1, min(1, v)) for v in v3_rot]

    #Can now discover the first 2 rotations
    #This is because the first two rotations determine the direction of the final axis,
    #and each rotation only affects 1 plane, therefore think of it like the first rotation
    #positions the third axis underneath its final spot, and the second rotation swings it up to its final position.
    #These can be calculated using trig and the known axis pattern.

	#Non-Circular, Repeated Axes
    #XZX, YXY, ZYZ
    if (i1 == 0 and i2 == 2 and i3 == 0) or     \
    (i1 == 1 and i2 == 0 and i3 == 1) or        \
    (i1 == 2 and i2 == 1 and i3 == 2):      
        out[0] = math.atan2(v3_rot[i1nn], v3_rot[i1n])
        out[1] = math.acos(v3_rot[i1])
	#Non-Circular, Non-Repeated Axes
    #XZY, YXZ, ZYX
    elif (i1 == 0 and i2 == 2 and i3 == 1) or   \
    (i1 == 1 and i2 == 0 and i3 == 2) or        \
    (i1 == 2 and i2 == 1 and i3 == 0):          
        out[0] = math.atan2(v3_rot[i1nn], v3_rot[i1n])
        out[1] = -math.asin(v3_rot[i1])
	#Circular, Repeated Axes
    #XYX, YZY, ZXZ
    elif (i1 == 0 and i2 == 1 and i3 == 0) or   \
    (i1 == 1 and i2 == 2 and i3 == 1) or        \
    (i1 == 2 and i2 == 0 and i3 == 2):
        out[0] = math.atan2(v3_rot[i1n], -v3_rot[i1nn])
        out[1] = math.acos(v3_rot[i1])
	#Circular, Non-Repeated Axes
    #XYZ, YZX, ZXY
    elif (i1 == 0 and i2 == 1 and i3 == 2) or   \
    (i1 == 1 and i2 == 2 and i3 == 0) or        \
    (i1 == 2 and i2 == 0 and i3 == 1):
        out[0] = math.atan2(-v3_rot[i1n], v3_rot[i1nn])
        out[1] = math.asin(v3_rot[i1]) #Note, the paper incorrectly says -asin
    else:
        raise ValueError("Invalid Order")

    #Now compute the third angle

    #Create a quaternion that applies the first two rotations
    q1_rotation = [0, 0, 0, 0]
    q1_rotation[W] = math.cos(out[0] / 2)
    q1_rotation[i1] = math.sin(out[0] / 2)

    q2_rotation = [0, 0, 0, 0]
    q2_rotation[W] = math.cos(out[1] / 2)
    q2_rotation[i2] = math.sin(out[1] / 2)

    q12 = quat_mul(q1_rotation, q2_rotation)

    #Now apply that quaternion and the original quaternion to the axis that will be rotated by the last axis
    #(Rotating along Axis 3 does not change the position of Axis 3, this is done to obtain an axis that will be rotated
    #so that the difference can be computed to decide how much rotation is needed for the last angle)
    i3n = (i3 + 1) % 3
    v3n = [0, 0, 0]
    v3n[i3n] = 1

    v3_q12 = v3n.copy()
    v3_q = v3n.copy()
    v3_q12 = quat_rotate_vec(q12, v3_q12)
    v3_q = quat_rotate_vec(in_quat, v3_q)

    #Now do trig to figure out how much rotation, and what direction, is needed to rotate v3_q12 to v3_q
    d = _vec.vec_dot(v3_q12, v3_q) #Get angle between the two, ensure in range of acos
    d = max(-1, min(1, d))
    magnitude = math.acos(d)

    #Determine the sign of the rotation
    cross = _vec.vec_cross(v3_q12, v3_q)
    sign = _vec.vec_dot(cross, v3_rot)
    if sign < 0: sign = -1
    else: sign = 1

    out[2] = sign * abs(magnitude)

    return out

def string_order_to_indices(order: str):
    order = order.lower()
    int_order = []
    if len(order) != 3: raise ValueError()
    for c in order:
        if c == 'x': int_order.append(0)
        elif c == 'y': int_order.append(1)
        elif c == 'z': int_order.append(2)
        else: raise ValueError()
    
    return int_order

def quat_to_euler_angles(in_quat: list[float], order: str|list[int], extrinsic=False):
    if isinstance(order, str):
        if 'e' in order.lower():
            extrinsic = True
        elif 'i' in order.lower():
            extrinsic = False
        #Only care about the first 3 chars, the 4th optional char is extrinsic vs intrinsic
        order = string_order_to_indices(order[:3])
    
    #Extrinsic is performing the rotations around the axes of the original fixed system (Rotates around world axes)
    #Intrinsic is performing the rotations around the axes of a coordinate system that rotates with each step (It rotates around the local axes of the rotating object).
    #These two representations are very similar, and even related. An extrinsic rotation is the same as an intrinsic rotation by the same angles, but with the order inverted.
    if extrinsic:
        #Taking advantage of the above info that intrinsic = extrinsic but in reverse and vice versa.
        order[0], order[2] = order[2], order[0]
    
    #Actual conversion, everything else is just setup
    angles = q2ea(in_quat, order)

    if extrinsic:
        #Swap back to the original desired order
        angles[0], angles[2] = angles[2], angles[0]
    
    return angles

def angles_to_quaternion(angles: list[float], order: str, degrees=True, extrinsic=False):
    quat = [0, 0, 0, 1]
    for i in range(len(angles)):
        axis = order[i]
        angle = angles[i]
        if degrees:
            angle = math.radians(angle)
        unit_vec = _vec.axis_to_unit_vector(axis)
        angle_quat = quat_from_axis_angle(unit_vec, angle)
        if extrinsic:
            quat = quat_mul(angle_quat, quat)
        else:
            quat = quat_mul(quat, angle_quat)
    return quat

def quat_from_angles(angles: list[float], order: str, degrees=True, extrinsic=False):
    return angles_to_quaternion(angles, order, degrees=degrees, extrinsic=extrinsic)

def quat_from_euler(angles: list[float], order: list[int], degrees=False, extrinsic=False):
    return angles_to_quaternion(angles, order, degrees=degrees, extrinsic=extrinsic)

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

#-------------------------------DEPRECATED FUNCTIONS-------------------------------

def quaternion_swap_axes(quat: list, old_order: str, new_order: str):
    """
    DEPRECATED: Use AxisOrder class from axes.py instead.
    """
    return quaternion_swap_axes_fast(quat, _vec.parse_axis_string_info(old_order), _vec.parse_axis_string_info(new_order))

def quaternion_swap_axes_fast(quat: list, old_parsed_order: list[list, list, bool], new_parsed_order: list[list, list, bool]):
    """
    DEPRECATED: Use AxisOrder class from axes.py instead.

    Like quaternion_swap_axes but uses the inputs of parsing the axis strings to avoid having to recompute
    the storage types.

    each order should be a sequence of [order, mults, right_handed]
    """
    old_order, old_mults, old_right_handed = old_parsed_order
    new_order, new_mults, new_right_handed = new_parsed_order

    #Undo the old negations
    base_quat = quat.copy()
    for i, mult in enumerate(old_mults):
        base_quat[i] *= mult
    
    #Now swap the positions and apply new multipliers
    new_quat = base_quat.copy()
    for i in range(3):
        new_quat[i] = base_quat[old_order.index(new_order[i])]
        new_quat[i] *= new_mults[i]
    
    if old_right_handed != new_right_handed:
        #Different handed systems rotate opposite directions. So to maintain the same rotation,
        #invert the quaternion
        new_quat = quat_inverse(new_quat)

    return new_quat