from brom_drake.watchers.diagram_target import DiagramTarget
from brom_drake.watchers.diagram_watcher import DiagramWatcher
from brom_drake.productions.types.motion_planning.offline.kinematic import KinematicMotionPlanningProduction as OfflineKinematicMotionPlanningProduction
from dataclasses import dataclass
from pathlib import Path
import numpy as np
from typing import Dict, List, Tuple

class OfflineKinematicMotionPlanningEvaluator:
    """
    Description
    -----------

    An evaluator for offline kinematic motion planning productions. 
    This evaluator checks if the produced motion plan is valid and if it reaches the goal configuration. (or gets close)
    
    """
    def __init__(
        self,
        production: OfflineKinematicMotionPlanningProduction,
        state_to_evaluate: DiagramTarget|None = None,
    ):
        # Save the production and derive all other fields from it
        self.production: OfflineKinematicMotionPlanningProduction = production

        # The start and goal configurations can be derived from the production's arm and its initial and final positions
        self.start_configuration: np.ndarray = self.production.start_configuration
        self.goal_configuration: np.ndarray = self.production.goal_configuration

        # The state to evaluate can be derived from the production's watcher and its target system and port
        if state_to_evaluate is None:
            # If no state to evaluate is provided, we will use the production's watcher to find the target system and port
            self.state_to_evaluate: DiagramTarget = DiagramTarget(
                name=self.production.station.get_name(),
                ports=["measured_arm_position"]
            )
        else:
            self.state_to_evaluate: DiagramTarget = state_to_evaluate

        # Cache station objects used for FK when evaluating Cartesian path metrics.
        self._fk_plant = self.production.station.plant
        self._fk_context = self._fk_plant.CreateDefaultContext()
        self._fk_arm_model_instance = self.production.station.arm

        # Prefer the station-defined EE frame (includes any tool offset), then fall back.
        if hasattr(self.production.station, "arm_ee_frame"):
            self._fk_end_effector_frame = self.production.station.arm_ee_frame
        else:
            try:
                self._fk_end_effector_frame = self._fk_plant.GetFrameByName(
                    "end_effector",
                    self._fk_arm_model_instance,
                )
            except RuntimeError:
                self._fk_end_effector_frame = self._fk_plant.GetFrameByName(
                    "tool0",
                    self._fk_arm_model_instance,
                )
        

    def discover_all_raw_data_files(self) -> List[Path]:
        """
        Description
        -----------

        Discover all raw data files in the brom watcher directory. 
        This can be used to find the configuration history file and any other relevant data files for evaluation.
        """
        # Setup
        raw_data_dir = self.production.watcher.options.base_directory / "raw_data"

        # For simplicity, we will assume that all raw data files are stored as .npy files in the raw_data directory. 
        raw_data_files = list(raw_data_dir.glob("*.npy"))  # Assuming raw data files are stored as .npy files
        return raw_data_files

    def evaluate(self, tolerance: float = 1e-5) -> Dict[str, float]:
        return {
            "Final Configuration Error": self.calculate_final_configuration_error(self.configuration_history),
            "Joint Space Length of Plan": self.calculate_joint_space_length_of_plan(self.configuration_history),
            "Cartesian Space Length of Plan": self.calculate_cartesian_space_length_of_plan(self.configuration_history),
        }

    def extract_system_and_port_name_from_file_name(self, file_name: str) -> Tuple[str, str]:
        """
        Description
        -----------

        Extract the system name and port name from a given file name. 
        This can be used to identify which system and port the data in the file corresponds to.

        Note: The system name and port name are expected to be encoded in the file name as follows:

        "system_<system_name>_port_<port_name>_data.npy"
        """
        # For simplicity, we will assume that the file name is in the format "system_<system_name>_port_<port_name>_data.npy"
        base_name = Path(file_name).stem  # Remove the .npy extension
        
        # Find the part that is "system" and the part that is "port"
        if "system" not in base_name:
            raise ValueError(f"File name {file_name} does not contain 'system' part")
        
        system_index = base_name.index("system")

        if "port" not in base_name:
            raise ValueError(f"File name {file_name} does not contain 'port' part")
    
        port_index = base_name.index("port")

        # Extract the system name and port name based on their positions in the file name
        safe_system_name  = base_name[len("system_") + system_index : port_index - len("_")]
        safe_port_name = base_name[port_index + len("port_") : -len("_data")]
        
        system_name = safe_system_name
        port_name = safe_port_name
        return system_name, port_name

    def _load_data_for_evaluation(self):
        """
        Description
        -----------

        Load the necessary data for evaluation from the brom watcher directory. 
        This includes the configuration history of the motion plan.
        """
        # Get the watchers for the diagram target
        port_watcher_for_target_port = self.production.watcher.get_port_watcher(
            system_name=self.state_to_evaluate.name,
            port_name=self.state_to_evaluate.ports[0]
        )

        # For simplicity, we will assume that the configuration history is stored in a file called "configuration_history.npy" in the brom watcher directory.
        arm_position_history_file = port_watcher_for_target_port.file_manager.raw_data_file_path(
            output_port=port_watcher_for_target_port.port,
        )
        if not arm_position_history_file.exists():
            raise FileNotFoundError(f"Configuration history file not found at {arm_position_history_file}")
        
        configuration_history = np.load(arm_position_history_file)
        return configuration_history

    def calculate_final_configuration_error(self, configuration_history: np.ndarray) -> float:
        """
        Description
        -----------

        Calculate the error between the final configuration in the configuration history and the goal configuration.
        """
        configuration_samples = self._configuration_history_as_samples(configuration_history)
        final_configuration = configuration_samples[-1]
        error = np.linalg.norm(final_configuration - self.goal_configuration)
        return error

    def calculate_joint_space_length_of_plan(self, configuration_history: np.ndarray) -> float:
        """
        Description
        -----------

        Calculate the joint space length of the motion plan based on the configuration history.
        This is done by summing the Euclidean distances between consecutive configurations in the history.
        """
        configuration_samples = self._configuration_history_as_samples(configuration_history)
        length = 0.0
        for i in range(1, len(configuration_samples)):
            length += np.linalg.norm(
                configuration_samples[i] - configuration_samples[i - 1]
            )
        return length

    def calculate_cartesian_space_length_of_plan(self, configuration_history: np.ndarray) -> float:
        """
        Description
        -----------

        Calculate the Cartesian space length of the motion plan based on the configuration history.
        This is done by summing the Euclidean distances between consecutive end-effector positions in the history.
        """
        configuration_samples = self._configuration_history_as_samples(configuration_history)
        length = 0.0
        for i in range(1, len(configuration_samples)):
            end_effector_position_current = self.get_end_effector_position(
                configuration_samples[i]
            )
            end_effector_position_previous = self.get_end_effector_position(
                configuration_samples[i - 1]
            )
            length += np.linalg.norm(end_effector_position_current - end_effector_position_previous)
        return length

    def _configuration_history_as_samples(self, configuration_history: np.ndarray) -> np.ndarray:
        """
        Description
        -----------

        Normalize the history to shape (num_samples, num_joints).
        Accepts either (num_samples, num_joints) or (num_joints, num_samples).
        """
        history = np.asarray(configuration_history)
        if history.ndim != 2:
            raise ValueError(
                f"Expected 2D configuration history, got shape {history.shape}."
            )

        num_joints = int(np.asarray(self.start_configuration).reshape(-1).size)
        if history.shape[1] == num_joints:
            return history
        if history.shape[0] == num_joints:
            return history.T

        raise ValueError(
            "Configuration history shape does not match robot joint count "
            + f"({num_joints}): got {history.shape}."
        )

    def get_end_effector_pose(self, configuration: np.ndarray):
        """
        Description
        -----------

        Compute the end-effector pose in the world frame for a given arm configuration
        using the UR10e station's MultibodyPlant forward kinematics.
        """
        q = np.asarray(configuration).reshape(-1)
        self._fk_plant.SetPositions(self._fk_context, self._fk_arm_model_instance, q)
        return self._fk_plant.CalcRelativeTransform(
            self._fk_context,
            self._fk_plant.world_frame(),
            self._fk_end_effector_frame,
        )

    def get_end_effector_position(self, configuration: np.ndarray) -> np.ndarray:
        """
        Description
        -----------

        Compute the end-effector position in the world frame for a given arm
        configuration using UR10e station forward kinematics.
        """
        X_WE = self.get_end_effector_pose(configuration)
        return np.asarray(X_WE.translation()).reshape(-1)