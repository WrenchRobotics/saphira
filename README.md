# saphira

A GitHub action for automated testing and validation of robotics algorithms.

## Saphira Facade Action

The `saphira-run` action is the single-entry-point interface for detecting and running a Saphira fanout. It runs your production's local cases and, when a Wrench API key is available, appends corner cases from the Wrench fanout service.

Copy this into `.github/workflows/saphira.yml` and change `path-to-example-run`. It works without any other configuration:

```yaml
name: Saphira

on: [pull_request, workflow_dispatch]

permissions:
  contents: read
  pull-requests: write

jobs:
  saphira:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: WrenchRobotics/saphira/.github/actions/saphira-run@main
        with:
          path-to-example-run: path/to/your_production.py
          fanout-token: ${{ secrets.WRENCH_SAPHIRA_API_KEY }}
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

Without a `WRENCH_SAPHIRA_API_KEY` secret, the run uses local cases only. Add the secret to include corner cases from `https://api.wrenchrobotics.com/v1/saphira/fanout`, the default `fanout-endpoint`. Optional inputs include:

```yaml
          fanout-source: local              # never contact the service
          fanout-kinds: productions,casts   # defaults to everything the key allows
          include-alternative-actors: 'true'
```

`local` never contacts the service. `remote` fails if the service cannot provide cases. `remote-with-fallback` uses local cases when the service is unreachable or returns a retryable server error, and when no token is available (as on pull requests from forks, where secrets are withheld). It still fails for invalid responses and authorization errors, including a `fanout-kinds` entry the key does not permit; the error names the missing scope. Remote cases are deduplicated against local cases by `case-id` and retain their source in result JSON.

The action passes the token to the facade through the `FANOUT_TOKEN` environment variable, never on the command line. When running `saphira-run-facade` yourself, prefer `FANOUT_TOKEN` over `--fanout-token` for the same reason.

The facade POSTs the detected parent and production, the local case IDs, and the `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_RUN_ID`, and `GITHUB_RUN_ATTEMPT` values so the service can choose relevant cases.

**Remote cases are data, never code.** A remote actor case only names a registered actor (`{"cast_call_index": 0, "actor": "bidirectional_rrt"}`), and Saphira expands it from the actor registry of the installed `saphira` version. A remote case that carries `import_statements`, `instantiation_code`, `variable_name`, or `method_to_call` fails the run under every fanout source. A remote actor this version does not know about, or one that does not fit the production's cast call, is skipped with a warning in the job summary.

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
