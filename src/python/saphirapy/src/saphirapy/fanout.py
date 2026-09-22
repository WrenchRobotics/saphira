"""Fanout contracts and optional remote fanout augmentation."""

import json
from dataclasses import dataclass
from http.client import HTTPResponse
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class FanoutRemoteError(RuntimeError):
    """A remote fanout request failed, with fallback eligibility attached."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class FanoutResolution:
    cases: list[dict[str, str]]
    source: str
    remote_status: str


def normalize_fanout_case(case: dict[str, Any], index: int) -> dict[str, str]:
    """Validate and normalize one local or remote cast-production case."""
    if not isinstance(case, dict):
        raise ValueError(f"Fanout case {index} must be an object")

    production_class = str(case.get("production-class") or "").strip()
    if not production_class:
        raise ValueError(f"Fanout case {index} is missing production-class")

    actor_variant = case.get("actor-variant-json", "")
    if not isinstance(actor_variant, str):
        actor_variant = json.dumps(actor_variant, sort_keys=True)

    case_id = str(case.get("case-id") or case.get("cast-production-name") or "").strip()
    if not case_id:
        case_id = f"production-{production_class}"

    normalized = {
        "case-id": case_id,
        "production-class": production_class,
        "actor-variant-json": actor_variant,
        "cast-production-name": str(
            case.get("cast-production-name") or case_id
        ),
        "artifact-name": str(case.get("artifact-name") or f"matrix-result-{case_id}"),
    }
    if "source" in case:
        normalized["source"] = str(case["source"])
    return normalized


def normalize_fanout_cases(cases: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [normalize_fanout_case(case, index) for index, case in enumerate(cases)]


def merge_fanout_cases(
    local_cases: list[dict[str, Any]], remote_cases: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Merge remote additions without allowing duplicates to run twice."""
    merged: list[dict[str, str]] = []
    seen_case_ids: set[str] = set()
    for source, cases in (("local", local_cases), ("remote", remote_cases)):
        for case in normalize_fanout_cases(cases):
            case["source"] = case.get("source", source)
            if case["case-id"] in seen_case_ids:
                continue
            seen_case_ids.add(case["case-id"])
            merged.append(case)
    return merged


def _decode_remote_cases(response: HTTPResponse) -> list[dict[str, Any]]:
    try:
        payload = json.loads(response.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FanoutRemoteError("Remote fanout response was not valid JSON", retryable=False) from error

    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise FanoutRemoteError("Remote fanout response must contain a cases list", retryable=False)
    return cases


def fetch_remote_fanout(
    endpoint: str,
    *,
    token: str | None = None,
    timeout_seconds: float = 10.0,
    opener: Callable[..., HTTPResponse] = urlopen,
) -> list[dict[str, Any]]:
    """Fetch additional cases without logging or exposing the bearer token."""
    request = Request(endpoint, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with opener(request, timeout=timeout_seconds) as response:
            if response.status >= 500:
                raise FanoutRemoteError(
                    f"Remote fanout service returned HTTP {response.status}", retryable=True
                )
            if response.status >= 400:
                raise FanoutRemoteError(
                    f"Remote fanout service returned HTTP {response.status}", retryable=False
                )
            return _decode_remote_cases(response)
    except HTTPError as error:
        raise FanoutRemoteError(
            f"Remote fanout service returned HTTP {error.code}",
            retryable=error.code >= 500,
        ) from error
    except (TimeoutError, URLError, OSError) as error:
        raise FanoutRemoteError("Remote fanout service was unreachable", retryable=True) from error


def resolve_fanout(
    local_cases: list[dict[str, Any]],
    *,
    source: str = "local",
    endpoint: str | None = None,
    token: str | None = None,
    timeout_seconds: float = 10.0,
    opener: Callable[..., HTTPResponse] = urlopen,
) -> FanoutResolution:
    """Resolve local cases and optional remote additions.

    ``remote-with-fallback`` falls back only for retryable transport/service failures.
    Invalid responses and authorization failures remain hard errors.
    """
    if source == "local":
        return FanoutResolution(merge_fanout_cases(local_cases, []), "local", "disabled")
    if source not in {"remote", "remote-with-fallback"}:
        raise ValueError(f"Unknown fanout source: {source}")
    if not endpoint:
        raise ValueError("fanout endpoint is required for remote fanout")

    try:
        remote_cases = fetch_remote_fanout(
            endpoint,
            token=token,
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
    except FanoutRemoteError as error:
        if source == "remote-with-fallback" and error.retryable:
            return FanoutResolution(
                merge_fanout_cases(local_cases, []), "local-fallback", str(error)
            )
        raise

    return FanoutResolution(
        merge_fanout_cases(local_cases, remote_cases), "local-plus-remote", "available"
    )