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
          fanout-source: local              # never contact the service
          fanout-kinds: productions,casts   # defaults to everything the key allows
          include-alternative-actors: 'true'
```

`local` never contacts the service. `remote` fails if the service cannot provide cases. `remote-with-fallback` uses local cases when the service is unreachable or returns a retryable server error, and when no token is available (as on pull requests from forks, where secrets are withheld). It still fails for invalid responses and authorization errors, including a `fanout-kinds` entry the key does not permit; the error names the missing scope. Remote cases are deduplicated against local cases by `case-id` and retain their source in result JSON.

### Keys tied to a repository

A Wrench API key can be tied to one GitHub repository, so that a leaked key is useless anywhere else. The Wrench API accepts such a key only together with a GitHub Actions OIDC token proving that the request comes from a workflow in that repository. `saphira-run` requests one and sends it in the `Wrench-GitHub-OIDC` header whenever the workflow grants `id-token: write`, as in the example above. Keys without a repository work with or without it, so keep the permission in every workflow: switching to a repository key then only means replacing the secret.

The token's audience is the scheme and host of `api-url` (`https://api.wrenchrobotics.com` by default), so a token requested for one Wrench environment does not work in another. Pull requests from forks get neither secrets nor OIDC tokens, so they use local cases. The `fanout-oidc` input controls the token:

- `auto` (default) sends a token when the workflow grants `id-token: write`. If GitHub fails to provide one, the run continues without it and the job summary says why.
- `always` fails the run when no token can be obtained. Use it with a key tied to a repository to catch a missing permission early.
- `never` sends no token.

The `oidc-status` output reports what happened: `sent`, `unavailable` (no permission), `failed`, `disabled`, or `not-requested` (no remote request was made). Neither the token nor the API key is ever printed.

The action passes the API key to the facade through the `FANOUT_TOKEN` environment variable, and to `saphira-submit` through `WRENCH_API_KEY`, never on the command line. When running `saphira-run-facade` yourself, prefer `FANOUT_TOKEN` over `--fanout-token` for the same reason.

`fanout-token` and `fanout-endpoint` are deprecated: `fanout-token` is an alias of `api-key`, and `fanout-endpoint` overrides the fanout endpoint derived from `api-url`.

The facade POSTs the detected parent and production, the local case IDs, and the `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_RUN_ID`, and `GITHUB_RUN_ATTEMPT` values so the service can choose relevant cases.

**Remote cases are data, never code.** A remote actor case only names a registered actor (`{"cast_call_index": 0, "actor": "bidirectional_rrt"}`), and Saphira expands it from the actor registry of the installed `saphira` version. A remote case that carries `import_statements`, `instantiation_code`, `variable_name`, or `method_to_call` fails the run under every fanout source. A remote actor this version does not know about, or one that does not fit the production's cast call, is skipped with a warning in the job summary.

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
