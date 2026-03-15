from brom_drake.productions.all import (
    KinematicMotionPlanningProduction,
    OfflineDynamicMotionPlanningProduction,
    ShelfPlanning1,
    ChemLab1,
    ChemLab2,
)


RELATED_PRODUCTIONS = {
    KinematicMotionPlanningProduction: [ChemLab1, ShelfPlanning1],
    OfflineDynamicMotionPlanningProduction: [ChemLab2],
}

ALL_SUPPORTED_PRODUCTIONS = set()
for prods in RELATED_PRODUCTIONS.values():
    ALL_SUPPORTED_PRODUCTIONS.update(prods)