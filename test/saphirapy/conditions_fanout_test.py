"""Conditions fanout: one production run under other initial conditions (saphira#9).

A conditions case carries ``scenario-json``, values for parameters that the production's
brom_drake ``Scenario`` declares. They are checked before the case runs and reach the
production only as constructor arguments built by ``Scenario.constructor_kwargs``.
"""

import ast
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import brom_drake.productions.all as brom_all

import saphirapy.restage_and_run_production as restage_module
from saphirapy.facade import _case_comparison_metadata, _run_case, parse_kinds
from saphirapy.fanout import (
    API_KEY_ENV,
    FanoutRemoteError,
    ScenarioUnavailable,
    case_run_signature,
    merge_fanout_cases,
    normalize_fanout_case,
    resolve_fanout,
)
from saphirapy.github_actions_contracts import build_summary, finalize_result_payload
from saphirapy.restaging import BromRestagingManager
from saphirapy.scenarios import scenario_metadata, validate_scenario, with_scenario_metadata

ENDPOINT = "https://fanout.example.test/v1/saphira/fanout"
TOKEN = "wrk_live_abcdefgh_" + "s" * 43
CHEM_LAB = Path(__file__).resolve().parents[1] / "data" / "motion_planning" / "chem_lab_demo.py"
# ShelfPlanning1's default shelf, moved 5 cm along x and y: the demo still plans around it.
NEAR_SHELF_POSE = {"xyz": [0.05, 1.05, 0.65], "wxyz": [0.7071, 0.0, 0.0, 0.7071]}


class Opener:
    status = 200

    def __init__(self, cases):
        self.body = json.dumps({"cases": cases}).encode("utf-8")

    def __call__(self, request, timeout):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def conditions_case(scenario, production="ShelfPlanning1", case_id="wrench-shelf-moved"):
    return {
        "case-id": case_id,
        "production-class": production,
        "actor-variant-json": "",
        "scenario-json": json.dumps(scenario),
        "comparison-metadata-json": json.dumps({"comparison-type": "conditions"}),
        "source": "wrench",
    }


LOCAL_BASELINE = {
    "case-id": "production-ShelfPlanning1",
    "production-class": "ShelfPlanning1",
    "actor-variant-json": "",
}


class NormalizationTest(unittest.TestCase):
    def test_scenario_is_kept_as_sorted_json(self):
        for raw in ('{"b": 1.0, "a": 2.0}', {"b": 1.0, "a": 2.0}):
            with self.subTest(raw=raw):
                case = normalize_fanout_case(
                    {"production-class": "ChemLab1", "scenario-json": raw}, 0
                )
                self.assertEqual(case["scenario-json"], '{"a": 2.0, "b": 1.0}')

    def test_cases_without_values_keep_their_shape(self):
        for raw in (None, "", "{}", {}):
            with self.subTest(raw=raw):
                case = normalize_fanout_case(
                    {"production-class": "ChemLab1", "scenario-json": raw}, 0
                )
                self.assertNotIn("scenario-json", case)

    def test_malformed_scenarios_are_refused(self):
        for raw in ("not json", "[1, 2]", "3", [1, 2]):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    normalize_fanout_case({"production-class": "ChemLab1", "scenario-json": raw}, 0)

    def test_scenario_is_part_of_the_run_signature(self):
        plain = normalize_fanout_case({"production-class": "ChemLab1"}, 0)
        varied = normalize_fanout_case(
            {"production-class": "ChemLab1", "scenario-json": {"table_height": 0.2}}, 1
        )
        self.assertNotEqual(case_run_signature(plain), case_run_signature(varied))

        merged = merge_fanout_cases(
            [{"production-class": "ChemLab1"}],
            [{"case-id": "wrench-t", "production-class": "ChemLab1",
              "scenario-json": {"table_height": 0.2}}],
        )
        self.assertEqual([case["case-id"] for case in merged],
                         ["production-ChemLab1", "wrench-t"])


class RemoteConditionsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        os.environ.pop(API_KEY_ENV, None)
        self.addCleanup(patcher.stop)

    def resolve(self, cases, source="remote", validator=validate_scenario):
        return resolve_fanout(
            [LOCAL_BASELINE],
            source=source,
            endpoint=ENDPOINT,
            token=TOKEN,
            opener=Opener(cases),
            scenario_validator=validator,
        )

    def test_valid_values_run_in_brom_normalized_form(self):
        resolution = self.resolve([conditions_case({"shelf_pose": NEAR_SHELF_POSE})])

        self.assertEqual(resolution.warnings, [])
        self.assertEqual(resolution.remote_case_count, 1)
        scenario = json.loads(resolution.cases[-1]["scenario-json"])
        # The quaternion arrives as roll-pitch-yaw, Brom's single form for poses.
        self.assertEqual(sorted(scenario["shelf_pose"]), ["rpy", "xyz"])
        self.assertAlmostEqual(scenario["shelf_pose"]["rpy"][2], 1.5708, places=3)

    def test_equal_values_in_either_orientation_form_run_once(self):
        rpy_pose = {"xyz": NEAR_SHELF_POSE["xyz"], "rpy": [0.0, 0.0, 1.5707963267948966]}
        quaternion = {"xyz": NEAR_SHELF_POSE["xyz"], "wxyz": [0.7071067811865476, 0.0, 0.0,
                                                             0.7071067811865476]}
        resolution = self.resolve([
            conditions_case({"shelf_pose": rpy_pose}, case_id="wrench-a"),
            conditions_case({"shelf_pose": quaternion}, case_id="wrench-b"),
        ])

        self.assertEqual([case["case-id"] for case in resolution.cases],
                         ["production-ShelfPlanning1", "wrench-a"])

    def test_values_the_production_refuses_are_skipped_with_a_warning(self):
        refused = [
            conditions_case({"shelf_height": 1.0}, case_id="wrench-unknown"),
            conditions_case({"shelf_pose": {"xyz": [9.0, 9.0, 9.0], "rpy": [0.0, 0.0, 0.0]}},
                            case_id="wrench-out-of-range"),
            conditions_case({"shelf_pose": 1.0}, case_id="wrench-wrong-type"),
            conditions_case({"start_configuration": [0.0] * 6,
                             "start_pose": {"xyz": [0.5, 0.5, 0.5], "rpy": [0.0, 0.0, 0.0]}},
                            case_id="wrench-exclusive"),
            conditions_case({"table_height": 0.2}, production="NotAProduction",
                            case_id="wrench-no-production"),
        ]
        resolution = self.resolve(refused + [conditions_case({"shelf_pose": NEAR_SHELF_POSE})])

        self.assertEqual([case["case-id"] for case in resolution.cases],
                         ["production-ShelfPlanning1", "wrench-shelf-moved"])
        self.assertEqual(len(resolution.warnings), len(refused))
        for case, warning in zip(refused, resolution.warnings):
            self.assertIn(case["case-id"], warning)
        self.assertIn("shelf_height", resolution.warnings[0])
        self.assertIn("outside the allowed range", resolution.warnings[1])

    def test_values_without_a_validator_are_skipped(self):
        resolution = self.resolve([conditions_case({"shelf_pose": NEAR_SHELF_POSE})],
                                  validator=None)

        self.assertEqual(len(resolution.cases), 1)
        self.assertIn("no scenario validator", resolution.warnings[0])

    def test_values_that_are_not_plain_data_reject_the_response(self):
        malformed = [
            {"shelf_pose": "__import__('os').system('x')"},
            {"table_height": True},
            {"table_height": float("nan")},
            {"table_height": [0.1, "0.2"]},
            {"table_height": []},
            {"shelf_pose": {"xyz": [0.0, 1.0, 0.6], "code": [1.0]}},
            {"shelf_pose": {"xyz": [0.0, 1.0, 0.6], "rpy": {"nested": 1.0}}},
            {"bad name": 1.0},
            {"__import__('os')": 1.0},
            {f"p{index}": 1.0 for index in range(51)},
            [1.0, 2.0],
            {"import_statements": ["import os"]},
        ]
        validator = mock.Mock(side_effect=AssertionError("must not validate"))
        for scenario in malformed:
            for source in ("remote", "remote-with-fallback"):
                with self.subTest(scenario=scenario, source=source):
                    with self.assertRaises(FanoutRemoteError) as context:
                        self.resolve(
                            [conditions_case({"shelf_pose": NEAR_SHELF_POSE}),
                             conditions_case(scenario, case_id="wrench-bad")],
                            source=source,
                            validator=validator,
                        )
                    self.assertFalse(context.exception.retryable)
        validator.assert_not_called()


class ScenarioMetadataTest(unittest.TestCase):
    def test_each_value_is_its_own_entry(self):
        self.assertEqual(
            scenario_metadata({"table_height": 0.2, "shelf_pose": None,
                               "start_configuration": [0.0, 1.5]}),
            {
                "scenario.shelf_pose": "default",
                "scenario.start_configuration": "[0.0,1.5]",
                "scenario.table_height": 0.2,
            },
        )

    def test_runner_values_replace_any_the_service_sent(self):
        metadata = with_scenario_metadata(
            {"comparison-type": "conditions", "label": "taller table",
             "scenario.table_height": 9.0, "scenario.ghost": 1.0},
            json.dumps({"table_height": 0.2}),
        )
        self.assertEqual(metadata, {"comparison-type": "conditions", "label": "taller table",
                                    "scenario.table_height": 0.2})

    def test_cases_with_values_and_no_type_are_conditions_cases(self):
        case = {"scenario-json": json.dumps({"table_height": 0.2})}
        self.assertEqual(_case_comparison_metadata(case)["comparison-type"], "conditions")
        self.assertIsNone(_case_comparison_metadata({}))

    def test_conditions_is_a_kind(self):
        self.assertEqual(parse_kinds("productions,conditions"), ["productions", "conditions"])

    def test_productions_without_a_scenario_are_unavailable(self):
        with mock.patch.object(brom_all, "PlainProduction", object, create=True):
            with self.assertRaisesRegex(ScenarioUnavailable, "declares no Scenario"):
                validate_scenario("PlainProduction", {})


class ApplyScenarioTest(unittest.TestCase):
    def apply(self, source, values, production="ChemLab1"):
        manager = BromRestagingManager(output_dir=Path("unused"))
        tree = ast.parse(source)
        ref = manager.find_production_instances(tree)[0]
        production_class = getattr(brom_all, production)
        tree = manager.replace_production_in_ast(tree, ref, production_class)
        values = validate_scenario(production, values)
        out = manager.apply_scenario_in_ast(tree, ref, production_class, values)
        return manager._node_at_path(out, ref.node_path).value

    def keyword_names(self, call):
        return [keyword.arg for keyword in call.keywords]

    def test_values_reach_the_constructor_through_constructor_kwargs(self):
        call = self.apply(
            "from brom_drake.productions.all import ChemLab1\n"
            "production = ChemLab1(meshcat_port_number=None, table_height=0.3)\n",
            {"table_height": 0.2},
        )

        self.assertEqual(
            ast.unparse(call),
            "ChemLab1(meshcat_port_number=None, "
            "**ChemLab1.Scenario.constructor_kwargs({'table_height': 0.2}))",
        )

    def test_set_values_remove_mutually_exclusive_arguments(self):
        call = self.apply(
            "from brom_drake.productions.all import ChemLab1\n"
            "production = ChemLab1(start_configuration=q0, goal_configuration=q1)\n",
            {"start_pose": {"xyz": [0.5, 0.5, 0.5], "rpy": [0.0, 0.0, 0.0]}},
        )

        self.assertEqual(self.keyword_names(call), ["goal_configuration", None])

    def test_positional_arguments_become_keywords_when_one_is_replaced(self):
        call = self.apply(
            "from brom_drake.productions.all import ChemLab1\n"
            "production = ChemLab1(dt, port, speed, 1.2)\n",
            {"table_length": 1.0},
        )

        self.assertEqual(call.args, [])
        self.assertEqual(
            self.keyword_names(call),
            ["time_step", "meshcat_port_number", "plan_execution_speed", None],
        )

    def test_positional_arguments_stay_when_none_is_replaced(self):
        call = self.apply(
            "from brom_drake.productions.all import ChemLab1\n"
            "production = ChemLab1(port)\n",
            {"table_height": 0.2},
        )

        self.assertEqual(ast.unparse(call.args[0]), "port")

    def test_starred_arguments_are_refused(self):
        with self.assertRaisesRegex(ValueError, r"\*args"):
            self.apply(
                "from brom_drake.productions.all import ChemLab1\n"
                "production = ChemLab1(*arguments)\n",
                {"table_height": 0.2},
            )

    def test_generated_arguments_match_brom_constructor_kwargs(self):
        values = {"shelf_pose": NEAR_SHELF_POSE}
        call = self.apply(
            "from brom_drake.productions.all import ShelfPlanning1\n"
            "production = ShelfPlanning1()\n",
            values,
            production="ShelfPlanning1",
        )
        # The literal holds exactly the validated values: evaluate it as data, not code.
        literal = ast.literal_eval(call.keywords[-1].value.args[0])
        self.assertEqual(literal, validate_scenario("ShelfPlanning1", values))


class RestageRefusesInvalidValuesTest(unittest.TestCase):
    def test_invalid_values_fail_before_the_production_runs(self):
        with TemporaryDirectory() as tmpdir:
            with mock.patch.object(restage_module, "_run_restaged_module") as run_module:
                with self.assertRaises(ScenarioUnavailable):
                    restage_module.restage_and_run_one(
                        target_file=CHEM_LAB,
                        production_class_name="ShelfPlanning1",
                        output_dir=Path(tmpdir),
                        scenario={"shelf_pose": {"xyz": [9.0, 9.0, 9.0],
                                                 "rpy": [0.0, 0.0, 0.0]}},
                    )
            run_module.assert_not_called()


class ConditionsSummaryTest(unittest.TestCase):
    def test_conditions_cases_are_compared_with_the_default_conditions(self):
        with TemporaryDirectory() as tmpdir:
            results = Path(tmpdir)
            finalize_result_payload(
                results / "production-ShelfPlanning1.json", "ShelfPlanning1", 0, 10.0, "",
                "production-ShelfPlanning1",
                {"comparison-type": "production", "actor-configuration": "current"},
            )
            finalize_result_payload(
                results / "production-ChemLab1.json", "ChemLab1", 0, 10.0, "",
                "production-ChemLab1",
                {"comparison-type": "production", "actor-configuration": "current"},
            )
            finalize_result_payload(
                results / "wrench-shelf.json", "ShelfPlanning1", 1, 12.0, "", "wrench-shelf",
                with_scenario_metadata(
                    {"comparison-type": "conditions"},
                    json.dumps({"shelf_pose": {"xyz": [0.0, 1.1, 0.6], "rpy": [0.0, 0.0, 0.3]},
                                "table_height": 0.2}),
                ),
            )

            summary = build_summary(str(results))

        self.assertIn("#### Conditions Comparison", summary)
        conditions = summary.split("#### Conditions Comparison")[1].split("####")[0]
        self.assertIn(
            '| Conditions | Defaults | shelf_pose = {"rpy":[0.0,0.0,0.3],"xyz":[0.0,1.1,0.6]}'
            "<br>table_height = 0.2 |",
            conditions,
        )
        self.assertIn("| Cast Production | production-ShelfPlanning1 | wrench-shelf |", conditions)
        # Only the conditions cases' own production is their baseline.
        self.assertNotIn("ChemLab1", conditions)


class ConditionsEndToEndTest(unittest.TestCase):
    """A conditions case restages and runs a real production, and is reported as one."""

    def test_conditions_case_runs_and_appears_in_the_summary(self):
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            case = normalize_fanout_case(
                conditions_case({"shelf_pose": NEAR_SHELF_POSE}), 0
            )
            case["scenario-json"] = json.dumps(
                validate_scenario("ShelfPlanning1", json.loads(case["scenario-json"])),
                sort_keys=True,
            )

            outcome = _run_case(case, str(CHEM_LAB), str(root / "restaged"),
                                str(root / "results"), 600)

            self.assertEqual(outcome["status"], "passed", outcome.get("stderr-tail"))
            payload = json.loads(Path(outcome["result-json-path"]).read_text())
            self.assertEqual(payload["comparison_type"], "conditions")
            self.assertEqual(payload["scenario"]["shelf_pose"]["xyz"], NEAR_SHELF_POSE["xyz"])
            self.assertIn("metrics", payload)
            restaged = Path(payload["restaged_file"]).read_text()
            self.assertIn("ShelfPlanning1.Scenario.constructor_kwargs(", restaged)
            manifest = Path(payload["restaging_manifest"]).read_text()
            self.assertIn('scenario: {"shelf_pose": {"xyz": [0.05, 1.05, 0.65]', manifest)

            summary = build_summary(str(root / "results"))

        self.assertIn("#### Conditions Comparison", summary)
        self.assertIn("shelf_pose = {", summary)


if __name__ == "__main__":
    unittest.main()
