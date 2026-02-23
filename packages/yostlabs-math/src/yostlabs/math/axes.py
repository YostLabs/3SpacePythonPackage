"""Module for working with axis orders."""


class AxisOrder:
    """
    Represents sensor axis order configuration.
    
    Notation Conventions:
    ---------------------
    XYZ Notation: 'xyz', '-xyz', 'zyx', etc.
        - Use x, y, z for axis names
        - Prefix '-' for negative direction
        - Order specifies which physical axis maps to each position
        
    Compass Notation: 'ned', 'enu', 'wsu', etc.
        - E/W = X axis (East/West, W is negative)
        - U/D = Y axis (Up/Down, D is negative) 
        - N/S = Z axis (North/South, S is negative)
        - Common: NED (North-East-Down), ENU (East-North-Up)
    
    Attributes:
        order (list[int]): Axis mapping [0-2] where 0=X, 1=Y, 2=Z
        multipliers (list[int]): Direction multipliers, 1 or -1
        is_right_handed (bool): True if right-handed coordinate system
    
    Examples:
        >>> axis = AxisOrder('xyz')
        >>> axis.to_xyz_string()  # 'xyz'
        >>> axis.to_compass_string()  # 'eun'
        >>> axis.order  # [0, 1, 2]
        >>> axis.is_right_handed  # True
    """
    
    # Compass mapping
    _COMPASS_TO_AXIS = {'e': 0, 'w': 0, 'u': 1, 'd': 1, 'n': 2, 's': 2}
    _NEGATIVE_COMPASS = "wds"
    _AXIS_TO_COMPASS = [['e', 'w'], ['u', 'd'], ['n', 's']]  # [axis][positive/negative]
    
    def __init__(self, axis_string: str):
        """Initialize from axis string ('xyz', 'ned', etc.)."""
        axis_string = axis_string.lower().strip()
        self.order, self.multipliers = self._parse_axis_string(axis_string)
        self.is_right_handed = self._compute_handedness(self.order, self.multipliers)
    
    @staticmethod
    def _parse_axis_string(axis: str) -> tuple[list[int], list[int]]:
        """Parse axis string into order and multipliers."""
        order = [0, 1, 2]
        multipliers = [1, 1, 1]
        
        if any(c in axis for c in ['x', 'y', 'z']):  # XYZ notation
            index = 0
            for c in axis:
                if c == '-':
                    multipliers[index] = -1
                else:
                    order[index] = ord(c) - ord('x')
                    index += 1
        else:  # Compass notation
            for i, c in enumerate(axis):
                order[i] = AxisOrder._COMPASS_TO_AXIS[c]
                if c in AxisOrder._NEGATIVE_COMPASS:
                    multipliers[i] = -1
        
        return order, multipliers
    
    @staticmethod
    def _compute_handedness(order: list[int], multipliers: list[int]) -> bool:
        """Compute if coordinate system is right-handed."""
        num_swaps = sum(1 for i in range(3) if i != order[i])
        right_handed = (num_swaps == 2)
        if multipliers.count(-1) & 1:  # Odd negations flip handedness
            right_handed = not right_handed
        return right_handed
    
    def swap_to(self, new_order: 'AxisOrder', vector: list[float], rotational: bool = False) -> list[float]:
        swapped = swap_vector_axes(vector, self, new_order, negate_on_handedness_change=rotational)
        # Copy any additional elements (e.g. for quaternions)
        for i in range(3, len(vector)):
            swapped.append(vector[i])
        return swapped

    def to_xyz_string(self, include_plus: bool = False) -> str:
        """Convert to XYZ notation ('xyz', '-xyz', etc.)."""
        result = []
        for i in range(3):
            if self.multipliers[i] < 0:
                result.append('-')
            elif include_plus:
                result.append('+')
            result.append('xyz'[self.order[i]])
        return ''.join(result)
    
    def to_compass_string(self) -> str:
        """Convert to compass notation ('ned', 'enu', etc.)."""
        result = []
        for i in range(3):
            idx = 0 if self.multipliers[i] >= 0 else 1
            result.append(self._AXIS_TO_COMPASS[self.order[i]][idx])
        return ''.join(result)
    
    def __str__(self) -> str:
        return self.to_xyz_string()
    
    def __repr__(self) -> str:
        return f"AxisOrder('{self.to_xyz_string()}')"
    
    def __eq__(self, other) -> bool:
        if isinstance(other, str):
            try:
                other = AxisOrder(other)
            except (ValueError, TypeError):
                return False
        if not isinstance(other, AxisOrder):
            return False
        return self.order == other.order and self.multipliers == other.multipliers
    
    def __hash__(self) -> int:
        return hash((tuple(self.order), tuple(self.multipliers)))
    
    @classmethod
    def from_order_and_multipliers(cls, order: list[int], multipliers: list[int]) -> 'AxisOrder':
        """Create from explicit order and multipliers lists."""
        axis_str = ''.join(('-' if multipliers[i] < 0 else '') + 'xyz'[order[i]] for i in range(3))
        return cls(axis_str)


def swap_vector_axes(vec: list[float], current_order: AxisOrder | str, new_order: AxisOrder | str, 
                     negate_on_handedness_change: bool = False) -> list[float]:
    """
    Swap the axes of a 3D vector from one axis order to another.
    
    Parameters
    ----------
    vec : list[float]
        The 3-element vector to transform
    current_order : AxisOrder or str
        Current axis order of the vector
    new_order : AxisOrder or str
        Target axis order for the vector
    negate_on_handedness_change : bool, optional
        If True, negate the vector when handedness changes. This is required for
        vectors that represent rotational quantities (like gyro rates) but not for
        positional quantities (like accelerations). Default is False.
    
    Returns
    -------
    list[float]
        The vector transformed to the new axis order
    
    Examples
    --------
    >>> vec = [1.0, 2.0, 3.0]  # xyz
    >>> swap_vector_axes(vec, 'xyz', 'zxy')  # [3.0, 1.0, 2.0]
    >>> swap_vector_axes([1, 0, 0], 'xyz', '-xyz')  # [-1, 0, 0]
    """
    if isinstance(current_order, str):
        current_order = AxisOrder(current_order)
    if isinstance(new_order, str):
        new_order = AxisOrder(new_order)
    
    # Undo the old negations
    base_vec = [vec[i] * current_order.multipliers[i] for i in range(3)]
    
    # Swap positions and apply new multipliers
    new_vec = [0.0, 0.0, 0.0]
    for i in range(3):
        new_vec[i] = base_vec[current_order.order.index(new_order.order[i])]
        new_vec[i] *= new_order.multipliers[i]

    # If handedness changed and negate_on_handedness_change is True, negate the vector
    if negate_on_handedness_change and (current_order.is_right_handed != new_order.is_right_handed):
        new_vec = [-v for v in new_vec]

    return new_vec


def swap_quaternion_axes(quat: list[float], current_order: AxisOrder | str, new_order: AxisOrder | str) -> list[float]:
    """
    Swap the axes of a quaternion (XYZW format) from one axis order to another.
    
    Parameters
    ----------
    quat : list[float]
        The 4-element quaternion in XYZW format to transform
    current_order : AxisOrder or str
        Current axis order of the quaternion
    new_order : AxisOrder or str  
        Target axis order for the quaternion
    
    Returns
    -------
    list[float]
        The quaternion transformed to the new axis order
    
    Notes
    -----
    When the handedness changes, the quaternion is inverted to maintain the same
    rotation direction (since different-handed systems rotate in opposite directions).
    """
    return swap_vector_axes(quat[:3], current_order, new_order, negate_on_handedness_change=True) + [quat[3]]

def axis_to_unit_vector(axis: str):
    if isinstance(axis, str):
        axis = axis.lower()
    if axis == 'x' or axis == 0: return [1, 0, 0]
    if axis == 'y' or axis == 1: return [0, 1, 0]
    if axis == 'z' or axis == 2: return [0, 0, 1]