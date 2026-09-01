from .productions.relationships import RELATED_PRODUCTIONS

def identify_algorithm_type(class_name: str) -> str:
    """
    Identify the algorithm type from a class name.

    Maps algorithm class names to their algorithm types, which are used as keys
    in the CAST_MEMBER_ALTERNATIVES registry.

    Args:
        class_name: str
            The class name of the algorithm (e.g., "RRTConnectPlanner", "PRMStarPlanner")

    Returns:
        str: The algorithm type (e.g., "motion_planning_rrt", "grasp_planning")

    Raises:
        ValueError: If the class name is not recognized
    """
    # Motion planning algorithms
    if class_name in ("RRTConnectPlanner", "PRMStarPlanner", "BiRRTPlanner"):
        return "motion_planning_rrt"

    # Grasp planning algorithms
    if class_name in ("GraspPlannerDex", "GraspPlannerSuction"):
        return "grasp_planning"

    # If we don't recognize the class, raise an error
    raise ValueError(f"Unknown algorithm class: {class_name}")
def find_all_alternative_productions(production_class: type) -> list[type]:
    """
    **Description**

    Given a production class, find all related alternative productions based on the RELATED_PRODUCTIONS mapping.

    **Arguments**

    production_class: type
        The production class for which to find alternatives.

    **Returns**
    alternatives: list[type]
        A list of alternative production classes related to the given production class.
    """
    # Determing the base class that the production class belongs to
    detected_base_class: type = None
    for base_class in RELATED_PRODUCTIONS:
        if issubclass(production_class, base_class):
            detected_base_class = base_class
            break

    # If we have not detected a base class, raise an error
    if not detected_base_class:
        raise ValueError(f"Production class {production_class.__name__} does not belong to any known base class in RELATED_PRODUCTIONS.")

    # Get the alternative productions for the detected base class
    all_related_productions = RELATED_PRODUCTIONS.get(detected_base_class, [])

    # remove the original production class from the list of alternatives if it is present
    alternatives = [prod for prod in all_related_productions if prod != production_class]

    return alternatives