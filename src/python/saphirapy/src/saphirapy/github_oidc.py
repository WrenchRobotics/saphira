"""GitHub Actions OIDC tokens for the Wrench API.

A Wrench API key can be tied to one GitHub repository. The API then accepts it only together with
a GitHub-signed OIDC token from that repository, sent in the ``Wrench-GitHub-OIDC`` header. A job
gets such a token when its workflow grants ``permissions: id-token: write``: GitHub then sets
``ACTIONS_ID_TOKEN_REQUEST_URL`` and ``ACTIONS_ID_TOKEN_REQUEST_TOKEN`` for every step. Pull
requests from forks never get them.

The token's audience is the Wrench API's own URL (the scheme and host of the endpoint), so a token
requested for staging does not work in production. Neither token is ever logged.
"""

import json
import os
from http.client import HTTPResponse
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

OIDC_HEADER = "Wrench-GitHub-OIDC"
ID_TOKEN_REQUEST_URL_ENV = "ACTIONS_ID_TOKEN_REQUEST_URL"
ID_TOKEN_REQUEST_TOKEN_ENV = "ACTIONS_ID_TOKEN_REQUEST_TOKEN"
# auto: send a token when the job can get one. always: fail without one. never: do not send one.
OIDC_MODES = ("auto", "always", "never")
PERMISSION_HINT = "add 'permissions: id-token: write' to the workflow or job"


class OIDCTokenError(RuntimeError):
    """GitHub could not provide an OIDC token. The message never contains a token."""


def audience_for(endpoint: str) -> str:
    """Return the Wrench API's URL for an endpoint on it: its scheme and host, without a path."""
    parts = urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        raise ValueError(f"cannot derive an OIDC audience from endpoint {endpoint!r}")
    return f"{parts.scheme}://{parts.netloc}"


def oidc_available(environ: Mapping[str, str] | None = None) -> bool:
    """Whether this job can request a token (the workflow grants ``id-token: write``)."""
    environ = os.environ if environ is None else environ
    return bool(environ.get(ID_TOKEN_REQUEST_URL_ENV) and environ.get(ID_TOKEN_REQUEST_TOKEN_ENV))


def request_oidc_token(
    audience: str,
    *,
    environ: Mapping[str, str] | None = None,
    timeout_seconds: float = 10.0,
    opener: Callable[..., HTTPResponse] = urlopen,
) -> str | None:
    """Return a GitHub Actions OIDC token for ``audience``, or ``None`` if the job cannot get one.

    Raises ``OIDCTokenError`` when the job can request a token but GitHub does not provide one.
    """
    environ = os.environ if environ is None else environ
    if not oidc_available(environ):
        return None
    url = environ[ID_TOKEN_REQUEST_URL_ENV]
    separator = "&" if "?" in url else "?"
    request = Request(
        f"{url}{separator}{urlencode({'audience': audience})}",
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {environ[ID_TOKEN_REQUEST_TOKEN_ENV]}",
        },
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            if response.status >= 400:
                raise OIDCTokenError(f"GitHub refused the OIDC token request (HTTP {response.status})")
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise OIDCTokenError(f"GitHub refused the OIDC token request (HTTP {error.code})") from error
    except (TimeoutError, URLError, OSError) as error:
        raise OIDCTokenError("GitHub's OIDC token service was unreachable") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OIDCTokenError("GitHub's OIDC token response was not valid JSON") from error

    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value:
        raise OIDCTokenError("GitHub's OIDC token response contained no token")
    return value
