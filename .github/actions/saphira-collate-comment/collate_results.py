import json
import os
from pathlib import Path


def _parse_directories(raw: str) -> list[Path]:
    dirs = [line.strip() for line in raw.splitlines() if line.strip()]
    if not dirs:
        dirs = ["productions/matrix-results"]
    return [Path(d) for d in dirs]


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

    rows: list[tuple[str, str, str, str, str]] = []
    for file_path in files:
        production = file_path.stem
        exit_code = "parse_error"
        status = "failed"
        duration_seconds = "n/a"
        restaged_file = "n/a"

        try:
            payload = json.loads(file_path.read_text())
            production = str(payload.get("production_class") or production)
            exit_code = str(payload.get("exit_code", "unknown"))
            duration_raw = payload.get("duration_seconds", "n/a")
            duration_seconds = str(duration_raw)
            restaged_file = str(payload.get("restaged_file") or "n/a")
        except Exception:
            parse_error_count += 1

        if exit_code == "0":
            status = "passed"
            pass_count += 1
        else:
            status = "failed"
            fail_count += 1

        rows.append((production, exit_code, duration_seconds, status, restaged_file))

    lines.append("| Production | Exit Code | Duration (s) | Status | Restaged File |")
    lines.append("| --- | ---: | ---: | --- | --- |")
    for production, exit_code, duration_seconds, status, restaged_file in rows:
        safe_file = restaged_file.replace("|", "\\|")
        lines.append(
            f"| {production} | {exit_code} | {duration_seconds} | {status} | {safe_file} |"
        )

    lines.append("")
    lines.append(f"Totals: {pass_count} passed, {fail_count} failed")
    if parse_error_count:
        lines.append(f"Warning: {parse_error_count} result file(s) could not be parsed")

    return "\n".join(lines)


if __name__ == "__main__":
    raw_directories = os.environ.get("RESULTS_DIRECTORIES", "")
    print(build_summary(raw_directories))
