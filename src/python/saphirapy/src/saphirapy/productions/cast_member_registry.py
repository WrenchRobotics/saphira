"""Registered external algorithm alternatives for cast-member recasting."""

from typing import Dict, List

from ..syntax_tree.cast_member_option import CastMemberOption


CAST_MEMBER_ALTERNATIVES: Dict[str, List[CastMemberOption]] = {
    "motion_planning_rrt": [
        CastMemberOption(
            name="rrt_connect",
            import_statements=[
                "from brom_drake.motion_planning.algorithms.rrt.connect import RRTConnectPlanner, RRTConnectPlannerConfig"
            ],
            instantiation_code="""rrt_connect_planner = RRTConnectPlanner(
    production.arm,
    production.plant,
    production.scene_graph,
    config=RRTConnectPlannerConfig(),
)""",
            variable_name="rrt_connect_planner",
            method_to_call="plan",
        ),
        CastMemberOption(
            name="bidirectional_rrt",
            import_statements=[
                "from brom_drake.motion_planning.algorithms.rrt.bidirectional import BidirectionalRRTPlanner, BidirectionalRRTPlannerConfig"
            ],
            instantiation_code="""bidirectional_rrt_planner = BidirectionalRRTPlanner(
    production.arm,
    production.plant,
    production.scene_graph,
    config=BidirectionalRRTPlannerConfig(),
)""",
            variable_name="bidirectional_rrt_planner",
            method_to_call="plan",
        ),
        CastMemberOption(
            name="bidirectional_rrt_connect",
            import_statements=[
                "from brom_drake.motion_planning.algorithms.rrt.bidirectional_connect import BidirectionalRRTConnectPlanner, BidirectionalRRTConnectPlannerConfig"
            ],
            instantiation_code="""bidirectional_rrt_connect_planner = BidirectionalRRTConnectPlanner(
    production.arm,
    production.plant,
    production.scene_graph,
    config=BidirectionalRRTConnectPlannerConfig(),
)""",
            variable_name="bidirectional_rrt_connect_planner",
            method_to_call="plan",
        ),
    ],
}


def get_alternatives_for_type(algorithm_type: str) -> List[CastMemberOption]:
    """Return the registered alternatives for ``algorithm_type``."""
    return CAST_MEMBER_ALTERNATIVES.get(algorithm_type, [])