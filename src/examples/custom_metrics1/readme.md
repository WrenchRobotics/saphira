# Custom metrics

[`chem_with_metrics.py`](chem_with_metrics.py) is the [ChemLab1 motion planning example](../motion_planning1/chem.py) with custom metrics added. Saphira reports and compares them on every pull request, alongside its built-in metrics.

It adds two top-level definitions to the script, and imports nothing from saphirapy:

- `saphira_metrics(production)` returns the metrics for one run, as a dict from name to number.
- `SAPHIRA_METRICS` says how to compare each metric: which direction is better, its unit, and how big a change counts as noise.

## What it measures

| Metric | Where the value comes from | Compared as |
|---|---|---|
| Planned Trajectory Duration | The production's objects: the planner's trajectory | Lower is better, in s, changes under 0.05 s ignored |
| Goal Reached | Recorded data, checked against the production's goal configuration | Higher is better (1 when reached, 0 when not) |
| Peak Joint Speed | Recorded data: the arm's measured joint positions over time | Lower is better, in rad/s, changes under 0.01 ignored |
| Final Configuration Error | Saphira's built-in value; the script only adds a tolerance | Lower is better, changes under 1e-4 ignored |

Saphira also runs this script with other scenes, such as `ShelfPlanning1`, and with other planners. The function checks for each object it needs before using it, and leaves a metric out when it can't compute it. Saphira's [custom metrics guide](../../../docs/custom-metrics.md#every-case-runs-the-same-function) explains why.

## Try it locally

```bash
python src/examples/custom_metrics1/chem_with_metrics.py
```

This runs the simulation, viewable in Meshcat on port 7001, and then prints the metrics, for example:

```
{'Planned Trajectory Duration': 59.6609425704187, 'Goal Reached': True, 'Peak Joint Speed': 0.15976920694697516}
```

The exact values depend on the plan the RRT-Connect planner finds. Saphira calls `main()` itself and ignores the `if __name__ == "__main__":` block, so the script can keep it.

## Run it with Saphira

Point `saphira-run` at the script:

```yaml
      - uses: WrenchRobotics/saphira/.github/actions/saphira-run@v0
        with:
          path-to-example-run: src/examples/custom_metrics1/chem_with_metrics.py
          api-key: ${{ secrets.WRENCH_SAPHIRA_API_KEY }}
```

The metrics appear as extra columns in the job summary (with units, as in `Peak Joint Speed (rad/s)`) and in the report.

## Use it for your own production

Copy `SAPHIRA_METRICS`, `saphira_metrics` and, if you read recorded data, `recorded_arm_positions` into your own production script. Then replace the metrics with your own. Your `main()` needs to assign its production to a variable named `production`. See the [custom metrics guide](../../../docs/custom-metrics.md) for every rule and option.
