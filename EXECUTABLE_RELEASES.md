# Executable build and release rules

Both Windows and Linux executables contain the Python backend and scanner, the local browser UI in `web/`, and bundled configuration and strategy files. Changes to these files require rebuilding both executables.

## Automatic builds

The Windows and Linux workflows run on pushes to any branch when at least one changed file matches these paths:

| Changed files | Reason |
| --- | --- |
| Root-level `*.py` files, including tests | Scanner, backend, launchers, updaters, and verification |
| `web/**` | Bundled local dashboard, styles, scripts, and chart assets |
| `skills/**` | Bundled strategy instructions |
| `scanner.json`, `stock_universe.json`, `tradingview_template.pine`, `.env.example` | Bundled default configuration and templates |
| Root-level `*requirements*.txt`, `pyproject.toml`, `poetry.lock`, `uv.lock` | Runtime and build dependencies |
| `scripts/build-*.sh`, `scripts/build-*.ps1`, `scripts/*-manifest.py`, `scripts/smoke-*.py` | Packaging, update manifests, and executable verification |
| `.github/workflows/**` | Build automation itself |

Documentation-only changes and changes confined to `cloud/` do not trigger executable builds. A hosted UI change in the shared `web/` directory does trigger builds because that directory is also bundled locally. If a new bundled input is added to a build script, add it to both workflow path filters.

Each workflow runs the Python tests, builds its executable, smoke-tests it, and saves the executable and update manifest as a GitHub Actions artifact for 14 days. Failed tests or builds prevent successful artifacts and release publication. Linux executables downloaded from an artifact may need `chmod +x` before running.

Manual workflow runs build the selected ref regardless of path filters. All pushed tags beginning with `v` also trigger builds regardless of changed paths. Documentation-only branch pushes skip the workflows entirely.

## Releases delivered to installed apps

Branch builds do not publish GitHub release assets or update installed applications. To ship an executable update:

1. Bump `app_version.VERSION` before tagging. Use a patch version for bug fixes and compatible UI changes, a minor version for backward-compatible features, and a major version for breaking behavior or configuration changes.
2. Commit and push the intended release code.
3. Push the matching `vX.Y.Z` tag. The workflows reject a tag that does not match `app_version.VERSION`.
4. Confirm both platform workflows succeed and both executables and update manifests appear on that GitHub release.

Only builds whose ref starts with `refs/tags/v` publish release assets. A manual run on a version tag can retry publication; a manual run on a branch only produces an artifact. Installed applications discover newer stable releases through their existing update checks. Use a new version for changed executable content so existing installations can recognize the update.

## Changelog and local credential instructions

Release publication uses `CHANGELOG.md` as its release notes. Keep the upcoming entry current before tagging. The offline guide in `web/update-notes.html` ships inside both executables and is linked from Settings. Update its pending-release label when publishing the corresponding version. Keep credentials as placeholders or empty examples only; builds include `.env.example`, never `.env`, `.env.prod`, local SQLite databases or downloaded histories. Existing credentials files are preserved by the launchers and binary updaters; adding a new provider key requires a local edit and restart.
