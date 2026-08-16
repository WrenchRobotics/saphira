from dataclasses import dataclass
import numpy as np

@dataclass
class GoalReached:
    """
    A class that represents what it means for a goal to be reached in a Brom production.
    """
    goal_configuration: np.ndarray
    tolerance: float = 1e-5

    def reached_at_any_time(self, configuration_history: np.ndarray) -> bool:
        """
        **Description**

        Check if any configuration in the history is close enough to the goal configuration.

        **Parameters**

        configuration_history: np.ndarray of shape (T, D) 
            where T is the number of time steps and D is the dimensionality of the configuration space.
        """
        distances: np.ndarray = np.linalg.norm(configuration_history - self.goal_configuration, axis=1)
        return np.any(distances < self.tolerance)
            
    def reached_at_end(self, configuration_history: np.ndarray) -> bool:
        """
        **Description**

        Check if the current configuration is close enough to the goal configuration.

        **Parameters**

        configuration_history: np.ndarray of shape (T, D) 
            where T is the number of time steps and D is the dimensionality of the configuration space.
        """
        current_configuration = configuration_history[-1]
        return np.linalg.norm(current_configuration - self.goal_configuration) < self.tolerance