import json
import os
import sys
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from saphirapy import report
from saphirapy.github_oidc import ID_TOKEN_REQUEST_TOKEN_ENV, ID_TOKEN_REQUEST_URL_ENV, OIDC_HEADER
from saphirapy.report import (
    API_KEY_ENV,
    MAX_REPORT_BODY_BYTES,
    ReportError,
    ReportOutcome,
    build_report,
    normalize_section_key,
    report_outcome,
    run_report,
    submit_report,
)

API_URL = "https://api-staging.wrenchrobotics.com"
REPORTS_URL = f"{API_URL}/v1/saphira/reports"
API_KEY = "wrk_live_abcdefgh_" + "s" * 43
REQUEST_URL = "https://pipelines.actions.githubusercontent.com/oidc/token?api-version=2.0"
REQUEST_TOKEN = "request-token-from-github"
OIDC_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJyZXBvc2l0b3J5Ijoib2N0by1vcmcvcm9ib3QifQ.signature"
GITHUB_ENV = {ID_TOKEN_REQUEST_URL_ENV: REQUEST_URL, ID_TOKEN_REQUEST_TOKEN_ENV: REQUEST_TOKEN}
SECRETS = (API_KEY, OIDC_TOKEN, REQUEST_TOKEN)
POSTED = {
    "report_id": "5f0c1d2e-3b4a-4c5d-8e9f-0a1b2c3d4e5f",
    "check_run_url": "https://github.com/octo-org/robot/runs/7",
    "comment_url": "https://github.com/octo-org/robot/pull/3#issuecomment-9",
    "warnings": [],
}
BODY = {"schema_version": 1, "section_key": "saphira-facade", "results": []}


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


def http_error(status, payload):
    body = BytesIO(json.dumps(payload).encode("utf-8"))
    return HTTPError(REPORTS_URL, status, "error", {}, body)


class RoutingOpener:
    """Answers GitHub's token service and the reports endpoint, recording every request.

    ``answers`` are the reports endpoint's answers in order; the last one repeats.
    """

    def __init__(self, *answers):
        self.answers = list(answers) or [FakeResponse(POSTED, 201)]
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        if request.full_url.startswith(REQUEST_URL):
            return FakeResponse({"value": OIDC_TOKEN})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def to(self, url_prefix):
        return [request for request in self.requests if request.full_url.startswith(url_prefix)]


def write_result(directory, name, **payload):
    path = Path(directory) / f"{name}.json"
    path.write_text(json.dumps({"cast_production_name": name, **payload}))
    return path


class BuildReportTest(unittest.TestCase):
    def test_body_is_built_from_the_result_files(self):
        with TemporaryDirectory() as directory:
            write_result(
                directory,
                "baseline-ChemLab1",
                production_class="ChemLab1",
                exit_code=0,
                duration_seconds=1.5,
                metrics={"path_length": 2.25},
                comparison_metadata={"comparison-type": "production", "baseline": True},
            )
            write_result(
                directory,
                "actor-ChemLab1-rrt",
                production_class="ChemLab1",
                exit_code=1,
                duration_seconds=3,
                comparison_metadata={"comparison-type": "actor", "cast-call-index": 0},
            )

            body = build_report(
                directory,
                section_key="Saphira Facade (Grasping)",
                section_heading=" Grasping ",
                detected_parent="ChemLabParent",
                detected_production="ChemLab1",
                fanout_source="local-plus-remote",
                remote_status="available",
                fanout_request_id="0b6f3c2e-7f1a-4c55-9d0e-5a4b3c2d1e0f",
                artifact_id="1234",
                artifact_name="saphira-facade-grasping",
            )

        self.assertEqual(body["schema_version"], 1)
        self.assertEqual(body["section_key"], "saphira-facade-grasping")
        self.assertEqual(body["section_heading"], "Grasping")
        self.assertEqual(body["detected_parent"], "ChemLabParent")
        self.assertEqual(body["detected_production"], "ChemLab1")
        self.assertEqual(body["fanout"], {
            "source": "local-plus-remote",
            "remote_status": "available",
            "request_id": "0b6f3c2e-7f1a-4c55-9d0e-5a4b3c2d1e0f",
        })
        self.assertEqual(body["artifact"], {"id": 1234, "name": "saphira-facade-grasping"})
        self.assertIn("saphira_version", body)
        self.assertEqual(body["results"], [
            {
                "production": "actor-ChemLab1-rrt",
                "production_class": "ChemLab1",
                "exit_code": "1",
                "duration_seconds": "3",
                "status": "failed",
                "comparison_type": "actor",
                "comparison_metadata": {"comparison-type": "actor", "cast-call-index": 0},
                "metrics": {},
            },
            {
                "production": "baseline-ChemLab1",
                "production_class": "ChemLab1",
                "exit_code": "0",
                "duration_seconds": "1.5",
                "status": "passed",
                "comparison_type": "production",
                "comparison_metadata": {"comparison-type": "production", "baseline": True},
                "metrics": {"path_length": "2.25"},
            },
        ])
        self.assertNotIn("repository", body)

    def test_unknown_values_are_omitted(self):
        with TemporaryDirectory() as directory:
            body = build_report(
                directory,
                section_key="",
                detected_parent="UNMAPPED",
                detected_production="",
                artifact_id="",
                artifact_name="",
            )

        self.assertIsNone(body["detected_parent"])
        self.assertIsNone(body["detected_production"])
        self.assertEqual(body["fanout"], {"source": None, "remote_status": None, "request_id": None})
        self.assertNotIn("artifact", body)
        self.assertEqual(body["results"], [])
        # Without a section key, the results directory names the section, as in the PR comment.
        self.assertRegex(body["section_key"], r"^[a-z0-9._-]{1,64}$")

    def test_values_are_cut_to_the_api_limits(self):
        with TemporaryDirectory() as directory:
            write_result(
                directory,
                "long",
                cast_production_name="p" * 300,
                production_class="ChemLab1",
                exit_code=0,
                metrics={f"m{i}": "v" * 300 for i in range(60)},
                comparison_metadata={
                    "comparison-type": "actor",
                    "nested": {"actor": "rrt", "options": [1, 2]},
                    "k" * 150: "x",
                },
            )

            row = build_report(directory, section_key="s")["results"][0]

        self.assertEqual(len(row["production"]), 200)
        self.assertEqual(len(row["metrics"]), 50)
        self.assertTrue(all(len(value) == 200 for value in row["metrics"].values()))
        self.assertEqual(
            row["comparison_metadata"]["nested"], '{"actor": "rrt", "options": [1, 2]}'
        )
        self.assertIn("k" * 100, row["comparison_metadata"])

    def test_section_keys_match_the_workflow_comment_and_the_api_pattern(self):
        for raw, expected in [
            ("saphira-facade", "saphira-facade"),
            ("Saphira Facade (Grasping)", "saphira-facade-grasping"),
            ("--a__b..c--", "a__b..c"),
            ("productions/facade-results", "productions-facade-results"),
            ("ü!!", "default"),
            ("x" * 80, "x" * 64),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_section_key(raw), expected)


class SubmitReportTest(unittest.TestCase):
    def submit(self, opener, **kwargs):
        sleeps = []
        response = submit_report(
            API_URL, BODY, api_key=API_KEY, oidc_token=OIDC_TOKEN, opener=opener,
            sleep=sleeps.append, **kwargs,
        )
        return response, sleeps

    def test_request_carries_both_credentials_and_the_body(self):
        opener = RoutingOpener()

        response, _ = self.submit(opener)

        self.assertEqual(response, POSTED)
        (request,) = opener.requests
        self.assertEqual(request.full_url, REPORTS_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {API_KEY}")
        self.assertEqual(request.headers[OIDC_HEADER.capitalize()], OIDC_TOKEN)
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(json.loads(request.data), BODY)

    def test_bad_gateway_and_unavailable_are_retried(self):
        for failure in [
            http_error(502, {"detail": "GitHub refused the request"}),
            FakeResponse({"detail": "down"}, 503),
            URLError("connection refused"),
        ]:
            with self.subTest(failure=failure):
                opener = RoutingOpener(failure, FakeResponse(POSTED, 200))

                response, sleeps = self.submit(opener)

                self.assertEqual(response, POSTED)
                self.assertEqual(len(opener.requests), 2)
                self.assertEqual(sleeps, [2.0])

    def test_retries_stop_after_the_last_attempt(self):
        opener = RoutingOpener(http_error(503, {"detail": "busy"}))

        with self.assertRaises(ReportError) as raised:
            self.submit(opener, attempts=3)

        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(raised.exception.status, 503)

    def test_refusals_are_not_retried_and_carry_the_fixing_link(self):
        install_url = "https://github.com/apps/wrench-saphira-staging/installations/new"
        opener = RoutingOpener(http_error(409, {
            "detail": "The Saphira GitHub App is not\ninstalled on octo-org/robot",
            "code": "app_not_installed",
            "install_url": install_url,
        }))

        with self.assertRaises(ReportError) as raised:
            self.submit(opener)

        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.link, install_url)
        self.assertEqual(
            str(raised.exception),
            "The Wrench API refused the report (HTTP 409): The Saphira GitHub App is not "
            "installed on octo-org/robot",
        )

    def test_oversized_bodies_are_refused_before_any_request(self):
        opener = RoutingOpener()
        body = {**BODY, "section_heading": "x" * MAX_REPORT_BODY_BYTES}

        with self.assertRaises(ReportError):
            submit_report(API_URL, body, api_key=API_KEY, oidc_token=OIDC_TOKEN, opener=opener)

        self.assertEqual(opener.requests, [])


class RunReportTest(unittest.TestCase):
    def run_report(self, mode, opener, *, api_key=API_KEY, environ=None, api_url=API_URL):
        return run_report(
            mode,
            BODY,
            api_url=api_url,
            api_key=api_key,
            environ=GITHUB_ENV if environ is None else environ,
            opener=opener,
            sleep=lambda _seconds: None,
        )

    def test_posted_report_links_the_comment_or_else_the_check(self):
        for status in (201, 200):
            with self.subTest(status=status):
                outcome = self.run_report("auto", RoutingOpener(FakeResponse(POSTED, status)))

                self.assertEqual(outcome.status, "posted")
                self.assertEqual(outcome.report_url, POSTED["comment_url"])
                self.assertEqual(outcome.report_id, POSTED["report_id"])
                self.assertFalse(outcome.workflow_comment)

        push = {**POSTED, "comment_url": None, "warnings": ["fanout request not linked"]}
        outcome = self.run_report("app", RoutingOpener(FakeResponse(push, 201)))
        self.assertEqual(outcome.report_url, POSTED["check_run_url"])
        self.assertEqual(outcome.warnings, ["fanout request not linked"])

    def test_oidc_audience_is_derived_from_the_api_url(self):
        for api_url, audience in [
            ("https://api.wrenchrobotics.com", "https://api.wrenchrobotics.com"),
            (API_URL, API_URL),
            ("http://localhost:8000", "http://localhost:8000"),
        ]:
            with self.subTest(api_url=api_url):
                opener = RoutingOpener()

                self.run_report("auto", opener, api_url=api_url)

                (token_request,) = opener.to(REQUEST_URL)
                query = parse_qs(urlsplit(token_request.full_url).query)
                self.assertEqual(query["audience"], [audience])
                self.assertEqual(opener.requests[-1].full_url, f"{api_url}/v1/saphira/reports")

    def test_api_key_is_read_from_the_environment(self):
        opener = RoutingOpener()

        self.run_report("auto", opener, api_key=None, environ={**GITHUB_ENV, API_KEY_ENV: API_KEY})

        self.assertEqual(opener.requests[-1].get_header("Authorization"), f"Bearer {API_KEY}")

    def test_missing_key_or_oidc_token_sends_no_report(self):
        for case, kwargs in [
            ("no key", {"api_key": ""}),
            ("no OIDC token", {"environ": {}}),
        ]:
            with self.subTest(case=case):
                for mode, status in (("auto", "fallback"), ("app", "failed")):
                    opener = RoutingOpener()

                    outcome = self.run_report(mode, opener, **kwargs)

                    self.assertEqual(outcome.status, status)
                    self.assertEqual(opener.to(REPORTS_URL), [])
                    self.assertTrue(outcome.reason)

    def test_auto_falls_back_and_app_fails_when_the_app_cannot_post(self):
        # Factories: an HTTPError's body can be read only once.
        failures = {
            401: lambda: http_error(401, {"detail": "Invalid API key", "code": "api_key_invalid"}),
            403: lambda: http_error(403, {
                "detail": "This API key's account has not connected the installation",
                "code": "installation_not_connected",
                "dashboard_url": "https://staging.wrenchrobotics.com/dashboard/github",
            }),
            409: lambda: http_error(409, {
                "detail": "The Saphira GitHub App is not installed on octo-org/robot",
                "install_url": "https://github.com/apps/wrench-saphira-staging/installations/new",
            }),
            "unreachable": lambda: URLError("connection refused"),
        }
        for failure_name, failure in failures.items():
            for mode, status in (("auto", "fallback"), ("app", "failed")):
                with self.subTest(failure=failure_name, mode=mode):
                    outcome = self.run_report(mode, RoutingOpener(failure()))

                    self.assertEqual(outcome.status, status)
                    self.assertEqual(outcome.workflow_comment, mode == "auto")
                    self.assertEqual(outcome.report_url, "")

        outcome = self.run_report("auto", RoutingOpener(failures[403]()))
        self.assertIn("https://staging.wrenchrobotics.com/dashboard/github", outcome.reason)

    def test_none_mode_sends_nothing(self):
        opener = RoutingOpener()

        outcome = self.run_report("none", opener)

        self.assertEqual(outcome.status, "skipped")
        self.assertFalse(outcome.workflow_comment)
        self.assertEqual(opener.requests, [])

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_report("sometimes", RoutingOpener())

    def test_tokens_never_reach_the_outcome_annotations_or_summary(self):
        failures = [
            http_error(401, {"detail": "Invalid API key"}),
            http_error(503, {"detail": "busy"}),
            URLError(f"refused {OIDC_TOKEN}"),
        ]
        for failure in failures:
            with self.subTest(failure=failure), TemporaryDirectory() as directory:
                summary = Path(directory) / "summary.md"
                outcome = self.run_report("app", RoutingOpener(failure))
                with mock.patch("builtins.print") as printed:
                    report_outcome(outcome, str(summary))

                written = summary.read_text() + repr(printed.call_args_list) + repr(outcome)
                for secret in SECRETS:
                    self.assertNotIn(secret, written)


class ReportOutcomeTest(unittest.TestCase):
    def outcome_text(self, outcome):
        with TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with mock.patch("builtins.print") as printed:
                report_outcome(outcome, str(summary))
            annotations = [call.args[0] for call in printed.call_args_list]
            return (summary.read_text() if summary.exists() else ""), annotations

    def test_posted_report_is_linked_in_the_summary(self):
        summary, annotations = self.outcome_text(
            ReportOutcome("posted", report_url=POSTED["comment_url"])
        )

        self.assertIn(POSTED["comment_url"], summary)
        self.assertEqual(annotations, [])

    def test_fallback_warns_and_says_why(self):
        summary, annotations = self.outcome_text(
            ReportOutcome("fallback", reason="No Wrench API key was provided")
        )

        self.assertIn("No Wrench API key was provided", summary)
        self.assertIn("own PR comment", summary)
        self.assertTrue(annotations[0].startswith("::warning title=Saphira report::"))

    def test_failure_is_an_error(self):
        _, annotations = self.outcome_text(ReportOutcome("failed", reason="HTTP 409"))

        self.assertTrue(annotations[0].startswith("::error title=Saphira report::"))

    def test_skipped_report_writes_nothing(self):
        summary, annotations = self.outcome_text(ReportOutcome("skipped"))

        self.assertEqual((summary, annotations), ("", []))


class SubmitCliTest(unittest.TestCase):
    def run_main(self, *args, environ=None):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            summary = Path(directory) / "summary.md"
            write_result(directory, "baseline-ChemLab1", production_class="ChemLab1", exit_code=0)
            argv = ["saphira-submit", "--results-directories", directory, "--github-output",
                    str(output), *args]
            env = {"GITHUB_STEP_SUMMARY": str(summary), **(environ or {})}
            with mock.patch.object(sys, "argv", argv), mock.patch.dict(os.environ, env, clear=True):
                with mock.patch("builtins.print"):
                    code = report.main()
            return code, output.read_text()

    def test_auto_without_a_key_falls_back(self):
        code, output = self.run_main("--report-mode", "auto", environ=GITHUB_ENV)

        self.assertEqual(code, 0)
        self.assertIn("report-status=fallback\n", output)
        self.assertIn("workflow-comment=true\n", output)

    def test_app_without_a_key_fails_the_step(self):
        code, output = self.run_main("--report-mode", "app", environ=GITHUB_ENV)

        self.assertEqual(code, 1)
        self.assertIn("report-status=failed\n", output)
        self.assertIn("workflow-comment=false\n", output)


if __name__ == "__main__":
    unittest.main()
