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

def vec_is_right_handed(order: str, negations: list[bool]):
    right_handed = order.lower() in ("xzy", "yxz", "zyx")
    for i in range(3):
        if negations[i]:
            right_handed = not right_handed
    return right_handed

def axis_to_unit_vector(axis: str):
    axis = axis.lower()
    if axis == 'x' or axis == 0: return [1, 0, 0]
    if axis == 'y' or axis == 1: return [0, 1, 0]
    if axis == 'z' or axis == 2: return [0, 0, 1]