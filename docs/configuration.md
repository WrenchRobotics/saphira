# Configuring `saphira-run`

The `saphira-run` action is the single entry point to Saphira. It detects the production in your script, builds the cases to run, runs them, and reports the results. The [README](../README.md) has the workflow to start from. This page covers everything else it accepts and returns.

## Inputs

| Input | Default | What it does |
|---|---|---|
| `path-to-example-run` | required | The production script, or a directory to search. In a directory, the first `.py` file that imports from `brom_drake.productions` is used. |
| `api-key` | none | Wrench API key, e.g. `${{ secrets.WRENCH_SAPHIRA_API_KEY }}`. Without it, only local cases run and the workflow posts its own PR comment. |
| `api-url` | `https://api.wrenchrobotics.com` | The Wrench API to use for fanout and reports. See [Environments](#environments). |
| `kinds` | empty | Case kinds to run besides the baseline. See [Case kinds](#case-kinds). |
| `fanout-source` | `remote-with-fallback` | Where cases come from. See [Fanout source](#fanout-source). |
| `report-mode` | `auto` | How results are posted: `auto`, `app`, or `none`. See [Results and reports](results.md#how-results-are-posted). |
| `name` | empty | Names these results. Needed when a workflow runs `saphira-run` more than once. See [Running more than once](#running-saphira-run-more-than-once). |
| `run-timeout-seconds` | `900` | How long a case may run before it is stopped and reported as timed out. |
| `max-concurrency` | empty | How many cases run at the same time. |
| `python-version` | `3.14` | Must be `3.14`, the only version that both brom_drake 1.x and Drake support. |
| `github-token` | `${{ github.token }}` | Used only for the workflow's own PR comment, when the app does not post. |

## Outputs

| Output | What it contains |
|---|---|
| `report-status` | `posted`, `fallback`, `skipped`, or `failed`. |
| `report-url` | The run's report page on the Wrench website or, where the API does not link one, the app's PR comment or Check. |
| `result-count` | How many cases ran. |
| `failed-count` | Cases that failed. The action fails when this is not zero. |
| `timed-out-count` | Cases that timed out. They are reported but do not fail the run. |
| `detected-parent` | The production family Saphira detected. |
| `fanout-source` | The fanout source that was used. |
| `remote-status` | Whether remote fanout was available. |
| `remote-case-count` | How many of the cases that ran came from the Wrench API. |
| `fanout-request-id` | The Wrench API's ID for the fanout request. Empty without remote fanout. |
| `oidc-status` | `sent`, `unavailable` (no `id-token: write` permission), `failed`, or `not-requested` (no remote request was made). |
| `results-directory` | Where the result JSON files are, for later steps that read them. |
| `restaged-directory` | Where the restaged production files are. |

## Case kinds

Every run includes the baseline: your production as written. `kinds` adds more cases:

- `productions` runs related productions (other scenes) with your current cast.
- `casts` runs your production with each alternative actor, such as a different motion planner.
- `conditions` runs your production under other initial conditions, such as a moved shelf or a taller table. Only the Wrench API provides these cases; see [Conditions cases](#conditions-cases).

```yaml
          kinds: productions,casts
```

`kinds` applies to local cases and to the cases requested from the Wrench API. When it is empty, local cases include `productions`, and the API returns every kind your key allows. Requesting a kind your key does not allow fails the run, and the error names the missing scope.

## Conditions cases

A conditions case runs the same production and cast with different values for some of the production's initial conditions. The production declares which conditions may change in its brom_drake `Scenario` (brom_drake 1.1.0 and later), with each parameter's type, units and allowed range. Brom's own productions (ChemLab1, ChemLab2, ChemLab3, ShelfPlanning1 and CornellTech1) already declare one; the brom_drake documentation's Scenarios page shows how to declare one for a new production. Each parameter must be a keyword argument of the production's constructor.

A case carries its values in `scenario-json`, a JSON object from parameter name to value:

```json
{"shelf_pose": {"xyz": [0.05, 1.05, 0.65], "rpy": [0.0, 0.0, 1.5708]}, "table_height": 0.2}
```

Values are numbers, lists of numbers, poses (`xyz` with either `rpy` or a scalar-first `wxyz` quaternion), or `null` for the production's default. Before a case runs, Saphira checks its values with the production's `Scenario`: a case with an unknown parameter, a value of the wrong type, or one outside its range is skipped with a warning in the job summary. The restaged production then receives the values as constructor arguments:

```python
production = ShelfPlanning1(
    meshcat_port_number=meshcat_port_number,
    **ShelfPlanning1.Scenario.constructor_kwargs({"shelf_pose": {...}}),
)
```

Arguments your script passes for the same parameters are removed from the call, and so are arguments the scenario rules out: setting `start_pose` removes `start_configuration`. The values are recorded in the case's result JSON (`scenario`) and restaging manifest.

## Fanout source

`fanout-source` decides where cases come from:

- `remote-with-fallback` (default) asks the Wrench API for cases. It uses local cases instead when no API key is set (as on pull requests from forks, where secrets are withheld), when the service is unreachable, or when it returns a retryable server error. It still fails for invalid responses and authorization errors.
- `remote` fails if the service cannot provide cases.
- `local` never contacts the service.

Remote cases are deduplicated against local cases by `case-id` and by what they run (production, actor and scenario values), and each result JSON records which source its case came from.

## Running `saphira-run` more than once

When a workflow runs `saphira-run` more than once, for example in a matrix, give each run its own `name`, such as `name: ${{ matrix.name }}`. The name is used for the run's Check, its PR comment section, and its artifact. With `name: Motion Planning`, the heading is `Saphira Results (Motion Planning)` and the artifact is `saphira-results-motion-planning`. Without a name, they are `Saphira Results` and `saphira-results`.

## Environments

`api-url` chooses the Wrench environment, and so which app posts the results:

- `https://api.wrenchrobotics.com` posts as Wrench Saphira.
- `https://api-staging.wrenchrobotics.com` posts as Wrench Saphira [Staging].

Each environment needs an API key from its own website.

## Pinning a version

The examples use `saphira-run@v0`, which moves to each new `v0.x.y` release, so you get fixes and new features without changes that break your workflow. To control exactly when your CI picks up changes, pin a release tag such as `@v0.1.0`, or a commit SHA. [RELEASING.md](../RELEASING.md) explains how the tags are managed.

## Debugging

Two environment variables help when debugging. Set them in the workflow's or step's `env`:

- `SAPHIRA_PROGRESS`: `auto` (default), `bar`, `lines`, or `none`.
- `SAPHIRA_FANOUT_TIMEOUT_SECONDS`: the timeout of the request to the Wrench API, `10` by default.

In `auto` mode, GitHub Actions uses line progress and reports when each case starts, then every 60 seconds while it is still running. Set `SAPHIRA_PROGRESS` to `none` to suppress these messages.
