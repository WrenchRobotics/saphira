# saphira

A GitHub action for automated testing and validation of robotics algorithms.

## Saphira Facade Action

The `saphira-run` action is the single-entry-point interface for detecting and running a Saphira fanout. It executes local cases by default and can append cases returned by a remote fanout service.

```yaml
- name: Run Saphira locally
  uses: kwesirutledge/saphira/.github/actions/saphira-run@main
  with:
    path-to-example-run: src/examples/motion_planning1/chem.py
    fanout-source: local
    include-alternative-actors: 'true'
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

To add corner cases from the Wrench fanout service, store a Wrench API key as a repository secret
and point the action at the service:

```yaml
- uses: ./.github/actions/saphira-run
  with:
    path-to-example-run: src/examples/motion_planning1/chem.py
    fanout-source: remote-with-fallback
    fanout-endpoint: https://api.wrenchrobotics.com/v1/saphira/fanout
    fanout-token: ${{ secrets.WRENCH_SAPHIRA_API_KEY }}
    fanout-kinds: productions,casts   # optional; defaults to everything the key allows
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

`local` never contacts the service. `remote` fails if the service cannot provide cases. `remote-with-fallback` uses local cases when the service is unreachable or returns a retryable server error, and when no token is available (as on pull requests from forks, where secrets are withheld). It still fails for invalid responses and authorization errors, including a `fanout-kinds` entry the key does not permit; the error names the missing scope. Remote cases are deduplicated against local cases by `case-id` and retain their source in result JSON.

The action passes the token to the facade through the `FANOUT_TOKEN` environment variable, never on the command line. When running `saphira-run-facade` yourself, prefer `FANOUT_TOKEN` over `--fanout-token` for the same reason.

The facade POSTs the detected parent and production, the local case IDs, and the `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_RUN_ID`, and `GITHUB_RUN_ATTEMPT` values so the service can choose relevant cases.

**Remote cases are data, never code.** A remote actor case only names a registered actor (`{"cast_call_index": 0, "actor": "bidirectional_rrt"}`), and Saphira expands it from the actor registry of the installed `saphira` version. A remote case that carries `import_statements`, `instantiation_code`, `variable_name`, or `method_to_call` fails the run under every fanout source. A remote actor this version does not know about, or one that does not fit the production's cast call, is skipped with a warning in the job summary.

For external consumers, pin the action to a release tag and commit SHA rather than `main` once releases are available.

## Collate Comment Sections for Multi-Step Workflows

When a workflow calls `saphira-collate-comment` more than once, each invocation should use a unique section key so results do not overwrite each other.

If you are using this action from another repository, reference it with `owner/repo/path@ref` format:
- `uses: kwesirutledge/saphira/.github/actions/saphira-collate-comment@main`

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
  uses: kwesirutledge/saphira/.github/actions/saphira-collate-comment@main
  with:
    results-directories: productions/matrix-results
    section-key: matrix-results
    section-heading: Matrix Production Results
    github-token: ${{ secrets.GITHUB_TOKEN }}

- name: Collate fallback runs
  if: needs.fallback.result == 'success'
  uses: kwesirutledge/saphira/.github/actions/saphira-collate-comment@main
  with:
    results-directories: productions/fallback-results
    section-key: fallback-results
    section-heading: Fallback Production Results
    github-token: ${{ secrets.GITHUB_TOKEN }}
```
