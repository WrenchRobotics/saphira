import ast
import json
from pathlib import Path
from typing import Any

from saphirapy.productions.cast_member_registry import get_alternatives_for_type
from saphirapy.restaging import BromRestagingManager
from saphirapy.fanout import normalize_fanout_cases


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

    cast_productions: list[dict[str, str]] = [
        {
            "production-class": str(production),
            "actor-variant-json": "",
            "cast-production-name": f"production-{production}",
            "artifact-name": f"matrix-result-{production}",
        }
        for production in related_productions
    ]
    cast_productions.extend(
        {
            "production-class": detected_production,
            "actor-variant-json": json.dumps(actor),
            "cast-production-name": f"actor-{actor['name']}-{actor['cast_call_index']}",
            "artifact-name": f"actor-result-{actor['name']}-{actor['cast_call_index']}",
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


def build_summary(results_directories: str) -> str:
    files = _collect_result_files(_parse_directories(results_directories))

    lines = ["### Combined Saphira Metrics"]
    if not files:
        lines.append("- No run-evaluate result files were found")
        return "\n".join(lines)

    pass_count = 0
    fail_count = 0
    parse_error_count = 0

    rows: list[tuple[str, str, str, str]] = []
    metric_names: list[str] = []
    metrics_by_production: dict[str, dict[str, str]] = {}
    for file_path in files:
        production = file_path.stem
        exit_code = "parse_error"
        status = "failed"
        duration_seconds = "n/a"

        try:
            payload = json.loads(file_path.read_text())
            production = str(
                payload.get("cast_production_name") or payload.get("production_class") or production
            )
            exit_code = str(payload.get("exit_code", "unknown"))
            duration_seconds = str(payload.get("duration_seconds", "n/a"))
            metrics_payload = payload.get("metrics")
            if isinstance(metrics_payload, dict) and metrics_payload:
                production_metrics = metrics_by_production.setdefault(production, {})
                for metric_name, metric_value in metrics_payload.items():
                    metric_name_str = str(metric_name)
                    if metric_name_str not in metric_names:
                        metric_names.append(metric_name_str)
                    production_metrics[metric_name_str] = str(metric_value)
        except Exception:
            parse_error_count += 1

        if exit_code == "0":
            status = "passed"
            pass_count += 1
        else:
            fail_count += 1

        rows.append((production, exit_code, duration_seconds, status))

    lines.append("| Production | Exit Code | Duration (s) | Status |")
    lines.append("| --- | ---: | ---: | --- |")
    for production, exit_code, duration_seconds, status in rows:
        lines.append(f"| {production} | {exit_code} | {duration_seconds} | {status} |")

    lines.append("")
    lines.append(f"Totals: {pass_count} passed, {fail_count} failed")
    if metric_names:
        lines.append("")
        lines.append("Additional Metrics:")
        safe_metric_names = [metric_name.replace("|", "\\|") for metric_name in metric_names]
        lines.append("| Production | " + " | ".join(safe_metric_names) + " |")
        lines.append("| --- | " + " | ".join("---:" for _ in metric_names) + " |")

        production_order = [production for production, _, _, _ in rows]
        seen_productions: set[str] = set()
        for production in production_order:
            if production in seen_productions:
                continue
            seen_productions.add(production)
            production_metrics = metrics_by_production.get(production)
            if not production_metrics:
                continue

            metric_values: list[str] = []
            for metric_name in metric_names:
                value = production_metrics.get(metric_name, "")
                metric_values.append(value.replace("|", "\\|"))
            lines.append(f"| {production} | " + " | ".join(metric_values) + " |")
    if parse_error_count:
        lines.append(f"Warning: {parse_error_count} result file(s) could not be parsed")

    return "\n".join(lines)