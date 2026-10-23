"""Fanout contracts and optional remote fanout augmentation.

Remote cases are data, never code. Saphira executes every case on the caller's CI runner, so a
remote case may only *select* behaviour Saphira already ships: a production class, and for actor
cases a ``{"cast_call_index", "actor"}`` reference that is expanded from the local actor
registry. A remote case carrying executable fields is rejected outright, even under
``remote-with-fallback``.
"""

import json
import os
import re
from dataclasses import dataclass, field
from http.client import HTTPResponse
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

FANOUT_SCHEMA_VERSION = 1
FANOUT_TOKEN_ENV = "FANOUT_TOKEN"
# urllib's default "Python-urllib/x.y" is blocked by CDN bot checks (e.g. Cloudflare error 1010).
FANOUT_USER_AGENT = f"saphira-facade (fanout-schema/{FANOUT_SCHEMA_VERSION})"

# Fields of a CastMemberOption that carry executable text. Only the local registry may supply them.
CODE_FIELDS = frozenset(
    {"import_statements", "instantiation_code", "variable_name", "method_to_call"}
)
# Remote identifiers become file paths, artifact names, and class lookups on the runner.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,199}$")
_MAX_ERROR_DETAIL = 300


class FanoutRemoteError(RuntimeError):
    """A remote fanout request failed, with fallback eligibility attached."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class RemoteActorUnavailable(LookupError):
    """A remote actor reference that this Saphira install cannot resolve; the case is skipped."""


# Expands (cast_call_index, actor name) into a full local actor variant, or raises
# RemoteActorUnavailable.
ActorResolver = Callable[[int, str], dict[str, Any]]


@dataclass(frozen=True)
class FanoutResolution:
    cases: list[dict[str, str]]
    source: str
    remote_status: str
    warnings: list[str] = field(default_factory=list)


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
    if "comparison-metadata-json" in case:
        normalized["comparison-metadata-json"] = str(case["comparison-metadata-json"])
    return normalized


def normalize_fanout_cases(cases: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [normalize_fanout_case(case, index) for index, case in enumerate(cases)]


def merge_fanout_cases(
    local_cases: list[dict[str, Any]], remote_cases: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Merge remote additions without allowing duplicates to run twice.

    ``remote_cases`` must already have passed :func:`prepare_remote_cases`.
    """
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


# --- Remote case trust boundary ---------------------------------------------------------------


def _untrusted(message: str) -> FanoutRemoteError:
    # Never retryable: a service sending code or malformed cases is an incident, not an outage.
    return FanoutRemoteError(f"Rejected remote fanout case: {message}", retryable=False)


def _find_code_fields(value: Any, path: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in CODE_FIELDS:
                found.append(f"{path}.{key}")
            found.extend(_find_code_fields(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_code_fields(item, f"{path}[{index}]"))
    return found


def _decode_json_field(case: dict[str, Any], key: str, index: int) -> Any:
    raw = case.get(key, "")
    if not isinstance(raw, str):
        return raw
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise _untrusted(f"case {index} has invalid {key}") from error


def _check_remote_case(case: Any, index: int) -> tuple[dict[str, Any], tuple[int, str] | None]:
    """Validate one remote case and return it with its actor reference, if any."""
    if not isinstance(case, dict):
        raise _untrusted(f"case {index} must be an object")

    actor_variant = _decode_json_field(case, "actor-variant-json", index)
    metadata = _decode_json_field(case, "comparison-metadata-json", index)
    found = (
        _find_code_fields(case, f"cases[{index}]")
        + _find_code_fields(actor_variant, f"cases[{index}].actor-variant-json")
        + _find_code_fields(metadata, f"cases[{index}].comparison-metadata-json")
    )
    if found:
        raise _untrusted(
            f"case {index} carries executable fields ({', '.join(found)}); remote cases may "
            "only reference actors by registered name"
        )

    production_class = str(case.get("production-class") or "")
    if not _IDENTIFIER.fullmatch(production_class):
        raise _untrusted(f"case {index} has an invalid production-class")
    for key in ("case-id", "cast-production-name", "artifact-name"):
        if key in case and not _SAFE_NAME.fullmatch(str(case[key])):
            raise _untrusted(f"case {index} has an invalid {key}")

    if actor_variant is None:
        return case, None
    if (
        not isinstance(actor_variant, dict)
        or set(actor_variant) != {"cast_call_index", "actor"}
        or type(actor_variant["cast_call_index"]) is not int
        or actor_variant["cast_call_index"] < 0
        or not isinstance(actor_variant["actor"], str)
        or not _IDENTIFIER.fullmatch(actor_variant["actor"])
    ):
        raise _untrusted(
            f'case {index} actor-variant-json must be {{"cast_call_index": <int>, '
            '"actor": "<registered name>"}'
        )
    return case, (actor_variant["cast_call_index"], actor_variant["actor"])


def prepare_remote_cases(
    remote_cases: list[Any], actor_resolver: ActorResolver | None = None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply the trust boundary to remote cases and expand actor references locally.

    Every case is checked before any is expanded, so one bad case rejects the whole response.
    Returns the runnable cases and warnings for actor references that were skipped.
    """
    checked = [_check_remote_case(case, index) for index, case in enumerate(remote_cases)]

    prepared: list[dict[str, Any]] = []
    warnings: list[str] = []
    for case, reference in checked:
        if reference is None:
            prepared.append(case)
            continue
        cast_call_index, actor_name = reference
        case_id = case.get("case-id") or actor_name
        try:
            if actor_resolver is None:
                raise RemoteActorUnavailable("no local actor registry is available")
            variant = actor_resolver(cast_call_index, actor_name)
        except RemoteActorUnavailable as error:
            warnings.append(f"Skipped remote case {case_id}: {error}")
            continue
        prepared.append({**case, "actor-variant-json": json.dumps(variant)})
    return prepared, warnings


# --- Remote request ---------------------------------------------------------------------------


def _decode_remote_cases(response: HTTPResponse) -> list[dict[str, Any]]:
    try:
        payload = json.loads(response.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FanoutRemoteError("Remote fanout response was not valid JSON", retryable=False) from error

    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise FanoutRemoteError("Remote fanout response must contain a cases list", retryable=False)
    return cases


def _error_detail(body: Any) -> str:
    """Return the service's ``detail`` message from an error body, if it has a usable one."""
    try:
        payload = json.loads(body.read().decode("utf-8"))
    except Exception:
        return ""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if not isinstance(detail, str):
        return ""
    # Error details reach CI logs and PR summaries; keep them single-line and short.
    detail = " ".join(detail.split())
    return f": {detail[:_MAX_ERROR_DETAIL]}"


def _http_error(status: int, body: Any) -> FanoutRemoteError:
    return FanoutRemoteError(
        f"Remote fanout service returned HTTP {status}{_error_detail(body)}",
        retryable=status >= 500,
    )


def fetch_remote_fanout(
    endpoint: str,
    *,
    token: str | None = None,
    timeout_seconds: float = 10.0,
    context: dict[str, Any] | None = None,
    opener: Callable[..., HTTPResponse] = urlopen,
) -> list[dict[str, Any]]:
    """POST the run context and return the service's cases, never logging the bearer token."""
    body = {"schema_version": FANOUT_SCHEMA_VERSION, **(context or {})}
    request = Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": FANOUT_USER_AGENT,
        },
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with opener(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise _http_error(response.status, response)
            return _decode_remote_cases(response)
    except HTTPError as error:
        raise _http_error(error.code, error) from error
    except (TimeoutError, URLError, OSError) as error:
        raise FanoutRemoteError("Remote fanout service was unreachable", retryable=True) from error


def resolve_fanout(
    local_cases: list[dict[str, Any]],
    *,
    source: str = "local",
    endpoint: str | None = None,
    token: str | None = None,
    timeout_seconds: float = 10.0,
    context: dict[str, Any] | None = None,
    actor_resolver: ActorResolver | None = None,
    opener: Callable[..., HTTPResponse] = urlopen,
) -> FanoutResolution:
    """Resolve local cases and optional remote additions.

    ``token`` defaults to the ``FANOUT_TOKEN`` environment variable. ``remote-with-fallback``
    falls back only for retryable transport/service failures and a missing token (as on fork
    pull requests, where secrets are unavailable). Invalid responses, remote cases carrying
    code, and authorization failures remain hard errors.
    """
    if source == "local":
        return FanoutResolution(merge_fanout_cases(local_cases, []), "local", "disabled")
    if source not in {"remote", "remote-with-fallback"}:
        raise ValueError(f"Unknown fanout source: {source}")
    if not endpoint:
        raise ValueError("fanout endpoint is required for remote fanout")

    token = (token or os.environ.get(FANOUT_TOKEN_ENV) or "").strip() or None
    if token is None:
        if source == "remote-with-fallback":
            return FanoutResolution(
                merge_fanout_cases(local_cases, []), "local-fallback", "no fanout token provided"
            )
        raise ValueError(
            f"fanout-source 'remote' needs a token: pass --fanout-token or set {FANOUT_TOKEN_ENV}"
        )

    try:
        remote_cases = fetch_remote_fanout(
            endpoint,
            token=token,
            timeout_seconds=timeout_seconds,
            context=context,
            opener=opener,
        )
    except FanoutRemoteError as error:
        if source == "remote-with-fallback" and error.retryable:
            return FanoutResolution(
                merge_fanout_cases(local_cases, []), "local-fallback", str(error)
            )
        raise

    remote_cases, warnings = prepare_remote_cases(remote_cases, actor_resolver)
    return FanoutResolution(
        merge_fanout_cases(local_cases, remote_cases), "local-plus-remote", "available", warnings
    )
