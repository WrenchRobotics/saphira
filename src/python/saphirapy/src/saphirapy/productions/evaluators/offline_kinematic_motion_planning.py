from dataclasses import dataclass
from pathlib import Path
import numpy as np
from typing import List, Tuple

@dataclass
class OfflineKinematicMotionPlanningEvaluator:
    """
    Description
    -----------

    An evaluator for offline kinematic motion planning productions. 
    This evaluator checks if the produced motion plan is valid and if it reaches the goal configuration. (or gets close)
    
    """
    brom_watcher_dir: Path
    start_configuration: np.ndarray
    goal_configuration: np.ndarray
    tolerance: float = 1e-5

    def discover_all_raw_data_files(self) -> List[Path]:
        """
        Description
        -----------

        Discover all raw data files in the brom watcher directory. 
        This can be used to find the configuration history file and any other relevant data files for evaluation.
        """
        # Setup
        raw_data_dir = self.brom_watcher_dir / "raw_data"

        # For simplicity, we will assume that all raw data files are stored as .npy files in the raw_data directory. 
        raw_data_files = list(raw_data_dir.glob("*.npy"))  # Assuming raw data files are stored as .npy files
        return raw_data_files

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
        safe_system_name  = file_name.stem[len("system_") + system_index : port_index - len("_")]
        safe_port_name = file_name.stem[port_index + len("port_") : -len("_data")]

        
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
        # For simplicity, we will assume that the configuration history is stored in a file called "configuration_history.npy" in the brom watcher directory.
        configuration_history_path = self.brom_watcher_dir / "configuration_history.npy"
        if not configuration_history_path.exists():
            raise FileNotFoundError(f"Configuration history file not found at {configuration_history_path}")
        
        configuration_history = np.load(configuration_history_path)
        return configuration_history