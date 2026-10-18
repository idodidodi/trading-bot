# Executable build and release rules

Windows and Linux executables contain the Python backend and scanner, the local browser UI in `web/`, and bundled configuration and strategy files. The Linux background process checks the latest stable GitHub release daily at 05:00 Israel time; Windows checks at startup and hourly. **Check for app update** requests an immediate background check on either platform. No browser page needs to be open for automatic updates. Updates compare numeric `X.Y.Z` versions, not commit hashes. Settings also shows code fingerprints for source comparisons; a differing commit alone does not install an update.

## Automatic releases

`standalone-release.yml` coordinates the reusable Linux and Windows build workflows. Relevant pushes to `main` automatically allocate a version, test and smoke-test both executables, verify their checksums and manifests, upload all four assets to a draft release, and only then publish it as latest. If either build or verification fails, no new stable release is published. Releases are serialized so an older build cannot publish after a newer one.

The build version advances numerically beyond existing stable-version tags. For example, when the latest tag is `v1.0.6`, the next successful main build publishes `v1.0.7`. `app_version.py` is the source version floor; increase it deliberately for a minor or major release. Actions writes the allocated version into each temporary build checkout so both binaries, manifests and bundled version metadata agree. It does not make version-bump commits or require an additional GitHub token. Source checkouts retain their declared base version; Settings compares code fingerprints separately from executable versions.

| Changed files | Effect |
| --- | --- |
| Root-level `*.py`, including tests | Rebuild scanner, backend, launchers and updater |
| `web/**` | Rebuild local dashboard; shared hosted UI changes also affect the executable |
| `skills/**` | Rebuild bundled strategy instructions |
| `scanner.json`, `stock_universe.json`, `tradingview_template.pine`, `.env.example` | Rebuild bundled defaults/templates |
| Root-level `*requirements*.txt`, `pyproject.toml`, `poetry.lock`, `uv.lock` | Rebuild dependencies |
| `scripts/build-*.sh`, `scripts/build-*.ps1`, `scripts/*-manifest.py`, `scripts/release-version.py`, `scripts/smoke-*.py` | Rebuild packaging and verification |
| `.github/workflows/**` | Rebuild with updated automation |

Documentation-only and `cloud/`-only branch pushes do not trigger executable builds. Feature-branch pushes build downloadable Actions artifacts for 14 days and never publish installed-app updates. Linux artifact downloads may need `chmod +x` before running. Only `main` and explicit stable-version tags publish releases.

## Manual releases and retries

Run **Build and publish standalone update** on `main` to build and publish a new patch release without another code push. A manual run on a feature branch builds artifacts only. A pushed `vX.Y.Z` tag uses that explicit version, which must not be below the source version floor. Rerun a failed workflow to retry; an automatic main retry may allocate a later patch version if other release tags appeared in the meantime. Do not move published tags or replace published executable contents with different code.

GitHub Actions performs the platform builds; source commits alone are not installable packages. Users can select **Check for app update** once the new release is published, or wait for the hourly check. Downloaded binaries are verified against their manifests before graceful replacement. Existing credentials and data stay outside the executable.

## Changelog and local credentials

Publication uses `CHANGELOG.md` as release notes. Keep the upcoming entry current before shipping. `web/update-notes.html` ships offline inside both executables and is linked from Settings; its pending label is replaced with the allocated executable version during packaging. Secrets remain local: builds include the empty `.env.example`, never `.env`, `.env.prod`, local SQLite databases or downloaded histories. Updates preserve credentials files. A new provider key requires a local edit and restart.
