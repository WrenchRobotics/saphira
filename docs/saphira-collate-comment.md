# Deprecated: `saphira-collate-comment`

`saphira-run` reports its own results, so `saphira-collate-comment` is deprecated and will be removed when the workflow's own PR comment is (see [#10](https://github.com/WrenchRobotics/saphira/issues/10)). Until then it still collates result JSON files into a section of the shared PR comment, and warns on every run:

```yaml
- uses: WrenchRobotics/saphira/.github/actions/saphira-collate-comment@main
  with:
    results-directories: productions/matrix-results
    section-key: matrix-results
    section-heading: Matrix Production Results
    github-token: ${{ secrets.GITHUB_TOKEN }}
```

Give each invocation in a workflow its own `section-key`, so results do not overwrite each other.
