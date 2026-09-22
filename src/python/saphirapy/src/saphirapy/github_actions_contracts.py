import ast
import json
from pathlib import Path
from typing import Any, TypedDict

from saphirapy.productions.cast_member_registry import get_alternatives_for_type
from saphirapy.restaging import BromRestagingManager
from saphirapy.fanout import normalize_fanout_cases


class ResultRecord(TypedDict):
    production: str
    production_class: str
    exit_code: str
    duration_seconds: str
    status: str
    comparison_type: str
    comparison_metadata: dict[str, Any]
    metrics: dict[str, str]


def _comparison_metadata(
    comparison_type: str,
    **metadata: Any,
) -> dict[str, Any]:
    return {
        "comparison-type": comparison_type,
        **metadata,
    }


def parse_detection_outputs(detection_json: str) -> dict[str, str]:
    payload = json.loads(detection_json)
    related_productions = payload.get("related_productions") or []
    if not isinstance(related_productions, list):
        raise ValueError("related_productions must be a list")

    return {
        "detected-parent": str(payload.get("detected_parent_class") or "UNMAPPED"),
        "related-productions-json": json.dumps(
            [str(production) for production in related_productions]
        ),
    }


def write_detection_outputs(detection_json: str, github_output_path: Path) -> dict[str, str]:
    outputs = parse_detection_outputs(detection_json)
    with github_output_path.open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")
    return outputs


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


def write_cast_production_outputs(
    detection_json: str,
    production_file: Path,
    github_output_path: Path,
    include_alternative_productions: bool,
    include_alternative_actors: bool,
) -> dict[str, str]:
    outputs = parse_detection_outputs(detection_json)
    outputs.update(
        build_cast_production_outputs(
            detection_json,
            production_file,
            include_alternative_productions,
            include_alternative_actors,
        )
    )
    with github_output_path.open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")
    return outputs


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
    payload["timed_out"] = exit_code == 124
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
        if comparison_type in {"actor", "production", "unmapped"}:
            return str(comparison_type), metadata

    comparison_type = payload.get("comparison_type")
    if comparison_type in {"actor", "production", "unmapped"}:
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
    duration_seconds = "n/a"
    metrics: dict[str, str] = {}
    comparison_type = "unmapped"
    comparison_metadata: dict[str, Any] = {}

    try:
        payload = json.loads(file_path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("result payload must be an object")
        production = str(
            payload.get("cast_production_name") or payload.get("production_class") or production
        )
        production_class = str(payload.get("production_class") or production_class)
        exit_code = str(payload.get("exit_code", "unknown"))
        duration_seconds = str(payload.get("duration_seconds", "n/a"))
        comparison_type, comparison_metadata = _classify_comparison(payload, production)
        metrics_payload = payload.get("metrics")
        if isinstance(metrics_payload, dict):
            metrics = {str(name): str(value) for name, value in metrics_payload.items()}
    except Exception:
        pass

    return {
        "production": production,
        "production_class": production_class,
        "exit_code": exit_code,
        "duration_seconds": duration_seconds,
        "status": "passed" if exit_code == "0" else "failed",
        "comparison_type": comparison_type,
        "comparison_metadata": comparison_metadata,
        "metrics": metrics,
    }


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


def build_summary(results_directories: str) -> str:
    files = _collect_result_files(_parse_directories(results_directories))

    lines = ["### Combined Saphira Metrics"]
    if not files:
        lines.append("- No run-evaluate result files were found")
        return "\n".join(lines)

    pass_count = 0
    fail_count = 0
    parse_error_count = 0

    rows: list[ResultRecord] = []
    for file_path in files:
        row = _normalize_result_file(file_path)
        if row["exit_code"] == "parse_error":
            parse_error_count += 1
        if row["status"] == "passed":
            pass_count += 1
        else:
            fail_count += 1

        rows.append(row)

    production_rows = [row for row in rows if row["comparison_type"] == "production"]
    actor_rows = [row for row in rows if row["comparison_type"] == "actor"]
    actor_rows.extend(
        row for row in production_rows if row["production"].startswith("baseline-")
    )
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
    _append_results_table(
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
        ],
        actor_table_rows,
        [name.replace("|", "\\|") for name in actor_metric_names],
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
    _append_results_table(
        lines,
        "#### Production Comparison",
        [
            "Production",
            "Cast Production",
            "Actor Configuration",
            "Exit Code",
            "Duration (s)",
            "Status",
        ],
        production_table_rows,
        [name.replace("|", "\\|") for name in production_metric_names],
    )

    other_metric_names = _metric_names(other_rows)
    other_table_rows: list[list[str]] = []
    for row in other_rows:
        other_table_rows.append(
            [
                row["production"].replace("|", "\\|"),
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
        [name.replace("|", "\\|") for name in other_metric_names],
    )

    lines.append("")
    lines.append(f"Totals: {pass_count} passed, {fail_count} failed")
    if parse_error_count:
        lines.append(f"Warning: {parse_error_count} result file(s) could not be parsed")

    return "\n".join(lines)