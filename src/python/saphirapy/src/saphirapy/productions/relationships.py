from brom_drake.productions.all import (
    KinematicMotionPlanningProduction,
    OfflineDynamicMotionPlanningProduction,
    ShelfPlanning1,
    ChemLab1,
    ChemLab2,
    CornellTech1,
)


RELATED_PRODUCTIONS = {
    KinematicMotionPlanningProduction: [ChemLab1, ShelfPlanning1],
    OfflineDynamicMotionPlanningProduction: [ChemLab2, CornellTech1],
}

ALL_SUPPORTED_PRODUCTIONS = set()
for prods in RELATED_PRODUCTIONS.values():
    ALL_SUPPORTED_PRODUCTIONS.update(prods)