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