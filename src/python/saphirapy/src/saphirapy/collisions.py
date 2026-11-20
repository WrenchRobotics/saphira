"""
Collision metrics for Brom productions.

The counting logic (``summarize_collisions``) is pure Python so it can run on any
per-sample collision log. ``collisions_along_configuration_history`` produces
that log by replaying a robot's configuration history through the production's
SceneGraph.

What counts as "a collision"
----------------------------
A collision is an *onset*: a pair of bodies that was not in collision at the
previous sample and is in collision at this one. A pair that stays in contact
for 50 samples is one collision, not 50; if it separates and touches again,
that is a second collision.
"""

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

import numpy as np

# One sample's collisions: (name_A, name_B, depth) for every colliding pair.
SampleCollisions = Sequence[Tuple[str, str, float]]
PairKey = FrozenSet[str]


@dataclass
class CollisionSummary:
    """
    Attributes
    ----------
    collided : bool
        True if any pair was in collision at any sample.
    num_collisions : int
        Number of collision onsets (see module docstring).
    num_samples_in_collision : int
        Number of samples with at least one colliding pair.
    max_depth : float
        Deepest penetration seen in meters (0.0 if no collisions).
    first_collision_index : Optional[int]
        Index of the first sample in collision, or None.
    collisions_per_pair : Dict[Tuple[str, str], int]
        Onset count for each pair of body names (names sorted).
    """

    collided: bool = False
    num_collisions: int = 0
    num_samples_in_collision: int = 0
    max_depth: float = 0.0
    first_collision_index: Optional[int] = None
    collisions_per_pair: Dict[Tuple[str, str], int] = field(default_factory=dict)

    def as_metrics(self) -> Dict[str, float]:
        """The headline numbers, in the evaluator's metric format."""
        return {
            "Collision Occurred": float(self.collided),
            "Number of Collisions": float(self.num_collisions),
        }


def _pair_key(name_a: str, name_b: str) -> PairKey:
    return frozenset((name_a, name_b))


def summarize_collisions(
    per_sample_collisions: Sequence[SampleCollisions],
    ignore_pairs: Iterable[Tuple[str, str]] = (),
) -> CollisionSummary:
    """
    Description
    -----------
    Decide whether a run collided and count its collisions.

    Parameters
    ----------
    per_sample_collisions : sequence of length T
        Element t lists every colliding pair at sample t as
        ``(name_A, name_B, depth)``. Order of A/B does not matter.
    ignore_pairs : iterable of (name_A, name_B)
        Pairs that are expected to touch (e.g. an object resting on a table).
    """
    ignored = {_pair_key(a, b) for a, b in ignore_pairs}

    summary = CollisionSummary()
    per_pair: Dict[PairKey, int] = {}
    previous: set = set()

    for t, sample in enumerate(per_sample_collisions):
        current = set()
        for name_a, name_b, depth in sample:
            key = _pair_key(name_a, name_b)
            if key in ignored:
                continue
            current.add(key)
            summary.max_depth = max(summary.max_depth, float(depth))

        if current:
            summary.num_samples_in_collision += 1
            if summary.first_collision_index is None:
                summary.first_collision_index = t

        for key in current - previous:
            per_pair[key] = per_pair.get(key, 0) + 1
        previous = current

    summary.num_collisions = sum(per_pair.values())
    summary.collided = summary.num_collisions > 0
    summary.collisions_per_pair = {
        tuple(sorted(key)) if len(key) == 2 else (next(iter(key)),) * 2: count
        for key, count in per_pair.items()
    }
    return summary


def _find_collisions_in_context(scene_graph, root_context, plant, threshold):
    """
    Uses ``brom_drake.utils.collision_checking.find_collisions`` when the
    installed brom has it, and an equivalent point-pair-penetration query otherwise.
    """
    try:
        from brom_drake.utils.collision_checking import find_collisions
    except ImportError:
        find_collisions = None

    if find_collisions is not None:
        return [
            (c.name_A, c.name_B, c.depth)
            for c in find_collisions(
                scene_graph, root_context, plant=plant, threshold=threshold
            )
        ]

    query_object = scene_graph.get_query_output_port().Eval(
        scene_graph.GetMyContextFromRoot(root_context)
    )
    inspector = query_object.inspector()

    def name_of(geometry_id):
        body = plant.GetBodyFromFrameId(inspector.GetFrameId(geometry_id))
        if body is None:
            return inspector.GetName(geometry_id)
        return f"{plant.GetModelInstanceName(body.model_instance())}::{body.name()}"

    return [
        (name_of(p.id_A), name_of(p.id_B), float(p.depth))
        for p in query_object.ComputePointPairPenetration()
        if p.depth > threshold
    ]


def collisions_along_configuration_history(
    plant,
    scene_graph,
    root_context,
    model_instance,
    configuration_samples: np.ndarray,
    threshold: float = 1e-3,
) -> List[SampleCollisions]:
    """
    Description
    -----------
    Replays each configuration of ``model_instance`` and records which pairs collide.
    Works on a clone of ``root_context``, so the caller's context is untouched and
    any collision filters already applied to it are kept.

    Parameters
    ----------
    configuration_samples : np.ndarray of shape (T, num_positions)
    threshold : float
        Penetration depth (m) above which a pair counts as colliding.

    Returns
    -------
    A list of length T, ready for ``summarize_collisions``.
    """
    context = root_context.Clone()
    plant_context = plant.GetMyMutableContextFromRoot(context)

    log: List[SampleCollisions] = []
    for q in np.asarray(configuration_samples):
        plant.SetPositions(plant_context, model_instance, np.asarray(q).reshape(-1))
        log.append(_find_collisions_in_context(scene_graph, context, plant, threshold))
    return log
