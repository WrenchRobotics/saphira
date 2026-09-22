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

For a remote service with local fallback, provide the endpoint and a token through a secret:

```yaml
- name: Run Saphira with optional premium cases
  uses: kwesirutledge/saphira/.github/actions/saphira-run@main
  with:
    path-to-example-run: src/examples/motion_planning1/chem.py
    fanout-source: remote-with-fallback
    fanout-endpoint: https://fanout.example.com/v1/cases
    fanout-token: ${{ secrets.SAPHIRA_FANOUT_TOKEN }}
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

`local` never contacts the service. `remote` fails if the service cannot provide cases. `remote-with-fallback` uses local cases when the service is unreachable or returns a retryable server error, but still fails for invalid responses or authorization errors. Remote cases are deduplicated against local cases by `case-id` and retain their source in result JSON.

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
  uses: kwesirutledge/saphira/.github/actions/saphira-collate-comment@main
  with:
    results-directories: productions/matrix-results
    section-key: matrix-results
    section-heading: Matrix Production Results
    allow-comment-update: ${{ needs.matrix.result == 'success' }}
    github-token: ${{ secrets.GITHUB_TOKEN }}

- name: Collate fallback runs
  uses: kwesirutledge/saphira/.github/actions/saphira-collate-comment@main
  with:
    results-directories: productions/fallback-results
    section-key: fallback-results
    section-heading: Fallback Production Results
    allow-comment-update: ${{ needs.fallback.result == 'success' }}
    github-token: ${{ secrets.GITHUB_TOKEN }}
```
