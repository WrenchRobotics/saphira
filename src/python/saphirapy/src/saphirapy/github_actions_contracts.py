import ast
import json
from pathlib import Path
from typing import Any, TypedDict

from saphirapy.productions.cast_member_registry import (
    CAST_MEMBER_ALTERNATIVES,
    get_alternatives_for_type,
)
from saphirapy.restaging import BromRestagingManager
from saphirapy.fanout import ActorResolver, RemoteActorUnavailable, normalize_fanout_cases
from saphirapy.scenarios import CONDITIONS_COMPARISON_TYPE, SCENARIO_METADATA_PREFIX


# The exit code of a case that ran out of time, from `timeout` or the facade's own limit.
TIMEOUT_EXIT_CODE = 124
# Statuses of cases whose simulation ran without succeeding, for example because the algorithm was
# too expensive for the task. They are reported but, unlike "failed", do not fail the run.
SIMULATION_OUTCOME_STATUSES = frozenset({"timed-out"})
# The comparison types the PR comment groups results by.
COMPARISON_TYPES = frozenset({"actor", "production", CONDITIONS_COMPARISON_TYPE, "unmapped"})


def case_status(exit_code: Any, simulation_outcome: Any = None) -> str:
    """Return a case's status: passed, one of SIMULATION_OUTCOME_STATUSES, or failed."""
    if str(exit_code) == "0":
        return "passed"
    if isinstance(simulation_outcome, dict) and simulation_outcome.get("timed_out") is True:
        return "timed-out"
    return "failed"


def fails_run(status: str) -> bool:
    """Whether a case with this status fails the run."""
    return status != "passed" and status not in SIMULATION_OUTCOME_STATUSES


class ResultRecord(TypedDict):
    production: str
    production_class: str
    exit_code: str
    duration_seconds: str
    status: str
    comparison_type: str
    comparison_metadata: dict[str, Any]
    metrics: dict[str, str]
    # How to compare each metric (better, unit, tolerance), from the built-in evaluator and the
    # script's SAPHIRA_METRICS. See saphirapy.custom_metrics.
    metric_definitions: dict[str, dict[str, Any]]
    # Problems with the script's custom metrics. They never fail the case.
    metric_warnings: list[str]


def _comparison_metadata(
    comparison_type: str,
    **metadata: Any,
) -> dict[str, Any]:
    return {
        "comparison-type": comparison_type,
        **{key.replace("_", "-"): value for key, value in metadata.items()},
    }


def _option_constructor_name(instantiation_code: str) -> str | None:
    for statement in ast.parse(instantiation_code).body:
        if not isinstance(statement, ast.Assign) or not isinstance(statement.value, ast.Call):
            continue
        constructor = statement.value.func
        if isinstance(constructor, ast.Name):
            return constructor.id
        if isinstance(constructor, ast.Attribute):
            return constructor.attr
    return None


def find_alternative_actors_for_production(production_file: Path) -> list[dict[str, Any]]:
    """Return registered actor replacements for each cast call in a production file."""
    parsed_tree = ast.parse(production_file.read_text())
    manager = BromRestagingManager(output_dir=production_file.parent)
    actors: list[dict[str, Any]] = []

    for cast_call_index, cast_call in enumerate(manager.find_easy_cast_and_build_calls(parsed_tree)):
        current_actor_class = manager._extract_class_name_from_instantiation(
            cast_call.instantiation_node
        )
        alternatives = []
        if cast_call.cast_member_type is not None:
            alternatives = [
                {
                    "name": option.name,
                    "import_statements": option.import_statements,
                    "instantiation_code": option.instantiation_code,
                    "variable_name": option.variable_name,
                    "method_to_call": option.method_to_call,
                }
                for option in get_alternatives_for_type(cast_call.cast_member_type)
                if _option_constructor_name(option.instantiation_code) != current_actor_class
            ]

        actors.append(
            {
                "cast_call_index": cast_call_index,
                "production_variable": cast_call.production_var,
                "current_actor": cast_call.cast_member_expr,
                "current_actor_class": current_actor_class,
                "actor_type": cast_call.cast_member_type,
                "alternatives": alternatives,
            }
        )

    return actors


def build_remote_actor_resolver(production_file: Path) -> ActorResolver:
    """Resolve remote ``{"cast_call_index", "actor"}`` references against the local registry.

    A reference expands to exactly the actor variant a local case for the same alternative
    would carry, so executable code only ever comes from this installed Saphira version.
    """
    alternatives_by_call: dict[int, dict[str, dict[str, Any]]] | None = None

    def resolve(cast_call_index: int, actor_name: str) -> dict[str, Any]:
        nonlocal alternatives_by_call
        if alternatives_by_call is None:
            alternatives_by_call = {
                actor["cast_call_index"]: {
                    alternative["name"]: alternative for alternative in actor["alternatives"]
                }
                for actor in find_alternative_actors_for_production(production_file)
            }
        registered = {
            option.name for options in CAST_MEMBER_ALTERNATIVES.values() for option in options
        }
        if actor_name not in registered:
            raise RemoteActorUnavailable(
                f"actor {actor_name!r} is not in this Saphira version's actor registry "
                "(it may need a newer saphira release)"
            )
        alternative = alternatives_by_call.get(cast_call_index, {}).get(actor_name)
        if alternative is None:
            raise RemoteActorUnavailable(
                f"actor {actor_name!r} is not an alternative for cast call {cast_call_index} "
                f"in {production_file.name}"
            )
        return {"cast_call_index": cast_call_index, **alternative}

    return resolve


def build_cast_production_outputs(
    detection_json: str,
    production_file: Path,
    include_alternative_productions: bool,
    include_alternative_actors: bool,
) -> dict[str, str]:
    """Build GitHub Actions matrix outputs for baseline and optional cast productions."""
    payload = json.loads(detection_json)
    related_productions = payload.get("related_productions") or []
    if not isinstance(related_productions, list):
        raise ValueError("related_productions must be a list")

    detected_production = str(payload.get("detected_production_class") or "UNMAPPED")
    alternative_productions: list[str] = []
    if include_alternative_productions and detected_production != "UNMAPPED":
        alternative_productions = [
            str(production)
            for production in related_productions
            if str(production) != detected_production
        ]

    alternative_actors: list[dict[str, Any]] = []
    if include_alternative_actors and detected_production != "UNMAPPED":
        for actor in find_alternative_actors_for_production(production_file):
            for alternative in actor["alternatives"]:
                alternative_actors.append(
                    {
                        "cast_call_index": actor["cast_call_index"],
                        **alternative,
                    }
                )

    # The detected production always runs, as the baseline the other cases compare against.
    cast_productions: list[dict[str, Any]] = [
        {
            "production-class": str(production),
            "actor-variant-json": "",
            "cast-production-name": f"production-{production}",
            "artifact-name": f"matrix-result-{production}",
            "comparison-metadata-json": json.dumps(
                _comparison_metadata(
                    "production",
                    actor_configuration="current",
                )
            ),
        }
        for production in related_productions
        if str(production) == detected_production or str(production) in alternative_productions
    ]
    cast_productions.extend(
        {
            "production-class": detected_production,
            "actor-variant-json": json.dumps(actor),
            "cast-production-name": f"actor-{actor['name']}-{actor['cast_call_index']}",
            "artifact-name": f"actor-result-{actor['name']}-{actor['cast_call_index']}",
            "comparison-metadata-json": json.dumps(
                _comparison_metadata(
                    "actor",
                    actor_name=actor["name"],
                    cast_call_index=actor["cast_call_index"],
                    current_actor_class=actor.get("current_actor_class"),
                    actor_type=actor.get("actor_type"),
                )
            ),
        }
        for actor in alternative_actors
    )
    cast_productions = normalize_fanout_cases(cast_productions)

    return {
        "detected-production": detected_production,
        "alternative-productions-json": json.dumps(alternative_productions),
        "alternative-actors-json": json.dumps(alternative_actors),
        "has-alternative-productions": str(bool(alternative_productions)).lower(),
        "has-alternative-actors": str(bool(alternative_actors)).lower(),
        "cast-productions-json": json.dumps(cast_productions),
    }


def finalize_result_payload(
    result_json_path: Path,
    production_class: str,
    exit_code: int,
    duration_seconds: float,
    restaged_file_default: str,
    cast_production_name: str | None = None,
    comparison_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if result_json_path.exists():
        try:
            payload = json.loads(result_json_path.read_text())
        except Exception:
            payload = {}

    payload.setdefault("production_class", production_class)
    payload.setdefault("restaged_file", restaged_file_default)
    payload["exit_code"] = int(exit_code)
    payload["duration_seconds"] = float(duration_seconds)
    # What the simulation did, as opposed to whether Saphira could run it. For now only timeouts.
    payload["simulation_outcome"] = {"timed_out": exit_code == TIMEOUT_EXIT_CODE}
    payload["status"] = case_status(exit_code, payload["simulation_outcome"])
    if cast_production_name is not None:
        payload["cast_production_name"] = cast_production_name
    if comparison_metadata:
        payload["comparison_metadata"] = comparison_metadata
        comparison_type = comparison_metadata.get("comparison-type")
        if comparison_type is not None:
            payload["comparison_type"] = str(comparison_type)

    result_json_path.parent.mkdir(parents=True, exist_ok=True)
    result_json_path.write_text(json.dumps(payload, indent=2))
    return payload


def _parse_directories(raw: str) -> list[Path]:
    directories = [line.strip() for line in raw.splitlines() if line.strip()]
    if not directories:
        directories = ["productions/matrix-results"]
    return [Path(directory) for directory in directories]


def _collect_result_files(directories: list[Path]) -> list[Path]:
    files: list[Path] = []
    for directory in directories:
        if directory.is_dir():
            files.extend(sorted(directory.rglob("*.json")))
    return files


def _classify_comparison(
    payload: dict[str, Any], fallback_name: str
) -> tuple[str, dict[str, Any]]:
    metadata = payload.get("comparison_metadata")
    if isinstance(metadata, dict):
        comparison_type = metadata.get("comparison-type") or metadata.get("comparison_type")
        if comparison_type in COMPARISON_TYPES:
            return str(comparison_type), metadata

    comparison_type = payload.get("comparison_type")
    if comparison_type in COMPARISON_TYPES:
        return str(comparison_type), metadata if isinstance(metadata, dict) else {}

    if fallback_name.startswith("actor-"):
        return "actor", {}
    if fallback_name.startswith("baseline-") or fallback_name.startswith("production-"):
        return "production", {}
    return "unmapped", {}


def _normalize_result_file(file_path: Path) -> ResultRecord:
    production = file_path.stem
    production_class = file_path.stem
    exit_code = "parse_error"
    simulation_outcome: Any = None
    duration_seconds = "n/a"
    metrics: dict[str, str] = {}
    metric_definitions: dict[str, dict[str, Any]] = {}
    metric_warnings: list[str] = []
    comparison_type = "unmapped"
    comparison_metadata: dict[str, Any] = {}

    try:
        payload = json.loads(file_path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("result payload must be an object")
        comparison_name = str(payload.get("cast_production_name") or file_path.stem)
        production = str(payload.get("cast_production_name") or file_path.stem)
        production_class = str(payload.get("production_class") or production_class)
        exit_code = str(payload.get("exit_code", "unknown"))
        simulation_outcome = payload.get("simulation_outcome")
        duration_seconds = str(payload.get("duration_seconds", "n/a"))
        comparison_type, comparison_metadata = _classify_comparison(payload, comparison_name)
        metrics_payload = payload.get("metrics")
        if isinstance(metrics_payload, dict):
            metrics = {str(name): str(value) for name, value in metrics_payload.items()}
        definitions_payload = payload.get("metric_definitions")
        if isinstance(definitions_payload, dict):
            metric_definitions = {
                str(name): dict(definition)
                for name, definition in definitions_payload.items()
                if isinstance(definition, dict)
            }
        warnings_payload = payload.get("metric_warnings")
        if isinstance(warnings_payload, list):
            metric_warnings = [str(warning) for warning in warnings_payload]
    except Exception:
        pass

    return {
        "production": production,
        "production_class": production_class,
        "exit_code": exit_code,
        "duration_seconds": duration_seconds,
        "status": case_status(exit_code, simulation_outcome),
        "comparison_type": comparison_type,
        "comparison_metadata": comparison_metadata,
        "metrics": metrics,
        "metric_definitions": metric_definitions,
        "metric_warnings": metric_warnings,
    }


def collect_result_records(results_directories: str) -> list[ResultRecord]:
    """Return one record per result JSON file under the newline-separated directories."""
    return [
        _normalize_result_file(file_path)
        for file_path in _collect_result_files(_parse_directories(results_directories))
    ]


def _metric_names(rows: list[ResultRecord]) -> list[str]:
    names: list[str] = []
    for row in rows:
        for name in row["metrics"]:
            if name not in names:
                names.append(name)
    return names


def _append_results_table(
    lines: list[str],
    heading: str,
    headers: list[str],
    rows: list[list[str]],
    metric_names: list[str],
) -> None:
    if not rows:
        return

    lines.append(heading)
    full_headers = headers + metric_names
    lines.append("| " + " | ".join(full_headers) + " |")
    alignments = ["---"] * len(headers) + ["---:"] * len(metric_names)
    lines.append("| " + " | ".join(alignments) + " |")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")


def _append_transposed_results_table(
    lines: list[str],
    heading: str,
    field_names: list[str],
    columns: list[list[str]],
) -> None:
    """Render a table where each entry of `columns` is a column; the first value becomes its header."""
    if not columns:
        return

    lines.append(heading)
    lines.append("| " + " | ".join([field_names[0]] + [column[0] for column in columns]) + " |")
    lines.append("| " + " | ".join(["---"] * (len(columns) + 1)) + " |")
    for index, field_name in enumerate(field_names[1:], start=1):
        lines.append(
            "| " + " | ".join([field_name] + [column[index] for column in columns]) + " |"
        )


def scenario_values(metadata: dict[str, Any]) -> list[tuple[str, str]]:
    """Return the (parameter, value) pairs a conditions case ran with, from its metadata."""
    return sorted(
        (str(key)[len(SCENARIO_METADATA_PREFIX):], str(value))
        for key, value in metadata.items()
        if str(key).startswith(SCENARIO_METADATA_PREFIX)
    )


def _append_conditions_table(
    lines: list[str],
    conditions_rows: list[ResultRecord],
    production_rows: list[ResultRecord],
    result_values: Any,
) -> None:
    """Compare conditions cases with their production run under its default conditions."""
    if not conditions_rows:
        return
    conditions_classes = {row["production_class"] for row in conditions_rows}
    rows = [
        row
        for row in production_rows
        if row["production_class"] in conditions_classes
        and str(row["comparison_metadata"].get("actor-configuration") or "current") == "current"
    ] + conditions_rows

    metric_names = _metric_names(rows)
    table_rows: list[list[str]] = []
    for row in rows:
        values = scenario_values(row["comparison_metadata"])
        changed = "<br>".join(f"{name} = {value}" for name, value in values) or "Defaults"
        table_rows.append(
            [
                changed.replace("|", "\\|"),
                row["production_class"].replace("|", "\\|"),
                row["production"].replace("|", "\\|"),
                row["exit_code"],
                row["duration_seconds"],
                row["status"],
                *result_values(row, metric_names),
            ]
        )
    _append_transposed_results_table(
        lines,
        "#### Conditions Comparison",
        [
            "Conditions",
            "Production",
            "Cast Production",
            "Exit Code",
            "Duration (s)",
            "Status",
            *_metric_headers(rows, metric_names),
        ],
        table_rows,
    )


def _metric_headers(rows: list[ResultRecord], names: list[str]) -> list[str]:
    """Table headers for the metric columns, with each metric's unit when a case defines one."""
    headers = []
    for name in names:
        unit = next(
            (
                str(row["metric_definitions"][name]["unit"])
                for row in rows
                if row["metric_definitions"].get(name, {}).get("unit")
            ),
            "",
        )
        header = f"{name} ({unit})" if unit else name
        headers.append(header.replace("|", "\\|"))
    return headers


MAX_SUMMARY_METRIC_WARNINGS = 20


def _append_metric_warnings(lines: list[str], rows: list[ResultRecord]) -> None:
    """List each distinct custom-metric warning once, with the cases it came from."""
    cases_by_warning: dict[str, list[str]] = {}
    for row in rows:
        for warning in row["metric_warnings"]:
            cases = cases_by_warning.setdefault(warning, [])
            if row["production"] not in cases:
                cases.append(row["production"])
    if not cases_by_warning:
        return

    lines.extend(["", "#### Custom Metric Warnings", ""])
    for warning, cases in list(cases_by_warning.items())[:MAX_SUMMARY_METRIC_WARNINGS]:
        shown = ", ".join(f"`{case}`" for case in cases[:3])
        more = f" and {len(cases) - 3} more" if len(cases) > 3 else ""
        # Exception messages can span lines; keep each warning on its list item.
        text = " ".join(warning.split())[:300]
        lines.append(f"- {text} ({shown}{more})")
    if (hidden := len(cases_by_warning) - MAX_SUMMARY_METRIC_WARNINGS) > 0:
        lines.append(f"- {hidden} more warning(s) are in the cases' result JSON files")


def build_summary(results_directories: str) -> str:
    rows = collect_result_records(results_directories)

    lines = ["### Combined Saphira Metrics"]
    if not rows:
        lines.append("- No result files were found")
        return "\n".join(lines)

    pass_count = 0
    timed_out_count = 0
    fail_count = 0
    parse_error_count = 0

    for row in rows:
        if row["exit_code"] == "parse_error":
            parse_error_count += 1
        if row["status"] == "passed":
            pass_count += 1
        elif row["status"] == "timed-out":
            timed_out_count += 1
        else:
            fail_count += 1

    production_rows = [row for row in rows if row["comparison_type"] == "production"]
    actor_rows = [row for row in rows if row["comparison_type"] == "actor"]
    actor_rows.extend(
        row for row in production_rows if row["production"].startswith("baseline-")
    )
    conditions_rows = [
        row for row in rows if row["comparison_type"] == CONDITIONS_COMPARISON_TYPE
    ]
    other_rows = [row for row in rows if row["comparison_type"] == "unmapped"]

    def result_values(row: ResultRecord, values: list[str]) -> list[str]:
        metric_values = [row["metrics"].get(name, "") for name in values]
        return [value.replace("|", "\\|") for value in metric_values]

    actor_metric_names = _metric_names(actor_rows)
    actor_table_rows: list[list[str]] = []
    for row in actor_rows:
        metadata = row["comparison_metadata"]
        actor_name = str(
            metadata.get("actor-name")
            or metadata.get("actor_name")
            or "Current actors"
        )
        cast_call_value = metadata.get("cast-call-index")
        if cast_call_value is None:
            cast_call_value = metadata.get("cast_call_index")
        cast_call_index = "" if cast_call_value is None else str(cast_call_value)
        actor_table_rows.append(
            [
                actor_name.replace("|", "\\|"),
                cast_call_index.replace("|", "\\|"),
                row["production_class"].replace("|", "\\|"),
                row["production"].replace("|", "\\|"),
                row["exit_code"],
                row["duration_seconds"],
                row["status"],
                *result_values(row, actor_metric_names),
            ]
        )
    _append_transposed_results_table(
        lines,
        "#### Actor Comparison",
        [
            "Actor",
            "Cast Call",
            "Production",
            "Cast Production",
            "Exit Code",
            "Duration (s)",
            "Status",
            *_metric_headers(actor_rows, actor_metric_names),
        ],
        actor_table_rows,
    )

    production_metric_names = _metric_names(production_rows)
    production_table_rows: list[list[str]] = []
    for row in production_rows:
        metadata = row["comparison_metadata"]
        actor_configuration = str(
            metadata.get("actor-configuration")
            or metadata.get("actor_configuration")
            or "Current actors"
        )
        production_table_rows.append(
            [
                row["production_class"].replace("|", "\\|"),
                row["production"].replace("|", "\\|"),
                actor_configuration.replace("|", "\\|"),
                row["exit_code"],
                row["duration_seconds"],
                row["status"],
                *result_values(row, production_metric_names),
            ]
        )
    _append_transposed_results_table(
        lines,
        "#### Production Comparison",
        [
            "Production",
            "Cast Production",
            "Actor Configuration",
            "Exit Code",
            "Duration (s)",
            "Status",
            *_metric_headers(production_rows, production_metric_names),
        ],
        production_table_rows,
    )

    _append_conditions_table(lines, conditions_rows, production_rows, result_values)

    other_metric_names = _metric_names(other_rows)
    other_table_rows: list[list[str]] = []
    for row in other_rows:
        other_table_rows.append(
            [
                row["production_class"].replace("|", "\\|"),
                row["exit_code"],
                row["duration_seconds"],
                row["status"],
                *result_values(row, other_metric_names),
            ]
        )
    _append_results_table(
        lines,
        "#### Other Results",
        ["Result", "Exit Code", "Duration (s)", "Status"],
        other_table_rows,
        _metric_headers(other_rows, other_metric_names),
    )

    lines.append("")
    timed_out = f"{timed_out_count} timed out, " if timed_out_count else ""
    lines.append(f"Totals: {pass_count} passed, {timed_out}{fail_count} failed")
    if parse_error_count:
        lines.append(f"Warning: {parse_error_count} result file(s) could not be parsed")
    _append_metric_warnings(lines, rows)

    return "\n".join(lines)