import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from saphirapy.detect_production_map import detect_production_mapping
from saphirapy.github_actions_contracts import (
    build_summary,
    finalize_result_payload,
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
            )
            finalize_result_payload(
                second_results_dir / "no-related-productions.json",
                "NO_RELATED_PRODUCTIONS",
                1,
                3.25,
                "",
            )

            summary = build_summary(f"{first_results_dir}\n{second_results_dir}")

            self.assertIn("| ShelfPlanning1 | 0 | 12.5 | passed |", summary)
            self.assertIn("| NO_RELATED_PRODUCTIONS | 1 | 3.25 | failed |", summary)
            self.assertNotIn("productions/restaged/shelf_planning1.py", summary)
            self.assertIn("Totals: 1 passed, 1 failed", summary)

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

            self.assertIn("Additional Metrics:", summary)
            self.assertIn(
                "| Production | Final Configuration Error | Joint Space Length of Plan |",
                summary,
            )
            self.assertIn(
                "| ChemLab1 | 0.01 | 2.5 |",
                summary,
            )


if __name__ == "__main__":
    unittest.main()