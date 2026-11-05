import json
import unittest
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from saphirapy.fanout import resolve_fanout
from saphirapy.github_oidc import (
    ID_TOKEN_REQUEST_TOKEN_ENV,
    ID_TOKEN_REQUEST_URL_ENV,
    OIDC_HEADER,
    OIDCTokenError,
    audience_for,
    request_oidc_token,
)

ENDPOINT = "https://api.wrenchrobotics.com/v1/saphira/fanout"
TOKEN = "wrk_live_abcdefgh_" + "s" * 43
REQUEST_URL = "https://pipelines.actions.githubusercontent.com/oidc/token?api-version=2.0"
REQUEST_TOKEN = "request-token-from-github"
OIDC_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJyZXBvc2l0b3J5Ijoib2N0by1vcmcvcm9ib3QifQ.signature"
GITHUB_ENV = {ID_TOKEN_REQUEST_URL_ENV: REQUEST_URL, ID_TOKEN_REQUEST_TOKEN_ENV: REQUEST_TOKEN}
LOCAL_CASES = [
    {
        "production-class": "ChemLab1",
        "actor-variant-json": "",
        "cast-production-name": "production-ChemLab1",
        "artifact-name": "matrix-result-ChemLab1",
    }
]


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class RoutingOpener:
    """Answers GitHub's token service and the fanout endpoint, recording every request."""

    def __init__(self, token_answer=None, fanout_answer=None):
        self.token_answer = token_answer or FakeResponse({"value": OIDC_TOKEN})
        self.fanout_answer = fanout_answer or FakeResponse({"cases": []})
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        answer = self.token_answer if request.full_url.startswith(REQUEST_URL) else self.fanout_answer
        if isinstance(answer, Exception):
            raise answer
        return answer

    def to(self, url_prefix):
        return [request for request in self.requests if request.full_url.startswith(url_prefix)]


class AudienceTest(unittest.TestCase):
    def test_audience_is_the_scheme_and_host_of_the_endpoint(self):
        for endpoint, audience in [
            (ENDPOINT, "https://api.wrenchrobotics.com"),
            (
                "https://api-staging.wrenchrobotics.com/v1/saphira/fanout",
                "https://api-staging.wrenchrobotics.com",
            ),
            ("http://localhost:8000/v1/saphira/fanout", "http://localhost:8000"),
        ]:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(audience_for(endpoint), audience)

    def test_an_endpoint_without_a_host_is_refused(self):
        with self.assertRaises(ValueError):
            audience_for("/v1/saphira/fanout")


class RequestTokenTest(unittest.TestCase):
    def test_requests_a_token_for_the_audience_with_githubs_request_token(self):
        opener = RoutingOpener()

        token = request_oidc_token("https://api.wrenchrobotics.com", environ=GITHUB_ENV, opener=opener)

        self.assertEqual(token, OIDC_TOKEN)
        (request,) = opener.requests
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["audience"], ["https://api.wrenchrobotics.com"])
        self.assertEqual(query["api-version"], ["2.0"])
        self.assertEqual(request.get_header("Authorization"), f"Bearer {REQUEST_TOKEN}")

    def test_returns_none_without_the_id_token_permission(self):
        opener = RoutingOpener()

        self.assertIsNone(request_oidc_token("https://a.test", environ={}, opener=opener))
        self.assertIsNone(
            request_oidc_token(
                "https://a.test", environ={ID_TOKEN_REQUEST_URL_ENV: REQUEST_URL}, opener=opener
            )
        )
        self.assertEqual(opener.requests, [])

    def test_failures_raise_without_revealing_the_request_token(self):
        failures = [
            HTTPError(REQUEST_URL, 403, "Forbidden", {}, BytesIO(b"{}")),
            URLError("down"),
            FakeResponse({"no": "value"}),
            FakeResponse({"value": ""}),
            FakeResponse({"value": OIDC_TOKEN}, status=500),
        ]
        for failure in failures:
            with self.subTest(failure=failure):
                with self.assertRaises(OIDCTokenError) as raised:
                    request_oidc_token(
                        "https://a.test", environ=GITHUB_ENV, opener=RoutingOpener(failure)
                    )
                self.assertNotIn(REQUEST_TOKEN, str(raised.exception))


class FanoutOIDCTest(unittest.TestCase):
    def resolve(self, opener, environ, **kwargs):
        return resolve_fanout(
            LOCAL_CASES,
            source="remote",
            endpoint=ENDPOINT,
            token=TOKEN,
            environ=environ,
            opener=opener,
            **kwargs,
        )

    def test_sends_the_token_next_to_the_api_key(self):
        opener = RoutingOpener()

        resolution = self.resolve(opener, GITHUB_ENV)

        (token_request,) = opener.to(REQUEST_URL)
        query = parse_qs(urlsplit(token_request.full_url).query)
        self.assertEqual(query["audience"], ["https://api.wrenchrobotics.com"])
        (fanout_request,) = opener.to(ENDPOINT)
        self.assertEqual(fanout_request.get_header("Authorization"), f"Bearer {TOKEN}")
        # urllib stores header names capitalized.
        self.assertEqual(fanout_request.get_header(OIDC_HEADER.capitalize()), OIDC_TOKEN)
        self.assertEqual(resolution.oidc_status, "sent")
        self.assertEqual(resolution.warnings, [])

    def test_sends_no_token_without_the_permission(self):
        opener = RoutingOpener()

        resolution = self.resolve(opener, {})

        (fanout_request,) = opener.requests
        self.assertIsNone(fanout_request.get_header(OIDC_HEADER.capitalize()))
        self.assertEqual(resolution.oidc_status, "unavailable")

    def test_carries_on_with_a_warning_when_github_fails(self):
        opener = RoutingOpener(token_answer=URLError("down"))

        resolution = self.resolve(opener, GITHUB_ENV)

        (fanout_request,) = opener.to(ENDPOINT)
        self.assertIsNone(fanout_request.get_header(OIDC_HEADER.capitalize()))
        self.assertEqual(resolution.oidc_status, "failed")
        self.assertEqual(len(resolution.warnings), 1)
        self.assertIn("No GitHub Actions OIDC token was sent", resolution.warnings[0])
        self.assertEqual(resolution.source, "local-plus-remote")

    def test_no_token_is_requested_when_the_run_falls_back_without_an_api_key(self):
        opener = RoutingOpener()

        resolution = resolve_fanout(
            LOCAL_CASES,
            source="remote-with-fallback",
            endpoint=ENDPOINT,
            token="",
            environ=GITHUB_ENV,
            opener=opener,
        )

        self.assertEqual(opener.requests, [])
        self.assertEqual(resolution.source, "local-fallback")
        self.assertEqual(resolution.oidc_status, "not-requested")

    def test_a_fallback_after_an_outage_still_reports_the_token(self):
        opener = RoutingOpener(fanout_answer=URLError("down"))

        resolution = resolve_fanout(
            LOCAL_CASES,
            source="remote-with-fallback",
            endpoint=ENDPOINT,
            token=TOKEN,
            environ=GITHUB_ENV,
            opener=opener,
        )

        self.assertEqual(resolution.source, "local-fallback")
        self.assertEqual(resolution.oidc_status, "sent")

    def test_neither_token_reaches_the_errors_from_a_refused_request(self):
        refusal = HTTPError(
            ENDPOINT, 403, "Forbidden", {}, BytesIO(json.dumps({"detail": "wrong repository"}).encode())
        )
        with self.assertRaises(Exception) as raised:
            self.resolve(RoutingOpener(fanout_answer=refusal), GITHUB_ENV)
        message = str(raised.exception)
        self.assertIn("wrong repository", message)
        for secret in (TOKEN, OIDC_TOKEN, REQUEST_TOKEN):
            self.assertNotIn(secret, message)


if __name__ == "__main__":
    unittest.main()
