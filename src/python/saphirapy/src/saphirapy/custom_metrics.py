"""Metrics that a production script defines for itself.

A script opts in by defining two top-level names. It imports nothing from saphirapy, so it still
runs on its own with ``python my_production.py``:

- ``saphira_metrics(production)``: returns a dict from metric name to number. Saphira calls it once
  per case, after the case's ``main()`` returns its production.
- ``SAPHIRA_METRICS`` (optional): a dict from metric name to how to compare it, with the keys
  ``better`` (``"higher"`` or ``"lower"``), ``unit`` and ``tolerance``.

Restaging rewrites a copy of the script, and top-level definitions are kept in that copy, so every
case (the baseline, related productions, alternative actors and conditions) runs the same metric
code. A metric that does not make sense for a case is the script's to skip, by leaving it out of
the returned dict.

Problems with custom metrics never fail a case. They are collected as warnings, written to the
case's result JSON as ``metric_warnings``, and shown in the job summary.
"""

import math
import numbers
import traceback
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Mapping

METRICS_FUNCTION_NAME = "saphira_metrics"
DEFINITIONS_NAME = "SAPHIRA_METRICS"

BETTER_VALUES = ("higher", "lower")
DEFINITION_KEYS = ("better", "unit", "tolerance")

# The Wrench API's limits on metric names and text values. Longer names are cut when the report is
# built, which can make two metrics collide, so they are refused here instead.
MAX_METRIC_NAME = 100
MAX_UNIT = 40


@dataclass
class CustomMetrics:
    """What a script's custom metrics produced for one case."""

    values: dict[str, float] = field(default_factory=dict)
    definitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def defines_custom_metrics(module: ModuleType | None) -> bool:
    """Whether the script defines ``saphira_metrics`` or ``SAPHIRA_METRICS``."""
    return module is not None and (
        hasattr(module, METRICS_FUNCTION_NAME) or hasattr(module, DEFINITIONS_NAME)
    )


def _name_problem(name: Any) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return f"metric names must be non-empty strings, not {name!r}"
    if len(name) > MAX_METRIC_NAME:
        return f"metric name {name[:40]!r}… is longer than {MAX_METRIC_NAME} characters"
    return None


def read_definitions(module: ModuleType) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Return the script's valid ``SAPHIRA_METRICS`` entries, and a warning for each problem.

    An invalid entry or key is left out rather than failing the case: the metric is still
    reported, only without that part of its definition.
    """
    raw = getattr(module, DEFINITIONS_NAME, None)
    if raw is None:
        return {}, []
    if not isinstance(raw, Mapping):
        return {}, [f"{DEFINITIONS_NAME} must be a dict, not {type(raw).__name__}; it was ignored"]

    definitions: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for name, entry in raw.items():
        if problem := _name_problem(name):
            warnings.append(f"{DEFINITIONS_NAME}: {problem}; the entry was ignored")
            continue
        if not isinstance(entry, Mapping):
            warnings.append(
                f"{DEFINITIONS_NAME}[{name!r}] must be a dict, not {type(entry).__name__}; "
                "it was ignored"
            )
            continue

        definition: dict[str, Any] = {}
        for key, value in entry.items():
            where = f"{DEFINITIONS_NAME}[{name!r}][{key!r}]"
            if key not in DEFINITION_KEYS:
                warnings.append(
                    f"{where} is not a known key ({', '.join(DEFINITION_KEYS)}); it was ignored"
                )
            elif key == "better":
                if value in BETTER_VALUES:
                    definition["better"] = value
                else:
                    warnings.append(f"{where} must be \"higher\" or \"lower\"; it was ignored")
            elif key == "unit":
                if isinstance(value, str) and len(value) <= MAX_UNIT:
                    definition["unit"] = value
                else:
                    warnings.append(
                        f"{where} must be a string of at most {MAX_UNIT} characters; "
                        "it was ignored"
                    )
            else:  # tolerance
                if _is_real(value) and math.isfinite(value) and value >= 0:
                    definition["tolerance"] = float(value)
                else:
                    warnings.append(f"{where} must be a number of at least 0; it was ignored")
        definitions[name] = definition
    return definitions, warnings


def _is_real(value: Any) -> bool:
    # numbers.Real covers int, float and NumPy's floating and integer scalars. bool is an int, and
    # True/False are reported as 1.0/0.0, which suits success-style metrics.
    return isinstance(value, numbers.Real)


def compute_values(module: ModuleType, production: Any) -> tuple[dict[str, float], list[str]]:
    """Call the script's ``saphira_metrics(production)`` and keep the values that are numbers."""
    function = getattr(module, METRICS_FUNCTION_NAME, None)
    if function is None:
        return {}, []
    if not callable(function):
        return {}, [f"{METRICS_FUNCTION_NAME} must be a function; it was ignored"]

    try:
        returned = function(production)
    except Exception as error:
        # The traceback goes to the case's stderr.log; the summary gets one line.
        traceback.print_exc()
        return {}, [
            f"{METRICS_FUNCTION_NAME} raised {type(error).__name__}: {error}; "
            "no custom metrics were recorded for this case (the traceback is in stderr.log)"
        ]

    if not isinstance(returned, Mapping):
        return {}, [
            f"{METRICS_FUNCTION_NAME} must return a dict, not {type(returned).__name__}; "
            "no custom metrics were recorded for this case"
        ]

    values: dict[str, float] = {}
    warnings: list[str] = []
    for name, value in returned.items():
        if problem := _name_problem(name):
            warnings.append(f"{METRICS_FUNCTION_NAME}: {problem}; the value was ignored")
        elif not _is_real(value):
            warnings.append(
                f"{METRICS_FUNCTION_NAME} returned {type(value).__name__} for {name!r}; "
                "metrics must be numbers, so it was ignored"
            )
        else:
            values[name] = float(value)
    return values, warnings


def collect_custom_metrics(module: ModuleType | None, production: Any) -> CustomMetrics:
    """Read the script's metric definitions and compute its metric values for one case.

    Without a production (``main()`` failed or returned nothing), only the definitions are read:
    the case already reports its failure, so no warning is added for the missing values.
    """
    if not defines_custom_metrics(module):
        return CustomMetrics()

    definitions, warnings = read_definitions(module)
    values: dict[str, float] = {}
    if production is not None:
        values, value_warnings = compute_values(module, production)
        warnings.extend(value_warnings)
    return CustomMetrics(values=values, definitions=definitions, warnings=warnings)


def merge_metrics(
    builtin_values: Mapping[str, float],
    builtin_definitions: Mapping[str, Mapping[str, Any]],
    custom: CustomMetrics,
) -> tuple[dict[str, float], dict[str, dict[str, Any]], list[str]]:
    """Combine Saphira's built-in metrics with the script's.

    The script wins: its value replaces a built-in metric of the same name (with a warning), and
    its definition replaces a built-in definition. A definition may also name a built-in metric
    without replacing its value, for example to give it a tolerance.
    """
    warnings = list(custom.warnings)
    values = dict(builtin_values)
    for name, value in custom.values.items():
        if name in values:
            warnings.append(
                f"{METRICS_FUNCTION_NAME} returned {name!r}, which replaces Saphira's built-in "
                "metric of the same name"
            )
        values[name] = value

    definitions = {
        name: dict(definition)
        for name, definition in builtin_definitions.items()
        if name in values
    }
    definitions.update(custom.definitions)
    return values, definitions, warnings
