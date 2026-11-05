# saphira

A GitHub action for automated testing and validation of robotics algorithms.

## Saphira Facade Action

The `saphira-run` action is the single-entry-point interface for detecting and running a Saphira fanout. It runs your production's local cases and, when a Wrench API key is available, appends corner cases from the Wrench fanout service and has the Saphira GitHub App post the results.

Copy this into `.github/workflows/saphira.yml` and change `path-to-example-run`. It works without any other configuration:

```yaml
name: Saphira

on: [pull_request, workflow_dispatch]

permissions:
  contents: read
  id-token: write       # lets saphira-run prove which repository and run it reports from
  pull-requests: write  # only for the fallback PR comment when the app cannot post

jobs:
  saphira:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: WrenchRobotics/saphira/.github/actions/saphira-run@main
        with:
          path-to-example-run: path/to/your_production.py
          api-key: ${{ secrets.WRENCH_SAPHIRA_API_KEY }}
```

Without a `WRENCH_SAPHIRA_API_KEY` secret, the run uses local cases only and the workflow posts its own PR comment. Add the secret to include corner cases from the Wrench API (`api-url`, `https://api.wrenchrobotics.com` by default) and to have the Saphira GitHub App post the results; see [Results from the Saphira GitHub App](#results-from-the-saphira-github-app). Optional inputs include:

```yaml
          kinds: productions,casts   # also try alternative actors
          fanout-source: local       # never contact the service
```

Every run includes the baseline: your production as written. `kinds` adds more cases: `productions` runs the related productions with your current cast, and `casts` runs your production with each alternative actor. It applies to local cases and to the cases requested from the Wrench API. When it is empty, local cases include `productions`, and the API returns every kind your key allows. Requesting a kind your key does not allow fails the run with the missing scope.

When a workflow runs `saphira-run` more than once, for example in a matrix, give each run its own `name`, such as `name: ${{ matrix.name }}`. It names the results' Check, PR comment section, and artifact. With `name: Motion Planning`, the heading is `Saphira Results (Motion Planning)` and the artifact is `saphira-results-motion-planning`.

`local` never contacts the service. `remote` fails if the service cannot provide cases. `remote-with-fallback` uses local cases when the service is unreachable or returns a retryable server error, and when no token is available (as on pull requests from forks, where secrets are withheld). It still fails for invalid responses and authorization errors, including a `kinds` entry the key does not permit; the error names the missing scope. Remote cases are deduplicated against local cases by `case-id` and retain their source in result JSON.

### Keys tied to a repository

A Wrench API key can be tied to one GitHub repository, so that a leaked key is useless anywhere else. The Wrench API accepts such a key only together with a GitHub Actions OIDC token proving that the request comes from a workflow in that repository. `saphira-run` requests one and sends it in the `Wrench-GitHub-OIDC` header whenever the workflow grants `id-token: write`, as in the example above. Keys without a repository work with or without it, so keep the permission in every workflow: switching to a repository key then only means replacing the secret.

The token's audience is the scheme and host of `api-url` (`https://api.wrenchrobotics.com` by default), so a token requested for one Wrench environment does not work in another. Pull requests from forks get neither secrets nor OIDC tokens, so they use local cases. If GitHub fails to provide a token, the run continues without it and the job summary says why; a key tied to a repository is then refused.

The `oidc-status` output reports what happened: `sent`, `unavailable` (no permission), `failed`, or `not-requested` (no remote request was made). Neither the token nor the API key is ever printed.

The action passes the API key to `saphira-run-facade` and `saphira-submit` through the `WRENCH_API_KEY` environment variable, never on the command line. Both CLIs read it only from there.

The facade POSTs the detected parent and production, the local case IDs, and the `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_RUN_ID`, and `GITHUB_RUN_ATTEMPT` values so the service can choose relevant cases.

**Remote cases are data, never code.** A remote actor case only names a registered actor (`{"cast_call_index": 0, "actor": "bidirectional_rrt"}`), and Saphira expands it from the actor registry of the installed `saphira` version. A remote case that carries `import_statements`, `instantiation_code`, `variable_name`, or `method_to_call` fails the run under every fanout source. A remote actor this version does not know about, or one that does not fit the production's cast call, is skipped with a warning in the job summary.

### Case outcomes and timeouts

Each case ends with one of three statuses:

- `passed`: the case exited with code 0.
- `timed-out`: the case ran past `run-timeout-seconds` (900 by default) and exited with code 124. This usually means the algorithm is too expensive for the task, not that Saphira broke, so it is a simulation outcome rather than a failure: it is reported but does not fail the run.
- `failed`: any other exit code, or a worker that crashed.

Every result JSON records the case's `status`, its `exit_code`, and a `simulation_outcome` object describing what the simulation did, as opposed to whether Saphira could run it. For now it only says whether the case timed out (`{"timed_out": true}`); it replaces the top-level `timed_out` field of earlier versions.

The `failed-count` output counts only `failed` cases, and the action fails when it is not zero. The `timed-out-count` output counts `timed-out` cases, which also appear in the job summary table and totals (`Totals: 3 passed, 1 timed out, 0 failed`) and as a notice on the run.

### Results from the Saphira GitHub App

With an API key, `saphira-run` submits its results to the Wrench API (`POST /v1/saphira/reports`), and the Saphira GitHub App posts them: a GitHub Check on the commit for every run, and a PR comment for pull request runs. Rerunning a job updates both in place. For this to work:

1. Install the Saphira GitHub App on the repository and connect the installation to your Wrench account on [`/dashboard/github`](https://www.wrenchrobotics.com/dashboard/github).
2. Create a Wrench API key for the repository and add it as the `WRENCH_SAPHIRA_API_KEY` secret.
3. Grant `id-token: write`, as in the example above. The API accepts a report only with both the key and a GitHub Actions OIDC token, which proves the repository, run, and commit it comes from.

The `report-mode` input decides what happens:

- `auto` (default) submits the report. When the app cannot post, the workflow posts its own PR comment instead, and the job summary says why: no API key, no OIDC token (pull requests from forks get neither), the key is refused, the app is not installed (with the install link), the key's account has not connected the installation (with the dashboard link), or the API is unreachable.
- `app` submits the report and fails the job when the app cannot post.
- `workflow` only posts the workflow's own PR comment, using `github-token`.
- `none` posts nothing. The results are still in the job summary and the uploaded artifacts.

The `report-status` output is `posted`, `fallback`, `workflow`, `skipped`, or `failed`, and `report-url` links the app's PR comment, or for other events its Check. The job still passes or fails on the results themselves, whatever the report mode.

`api-url` chooses the Wrench environment and so which app posts: `https://api.wrenchrobotics.com` posts as Wrench Saphira, and `https://api-staging.wrenchrobotics.com` as Wrench Saphira [Staging]. Each needs a key from its own environment.

For external consumers, pin the action to a release tag and commit SHA rather than `main` once releases are available.

## Collate Comment Sections for Multi-Step Workflows

When a workflow calls `saphira-collate-comment` more than once, each invocation should use a unique section key so results do not overwrite each other.

If you are using this action from another repository, reference it with `owner/repo/path@ref` format:
- `uses: WrenchRobotics/saphira/.github/actions/saphira-collate-comment@main`

For production workflows, pin to a tag or commit SHA instead of `main`.

How it works:
- `comment-marker` still identifies one shared PR comment.
- `section-key` identifies one upsert target within that comment.
- `section-heading` controls the visible heading for that section.

Recommended pattern:
- Use a stable `section-key` per collate step (for example, `matrix-results`, `fallback-results`, `integration-suite`).
- Keep `section-heading` human-readable for reviewers.

Example:

```yaml
- name: Collate matrix runs
  if: needs.matrix.result == 'success'
  uses: WrenchRobotics/saphira/.github/actions/saphira-collate-comment@main
  with:
    results-directories: productions/matrix-results
    section-key: matrix-results
    section-heading: Matrix Production Results
    github-token: ${{ secrets.GITHUB_TOKEN }}

- name: Collate fallback runs
  if: needs.fallback.result == 'success'
  uses: WrenchRobotics/saphira/.github/actions/saphira-collate-comment@main
  with:
    results-directories: productions/fallback-results
    section-key: fallback-results
    section-heading: Fallback Production Results
    github-token: ${{ secrets.GITHUB_TOKEN }}
```
