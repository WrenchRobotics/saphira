# Results and reports

## Case outcomes and timeouts

Each case ends with one of three statuses:

- `passed`: the case exited with code 0.
- `timed-out`: the case ran past `run-timeout-seconds` (900 by default) and exited with code 124. This usually means the algorithm is too expensive for the task, not that Saphira broke, so it is a simulation outcome rather than a failure: it is reported but does not fail the run.
- `failed`: any other exit code, or a worker that crashed.

The `failed-count` output counts only `failed` cases, and the action fails when it is not zero. The `timed-out-count` output counts `timed-out` cases, which also appear in the job summary table and totals (`Totals: 3 passed, 1 timed out, 0 failed`) and as a notice on the run. The job passes or fails on the results themselves, whatever the report mode.

## Result files

Every result JSON records the case's `status`, its `exit_code`, and a `simulation_outcome` object describing what the simulation did, as opposed to whether Saphira could run it. For now it only says whether the case timed out (`{"timed_out": true}`). It replaces the top-level `timed_out` field of earlier versions.

## Logs

Each case's stdout and stderr are saved as `stdout.log` and `stderr.log` next to its restaged file in the uploaded artifact, and the result JSON records their paths as `stdout_log` and `stderr_log`. When a case does not pass, the result JSON also keeps the last 40 lines of stderr as `stderr_tail`, and the job log prints them in a collapsible group under the case's progress line.

## Artifacts and working directories

The action writes its files under `$RUNNER_TEMP`, never into your checkout, and uploads them as an artifact (`saphira-results`, or `saphira-results-<name>` with a `name`). Later steps can read them from the `results-directory` (result JSON files) and `restaged-directory` (restaged productions) outputs.

Each case runs with its restaged directory as its working directory, so files a production writes to relative paths (such as brom_drake's `./brom` watcher output) stay separate from cases running at the same time. The workspace stays on `PYTHONPATH`, so the example can still import modules from the repository, but paths it opens relative to the working directory resolve inside the case's restaged directory. The `brom` watcher output is not uploaded with the artifact.

## How results are posted

With an API key, `saphira-run` submits its results to the Wrench API (`POST /v1/saphira/reports`), and the Saphira GitHub App posts them: a GitHub Check on the commit for every run, and a PR comment for pull request runs. Rerunning a job updates both in place.

This needs the app installed and connected to your Wrench account, an API key for the repository, and `id-token: write` in the workflow's permissions. The [README](../README.md#set-up-saphira) walks through all three. The API accepts a report only with both the key and a GitHub Actions OIDC token, which proves the repository, run, and commit it comes from.

Each pull request's report is compared with the default branch's results at the point where the pull request branched off. That is why the example workflow also runs on pushes to the default branch: without those runs there is nothing to compare with.

The `report-mode` input decides what happens:

- `auto` (default) submits the report. When the app cannot post, the workflow posts its own PR comment instead, and the job summary says why: no API key, no OIDC token (pull requests from forks get neither), the key is refused, the app is not installed (with the install link), the key's account has not connected the installation (with the dashboard link), or the API is unreachable.
- `app` submits the report and fails the job when the app cannot post.
- `none` posts nothing. The results are still in the job summary and the uploaded artifacts.

The `report-status` output is `posted`, `fallback`, `skipped`, or `failed`. `report-url` links the run's report page on the Wrench website or, where the API does not link that page, the app's PR comment, or for other events its Check.
