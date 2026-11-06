# Saphira

Saphira tests your robotics algorithm in situations you did not write tests for. On every pull request it runs your [brom_drake](https://github.com/kwesiRutledge/brom_drake-py) production as written, then runs the same algorithm in related scenes and with alternative algorithms, and reports what changed compared with your main branch.

Results appear as a GitHub Check and a pull request comment, with a link to a full report on the Wrench website.

## Before you start

You need:

- A GitHub repository where you can add workflows and Actions secrets.
- A Python script in that repository that builds and simulates a brom_drake production. Saphira currently supports `ChemLab1` and `ShelfPlanning1` (kinematic motion planning) and `ChemLab2` (offline dynamic motion planning). [`src/examples/motion_planning1/chem.py`](src/examples/motion_planning1/chem.py) is a working example.

Saphira is in alpha, and access is by request.

## Set up Saphira

The [Wrench dashboard](https://www.wrenchrobotics.com/dashboard) walks you through the same steps and tracks your progress through them.

### 1. Get access

[Request access to Saphira](https://www.wrenchrobotics.com/saphira/request-access). Once your request is approved, [sign in](https://www.wrenchrobotics.com/login) with GitHub.

### 2. Install the Saphira GitHub App

On [Dashboard → GitHub](https://www.wrenchrobotics.com/dashboard/github), install the Wrench Saphira app and choose the repositories that will run Saphira. The app posts each run's results to GitHub. It asks for read access to Actions and contents, and write access to Checks and pull requests.

### 3. Set up a repository

From the dashboard, open the setup page for your repository. It gives you a ready-made workflow with your settings filled in. To do the same by hand:

1. Create an API key for the repository on [Dashboard → API keys](https://www.wrenchrobotics.com/dashboard/keys). Copy it right away: it is not shown again.
2. In your repository, go to **Settings → Secrets and variables → Actions** and add the key as a secret named `WRENCH_SAPHIRA_API_KEY`.
3. Add this workflow as `.github/workflows/saphira.yml`, and change `path-to-example-run` to your production script:

```yaml
name: Saphira

on:
  pull_request:
  push:
    branches: [main]  # pull requests are compared with main's latest results
  workflow_dispatch:

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

If your default branch is not `main`, change it under `push`.

### 4. See your first report

1. Commit the workflow to your default branch. That run records the results that pull requests are compared with.
2. Open a pull request. When the run finishes, the Saphira GitHub App adds a **Saphira Results** Check and a comment to the pull request, each linking to the full report.

Every report is also listed on [Dashboard → Saphira reports](https://www.wrenchrobotics.com/dashboard/saphira/reports).

## If no report appears

Open the workflow run and read its job summary. When the app cannot post, Saphira posts its own pull request comment instead, and the summary says why:

| The summary says | What to do |
|---|---|
| No API key | Add the `WRENCH_SAPHIRA_API_KEY` secret (step 3). |
| No OIDC token | Add `id-token: write` to the workflow's `permissions`. |
| The key was refused | Check that the key is active and was created for this repository. Create a new one if needed. |
| The app is not installed | Install the app on this repository (step 2). The summary includes the install link. |
| The installation is not connected | Connect it on [Dashboard → GitHub](https://www.wrenchrobotics.com/dashboard/github), signed in to the account that owns the key. |

Pull requests from forks get no secrets or OIDC tokens. They run local cases only, and the results stay in the job summary.

A case that **fails** fails the run. Its error output is in the job log, under the case's progress line. A case that **times out** is reported but does not fail the run. See [Results and reports](docs/results.md) for details.

## More documentation

- [Configuration](docs/configuration.md): every input and output of `saphira-run`, running it more than once in a workflow, and debugging settings.
- [Results and reports](docs/results.md): case outcomes, logs and artifacts, and how reports are posted.
- [Security](docs/security.md): API keys tied to a repository, OIDC tokens, and what Saphira sends to the Wrench API.
- [`saphira-collate-comment` (deprecated)](docs/saphira-collate-comment.md).

## License

[MIT](LICENSE)
