# Custom metrics

Saphira reports a few metrics on its own, such as how far a motion plan ends from its goal. Your own measures of success, like whether a grasp held, peak joint torque, or time to first contact, can be reported alongside them. Add a `saphira_metrics` function to the production script that `saphira-run` already points at. Saphira calls it on every case and compares the results with your default branch, just like its built-in metrics.

You don't install or import anything new. Your script still runs on its own with `python your_production.py`.

For a complete, working script, see the [custom metrics example](../src/examples/custom_metrics1/readme.md). It's the ChemLab1 motion planning example with metrics taken from the planned trajectory, from data recorded during the simulation, and from the production's goal.

## Quick start

Add these two top-level definitions to your production script, next to `main()`:

```python
import numpy as np

# Optional: how Saphira should compare each metric.
SAPHIRA_METRICS = {
    "Goal Reached": {"better": "higher"},
    "Start-Goal Distance": {"better": "lower", "unit": "rad", "tolerance": 1e-3},
}


def saphira_metrics(production):
    """Return {metric name: number} for one finished run of the production."""
    distance = np.linalg.norm(production.goal_configuration - production.start_configuration)
    return {
        "Goal Reached": distance < 0.01,  # True and False are reported as 1 and 0
        "Start-Goal Distance": distance,
    }


def main():
    production = ShelfPlanning1(...)  # must be assigned to a variable named `production`
    ...
```

Commit the change to your default branch, then open a pull request. Your metrics appear as extra columns in the job summary and in the report, next to Saphira's own.

## `saphira_metrics(production)`

Saphira calls this function once per case, after that case's `main()` finishes. It takes one argument: the production object your `main()` built and ran, as brom_drake left it after the simulation. Use it to read whatever your metric needs, such as the station, the start and goal configurations, or the watcher's recorded data.

It must return a dict:

- **Keys** are metric names: non-empty strings of at most 100 characters. A name is the metric's identity, so keep it the same across commits. Renaming a metric makes the report show the old name as removed and the new one as added, not as a change.
- **Values** must be numbers: `int`, `float`, `bool`, or NumPy scalars such as `np.float64`. Booleans become `1.0` and `0.0`. Values of any other type (strings, `None`, arrays) are left out, with a warning.

Two requirements in `main()`:

- **The production must be in a local variable named `production`.** Saphira finds it by that name when `main()` returns. If `main()` finishes without one, Saphira skips `saphira_metrics` and warns you.
- **`main()` must finish.** If the case fails or times out, there is no finished production to measure, so Saphira doesn't call `saphira_metrics`. The case's failure is reported as usual.

### Every case runs the same function

Saphira runs your script more than once. It runs your production as written (the baseline), and depending on `kinds` it also runs it in related scenes, with alternative actors, and under other initial conditions. It rewrites a copy of your script for each case and keeps your top-level definitions in every copy, so `saphira_metrics` runs for all of them.

So the `production` you receive is not always the class you wrote. In a `productions` case it may be a different scene. Write metrics that hold for every production you expect. Where a metric doesn't apply, leave its name out of the dict rather than returning a placeholder:

```python
def saphira_metrics(production):
    metrics = {"Start-Goal Distance": np.linalg.norm(
        production.goal_configuration - production.start_configuration
    )}
    if hasattr(production, "shelf_pose"):  # only some scenes have a shelf
        metrics["Shelf Clearance"] = shelf_clearance(production)
    return metrics
```

A metric missing from a case is shown as blank for that case and is not compared.

### Reading recorded data

When `main()` builds the production with `with_watcher=True`, its brom_drake watcher records the ports it watches during the simulation. Read that data from memory through the watcher's port watchers. This works the same way when your script runs on its own and when Saphira runs it:

```python
def saphira_metrics(production):
    watcher = production.watcher
    port_watcher = watcher.get_port_watcher(
        system_name=production.station.get_name(),
        port_name="measured_arm_position",
    )
    data = port_watcher.get_data_dictionary(watcher.diagram_context)
    positions = next(iter(data.values()))  # shape: (number of joints, number of samples)
    times = port_watcher.get_timing_array(watcher.diagram_context)
    return {"Samples Recorded": positions.shape[1]}
```

`get_port_watcher` raises a `ValueError` when the system or port isn't being watched, which can happen in some productions Saphira runs. The [example](../src/examples/custom_metrics1/chem_with_metrics.py) catches it and leaves those metrics out.

Saphira also saves the watcher's raw data and figures to disk before calling `saphira_metrics`. If that fails, it still calls `saphira_metrics` and adds a warning.

### Errors never fail a case

If `saphira_metrics` raises an exception, returns something other than a dict, or returns values Saphira can't use, the case still passes or fails on its own outcome. Saphira records a warning instead:

- **Job summary:** the **Custom Metric Warnings** list, with each distinct warning shown once and the cases it came from.
- **Case's result JSON:** the `metric_warnings` field.
- **Case's `stderr.log`:** the full traceback, when your function raised an exception.

When `saphira_metrics` raises, that case reports none of your metrics, only Saphira's built-in ones. To keep one fragile metric from taking down the others, catch its errors inside your function and leave it out of the dict.

## `SAPHIRA_METRICS`: how to compare each metric

`SAPHIRA_METRICS` is optional. Without it, Saphira still reports your metrics, but a pull request that changes a value is shown as "changed", not as "better" or "worse". Each entry maps a metric name to a dict with any of these keys:

| Key | Values | What it does |
|---|---|---|
| `better` | `"higher"` or `"lower"` | Which direction is an improvement. With it, a change is reported as better or worse. |
| `unit` | A string of up to 40 characters, such as `"m"`, `"rad"`, `"N·m"` or `"s"` | Shown with the metric's name in result tables, as in `Peak Joint Torque (N·m)`. |
| `tolerance` | A number of at least 0 | How large a change, in the metric's own units, still counts as unchanged. For example, with `0.001` a change from `0.2500` to `0.2508` is not flagged. Use it for metrics that vary slightly from run to run. Without it, any difference counts as a change. |

Saphira checks every entry. An invalid entry, or an invalid key within one, is ignored with a warning, and the metric is still reported. For example, a `better` of `"smaller"` means the metric has no direction, but its values still appear.

You may define a metric your function only reports for some productions, or one it doesn't report at all yet. Defining a metric doesn't require a value.

## Saphira's built-in metrics

For kinematic motion planning productions (`ChemLab1` and `ShelfPlanning1`), Saphira also reports these metrics, all with `better: "lower"`:

| Metric | Unit |
|---|---|
| `Final Configuration Error` | |
| `Joint Space Length of Plan` | |
| `Cartesian Space Length of Plan` | m |

Your script can change how these behave:

- **Refine their comparison.** An entry in `SAPHIRA_METRICS` with a built-in metric's name replaces Saphira's definition for it, for example to add a `tolerance`. The value is still Saphira's.
- **Replace their value.** If `saphira_metrics` returns a built-in metric's name, your value is reported instead of Saphira's, and the job summary warns you that this happened. Pick a different name unless you mean to replace it.

## Trying your metrics locally

Your script doesn't import saphirapy, so you can test the function directly. The example does it by returning the production from `main()` and printing the metrics when the file is run on its own:

```python
def main():
    production = ShelfPlanning1(...)
    ...
    return production


if __name__ == "__main__":
    print(saphira_metrics(main()))
```

Saphira calls `main()` itself and ignores the `if __name__ == "__main__":` block, so you can keep it in the committed script.

## Limits

- **At most 50 metrics per case,** built-in and custom together. A report keeps the first 50.
- **Metric names** can be at most 100 characters.
- **Units** can be at most 40 characters.
- **Each metric's value** is sent to the Wrench API as text of at most 200 characters, which any number fits.

## Troubleshooting

| The warning says | What to do |
|---|---|
| `main() has no local variable named production` | Assign the production to a variable named `production` inside `main()`. |
| `saphira_metrics raised …` | Read the traceback in the case's `stderr.log`, in the uploaded `saphira-results` artifact. If the error happens only for some cases, check for the attribute your metric needs before using it (see [Every case runs the same function](#every-case-runs-the-same-function)). |
| `saphira_metrics must return a dict` | Return a dict from metric names to numbers, even an empty one. |
| `saphira_metrics returned str for 'name'` (or another type) | Convert the value to a number. For a pass/fail metric, return a `bool`. |
| `… replaces Saphira's built-in metric of the same name` | Rename your metric, unless you meant to replace Saphira's value. |
| `SAPHIRA_METRICS[…] …` | Fix the entry as the warning says. Allowed keys are `better`, `unit` and `tolerance`. |
