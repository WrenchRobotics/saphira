"""Single-entry-point orchestration for Saphira fanout runs."""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from saphirapy.detect_production_map import detect_production_mapping
from saphirapy.fanout import (
    DEFAULT_API_URL,
    FANOUT_PATH,
    FANOUT_TOKEN_ENV,
    FanoutResolution,
    api_endpoint,
    resolve_fanout,
)
from saphirapy.github_oidc import OIDC_MODES
from saphirapy.github_actions_contracts import (
    build_cast_production_outputs,
    build_remote_actor_resolver,
    finalize_result_payload,
)

# The fanout service bounds local_cases; it only reads their identity, so send no more than that.
MAX_CONTEXT_LOCAL_CASES = 500
_GITHUB_CONTEXT_ENV = {
    "repository": "GITHUB_REPOSITORY",
    "sha": "GITHUB_SHA",
    "run_id": "GITHUB_RUN_ID",
    "run_attempt": "GITHUB_RUN_ATTEMPT",
}


def locate_example(path: Path) -> Path:
    if path.is_file():
        return path
    candidates = sorted(path.rglob("*.py"))
    for candidate in candidates:
        if "brom_drake.productions" in candidate.read_text():
            return candidate
    raise FileNotFoundError(f"No production example found under {path}")


def _case_comparison_metadata(case: dict[str, str]) -> dict | None:
    raw = case.get("comparison-metadata-json")
    return json.loads(raw) if raw else None


def _run_case(case: dict[str, str], target_file: str, restaged_root: str, results_root: str,
              run_timeout_seconds: int, request_id: str | None = None) -> dict[str, str]:
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
        _case_comparison_metadata(case),
    )
    payload["case_id"] = case_id
    payload["fanout_source"] = case.get("source", "local")
    payload["fanout_request_id"] = request_id
    result_path.write_text(json.dumps(payload, indent=2))
    return {"case-id": case_id, "result-json-path": str(result_path), "exit-code": str(exit_code)}


def _record_worker_failure(case: dict[str, str], results_root: Path, error: Exception,
                           request_id: str | None = None) -> dict[str, str]:
    result_path = results_root / f"{case['case-id']}.json"
    payload = finalize_result_payload(
        result_path,
        case["production-class"],
        1,
        0.0,
        "",
        case["cast-production-name"],
        _case_comparison_metadata(case),
    )
    payload["case_id"] = case["case-id"]
    payload["fanout_source"] = case.get("source", "local")
    payload["fanout_request_id"] = request_id
    payload["error"] = f"Worker failed: {error}"
    result_path.write_text(json.dumps(payload, indent=2))
    return {"case-id": case["case-id"], "result-json-path": str(result_path), "exit-code": "1"}


class ProgressReporter:
    def __init__(self, total: int, mode: str) -> None:
        if mode not in {"auto", "bar", "lines", "none"}:
            raise ValueError(f"Unknown progress mode: {mode}")
        if mode == "auto":
            mode = "lines" if os.environ.get("GITHUB_ACTIONS") or not sys.stderr.isatty() else "bar"
        self.mode = mode
        self.total = total
        self.completed = 0
        self.bar = (
            tqdm(total=total, desc="Saphira cast productions", unit="case", dynamic_ncols=False)
            if mode == "bar"
            else None
        )

    def record(self, result: dict[str, str]) -> None:
        self.completed += 1
        passed = result["exit-code"] == "0"
        status = "passed" if passed else f"failed (exit code {result['exit-code']})"
        if self.bar is not None:
            self.bar.update(1)
            self.bar.set_postfix(case=result["case-id"], status="passed" if passed else "failed")
        elif self.mode == "lines":
            print(
                f"Saphira facade: [{self.completed}/{self.total}] "
                f"{result['case-id']} {status}",
                flush=True,
            )

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()


def run_fanout(
    target_file: Path,
    resolution: FanoutResolution,
    restaged_root: Path,
    results_root: Path,
    max_workers: int | None,
    run_timeout_seconds: int,
    progress_mode: str = "auto",
) -> list[dict[str, str]]:
    results_root.mkdir(parents=True, exist_ok=True)
    restaged_root.mkdir(parents=True, exist_ok=True)
    worker_count = max_workers or min(os.cpu_count() or 1, max(1, len(resolution.cases)))
    worker_count = max(1, min(worker_count, max(1, len(resolution.cases))))
    completed: list[dict[str, str]] = []
    progress = ProgressReporter(len(resolution.cases), progress_mode)
    try:
        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(
                    _run_case,
                    case,
                    str(target_file.resolve()),
                    str(restaged_root),
                    str(results_root),
                    run_timeout_seconds,
                    resolution.request_id,
                ): case
                for case in resolution.cases
            }
            for future in as_completed(futures):
                case = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    result = _record_worker_failure(
                        case, results_root, error, resolution.request_id
                    )
                completed.append(result)
                progress.record(result)
    finally:
        progress.close()
    return sorted(completed, key=lambda result: result["case-id"])


def parse_fanout_kinds(raw: str | None) -> list[str] | None:
    """Parse a comma-separated kind list; empty means "everything the key permits" (None)."""
    kinds = list(dict.fromkeys(kind.strip() for kind in (raw or "").split(",") if kind.strip()))
    return kinds or None


def build_fanout_context(
    detection: dict,
    local_cases: list[dict[str, str]],
    fanout_kinds: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> dict:
    """Describe this run to the fanout service. Every field is a selection hint, not a credential."""
    environ = os.environ if environ is None else environ
    context: dict = {
        key: environ[name] for key, name in _GITHUB_CONTEXT_ENV.items() if environ.get(name)
    }
    for key, detection_key in (
        ("detected_parent", "detected_parent_class"),
        ("detected_production", "detected_production_class"),
    ):
        value = detection.get(detection_key)
        if value and value != "UNMAPPED":
            context[key] = str(value)
    if fanout_kinds:
        context["fanout_kinds"] = fanout_kinds
    context["local_cases"] = [
        {"case-id": case["case-id"], "production-class": case["production-class"]}
        for case in local_cases[:MAX_CONTEXT_LOCAL_CASES]
    ]
    return context


def report_fanout_warnings(warnings: list[str], summary_path: str | None) -> None:
    """Surface skipped remote cases as workflow annotations and in the job summary."""
    if not warnings:
        return
    for warning in warnings:
        print(f"::warning title=Saphira fanout::{warning}", flush=True)
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("### Saphira fanout warnings\n\n")
            handle.writelines(f"- {warning}\n" for warning in warnings)
            handle.write("\n")


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
    parser.add_argument(
        "--api-url",
        default=DEFAULT_API_URL,
        help="The Wrench API. The fanout endpoint and the OIDC audience are derived from it.",
    )
    parser.add_argument(
        "--fanout-endpoint",
        help=f"Deprecated: overrides the fanout endpoint, which defaults to {FANOUT_PATH} on "
        "--api-url.",
    )
    parser.add_argument(
        "--fanout-token",
        help=f"Bearer token for the fanout endpoint. Prefer the {FANOUT_TOKEN_ENV} environment "
        "variable, which keeps the token out of the process list.",
    )
    parser.add_argument(
        "--fanout-kinds",
        default="",
        help="Comma-separated remote fanout kinds, e.g. productions,casts. Empty requests every "
        "kind the token permits.",
    )
    parser.add_argument(
        "--fanout-oidc",
        choices=OIDC_MODES,
        default="auto",
        help="Send a GitHub Actions OIDC token with the fanout request, which keys tied to a "
        "repository require: auto (when the workflow grants id-token: write), always, or never.",
    )
    parser.add_argument("--fanout-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--include-alternative-productions", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-alternative-actors", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--run-timeout-seconds", type=int, default=900)
    parser.add_argument("--progress", choices=("auto", "bar", "lines", "none"), default="auto")
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
        endpoint=args.fanout_endpoint or api_endpoint(args.api_url, FANOUT_PATH),
        token=args.fanout_token,
        timeout_seconds=args.fanout_timeout_seconds,
        context=build_fanout_context(
            detection, local_cases, parse_fanout_kinds(args.fanout_kinds)
        ),
        actor_resolver=build_remote_actor_resolver(target_file),
        oidc=args.fanout_oidc,
    )
    report_fanout_warnings(resolution.warnings, os.environ.get("GITHUB_STEP_SUMMARY"))
    results = run_fanout(
        target_file,
        resolution,
        args.restaged_output_directory,
        args.result_directory,
        args.max_concurrency,
        args.run_timeout_seconds,
        args.progress,
    )
    outputs = {
        "example-file": str(target_file),
        "detected-parent": str(detection.get("detected_parent_class") or "UNMAPPED"),
        "detected-production": str(detection.get("detected_production_class") or "UNMAPPED"),
        "fanout-source": resolution.source,
        "remote-status": resolution.remote_status,
        "oidc-status": resolution.oidc_status,
        "fanout-request-id": resolution.request_id or "",
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