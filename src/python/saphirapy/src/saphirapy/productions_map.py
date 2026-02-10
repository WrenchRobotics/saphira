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