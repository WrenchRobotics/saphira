import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from saphirapy.detect_production_map import detect_production_mapping
from saphirapy.github_actions_contracts import (
    build_cast_production_outputs,
    build_summary,
    case_status,
    fails_run,
    finalize_result_payload,
    find_alternative_actors_for_production,
    parse_detection_outputs,
)


class TestGitHubActionContracts(unittest.TestCase):
    def setUp(self):
        self.data_directory = Path(__file__).resolve().parents[1] / "data"

    def test_parse_detection_outputs_matches_matrix_contract(self):
        target_file = self.data_directory / "motion_planning" / "chem_lab_demo.py"

        detection_payload = detect_production_mapping(target_file)
        outputs = parse_detection_outputs(json.dumps(detection_payload))

        related_productions = json.loads(outputs["related-productions-json"])

        self.assertEqual(outputs["detected-parent"], detection_payload["detected_parent_class"])
        self.assertEqual(related_productions, detection_payload["related_productions"])
        self.assertGreater(len(related_productions), 0)
        self.assertTrue(all(isinstance(production, str) for production in related_productions))

    def test_parse_detection_outputs_defaults_to_unmapped(self):
        outputs = parse_detection_outputs(
            json.dumps({
                "detected_parent_class": None,
                "related_productions": [],
            })
        )

        self.assertEqual(outputs["detected-parent"], "UNMAPPED")
        self.assertEqual(outputs["related-productions-json"], "[]")

    def test_finds_registered_alternative_actors_for_production(self):
        target_file = self.data_directory / "motion_planning" / "chem_lab_demo.py"

        actors = find_alternative_actors_for_production(target_file)

        self.assertEqual(len(actors), 1)
        self.assertEqual(actors[0]["production_variable"], "production")
        self.assertEqual(actors[0]["current_actor"], "planner2.plan")
        self.assertEqual(actors[0]["current_actor_class"], "RRTConnectPlanner")
        self.assertEqual(actors[0]["actor_type"], "motion_planning_rrt")
        self.assertEqual(
            [alternative["name"] for alternative in actors[0]["alternatives"]],
            ["bidirectional_rrt", "bidirectional_rrt_connect"],
        )
        self.assertTrue(
            all(
                {"import_statements", "instantiation_code", "variable_name", "method_to_call"}
                <= alternative.keys()
                for alternative in actors[0]["alternatives"]
            )
        )

    def test_cast_production_outputs_keep_baseline_separate_from_opt_in_variants(self):
        target_file = self.data_directory / "motion_planning" / "chem_lab_demo.py"
        detection_payload = json.dumps(detect_production_mapping(target_file))

        disabled_outputs = build_cast_production_outputs(
            detection_payload, target_file, False, False
        )
        enabled_outputs = build_cast_production_outputs(
            detection_payload, target_file, True, True
        )

        self.assertEqual(disabled_outputs["detected-production"], "ChemLab1")
        self.assertEqual(json.loads(disabled_outputs["alternative-productions-json"]), [])
        self.assertEqual(json.loads(disabled_outputs["alternative-actors-json"]), [])
        self.assertEqual(enabled_outputs["has-alternative-productions"], "true")
        self.assertEqual(
            json.loads(enabled_outputs["alternative-productions-json"]), ["ShelfPlanning1"]
        )
        self.assertEqual(
            [actor["name"] for actor in json.loads(enabled_outputs["alternative-actors-json"])],
            ["bidirectional_rrt", "bidirectional_rrt_connect"],
        )

    def test_cast_productions_json_combines_productions_and_actors(self):
        target_file = self.data_directory / "motion_planning" / "chem_lab_demo.py"
        detection_payload = json.dumps(detect_production_mapping(target_file))

        disabled_outputs = build_cast_production_outputs(
            detection_payload, target_file, False, False
        )
        enabled_outputs = build_cast_production_outputs(
            detection_payload, target_file, True, True
        )

        disabled_cast_productions = json.loads(disabled_outputs["cast-productions-json"])
        self.assertEqual(
            [cast_production["cast-production-name"] for cast_production in disabled_cast_productions],
            ["production-ChemLab1"],
        )
        self.assertTrue(
            all(cast_production["actor-variant-json"] == "" for cast_production in disabled_cast_productions)
        )

        enabled_cast_productions = json.loads(enabled_outputs["cast-productions-json"])
        self.assertEqual(
            [cast_production["cast-production-name"] for cast_production in enabled_cast_productions],
            [
                "production-ChemLab1",
                "production-ShelfPlanning1",
                "actor-bidirectional_rrt-0",
                "actor-bidirectional_rrt_connect-0",
            ],
        )
        actor_cast_productions = [
            cast_production
            for cast_production in enabled_cast_productions
            if cast_production["actor-variant-json"]
        ]
        self.assertTrue(
            all(cast_production["production-class"] == "ChemLab1" for cast_production in actor_cast_productions)
        )
        self.assertTrue(
            all(
                json.loads(cast_production["actor-variant-json"])["cast_call_index"] == 0
                for cast_production in actor_cast_productions
            )
        )
        actor_metadata = json.loads(actor_cast_productions[0]["comparison-metadata-json"])
        self.assertEqual(actor_metadata["comparison-type"], "actor")
        self.assertEqual(actor_metadata["actor-name"], "bidirectional_rrt")
        self.assertNotIn("instantiation_code", actor_metadata)

    def test_result_payloads_feed_combined_summary(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            first_results_dir = tmp_path / "matrix-results-a"
            second_results_dir = tmp_path / "matrix-results-b"

            finalize_result_payload(
                first_results_dir / "ShelfPlanning1.json",
                "ShelfPlanning1",
                0,
                12.5,
                "productions/restaged/shelf_planning1.py",
                "baseline-ChemLab1",
            )
            finalize_result_payload(
                second_results_dir / "no-related-productions.json",
                "NO_RELATED_PRODUCTIONS",
                1,
                3.25,
                "",
            )

            summary = build_summary(f"{first_results_dir}\n{second_results_dir}")

            self.assertIn("| Production | ShelfPlanning1 |", summary)
            self.assertIn("| Actor Configuration | Current actors |", summary)
            self.assertIn("#### Actor Comparison", summary)
            self.assertIn("| Actor | Current actors |", summary)
            self.assertIn("| Cast Production | baseline-ChemLab1 |", summary)
            self.assertIn("| Duration (s) | 12.5 |", summary)
            self.assertIn("#### Production Comparison", summary)
            self.assertIn("| NO_RELATED_PRODUCTIONS | 1 | 3.25 | failed |", summary)
            self.assertNotIn("productions/restaged/shelf_planning1.py", summary)
            self.assertIn("Totals: 1 passed, 1 failed", summary)

    def test_timeout_exit_code_is_recorded_in_result_payload(self):
        with TemporaryDirectory() as tmpdir:
            result_path = Path(tmpdir) / "timed-out.json"

            payload = finalize_result_payload(
                result_path,
                "ChemLab1",
                124,
                900.0,
                "productions/restaged/chem_lab.py",
            )

            self.assertEqual(payload["simulation_outcome"], {"timed_out": True})
            self.assertEqual(payload["status"], "timed-out")
            written = json.loads(result_path.read_text())
            self.assertEqual(written["simulation_outcome"], {"timed_out": True})
            self.assertEqual(written["status"], "timed-out")

    def test_other_exit_codes_are_not_simulation_outcomes(self):
        with TemporaryDirectory() as tmpdir:
            for exit_code, status in ((0, "passed"), (1, "failed"), (137, "failed")):
                with self.subTest(exit_code=exit_code):
                    payload = finalize_result_payload(
                        Path(tmpdir) / f"{exit_code}.json", "ChemLab1", exit_code, 1.0, ""
                    )
                    self.assertEqual(payload["simulation_outcome"], {"timed_out": False})
                    self.assertEqual(payload["status"], status)

    def test_case_status_and_whether_it_fails_the_run(self):
        cases = [
            ("0", None, "passed", False),
            ("0", {"timed_out": True}, "passed", False),
            ("124", {"timed_out": True}, "timed-out", False),
            ("124", None, "failed", True),
            ("1", {"timed_out": False}, "failed", True),
            ("parse_error", None, "failed", True),
        ]
        for exit_code, outcome, status, fails in cases:
            with self.subTest(exit_code=exit_code, outcome=outcome):
                self.assertEqual(case_status(exit_code, outcome), status)
                self.assertEqual(fails_run(status), fails)

    def test_summary_counts_timed_out_cases_apart_from_failures(self):
        with TemporaryDirectory() as tmpdir:
            results_directory = Path(tmpdir)
            for name, exit_code in (("passed", 0), ("slow", 124), ("broken", 1)):
                finalize_result_payload(results_directory / f"{name}.json", name, exit_code, 1.0, "")

            summary = build_summary(str(results_directory))

            self.assertIn("| slow | 124 | 1.0 | timed-out |", summary)
            self.assertIn("Totals: 1 passed, 1 timed out, 1 failed", summary)

    def test_summary_includes_additional_metrics_when_present(self):
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            result_path = tmp_path / "matrix-results" / "ChemLab1.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps(
                    {
                        "production_class": "ChemLab1",
                        "restaged_file": "productions/restaged/chem_lab1.py",
                        "exit_code": 0,
                        "duration_seconds": 1.5,
                        "metrics": {
                            "Final Configuration Error": 0.01,
                            "Joint Space Length of Plan": 2.5,
                        },
                    }
                )
            )

            summary = build_summary(str(result_path.parent))

            self.assertIn(
                "| Result | Exit Code | Duration (s) | Status | Final Configuration Error | Joint Space Length of Plan |",
                summary,
            )
            self.assertIn(
                "| ChemLab1 | 0 | 1.5 | passed | 0.01 | 2.5 |",
                summary,
            )

    def test_summary_separates_actor_variants_from_production_variants(self):
        with TemporaryDirectory() as tmpdir:
            results_directory = Path(tmpdir)
            result_payloads = {
                "baseline-ChemLab1.json": {
                    "production_class": "ChemLab1",
                    "cast_production_name": "baseline-ChemLab1",
                    "exit_code": 0,
                    "duration_seconds": 1.0,
                },
                "actor-bidirectional_rrt-0.json": {
                    "production_class": "ChemLab1",
                    "cast_production_name": "actor-bidirectional_rrt-0",
                    "exit_code": 0,
                    "duration_seconds": 1.5,
                    "comparison_type": "actor",
                    "comparison_metadata": {
                        "comparison-type": "actor",
                        "actor-name": "bidirectional_rrt",
                        "cast-call-index": 0,
                    },
                },
                "production-ShelfPlanning1.json": {
                    "production_class": "ShelfPlanning1",
                    "cast_production_name": "production-ShelfPlanning1",
                    "exit_code": 1,
                    "duration_seconds": 2.0,
                },
            }
            for filename, payload in result_payloads.items():
                (results_directory / filename).write_text(json.dumps(payload))

            summary = build_summary(str(results_directory))

            self.assertIn("#### Actor Comparison", summary)
            self.assertIn("| Actor | bidirectional_rrt | Current actors |", summary)
            self.assertIn("| Cast Call | 0 |  |", summary)
            self.assertIn("| Production | ChemLab1 | ChemLab1 |", summary)
            self.assertIn(
                "| Cast Production | actor-bidirectional_rrt-0 | baseline-ChemLab1 |", summary
            )
            self.assertIn("#### Production Comparison", summary)
            self.assertIn("| Production | ChemLab1 | ShelfPlanning1 |", summary)
            self.assertIn(
                "| Cast Production | baseline-ChemLab1 | production-ShelfPlanning1 |", summary
            )

    def test_summary_omits_empty_comparison_sections(self):
        with TemporaryDirectory() as tmpdir:
            results_directory = Path(tmpdir)
            (results_directory / "actor-only.json").write_text(
                json.dumps(
                    {
                        "production_class": "ChemLab1",
                        "cast_production_name": "actor-bidirectional_rrt-0",
                        "exit_code": 0,
                        "duration_seconds": 1.5,
                        "comparison_type": "actor",
                        "comparison_metadata": {
                            "comparison-type": "actor",
                            "actor-name": "bidirectional_rrt",
                            "cast-call-index": 0,
                        },
                    }
                )
            )

            summary = build_summary(str(results_directory))

            self.assertIn("#### Actor Comparison", summary)
            self.assertNotIn("#### Production Comparison", summary)
            self.assertNotIn("#### Other Results", summary)

    def test_summary_reports_malformed_results_and_uses_prefix_fallback(self):
        with TemporaryDirectory() as tmpdir:
            results_directory = Path(tmpdir)
            (results_directory / "production-ShelfPlanning1.json").write_text(
                json.dumps(
                    {
                        "production_class": "ShelfPlanning1",
                        "exit_code": 0,
                        "duration_seconds": 2.0,
                    }
                )
            )
            (results_directory / "broken.json").write_text("not json")

            summary = build_summary(str(results_directory))

            self.assertIn("#### Production Comparison", summary)
            self.assertIn("| Production | ShelfPlanning1 |", summary)
            self.assertIn("| Cast Production | production-ShelfPlanning1 |", summary)
            self.assertIn("#### Other Results", summary)
            self.assertIn("| broken | parse_error | n/a | failed |", summary)
            self.assertIn("Warning: 1 result file(s) could not be parsed", summary)


if __name__ == "__main__":
    unittest.main()