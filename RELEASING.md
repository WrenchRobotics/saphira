# Releasing

Users reference the actions by major version, as in
`WrenchRobotics/saphira/.github/actions/saphira-run@v0`. GitHub does not resolve versions: `v0`
is an ordinary git tag that points to the newest `v0.x.y` release.

## Every release

Tag a commit on `main` and push the tag:

```bash
git tag v0.2.0
git push origin v0.2.0
```

[`move-major-tag.yml`](.github/workflows/move-major-tag.yml) then moves `v0` to the same commit.
Release tags are never moved or deleted. Publish a GitHub Release for `v0.2.0` with the changes,
but never attach one to `v0` itself.

The workflow refuses a tag that is not on `main`. It leaves `v0` where it is for a release to an
older minor version (`v0.1.1` after `v0.2.0`), and for pre-release tags such as `v0.2.0-rc.1`,
which do not start it.

## Breaking changes

Removing or renaming an input or output, or changing a default so that results change, needs a new
major version. Tag `v1.0.0`; the workflow creates `v1` and leaves `v0` alone, so existing users are
not broken. Update the README examples to the new major version.
