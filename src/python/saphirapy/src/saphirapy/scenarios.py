"""Initial-condition values for conditions fanout cases, checked by Brom's ``Scenario``.

A brom_drake production declares the initial conditions a caller may change as a nested
``Scenario`` (brom_drake 1.1 and later): each parameter's name, type, units and allowed range.
A conditions case carries values for some of those parameters in ``scenario-json``. Saphira
checks them with ``Scenario.validate`` before a case runs, and the restaged production receives
them through ``Scenario.constructor_kwargs``, so values never become code.
"""

import json
from typing import Any

import brom_drake.productions.all as brom_all
from brom_drake.productions.types.base import Scenario, ScenarioError

from saphirapy.fanout import ScenarioUnavailable

# The comparison-type of conditions cases, which groups them in the PR comment.
CONDITIONS_COMPARISON_TYPE = "conditions"
# Each scenario value is reported as its own comparison metadata entry under this prefix, since
# reports bound every metadata value's length.
SCENARIO_METADATA_PREFIX = "scenario."
# How a null value, "use the production's default", reads in reports.
DEFAULT_VALUE_LABEL = "default"


def production_scenario(production_class_name: str) -> type[Scenario]:
    """Return the Scenario a brom_drake production declares, or raise ScenarioUnavailable."""
    production = getattr(brom_all, production_class_name, None)
    if not isinstance(production, type):
        raise ScenarioUnavailable(
            f"production {production_class_name!r} is not in this brom_drake version"
        )
    scenario = getattr(production, "Scenario", None)
    if not (isinstance(scenario, type) and issubclass(scenario, Scenario)):
        raise ScenarioUnavailable(
            f"{production_class_name} declares no Scenario in this brom_drake version"
        )
    return scenario


def validate_scenario(production_class_name: str, values: dict[str, Any]) -> dict[str, Any]:
    """Check values against the production's Scenario and return Brom's normalized form.

    Only the given parameters come back, as plain JSON: floats, lists of floats, and poses with
    ``xyz`` and ``rpy`` (a ``wxyz`` quaternion is converted). Raises ScenarioUnavailable naming
    every problem.
    """
    scenario = production_scenario(production_class_name)
    try:
        return scenario.validate(values)
    except ScenarioError as error:
        raise ScenarioUnavailable(
            f"{production_class_name} does not accept these scenario values: "
            + "; ".join(error.problems)
        ) from error


def _metadata_value(value: Any) -> Any:
    if value is None:
        return DEFAULT_VALUE_LABEL
    if isinstance(value, int | float):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def scenario_metadata(values: dict[str, Any]) -> dict[str, Any]:
    """Return one comparison metadata entry per scenario value, for results and reports."""
    return {
        f"{SCENARIO_METADATA_PREFIX}{name}": _metadata_value(value)
        for name, value in sorted(values.items())
    }


def with_scenario_metadata(
    metadata: dict[str, Any] | None, scenario_json: str
) -> dict[str, Any] | None:
    """Record the values a case runs with in its comparison metadata.

    Entries under the scenario prefix always describe the values this runner validated and used,
    so any the fanout service sent are replaced. A case with values but no comparison-type is a
    conditions case.
    """
    if not scenario_json:
        return metadata
    recorded = {
        key: value
        for key, value in (metadata or {}).items()
        if not str(key).startswith(SCENARIO_METADATA_PREFIX)
    }
    recorded.setdefault("comparison-type", CONDITIONS_COMPARISON_TYPE)
    recorded.update(scenario_metadata(json.loads(scenario_json)))
    return recorded
