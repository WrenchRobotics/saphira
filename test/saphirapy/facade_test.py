import io
import json
import os
import subprocess
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from saphirapy.facade import (
    STDERR_TAIL_CHARACTERS,
    STDERR_TAIL_LINES,
    ProgressReporter,
    _run_case,
    run_fanout,
    stderr_tail,
)

CASE = {
    "case-id": "production-ShelfPlanning1",
    "production-class": "ShelfPlanning1",
    "cast-production-name": "production-ShelfPlanning1",
    "actor-variant-json": "",
}


def completed(returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class RunCaseTest(unittest.TestCase):
    def run_case(self, outcome):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run = mock.patch("saphirapy.facade.subprocess.run")
            with run as fake_run:
                if isinstance(outcome, BaseException):
                    fake_run.side_effect = outcome
                else:
                    fake_run.return_value = outcome
                result = _run_case(
                    CASE, "chem.py", str(root / "restaged"), str(root / "results"), 300
                )
            payload = json.loads(Path(result["result-json-path"]).read_text())
            logs = {
                name: Path(payload[name]).read_text() for name in ("stdout_log", "stderr_log")
            }
        return result, payload, logs

    def test_failed_case_keeps_its_output(self):
        result, payload, logs = self.run_case(
            completed(1, stdout="building scene\n", stderr="Traceback\nRuntimeError: no plan\n")
        )

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(logs, {"stdout_log": "building scene\n",
                                "stderr_log": "Traceback\nRuntimeError: no plan\n"})
        self.assertEqual(payload["stderr_tail"], "Traceback\nRuntimeError: no plan")
        self.assertEqual(result["stderr-tail"], payload["stderr_tail"])

    def test_passed_case_keeps_logs_but_no_tail(self):
        result, payload, logs = self.run_case(completed(0, stderr="a warning\n"))

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(logs["stderr_log"], "a warning\n")
        self.assertNotIn("stderr_tail", payload)
        self.assertEqual(result["stderr-tail"], "")

    def test_timed_out_case_decodes_partial_output(self):
        timeout = subprocess.TimeoutExpired(cmd=[], timeout=300, output=b"step 1\n",
                                            stderr=b"still planning\n")

        result, payload, logs = self.run_case(timeout)

        self.assertEqual(result["exit-code"], "124")
        self.assertEqual(logs, {"stdout_log": "step 1\n", "stderr_log": "still planning\n"})
        self.assertEqual(payload["stderr_tail"], "still planning")

    def test_case_runs_in_its_own_directory_with_the_workspace_importable(self):
        previous = Path.cwd()
        with TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            with mock.patch("saphirapy.facade.subprocess.run") as fake_run, \
                    mock.patch.dict("os.environ", {"PYTHONPATH": "/extra"}):
                fake_run.return_value = completed(0)
                os.chdir(workspace)
                try:
                    _run_case(CASE, "chem.py", "restaged", "results", 300)
                finally:
                    os.chdir(previous)

            command = fake_run.call_args.args[0]
            options = fake_run.call_args.kwargs
            output_dir = workspace / "restaged" / CASE["case-id"]
            self.assertEqual(options["cwd"], output_dir)
            self.assertEqual(options["env"]["PYTHONPATH"], f"{workspace}{os.pathsep}/extra")
            # Relative roots are resolved before the working directory changes.
            self.assertEqual(command[command.index("--output-dir") + 1], str(output_dir))
            self.assertEqual(command[command.index("--result-json") + 1],
                             str(workspace / "results" / f"{CASE['case-id']}.json"))

    def test_timed_out_case_without_output(self):
        _result, payload, logs = self.run_case(subprocess.TimeoutExpired(cmd=[], timeout=300))

        self.assertEqual(logs, {"stdout_log": "", "stderr_log": ""})
        self.assertNotIn("stderr_tail", payload)

    def test_reports_start_and_heartbeat_while_case_runs(self):
        heartbeat_received = threading.Event()
        events = []

        def fake_run(*_args, **_kwargs):
            events.append("run")
            self.assertTrue(heartbeat_received.wait(timeout=1))
            return completed(0)

        def on_heartbeat(case_id, elapsed):
            events.append(("heartbeat", case_id, elapsed))
            heartbeat_received.set()

        with TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch("saphirapy.facade.subprocess.run", side_effect=fake_run), \
                    mock.patch("saphirapy.facade.HEARTBEAT_INTERVAL_SECONDS", 0.001):
                _run_case(
                    CASE,
                    "chem.py",
                    str(root / "restaged"),
                    str(root / "results"),
                    300,
                    on_started=lambda case_id: events.append(("started", case_id)),
                    on_heartbeat=on_heartbeat,
                )

        self.assertEqual(events[0], ("started", CASE["case-id"]))
        self.assertEqual(events[1], "run")
        self.assertEqual(events[2][0:2], ("heartbeat", CASE["case-id"]))


class StderrTailTest(unittest.TestCase):
    def test_keeps_the_last_lines(self):
        stderr = "".join(f"line {number}\n" for number in range(STDERR_TAIL_LINES + 10))

        tail = stderr_tail(stderr).splitlines()

        self.assertEqual(len(tail), STDERR_TAIL_LINES)
        self.assertEqual(tail[-1], f"line {STDERR_TAIL_LINES + 9}")

    def test_bounds_long_lines(self):
        tail = stderr_tail("x" * (STDERR_TAIL_CHARACTERS * 2) + "END")

        self.assertEqual(len(tail), STDERR_TAIL_CHARACTERS)
        self.assertTrue(tail.endswith("END"))


class ProgressReporterTest(unittest.TestCase):
    def record(self, environment, result):
        output = io.StringIO()
        with mock.patch.dict("os.environ", environment, clear=True), redirect_stdout(output):
            ProgressReporter(1, "lines").record(result)
        return output.getvalue()

    def test_groups_the_stderr_tail_in_github_actions(self):
        output = self.record(
            {"GITHUB_ACTIONS": "true"},
            {"case-id": "c", "status": "failed", "exit-code": "1", "stderr-tail": "boom"},
        )

        self.assertIn("c failed (exit code 1)", output)
        self.assertIn("::group::c stderr (last lines)\nboom\n::endgroup::", output)

    def test_prints_nothing_extra_without_a_tail(self):
        output = self.record({}, {"case-id": "c", "status": "passed", "exit-code": "0"})

        self.assertEqual(output, "Saphira facade: [1/1] c passed\n")

    def test_reports_start_and_heartbeats_in_lines_mode(self):
        output = io.StringIO()
        with mock.patch.dict("os.environ", {}, clear=True), redirect_stdout(output):
            progress = ProgressReporter(1, "lines")
            progress.started("c")
            progress.heartbeat("c", 120)

        self.assertEqual(
            output.getvalue(),
            "Saphira facade: c started\n"
            "Saphira facade: c still running (120s elapsed)\n",
        )

    def test_suppresses_start_and_heartbeats_in_none_mode(self):
        output = io.StringIO()
        with redirect_stdout(output):
            progress = ProgressReporter(1, "none")
            progress.started("c")
            progress.heartbeat("c", 120)

        self.assertEqual(output.getvalue(), "")


class RunFanoutTest(unittest.TestCase):
    def test_passes_progress_callbacks_to_workers(self):
        case = {**CASE, "case-id": "case-a"}

        def fake_run_case(case, _target_file, _restaged_root, _results_root, _timeout,
                          _request_id, on_started, on_heartbeat):
            on_started(case["case-id"])
            on_heartbeat(case["case-id"], 60)
            return {
                "case-id": case["case-id"],
                "status": "passed",
                "exit-code": "0",
            }

        output = io.StringIO()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch("saphirapy.facade._run_case", side_effect=fake_run_case), \
                    redirect_stdout(output):
                results = run_fanout(
                    root / "chem.py",
                    SimpleNamespace(cases=[case], request_id=None),
                    root / "restaged",
                    root / "results",
                    max_workers=1,
                    run_timeout_seconds=300,
                    progress_mode="lines",
                )

        self.assertEqual(results[0]["case-id"], "case-a")
        self.assertEqual(
            output.getvalue(),
            "Saphira facade: case-a started\n"
            "Saphira facade: case-a still running (60s elapsed)\n"
            "Saphira facade: [1/1] case-a passed\n",
        )


if __name__ == "__main__":
    unittest.main()
