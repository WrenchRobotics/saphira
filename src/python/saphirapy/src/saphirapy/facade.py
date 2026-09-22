"""Single-entry-point orchestration for Saphira fanout runs."""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from saphirapy.detect_production_map import detect_production_mapping
from saphirapy.fanout import FanoutResolution, resolve_fanout
from saphirapy.github_actions_contracts import (
    build_cast_production_outputs,
    finalize_result_payload,
)


def locate_example(path: Path) -> Path:
    if path.is_file():
        return path
    candidates = sorted(path.rglob("*.py"))
    for candidate in candidates:
        if "brom_drake.productions" in candidate.read_text():
            return candidate
    raise FileNotFoundError(f"No production example found under {path}")


def _run_case(case: dict[str, str], target_file: str, restaged_root: str, results_root: str,
              run_timeout_seconds: int) -> dict[str, str]:
    case_id = case["case-id"]
    result_path = Path(results_root) / f"{case_id}.json"
    output_dir = Path(restaged_root) / case_id
    command = [
        sys.executable,
        "-m",
        "saphirapy.restage_and_run_production",
        "--target-file",
        target_file,
        "--production-class",
        case["production-class"],
        "--output-dir",
        str(output_dir),
        "--result-json",
        str(result_path),
        "--cast-production-name",
        case["cast-production-name"],
    ]
    if case["actor-variant-json"]:
        command.extend(["--actor-variant-json", case["actor-variant-json"]])

    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=run_timeout_seconds,
        )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124
    duration = time.monotonic() - started
    payload = finalize_result_payload(
        result_path,
        case["production-class"],
        exit_code,
        duration,
        str(output_dir),
        case["cast-production-name"],
    )
    payload["case_id"] = case_id
    payload["fanout_source"] = case.get("source", "local")
    result_path.write_text(json.dumps(payload, indent=2))
    return {"case-id": case_id, "result-json-path": str(result_path), "exit-code": str(exit_code)}


def run_fanout(
    target_file: Path,
    resolution: FanoutResolution,
    restaged_root: Path,
    results_root: Path,
    max_workers: int | None,
    run_timeout_seconds: int,
) -> list[dict[str, str]]:
    results_root.mkdir(parents=True, exist_ok=True)
    restaged_root.mkdir(parents=True, exist_ok=True)
    worker_count = max_workers or min(os.cpu_count() or 1, max(1, len(resolution.cases)))
    worker_count = max(1, min(worker_count, max(1, len(resolution.cases))))
    completed: list[dict[str, str]] = []
    with ProcessPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(
                _run_case,
                case,
                str(target_file.resolve()),
                str(restaged_root),
                str(results_root),
                run_timeout_seconds,
            ): case
            for case in resolution.cases
        }
        for future in as_completed(futures):
            completed.append(future.result())
    return sorted(completed, key=lambda result: result["case-id"])


def write_output(path: str | None, key: str, value: str) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        if "\n" in value:
            handle.write(f"{key}<<EOF\n{value}\nEOF\n")
        else:
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect, augment, and run Saphira fanout cases.")
    parser.add_argument("--path-to-example-run", type=Path, required=True)
    parser.add_argument("--fanout-source", choices=("local", "remote", "remote-with-fallback"), default="local")
    parser.add_argument("--fanout-endpoint")
    parser.add_argument("--fanout-token")
    parser.add_argument("--fanout-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--include-alternative-productions", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-alternative-actors", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--run-timeout-seconds", type=int, default=900)
    parser.add_argument("--restaged-output-directory", type=Path, default=Path("productions/facade-restaged"))
    parser.add_argument("--result-directory", type=Path, default=Path("productions/facade-results"))
    parser.add_argument("--github-output")
    args = parser.parse_args()

    target_file = locate_example(args.path_to_example_run).resolve()
    detection = detect_production_mapping(target_file)
    local_outputs = build_cast_production_outputs(
        json.dumps(detection),
        target_file,
        args.include_alternative_productions,
        args.include_alternative_actors,
    )
    local_cases = json.loads(local_outputs["cast-productions-json"])
    resolution = resolve_fanout(
        local_cases,
        source=args.fanout_source,
        endpoint=args.fanout_endpoint,
        token=args.fanout_token,
        timeout_seconds=args.fanout_timeout_seconds,
    )
    results = run_fanout(
        target_file,
        resolution,
        args.restaged_output_directory,
        args.result_directory,
        args.max_concurrency,
        args.run_timeout_seconds,
    )
    outputs = {
        "example-file": str(target_file),
        "detected-parent": str(detection.get("detected_parent_class") or "UNMAPPED"),
        "detected-production": str(detection.get("detected_production_class") or "UNMAPPED"),
        "fanout-source": resolution.source,
        "remote-status": resolution.remote_status,
        "fanout-cases-json": json.dumps(resolution.cases),
        "results-directory": str(args.result_directory),
        "result-count": str(len(results)),
        "failed-count": str(sum(result["exit-code"] != "0" for result in results)),
    }
    for key, value in outputs.items():
        write_output(args.github_output or os.environ.get("GITHUB_OUTPUT"), key, value)
    return 0 if outputs["failed-count"] == "0" else 1


if __name__ == "__main__":
    raise SystemExit(main())