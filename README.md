# saphira
A GitHub action for automated testing and validation of robotics algorithms.

## Collate Comment Sections for Multi-Step Workflows

When a workflow calls `saphira-collate-comment` more than once, each invocation should use a unique section key so results do not overwrite each other.

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
	uses: ./.github/actions/saphira-collate-comment
	with:
		results-directories: productions/matrix-results
		section-key: matrix-results
		section-heading: Matrix Production Results
		allow-comment-update: ${{ needs.matrix.result == 'success' }}
		github-token: ${{ secrets.GITHUB_TOKEN }}

- name: Collate fallback runs
	uses: ./.github/actions/saphira-collate-comment
	with:
		results-directories: productions/fallback-results
		section-key: fallback-results
		section-heading: Fallback Production Results
		allow-comment-update: ${{ needs.fallback.result == 'success' }}
		github-token: ${{ secrets.GITHUB_TOKEN }}
```
