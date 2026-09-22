import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError

from saphirapy.fanout import FanoutRemoteError, resolve_fanout


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


class FanoutTest(unittest.TestCase):
    local_cases = [
        {
            "production-class": "ChemLab1",
            "actor-variant-json": "",
            "cast-production-name": "production-ChemLab1",
            "artifact-name": "matrix-result-ChemLab1",
        }
    ]

    def test_local_is_default_and_does_not_call_remote(self):
        def opener(*_args, **_kwargs):
            raise AssertionError("remote opener should not be called")

        resolution = resolve_fanout(self.local_cases, opener=opener)

        self.assertEqual(resolution.source, "local")
        self.assertEqual(resolution.remote_status, "disabled")
        self.assertEqual(resolution.cases[0]["case-id"], "production-ChemLab1")

    def test_remote_cases_are_added_and_duplicates_are_removed(self):
        def opener(*_args, **_kwargs):
            return FakeResponse(
                {
                    "cases": [
                        {"case-id": "production-ChemLab1", "production-class": "ChemLab1"},
                        {"case-id": "premium-ChemLab1", "production-class": "ChemLab1"},
                    ]
                }
            )

        resolution = resolve_fanout(
            self.local_cases,
            source="remote",
            endpoint="https://fanout.example.test/cases",
            opener=opener,
        )

        self.assertEqual(resolution.source, "local-plus-remote")
        self.assertEqual([case["case-id"] for case in resolution.cases], [
            "production-ChemLab1", "premium-ChemLab1"
        ])
        self.assertEqual(resolution.cases[1]["source"], "remote")

    def test_unreachable_remote_uses_fallback_when_enabled(self):
        def opener(*_args, **_kwargs):
            raise URLError("connection refused")

        resolution = resolve_fanout(
            self.local_cases,
            source="remote-with-fallback",
            endpoint="https://fanout.example.test/cases",
            opener=opener,
        )

        self.assertEqual(resolution.source, "local-fallback")
        self.assertIn("unreachable", resolution.remote_status)
        self.assertEqual(len(resolution.cases), 1)

    def test_unreachable_remote_fails_without_fallback(self):
        def opener(*_args, **_kwargs):
            raise URLError("connection refused")

        with self.assertRaises(FanoutRemoteError):
            resolve_fanout(
                self.local_cases,
                source="remote",
                endpoint="https://fanout.example.test/cases",
                opener=opener,
            )

    def test_malformed_remote_response_does_not_use_fallback(self):
        def opener(*_args, **_kwargs):
            return FakeResponse({"unexpected": []})

        with self.assertRaises(FanoutRemoteError) as context:
            resolve_fanout(
                self.local_cases,
                source="remote-with-fallback",
                endpoint="https://fanout.example.test/cases",
                opener=opener,
            )

        self.assertFalse(context.exception.retryable)

    def test_unauthorized_remote_response_does_not_use_fallback(self):
        def opener(*_args, **_kwargs):
            return FakeResponse({"cases": []}, status=401)

        with self.assertRaises(FanoutRemoteError) as context:
            resolve_fanout(
                self.local_cases,
                source="remote-with-fallback",
                endpoint="https://fanout.example.test/cases",
                opener=opener,
            )

        self.assertFalse(context.exception.retryable)


class FanoutMockServerTest(unittest.TestCase):
    local_cases = [{"production-class": "ChemLab1", "case-id": "local-ChemLab1"}]

    def run_server(self, status, payload):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
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
        return f"http://127.0.0.1:{server.server_port}/fanout"

    def test_real_http_server_adds_remote_case(self):
        endpoint = self.run_server(
            200,
            {"cases": [{"case-id": "premium-ChemLab1", "production-class": "ChemLab1"}]},
        )

        resolution = resolve_fanout(
            self.local_cases,
            source="remote",
            endpoint=endpoint,
        )

        self.assertEqual(resolution.source, "local-plus-remote")
        self.assertEqual(resolution.cases[-1]["source"], "remote")

    def test_real_http_server_service_error_uses_fallback(self):
        endpoint = self.run_server(503, {"error": "temporarily unavailable"})

        resolution = resolve_fanout(
            self.local_cases,
            source="remote-with-fallback",
            endpoint=endpoint,
        )

        self.assertEqual(resolution.source, "local-fallback")
        self.assertIn("HTTP 503", resolution.remote_status)


if __name__ == "__main__":
    unittest.main()