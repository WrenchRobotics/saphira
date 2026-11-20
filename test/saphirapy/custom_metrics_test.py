import types
import unittest
from contextlib import redirect_stderr
from io import StringIO

import numpy as np

from saphirapy.custom_metrics import (
    CustomMetrics,
    collect_custom_metrics,
    defines_custom_metrics,
    merge_metrics,
)


def script(**names) -> types.ModuleType:
    """A stand-in for a loaded production script with the given top-level names."""
    module = types.ModuleType("example_production")
    for name, value in names.items():
        setattr(module, name, value)
    return module


class CollectCustomMetricsTest(unittest.TestCase):
    def test_script_without_custom_metrics_reports_nothing(self):
        module = script(main=lambda: None)

        self.assertFalse(defines_custom_metrics(module))
        self.assertEqual(collect_custom_metrics(module, object()), CustomMetrics())
        self.assertEqual(collect_custom_metrics(None, object()), CustomMetrics())

    def test_values_and_definitions_are_collected(self):
        production = object()
        seen = []

        def saphira_metrics(received):
            seen.append(received)
            return {
                "Grasp Success": True,
                "Peak Joint Torque": np.float64(12.5),
                "Contacts": np.int64(3),
            }

        module = script(
            saphira_metrics=saphira_metrics,
            SAPHIRA_METRICS={
                "Grasp Success": {"better": "higher"},
                "Peak Joint Torque": {"better": "lower", "unit": "N·m", "tolerance": 0.5},
            },
        )

        result = collect_custom_metrics(module, production)

        self.assertEqual(seen, [production])
        self.assertEqual(
            result.values,
            {"Grasp Success": 1.0, "Peak Joint Torque": 12.5, "Contacts": 3.0},
        )
        self.assertTrue(all(type(value) is float for value in result.values.values()))
        self.assertEqual(
            result.definitions,
            {
                "Grasp Success": {"better": "higher"},
                "Peak Joint Torque": {"better": "lower", "unit": "N·m", "tolerance": 0.5},
            },
        )
        self.assertEqual(result.warnings, [])

    def test_an_exception_becomes_a_warning_and_keeps_the_definitions(self):
        def saphira_metrics(production):
            raise KeyError("station")

        module = script(
            saphira_metrics=saphira_metrics,
            SAPHIRA_METRICS={"Grasp Success": {"better": "higher"}},
        )

        stderr = StringIO()
        with redirect_stderr(stderr):
            result = collect_custom_metrics(module, object())

        self.assertEqual(result.values, {})
        self.assertEqual(result.definitions, {"Grasp Success": {"better": "higher"}})
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("saphira_metrics raised KeyError", result.warnings[0])
        self.assertIn("Traceback", stderr.getvalue())

    def test_values_that_are_not_numbers_are_dropped_with_a_warning(self):
        module = script(
            saphira_metrics=lambda production: {
                "ok": 1.0,
                "label": "fast",
                "missing": None,
                "": 2.0,
                "x" * 101: 3.0,
            }
        )

        result = collect_custom_metrics(module, object())

        self.assertEqual(result.values, {"ok": 1.0})
        self.assertEqual(len(result.warnings), 4)
        self.assertIn("returned str for 'label'", result.warnings[0])

    def test_a_return_value_that_is_not_a_dict_is_a_warning(self):
        module = script(saphira_metrics=lambda production: [1.0, 2.0])

        result = collect_custom_metrics(module, object())

        self.assertEqual(result.values, {})
        self.assertIn("must return a dict, not list", result.warnings[0])

    def test_without_a_production_the_function_is_not_called(self):
        def saphira_metrics(production):
            raise AssertionError("should not be called")

        module = script(
            saphira_metrics=saphira_metrics,
            SAPHIRA_METRICS={"Grasp Success": {"better": "higher"}},
        )

        result = collect_custom_metrics(module, None)

        self.assertEqual(result.values, {})
        self.assertEqual(result.definitions, {"Grasp Success": {"better": "higher"}})
        self.assertEqual(result.warnings, [])

    def test_invalid_definition_parts_are_dropped_and_the_rest_kept(self):
        module = script(
            SAPHIRA_METRICS={
                "Peak Joint Torque": {
                    "better": "smaller",
                    "unit": "N·m",
                    "tolerance": -1,
                    "colour": "red",
                },
                "Grasp Success": "higher",
                7: {"better": "lower"},
            }
        )

        result = collect_custom_metrics(module, object())

        self.assertEqual(result.definitions, {"Peak Joint Torque": {"unit": "N·m"}})
        self.assertEqual(len(result.warnings), 5)

    def test_definitions_that_are_not_a_dict_are_ignored(self):
        result = collect_custom_metrics(script(SAPHIRA_METRICS=["Grasp Success"]), object())

        self.assertEqual(result.definitions, {})
        self.assertIn("SAPHIRA_METRICS must be a dict, not list", result.warnings[0])


class MergeMetricsTest(unittest.TestCase):
    BUILTIN_DEFINITIONS = {
        "Final Configuration Error": {"better": "lower"},
        "Joint Space Length of Plan": {"better": "lower"},
    }

    def test_custom_metrics_are_added_to_the_builtin_ones(self):
        values, definitions, warnings = merge_metrics(
            {"Final Configuration Error": 0.1},
            self.BUILTIN_DEFINITIONS,
            CustomMetrics(
                values={"Grasp Success": 1.0},
                definitions={"Grasp Success": {"better": "higher"}},
            ),
        )

        self.assertEqual(values, {"Final Configuration Error": 0.1, "Grasp Success": 1.0})
        # Only built-in definitions for metrics this case reported.
        self.assertEqual(
            definitions,
            {
                "Final Configuration Error": {"better": "lower"},
                "Grasp Success": {"better": "higher"},
            },
        )
        self.assertEqual(warnings, [])

    def test_a_custom_value_with_a_builtin_name_wins_with_a_warning(self):
        values, _, warnings = merge_metrics(
            {"Final Configuration Error": 0.1},
            self.BUILTIN_DEFINITIONS,
            CustomMetrics(values={"Final Configuration Error": 0.2}),
        )

        self.assertEqual(values, {"Final Configuration Error": 0.2})
        self.assertEqual(len(warnings), 1)
        self.assertIn("replaces Saphira's built-in metric", warnings[0])

    def test_a_custom_definition_can_refine_a_builtin_metric(self):
        values, definitions, warnings = merge_metrics(
            {"Final Configuration Error": 0.1},
            self.BUILTIN_DEFINITIONS,
            CustomMetrics(
                definitions={"Final Configuration Error": {"better": "lower", "tolerance": 1e-3}}
            ),
        )

        self.assertEqual(values, {"Final Configuration Error": 0.1})
        self.assertEqual(
            definitions, {"Final Configuration Error": {"better": "lower", "tolerance": 1e-3}}
        )
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
