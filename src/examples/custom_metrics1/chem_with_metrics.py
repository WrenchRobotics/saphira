"""
chem_with_metrics.py
Description:

    The ChemLab1 motion planning example, with custom metrics that Saphira reports and compares
    on every pull request. It shows the three ways a metric can get its value:

    1. From the production's own objects (the planned trajectory).
    2. From data the brom_drake watcher recorded during the simulation (the arm's joint positions).
    3. From the production's definition (its goal configuration).

    It also refines one of Saphira's built-in metrics by giving it a tolerance.

    Nothing here imports saphirapy. Run it on its own to see the metrics it reports:

        python src/examples/custom_metrics1/chem_with_metrics.py
"""

import numpy as np
from pydrake.all import Simulator

# Internal imports
from brom_drake.motion_planning.algorithms.rrt.connect import (
    RRTConnectPlannerConfig,
    RRTConnectPlanner,
)
from brom_drake.productions.motion_planning.offline import ChemLab1

# --- Custom metrics --------------------------------------------------------------------------
#
# Saphira looks for these two top-level names. See docs/custom-metrics.md.

# How Saphira should compare each metric between your pull request and your main branch.
# Every key is optional: "better" ("higher" or "lower"), "unit", and "tolerance" (the largest
# change, in the metric's own units, that still counts as unchanged).
SAPHIRA_METRICS = {
    "Goal Reached": {"better": "higher"},
    "Planned Trajectory Duration": {"better": "lower", "unit": "s", "tolerance": 0.05},
    "Peak Joint Speed": {"better": "lower", "unit": "rad/s", "tolerance": 0.01},
    # A built-in metric that Saphira computes itself. Defining it here keeps Saphira's value but
    # treats changes smaller than 1e-4 as noise.
    "Final Configuration Error": {"better": "lower", "tolerance": 1e-4},
}

# How close, in joint space, the arm must end to its goal for "Goal Reached".
GOAL_REACHED_TOLERANCE = 1e-2


def saphira_metrics(production) -> dict[str, float]:
    """
    Description:

        Saphira calls this once for every case it runs, after main() returns, with the production
        that main() built and simulated. Return a dict from metric name to number.

        Saphira also runs this script with other productions (such as ShelfPlanning1) and other
        planners. A metric that cannot be computed for a case is left out of the dict, not set to
        a placeholder.
    """
    metrics = {}

    # 1. From the production's own objects: how long the planner's trajectory takes to execute.
    plan_dispenser = getattr(production, "plan_dispenser", None)
    trajectory = getattr(plan_dispenser, "planned_trajectory", None)
    if trajectory is not None:
        metrics["Planned Trajectory Duration"] = trajectory.end_time() - trajectory.start_time()

    # 2. From data the watcher recorded during the simulation.
    recorded = recorded_arm_positions(production)
    if recorded is not None:
        times, positions = recorded

        # 3. Against the production's goal. True and False are reported as 1 and 0.
        final_error = np.linalg.norm(positions[:, -1] - production.goal_configuration)
        metrics["Goal Reached"] = bool(final_error < GOAL_REACHED_TOLERANCE)

        # The fastest any joint moved between two samples.
        steps = np.diff(times)
        moving = steps > 0
        if np.any(moving):
            speeds = np.abs(np.diff(positions, axis=1)[:, moving]) / steps[moving]
            metrics["Peak Joint Speed"] = float(np.max(speeds))

    return metrics


def recorded_arm_positions(production):
    """
    Description:

        The arm's measured joint positions as the watcher recorded them, as (times, positions)
        with positions shaped (number of joints, number of samples). None when the production
        was built without a watcher, or the watcher did not record the arm.
    """
    watcher = getattr(production, "watcher", None)
    if watcher is None or watcher.diagram_context is None:
        return None

    try:
        port_watcher = watcher.get_port_watcher(
            system_name=production.station.get_name(),
            port_name="measured_arm_position",
        )
    except (AttributeError, ValueError, KeyError):
        return None

    # The data is read from the simulation's logs in memory, so this works the same when the
    # script runs on its own and when Saphira runs it.
    data = port_watcher.get_data_dictionary(watcher.diagram_context)
    times = port_watcher.get_timing_array(watcher.diagram_context)
    positions = next(iter(data.values()))
    if positions.shape[1] == 0:
        return None
    return times, positions


# --- The production --------------------------------------------------------------------------
#
# The same as src/examples/motion_planning1/chem.py, except that main() returns the production so
# that running this file on its own can print the metrics.


def main(meshcat_port_number: int = 7001):
    # Setup
    if meshcat_port_number < 0:
        meshcat_port_number = None  # Use None for CI

    # Create the production. Saphira finds it by this variable name, `production`.
    production = ChemLab1(
        meshcat_port_number=meshcat_port_number,  # Use None for CI
    )

    # Create a planner object which will be used to plan the motion
    config = RRTConnectPlannerConfig(
        steering_step_size=0.1,
        prob_sample_goal=0.03,
        max_iterations=int(1e5),
        convergence_threshold=1e-3,
        debug_flag=False,
    )
    planner2 = RRTConnectPlanner(
        production.arm,
        production.plant,
        production.scene_graph,
        config=config,
    )

    # The watcher records the arm's positions, which saphira_metrics() reads.
    diagram, diagram_context = production.easy_cast_and_build(
        planner2.plan,
        with_watcher=True,
    )

    print("Simulating...")

    # Simulate the diagram
    simulator = Simulator(diagram, diagram_context)
    simulator.set_target_realtime_rate(1.0)

    # Run simulation
    simulator.Initialize()
    simulator.AdvanceTo(0.1)
    planned_trajectory = production.plan_dispenser.planned_trajectory
    print(f"Expected end time of trajectory: {planned_trajectory.end_time()}")

    # Advance to the end of the sim
    simulator.AdvanceTo(planned_trajectory.end_time() + 1.0)

    return production


if __name__ == "__main__":
    # Try the metrics without Saphira.
    print(saphira_metrics(main()))
