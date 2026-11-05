"""Submit a run's results to the Wrench API, which posts them as the Saphira GitHub App.

``POST /v1/saphira/reports`` takes one section of a run's results. It requires two credentials:
a Wrench API key, which says which account reports, and a GitHub Actions OIDC token, which proves
the repository, run, and commit. The app then posts a GitHub Check, and a PR comment for pull
request runs. Resubmitting the same run attempt and section updates them in place.

When the app cannot post (no key, no OIDC token as on fork pull requests, the app not installed or
not connected), ``report-mode`` ``auto`` falls back to the workflow's own PR comment and says why
in the job summary. Neither token is ever logged.
"""

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from http.client import HTTPResponse
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from saphirapy.facade import write_output
from saphirapy.fanout import API_KEY_ENV, DEFAULT_API_URL, api_endpoint
from saphirapy.github_actions_contracts import ResultRecord, collect_result_records
from saphirapy.github_oidc import (
    OIDC_HEADER,
    PERMISSION_HINT,
    OIDCTokenError,
    audience_for,
    oidc_available,
    request_oidc_token,
)

REPORT_SCHEMA_VERSION = 1
REPORTS_PATH = "/v1/saphira/reports"
REPORT_USER_AGENT = f"saphira-report (report-schema/{REPORT_SCHEMA_VERSION})"
# auto: post as the app, else fall back to the workflow comment. app: post as the app or fail.
# workflow: only the workflow comment. none: neither, only the job summary.
REPORT_MODES = ("auto", "app", "workflow", "none")
# Statuses worth retrying: the endpoint is idempotent per run, attempt, and section.
RETRYABLE_STATUSES = frozenset({502, 503})

# The API's limits (ReportRequest in wrench-backend). Longer values are cut rather than refused.
MAX_REPORT_BODY_BYTES = 256 * 1024
MAX_REPORT_RESULTS = 500
MAX_MAP_ENTRIES = 50
MAX_TEXT = 200
MAX_MAP_KEY = 100
MAX_SECTION_KEY = 64
_MAX_ERROR_DETAIL = 300


class ReportError(RuntimeError):
    """The API did not accept the report. ``link`` is a URL that fixes the problem, if any."""

    def __init__(self, message: str, *, status: int | None = None, link: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.link = link


@dataclass(frozen=True)
class ReportOutcome:
    # posted, fallback, workflow, skipped, or failed.
    status: str
    report_url: str = ""
    report_id: str = ""
    # Why the app did not post, with a link that fixes it when the API gave one.
    reason: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def workflow_comment(self) -> bool:
        """Whether the workflow should post its own PR comment."""
        return self.status in {"fallback", "workflow"}


# --- Body -------------------------------------------------------------------------------------


def normalize_section_key(raw: str, fallback: str = "default") -> str:
    """Normalize a section key as the workflow comment does, within the API's pattern."""
    key = "".join(c if c.isascii() and (c.isalnum() or c in "._-") else "-" for c in raw.lower())
    key = "-".join(part for part in key.split("-") if part)
    return key[:MAX_SECTION_KEY].strip("-") or fallback


def _text(value: Any) -> str:
    return str(value)[:MAX_TEXT]


def _metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:MAX_TEXT]
    return json.dumps(value, sort_keys=True, default=str)[:MAX_TEXT]


def _report_row(record: ResultRecord) -> dict[str, Any]:
    metadata = list(record["comparison_metadata"].items())[:MAX_MAP_ENTRIES]
    metrics = list(record["metrics"].items())[:MAX_MAP_ENTRIES]
    return {
        "production": _text(record["production"]),
        "production_class": _text(record["production_class"]),
        "exit_code": _text(record["exit_code"]),
        "duration_seconds": _text(record["duration_seconds"]),
        "status": _text(record["status"]),
        "comparison_type": _text(record["comparison_type"]),
        "comparison_metadata": {
            str(key)[:MAX_MAP_KEY]: _metadata_value(value) for key, value in metadata
        },
        "metrics": {str(key)[:MAX_MAP_KEY]: _text(value) for key, value in metrics},
    }


def _saphira_version() -> str | None:
    try:
        return version("saphirapy")
    except PackageNotFoundError:
        return None


def _known(value: str | None) -> str | None:
    value = (value or "").strip()
    return value[:MAX_TEXT] if value and value != "UNMAPPED" else None


def build_report(
    results_directories: str,
    *,
    section_key: str,
    section_heading: str = "",
    detected_parent: str | None = None,
    detected_production: str | None = None,
    fanout_source: str | None = None,
    remote_status: str | None = None,
    fanout_request_id: str | None = None,
    artifact_id: str | None = None,
    artifact_name: str | None = None,
) -> dict[str, Any]:
    """Build the report body from the result files. Identifies the run only through OIDC."""
    records = collect_result_records(results_directories)
    fallback_key = results_directories.strip() or "default"
    body: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "section_key": normalize_section_key(section_key or fallback_key),
        "section_heading": section_heading.strip()[:MAX_TEXT],
        "detected_parent": _known(detected_parent),
        "detected_production": _known(detected_production),
        "fanout": {
            "source": _known(fanout_source),
            "remote_status": _known(remote_status),
            "request_id": _known(fanout_request_id),
        },
        "saphira_version": _saphira_version(),
        "results": [_report_row(record) for record in records[:MAX_REPORT_RESULTS]],
    }
    if (artifact_id or "").isdigit() or _known(artifact_name):
        body["artifact"] = {
            "id": int(artifact_id) if (artifact_id or "").isdigit() else None,
            "name": _known(artifact_name),
        }
    return body


# --- Request ----------------------------------------------------------------------------------


def _error_body(body: Any) -> dict[str, Any]:
    try:
        payload = json.loads(body.read().decode("utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _report_error(status: int, body: Any) -> ReportError:
    payload = _error_body(body)
    detail = payload.get("detail")
    message = f"The Wrench API refused the report (HTTP {status})"
    if isinstance(detail, str) and detail.strip():
        # Error details reach CI logs and job summaries; keep them single-line and short.
        message += f": {' '.join(detail.split())[:_MAX_ERROR_DETAIL]}"
    link = payload.get("install_url") or payload.get("dashboard_url")
    return ReportError(message, status=status, link=link if isinstance(link, str) else None)


def submit_report(
    api_url: str,
    body: dict[str, Any],
    *,
    api_key: str,
    oidc_token: str,
    timeout_seconds: float = 30.0,
    attempts: int = 3,
    opener: Callable[..., HTTPResponse] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """POST the report and return the API's response, retrying on 502, 503, and no connection."""
    data = json.dumps(body).encode("utf-8")
    if len(data) > MAX_REPORT_BODY_BYTES:
        raise ReportError(
            f"The report is {len(data)} bytes, over the API's limit of {MAX_REPORT_BODY_BYTES}"
        )
    request = Request(
        api_endpoint(api_url, REPORTS_PATH),
        data=data,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": REPORT_USER_AGENT,
            "Authorization": f"Bearer {api_key}",
            OIDC_HEADER: oidc_token,
        },
    )
    for attempt in range(1, attempts + 1):
        try:
            with opener(request, timeout=timeout_seconds) as response:
                if response.status < 400:
                    payload = json.loads(response.read().decode("utf-8"))
                    if not isinstance(payload, dict):
                        raise ReportError("The Wrench API's response was not an object")
                    return payload
                failure = _report_error(response.status, response)
        except HTTPError as error:
            failure = _report_error(error.code, error)
        except (TimeoutError, URLError, OSError):
            failure = ReportError("The Wrench API was unreachable")
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReportError("The Wrench API's response was not valid JSON") from error

        retryable = failure.status is None or failure.status in RETRYABLE_STATUSES
        if not retryable or attempt == attempts:
            raise failure
        sleep(2.0 * attempt)
    raise AssertionError("unreachable")  # pragma: no cover


def run_report(
    mode: str,
    body: dict[str, Any],
    *,
    api_url: str = DEFAULT_API_URL,
    api_key: str | None = None,
    environ: Mapping[str, str] | None = None,
    timeout_seconds: float = 30.0,
    opener: Callable[..., HTTPResponse] = urlopen,
    sleep: Callable[[float], None] = time.sleep,
) -> ReportOutcome:
    """Report as ``mode`` says. ``api_key`` defaults to the ``WRENCH_API_KEY`` variable."""
    if mode not in REPORT_MODES:
        raise ValueError(f"Unknown report mode: {mode}")
    if mode == "none":
        return ReportOutcome("skipped")
    if mode == "workflow":
        return ReportOutcome("workflow")

    environ = os.environ if environ is None else environ
    api_key = (api_key or environ.get(API_KEY_ENV) or "").strip() or None
    try:
        if api_key is None:
            raise ReportError("No Wrench API key was provided: set the api-key input")
        if not oidc_available(environ):
            raise ReportError(
                f"No GitHub Actions OIDC token is available: {PERMISSION_HINT} (pull requests "
                "from forks never get one)"
            )
        try:
            oidc_token = request_oidc_token(
                audience_for(api_url),
                environ=environ,
                timeout_seconds=timeout_seconds,
                opener=opener,
            )
        except OIDCTokenError as error:
            raise ReportError(str(error)) from error
        if oidc_token is None:
            raise ReportError("GitHub provided no OIDC token")
        response = submit_report(
            api_url,
            body,
            api_key=api_key,
            oidc_token=oidc_token,
            timeout_seconds=timeout_seconds,
            opener=opener,
            sleep=sleep,
        )
    except ReportError as error:
        reason = f"{error} ({error.link})" if error.link else str(error)
        return ReportOutcome("fallback" if mode == "auto" else "failed", reason=reason)

    warnings = response.get("warnings")
    return ReportOutcome(
        "posted",
        report_url=str(response.get("comment_url") or response.get("check_run_url") or ""),
        report_id=str(response.get("report_id") or ""),
        warnings=[str(w) for w in warnings] if isinstance(warnings, list) else [],
    )


# --- Workflow output --------------------------------------------------------------------------


def report_outcome(outcome: ReportOutcome, summary_path: str | None) -> None:
    """Surface the outcome as workflow annotations and in the job summary."""
    lines: list[str] = []
    if outcome.status == "posted":
        lines.append(f"Posted by the Saphira GitHub App: {outcome.report_url}")
    elif outcome.status == "fallback":
        print(f"::warning title=Saphira report::{outcome.reason}", flush=True)
        lines.append(f"The Saphira GitHub App did not post these results. {outcome.reason}.")
        lines.append("The workflow posts its own PR comment instead.")
    elif outcome.status == "failed":
        print(f"::error title=Saphira report::{outcome.reason}", flush=True)
        lines.append(f"The Saphira GitHub App could not post these results. {outcome.reason}.")
    for warning in outcome.warnings:
        print(f"::warning title=Saphira report::{warning}", flush=True)
        lines.append(f"- {warning}")
    if summary_path and lines:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("### Saphira report\n\n")
            handle.writelines(f"{line}\n" for line in lines)
            handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Submit Saphira results to the Wrench API. The API key is read from "
        f"{API_KEY_ENV}, never the command line."
    )
    parser.add_argument("--results-directories", required=True)
    parser.add_argument("--report-mode", choices=REPORT_MODES, default="auto")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--section-key", default="")
    parser.add_argument("--section-heading", default="")
    parser.add_argument("--detected-parent")
    parser.add_argument("--detected-production")
    parser.add_argument("--fanout-source")
    parser.add_argument("--remote-status")
    parser.add_argument("--fanout-request-id")
    parser.add_argument("--artifact-id")
    parser.add_argument("--artifact-name")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--github-output")
    args = parser.parse_args()

    body = build_report(
        args.results_directories,
        section_key=args.section_key,
        section_heading=args.section_heading,
        detected_parent=args.detected_parent,
        detected_production=args.detected_production,
        fanout_source=args.fanout_source,
        remote_status=args.remote_status,
        fanout_request_id=args.fanout_request_id,
        artifact_id=args.artifact_id,
        artifact_name=args.artifact_name,
    )
    outcome = run_report(
        args.report_mode, body, api_url=args.api_url, timeout_seconds=args.timeout_seconds
    )
    report_outcome(outcome, os.environ.get("GITHUB_STEP_SUMMARY"))
    github_output = args.github_output or os.environ.get("GITHUB_OUTPUT")
    for key, value in {
        "report-status": outcome.status,
        "report-url": outcome.report_url,
        "report-id": outcome.report_id,
        "workflow-comment": str(outcome.workflow_comment).lower(),
    }.items():
        write_output(github_output, key, value)
    return 1 if outcome.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
