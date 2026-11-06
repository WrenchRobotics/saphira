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
| `python-version` | `3.14` | `3.12`, `3.13`, or `3.14`, the versions Drake publishes wheels for. |
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

```yaml
          kinds: productions,casts
```

`kinds` applies to local cases and to the cases requested from the Wrench API. When it is empty, local cases include `productions`, and the API returns every kind your key allows. Requesting a kind your key does not allow fails the run, and the error names the missing scope.

## Fanout source

`fanout-source` decides where cases come from:

- `remote-with-fallback` (default) asks the Wrench API for cases. It uses local cases instead when no API key is set (as on pull requests from forks, where secrets are withheld), when the service is unreachable, or when it returns a retryable server error. It still fails for invalid responses and authorization errors.
- `remote` fails if the service cannot provide cases.
- `local` never contacts the service.

Remote cases are deduplicated against local cases by `case-id`, and each result JSON records which source its case came from.

## Running `saphira-run` more than once

When a workflow runs `saphira-run` more than once, for example in a matrix, give each run its own `name`, such as `name: ${{ matrix.name }}`. The name is used for the run's Check, its PR comment section, and its artifact. With `name: Motion Planning`, the heading is `Saphira Results (Motion Planning)` and the artifact is `saphira-results-motion-planning`. Without a name, they are `Saphira Results` and `saphira-results`.

## Environments

`api-url` chooses the Wrench environment, and so which app posts the results:

- `https://api.wrenchrobotics.com` posts as Wrench Saphira.
- `https://api-staging.wrenchrobotics.com` posts as Wrench Saphira [Staging].

Each environment needs an API key from its own website.

## Pinning a version

The examples use `saphira-run@main`, which follows the latest changes. To control when your CI picks up changes, pin a release tag such as `@v0.1.0`, or a commit SHA.

## Debugging

Two environment variables help when debugging. Set them in the workflow's or step's `env`:

- `SAPHIRA_PROGRESS`: `auto` (default), `bar`, `lines`, or `none`.
- `SAPHIRA_FANOUT_TIMEOUT_SECONDS`: the timeout of the request to the Wrench API, `10` by default.
