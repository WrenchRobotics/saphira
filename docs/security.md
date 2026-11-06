# Security

## Keys tied to a repository

A Wrench API key can be tied to one GitHub repository, so that a leaked key is useless anywhere else. The Wrench API accepts such a key only together with a GitHub Actions OIDC token proving that the request comes from a workflow in that repository. `saphira-run` requests one and sends it in the `Wrench-GitHub-OIDC` header whenever the workflow grants `id-token: write`, as in the README's example. Keys without a repository work with or without it, so keep the permission in every workflow: switching to a repository key then only means replacing the secret.

## OIDC tokens

The token's audience is the scheme and host of `api-url` (`https://api.wrenchrobotics.com` by default), so a token requested for one Wrench environment does not work in another. Pull requests from forks get neither secrets nor OIDC tokens, so they use local cases. If GitHub fails to provide a token, the run continues without it and the job summary says why. A key tied to a repository is then refused.

The `oidc-status` output reports what happened: `sent`, `unavailable` (no permission), `failed`, or `not-requested` (no remote request was made).

## Handling of secrets

Neither the OIDC token nor the API key is ever printed. The action passes the API key to `saphira-run-facade` and `saphira-submit` through the `WRENCH_API_KEY` environment variable, never on the command line. Both CLIs read it only from there.

## What Saphira sends to the Wrench API

To request cases, the facade sends the detected parent and production, the local case IDs, and the `GITHUB_REPOSITORY`, `GITHUB_SHA`, `GITHUB_RUN_ID`, and `GITHUB_RUN_ATTEMPT` values, so the service can choose relevant cases.

To post results, `saphira-submit` sends the detected parent and production, each case's status, exit code, duration and metrics, and the ID and name of the run's uploaded artifact. It does not send your source code or the cases' logs.

## Remote cases are data, never code

A remote actor case only names a registered actor (`{"cast_call_index": 0, "actor": "bidirectional_rrt"}`), and Saphira expands it from the actor registry of the installed `saphira` version. A remote case that carries `import_statements`, `instantiation_code`, `variable_name`, or `method_to_call` fails the run under every fanout source. A remote actor this version does not know about, or one that does not fit the production's cast call, is skipped with a warning in the job summary.
