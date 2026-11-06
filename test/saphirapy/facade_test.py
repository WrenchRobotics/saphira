import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from saphirapy.facade import (
    STDERR_TAIL_CHARACTERS,
    STDERR_TAIL_LINES,
    ProgressReporter,
    _run_case,
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

    def test_timed_out_case_without_output(self):
        _result, payload, logs = self.run_case(subprocess.TimeoutExpired(cmd=[], timeout=300))

        self.assertEqual(logs, {"stdout_log": "", "stderr_log": ""})
        self.assertNotIn("stderr_tail", payload)


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


if __name__ == "__main__":
    unittest.main()
