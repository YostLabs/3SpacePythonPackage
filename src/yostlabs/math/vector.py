def vec_len(vector: list[float|int]):
    return sum(v ** 2 for v in vector) ** 0.5

def vec_dot(a: list[float], b: list[float]):
    return sum(a[i] * b[i] for i in range(len(a)))

def vec_cross(a: list[float], b: list[float]):
    cross = [0, 0, 0]
    cross[0] = a[1] * b[2] - a[2] * b[1]
    cross[1] = a[2] * b[0] - a[0] * b[2]
    cross[2] = a[0] * b[1] - a[1] * b[0]
    return cross

def vec_normalize(vec: list[float]):
    l = vec_len(vec)
    if l == 0:
        return vec
    return [v / l for v in vec]

def vec_is_right_handed(order: str, negations: list[bool] = None):
    order = order.lower()
    if negations is None: #Must build the negation list
        base_order = order.replace('-', '')
        num_negations = order.count('-')
    else:
        base_order = order
        num_negations = sum(negations)

    right_handed = base_order in ("xzy", "yxz", "zyx")
    if num_negations & 1: #Odd number of negations causes handedness to swap
        right_handed = not right_handed
    
    return right_handed

def axis_to_unit_vector(axis: str):
    axis = axis.lower()
    if axis == 'x' or axis == 0: return [1, 0, 0]
    if axis == 'y' or axis == 1: return [0, 1, 0]
    if axis == 'z' or axis == 2: return [0, 0, 1]

def parse_axis_string(axis: str):
    """
    Given an axis order string, convert it to an array representing the axis order and negations/multipliers
    """
    axis = axis.lower()
    order = [0, 1, 2]
    multipliers = [1, 1, 1]
    if 'x' in axis: #Using XYZ notation
        index = 0
        for c in axis:
            if c == '-':
                multipliers[index] = -1
                continue
            order[index] = ord(c) - ord('x')
            index += 1
    else:           #Using N/S E/W U/D notation
        axis_lookup = {'e': 0, 'w': 0, 'u': 1, 'd': 1, 'n': 2, 's': 2}
        negative_axes = "wds"
        for i, c in enumerate(axis):
            order[i] = axis_lookup[c]
            if c in negative_axes:
                multipliers[i] = -1
    
    return order, multipliers

def parse_axis_string_info(axis: str):
    order, mult = parse_axis_string(axis)
    right_handed = axis_is_righthanded(order, mult)
    return [order, mult, right_handed]

def axis_is_righthanded(order: list[int], negations: list[int]):
    num_swaps = 0
    for i in range(3):
        if i != order[i]:
            num_swaps += 1
    
    right_handed = num_swaps == 2 #Defaults to left handed, but if a singular swap occured, the handedness swaps
    if negations.count(-1) & 1: #Odd number of negations causes handedness to swap
        right_handed = not right_handed
    
    return right_handed