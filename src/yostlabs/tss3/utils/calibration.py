import yostlabs.math.quaternion as quat
import yostlabs.math.vector as vec

import numpy as np
from dataclasses import dataclass
import copy

class ThreespaceGradientDescentCalibration:

    @dataclass
    class StageInfo:
        start_vector: int
        end_vector: int
        stage: int
        scale: float

        count: int = 0

    MAX_SCALE = 1000000000
    MIN_SCALE = 1
    STAGES = [
        StageInfo(0, 6, 0, MAX_SCALE),
        StageInfo(0, 12, 1, MAX_SCALE),
        StageInfo(0, 24, 2, MAX_SCALE)
    ]

    #Note that each entry has a positive and negative vector included in this list
    CHANGE_VECTORS = [
        np.array([0,0,0,0,0,0,0,0,0,.0001,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,0,0,0,-.0001,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,0,0,0,0,.0001,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,0,0,0,0,-.0001,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,0,0,0,0,0,.0001], dtype=np.float64),
        np.array([0,0,0,0,0,0,0,0,0,0,0,-.0001], dtype=np.float64), #First 6 only try to change the bias
        np.array([.001,0,0,0,0,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([-.001,0,0,0,0,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,.001,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,-.001,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,0,0,.001,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,0,0,-.001,0,0,0], dtype=np.float64), #Next 6 only try to change the scale
        np.array([0,.0001,0,0,0,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,-.0001,0,0,0,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,.0001,0,0,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,-.0001,0,0,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,.0001,0,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,-.0001,0,0,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,.0001,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,-.0001,0,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,.0001,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,-.0001,0,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,0,.0001,0,0,0,0], dtype=np.float64),
        np.array([0,0,0,0,0,0,0,-.0001,0,0,0,0], dtype=np.float64), #Next 12 only try to change the shear
    ]

    def __init__(self, relative_sensor_orients: list[np.ndarray[float]], no_inverse=False):
        """
        Params
        ------
        relative_sensor_orients : The orientation of the sensor during which each sample is taken if it was tared as if pointing into the screen. 
        The inverse of these will be used to calculate where the axes should be located relative to the sensor
        no_inverse : The relative_sensor_orients will be treated as the sample_rotations
        """
        if no_inverse:
            self.rotation_quats = relative_sensor_orients
        else:
            self.rotation_quats = [np.array(quat.quat_inverse(orient)) for orient in relative_sensor_orients]

    def apply_parameters(self, sample: np.ndarray[float], params: np.ndarray[float]):
        bias = params[9:]
        scale = params[:9]
        scale = scale.reshape((3, 3))
        return scale @ (sample + bias)

    def rate_parameters(self, params: np.ndarray[float], samples: list[np.ndarray[float]], targets: list[np.ndarray[float]]):
        total_error = 0
        for i in range(len(samples)):
            sample = samples[i]
            target = targets[i]

            sample = self.apply_parameters(sample, params)
            
            error = target - sample
            total_error += vec.vec_len(error)
        return total_error

    def generate_target_list(self, origin: np.ndarray):
        targets = []
        for orient in self.rotation_quats:
            new_vec = np.array(quat.quat_rotate_vec(orient, origin), dtype=np.float64)
            targets.append(new_vec)
        return targets

    def __get_stage(self, stage_number: int):
        if stage_number >= len(self.STAGES):
            return None
        #Always get a shallow copy of the stage so can modify without removing the initial values
        return copy.copy(self.STAGES[stage_number])

    def calculate(self, samples: list[np.ndarray[float]], origin: np.ndarray[float], verbose=False, max_cycles_per_stage=1000):
        targets = self.generate_target_list(origin)
        initial_params = np.array([1,0,0,0,1,0,0,0,1,0,0,0], dtype=np.float64)
        stage = self.__get_stage(0)

        best_params = initial_params
        best_rating = self.rate_parameters(best_params, samples, targets)
        count = 0
        while True:
            last_best_rating = best_rating
            params = best_params

            #Apply all the changes to see if any improve the result
            for change_index in range(stage.start_vector, stage.end_vector):
                change_vector = self.CHANGE_VECTORS[change_index]
                new_params = params + (change_vector * stage.scale)
                rating = self.rate_parameters(new_params, samples, targets)

                #A better rating, store it
                if rating < best_rating:
                    best_params = new_params
                    best_rating = rating
            
            if verbose and count % 100 == 0:
                print(f"Round {count}: {best_rating=} {stage=}")
            
            #Decide if need to go to the next stage or not
            count += 1
            stage.count += 1
            if stage.count >= max_cycles_per_stage:
                stage = self.__get_stage(stage.stage + 1)
                if stage is None:
                    if verbose: print("Done from reaching count limit")
                    break
                if verbose: print("Going to next stage from count limit")
                
            if best_rating == last_best_rating: #The rating did not improve
                if stage.scale == self.MIN_SCALE: #Go to the next stage since can't get any better in this stage!
                    stage = self.__get_stage(stage.stage + 1)
                    if stage is None:
                        if verbose: print("Done from exhaustion")
                        break
                    if verbose: print("Going to next stage from exhaustion")
                else:   #Reduce the size of the changes to hopefully get more accurate tuning
                    stage.scale *= 0.1  
                    if stage.scale < self.MIN_SCALE:
                        stage.scale = self.MIN_SCALE
            else: #Rating got better! To help avoid falling in a local minimum, increase the size of the change to see if that could make it better
                stage.scale *= 1.1
        
        if verbose:
            print(f"Final Rating: {best_rating}")
            print(f"Final Params: {best_params}")

        return best_params
    
def fibonacci_sphere(samples=1000):
    points = []
    phi = np.pi * (3. - np.sqrt(5.))  # golden angle

    for i in range(samples):
        y = 1 - (i / float(samples - 1)) * 2  # y goes from 1 to -1
        radius = np.sqrt(1 - y * y)
        theta = phi * i
        x = np.cos(theta) * radius
        z = np.sin(theta) * radius
        points.append((x, y, z))

    return np.array(points)

class ThreespaceSphereCalibration:

    def __init__(self, max_comparison_points=500):
        self.buffer = 0.1
        self.test_points = fibonacci_sphere(samples=max_comparison_points)
        self.clear()

    def process_point(self, raw_mag: list[float]):
        if len(self.samples) == 0:
            self.samples = np.array([raw_mag])
            return True
        
        raw_mag = np.array(raw_mag, dtype=np.float64)
        new_len = np.linalg.norm(raw_mag)

        avg_len = np.linalg.norm(self.samples, axis=1)
        avg_len = (avg_len + new_len) / 2 * self.buffer

        dist = np.linalg.norm(self.samples - raw_mag, axis=1)
        if np.any(dist < avg_len):
            return False
        
        self.samples = np.concatenate((self.samples, [raw_mag]))
        self.__update_density(raw_mag / new_len)
        return True
    
    def __update_density(self, normalized_point: np.ndarray):
        #First check to see if the new point is the closest point for any of the previous points
        dots = np.sum(self.test_points * normalized_point, axis=1)
        self.closest_dot = np.maximum(self.closest_dot, dots)
        self.largest_delta_index = np.argmin(self.closest_dot)
        self.largest_delta = np.rad2deg(np.acos(self.closest_dot[self.largest_delta_index]))

    def clear(self):
        self.samples = np.array([])
        self.closest_dot = np.array([-1] * len(self.test_points))
        self.largest_delta = 180
        self.largest_delta_index = 0

    @property
    def sparsest_vector(self):
        return self.test_points[self.largest_delta_index]

    def calculate(self):
        """
        Returns matrix and bias
        """
        comp_A, comp_b, comp_d = ThreespaceSphereCalibration.alternating_least_squares(np.array(self.samples))
        comp_A, comp_b = ThreespaceSphereCalibration.make_calibration_params(comp_A, comp_b, comp_d, 1)

        comp_scale: float = comp_A[0][0] + comp_A[1][1] + comp_A[2][2]
        comp_scale /= 3

        comp_A /= comp_scale

        return comp_A.flatten().tolist(), comp_b.tolist()

    @staticmethod
    def alternating_least_squares(data: np.ndarray[np.ndarray]) -> tuple[np.ndarray, np.ndarray, float]:
        n = 3
        m = len(data)

        #Step 1
        noise_variance = 0.1
        sigma2 = noise_variance ** 2
        rotini = data.T
        rotini2 = rotini ** 2

        T = np.zeros((5,n,m))
        T[0,:,:] = 1
        T[1,:,:] = rotini
        T[2,:,:] = rotini2 - sigma2
        T[3,:,:] = rotini2 * rotini - 3 * rotini * sigma2
        T[4,:,:] = rotini2 * rotini2 - 6 * rotini2 * sigma2 + 3 * sigma2 * sigma2

        #Step 2
        one = np.ones((n+1,))
        i = np.arange(1, n+1)
        i = np.append(i, 0)

        m1 = np.outer(i.T, one)
        m2 = np.outer(one.T, i)

        M = np.array([ThreespaceSphereCalibration.vec_s(m1), ThreespaceSphereCalibration.vec_s(m2)])

        #Step 3
        nb = int((n + 1) * n / 2 + n + 1)
        R = np.zeros((nb,nb,n), dtype=np.int32)
        
        for p in range(nb):
            for q in range(p, nb):
                for i in range(1, n+1):
                    R[p,q,i-1] = int(M[0,p] == i) + int(M[1,p] == i) + int(M[0,q] == i) + int(M[1,q] == i)

        #Step 4
        nals = np.zeros((nb, nb))
        for p in range(nb):
            for q in range(p, nb):
                sum = 0
                for l in range(m):
                    prod = 1
                    for i in range(n):
                        prod *= T[R[p,q,i],i,l]
                    sum += prod
                nals[p,q] = sum

        #Step 5
        D2 = [i * (i + 1) / 2 for i in range(1, n+1)]
        D = [d for d in range(1, (n+1) * n // 2 + 1) if not d in D2]

        #Step 6
        menorah_als = np.zeros((nb, nb))
        
        for p in range(nb):
            for q in range(p, nb):
                coeff = 2
                if p + 1 in D and q + 1 in D:
                    coeff = 4
                elif not p + 1 in D and not q + 1 in D:
                    coeff = 1
                menorah_als[p,q] = coeff * nals[p,q]
        
        # Fill the lower triangle with the upper triangle values
        i_lower, j_lower = np.tril_indices(nb, k=-1) 
        menorah_als[i_lower, j_lower] = menorah_als[j_lower, i_lower]

        #Step 7
        eigenmat = menorah_als

        #It is unclear if this is correct, there are differences in sign and positions
        eigenvalues, eigenvectors = np.linalg.eig(eigenmat)
        eigenvectors = eigenvectors.T

        #Looks like this section gets the eigen vector with the largest eigen value?
        combinedmatr = [[abs(eigenvalues[i]), eigenvectors[i]] for i in range(len(eigenvalues))]
        combinedmatr.sort(key=lambda a: a[0], reverse=True)

        bals = combinedmatr[-1][1]

        #Step 8 : ensure normalized
        bals = bals / np.linalg.norm(bals)

        #Step 9:
        triangle = n*(n+1)//2
        A = ThreespaceSphereCalibration.inv_vec_s(bals[:triangle])
        b = bals[triangle:nb-1]
        d = bals[-1]

        return A, b, d

    @staticmethod
    def make_calibration_params(Q: np.ndarray, u: np.ndarray, k: float, H_m: float) -> tuple[np.ndarray,np.ndarray]:
        pa = np.linalg.inv(Q)
        pb = u.T
        b = np.dot(pa, pb) * 0.5

        eigenvalues, V = np.linalg.eig(Q)
        D = np.diag(eigenvalues)

        vu_prod = np.dot(V.T, u.T)
        p1a = np.dot(vu_prod.T, np.linalg.inv(D))
        p1b = np.dot(p1a, vu_prod)
        p1 = p1b - (4 * k)

        alpha = 4 * (H_m ** 2) / p1

        aD = np.diag(abs(alpha * eigenvalues) ** 0.5)

        A = np.dot(np.dot(V, aD), V.T)
        
        return A, b

    @staticmethod
    def vec_s(matrix: np.ndarray):
        rows, cols = np.tril_indices(matrix.shape[0])
        return matrix[rows, cols]

    @staticmethod
    def inv_vec_s(vec: np.ndarray):
        #Its unclear if this function works as intended. But this is how the suite does it.
        size = int((-1 + (1 + 8 * len(vec)) ** 0.5) / 2)
        matr = np.zeros((size,size))
        base = 0
        for i in range(size):
            for j in range(i):
                matr[i,j] = vec[base + j]
                matr[j,i] = vec[base + j]
            matr[i,i] = vec[base + i]
            base += i + 1
        
        return matr

    @property
    def num_points(self):
        return len(self.samples)
