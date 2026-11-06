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
    FanoutResolution,
    api_endpoint,
    resolve_fanout,
)
from saphirapy.github_actions_contracts import (
    build_cast_production_outputs,
    build_remote_actor_resolver,
    fails_run,
    finalize_result_payload,
)

# Case kinds beyond the baseline: productions are the related productions, casts the alternative
# actors for each cast call.
KINDS = ("productions", "casts")
# The kinds of local cases when none are chosen. Remote fanout then gets every kind the key permits.
DEFAULT_LOCAL_KINDS = ("productions",)
# Debugging knobs. They set the defaults of --fanout-timeout-seconds and --progress, so they reach
# the facade from a workflow's env without being action inputs.
FANOUT_TIMEOUT_ENV = "SAPHIRA_FANOUT_TIMEOUT_SECONDS"
PROGRESS_ENV = "SAPHIRA_PROGRESS"
PROGRESS_MODES = ("auto", "bar", "lines", "none")
# The fanout service bounds local_cases; it only reads their identity, so send no more than that.
MAX_CONTEXT_LOCAL_CASES = 500
_GITHUB_CONTEXT_ENV = {
    "repository": "GITHUB_REPOSITORY",
    "sha": "GITHUB_SHA",
    "run_id": "GITHUB_RUN_ID",
    "run_attempt": "GITHUB_RUN_ATTEMPT",
}
# How much of a case's stderr the result JSON and the job log keep when the case does not pass.
# The full output stays in the case's log files in the artifact.
STDERR_TAIL_LINES = 40
STDERR_TAIL_CHARACTERS = 4000


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


def _decode_output(output: str | bytes | None) -> str:
    # TimeoutExpired carries bytes even when the process was run with text=True.
    if isinstance(output, bytes):
        return output.decode(errors="replace")
    return output or ""


def stderr_tail(stderr: str) -> str:
    """Return the last lines of a case's stderr, short enough for a result JSON and a job log."""
    tail = "\n".join(stderr.rstrip().splitlines()[-STDERR_TAIL_LINES:])
    if len(tail) > STDERR_TAIL_CHARACTERS:
        tail = tail[-STDERR_TAIL_CHARACTERS:]
    return tail


def _write_case_logs(output_dir: Path, stdout: str, stderr: str) -> dict[str, str]:
    stdout_log = output_dir / "stdout.log"
    stderr_log = output_dir / "stderr.log"
    stdout_log.write_text(stdout)
    stderr_log.write_text(stderr)
    return {"stdout_log": str(stdout_log), "stderr_log": str(stderr_log)}


def _case_environment() -> dict[str, str]:
    # Each case runs in its own restaged directory, so that files a production writes relative
    # to the working directory (brom_drake's ./brom watcher output, which it deletes and recreates
    # on startup) are not shared with cases running at the same time. `python -m` would otherwise
    # put that directory on sys.path instead of the workspace, so keep the workspace importable.
    environment = dict(os.environ)
    paths = [os.getcwd(), environment.get("PYTHONPATH", "")]
    environment["PYTHONPATH"] = os.pathsep.join(path for path in paths if path)
    return environment


def _run_case(case: dict[str, str], target_file: str, restaged_root: str, results_root: str,
              run_timeout_seconds: int, request_id: str | None = None) -> dict[str, str]:
    case_id = case["case-id"]
    result_path = (Path(results_root) / f"{case_id}.json").resolve()
    output_dir = (Path(restaged_root) / case_id).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
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
            cwd=output_dir,
            env=_case_environment(),
        )
        exit_code = completed.returncode
        stdout, stderr = completed.stdout, completed.stderr
    except subprocess.TimeoutExpired as timeout:
        exit_code = 124
        stdout, stderr = _decode_output(timeout.stdout), _decode_output(timeout.stderr)
    duration = time.monotonic() - started
    log_files = _write_case_logs(output_dir, stdout, stderr)
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
    payload.update(log_files)
    tail = stderr_tail(stderr) if payload["status"] != "passed" else ""
    if tail:
        payload["stderr_tail"] = tail
    result_path.write_text(json.dumps(payload, indent=2))
    return {
        "case-id": case_id,
        "result-json-path": str(result_path),
        "exit-code": str(exit_code),
        "status": payload["status"],
        "stderr-tail": tail,
    }


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
    return {
        "case-id": case["case-id"],
        "result-json-path": str(result_path),
        "exit-code": "1",
        "status": payload["status"],
    }


class ProgressReporter:
    def __init__(self, total: int, mode: str) -> None:
        if mode not in PROGRESS_MODES:
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
        status = result["status"]
        if status != "passed":
            status = f"{status} (exit code {result['exit-code']})"
        if self.bar is not None:
            self.bar.update(1)
            self.bar.set_postfix(case=result["case-id"], status=result["status"])
        elif self.mode == "lines":
            print(
                f"Saphira facade: [{self.completed}/{self.total}] "
                f"{result['case-id']} {status}",
                flush=True,
            )
        tail = result.get("stderr-tail")
        if tail and self.mode != "none":
            self._write_stderr_tail(result["case-id"], tail)

    def _write_stderr_tail(self, case_id: str, tail: str) -> None:
        if os.environ.get("GITHUB_ACTIONS"):
            text = f"::group::{case_id} stderr (last lines)\n{tail}\n::endgroup::"
        else:
            text = f"--- {case_id} stderr (last lines) ---\n{tail}"
        if self.bar is not None:
            self.bar.write(text)
        else:
            print(text, flush=True)

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


def parse_kinds(raw: str | None) -> list[str] | None:
    """Parse a comma-separated kind list; empty means the defaults (None)."""
    kinds = list(dict.fromkeys(kind.strip() for kind in (raw or "").split(",") if kind.strip()))
    unknown = [kind for kind in kinds if kind not in KINDS]
    if unknown:
        raise ValueError(f"Unknown kinds: {', '.join(unknown)}. Choose from {', '.join(KINDS)}.")
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
        "--kinds",
        default="",
        help=f"Comma-separated case kinds to run besides the baseline: {', '.join(KINDS)}. Empty "
        f"runs {', '.join(DEFAULT_LOCAL_KINDS)} locally and requests every kind the key permits "
        "from the Wrench API.",
    )
    # argparse converts string defaults with type, so a bad value is refused like a bad flag.
    parser.add_argument(
        "--fanout-timeout-seconds", type=float, default=os.environ.get(FANOUT_TIMEOUT_ENV, "10")
    )
    parser.add_argument("--max-concurrency", type=int)
    parser.add_argument("--run-timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--progress", choices=PROGRESS_MODES, default=os.environ.get(PROGRESS_ENV) or "auto"
    )
    parser.add_argument("--restaged-output-directory", type=Path, default=Path("productions/facade-restaged"))
    parser.add_argument("--result-directory", type=Path, default=Path("productions/facade-results"))
    parser.add_argument("--github-output")
    args = parser.parse_args()
    if args.progress not in PROGRESS_MODES:
        parser.error(f"{PROGRESS_ENV} must be one of {', '.join(PROGRESS_MODES)}")

    try:
        kinds = parse_kinds(args.kinds)
    except ValueError as error:
        parser.error(str(error))
    local_kinds = kinds or DEFAULT_LOCAL_KINDS
    target_file = locate_example(args.path_to_example_run).resolve()
    detection = detect_production_mapping(target_file)
    local_outputs = build_cast_production_outputs(
        json.dumps(detection),
        target_file,
        "productions" in local_kinds,
        "casts" in local_kinds,
    )
    local_cases = json.loads(local_outputs["cast-productions-json"])
    resolution = resolve_fanout(
        local_cases,
        source=args.fanout_source,
        endpoint=api_endpoint(args.api_url, FANOUT_PATH),
        timeout_seconds=args.fanout_timeout_seconds,
        context=build_fanout_context(
            detection, local_cases, kinds
        ),
        actor_resolver=build_remote_actor_resolver(target_file),
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
        "remote-case-count": str(resolution.remote_case_count),
        "results-directory": str(args.result_directory),
        "result-count": str(len(results)),
        # Cases that timed out are simulation outcomes: reported, but not failures of the run.
        "failed-count": str(sum(fails_run(result["status"]) for result in results)),
        "timed-out-count": str(sum(result["status"] == "timed-out" for result in results)),
    }
    for key, value in outputs.items():
        write_output(args.github_output or os.environ.get("GITHUB_OUTPUT"), key, value)
    return 0 if outputs["failed-count"] == "0" else 1


if __name__ == "__main__":
    raise SystemExit(main())