import json
from pathlib import Path
from typing import Any


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


def finalize_result_payload(
    result_json_path: Path,
    production_class: str,
    exit_code: int,
    duration_seconds: float,
    restaged_file_default: str,
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
            production = str(payload.get("production_class") or production)
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