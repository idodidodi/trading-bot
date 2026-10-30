# Repository agent instructions

## Required verification after every GitHub push

A successful push is not task completion. The agent that pushes owns verification:

1. Record the pushed commit SHA and branch. Find all GitHub Actions runs and deployment checks for that exact SHA (including external providers such as Cloudflare). Allow time for checks to register; no checks is not evidence of success.
2. Wait for every applicable build, test, and deployment check to reach a terminal state. Inspect failed job logs, fix the cause, run relevant tests, push the repair, and repeat verification against the new SHA. Never rely on an older green run or report queued/running checks as passed.
3. For an actual deployment, verify the destination after publication: check its health and version/manifest against the expected source and exercise the affected behavior with a safe smoke test. A green build alone does not prove production is updated. For standalone artifacts, require both Linux and Windows builds and their frozen-app smoke tests.
4. Report the verified SHA, run/deployment links, outcome, and material limits. If access or an external failure prevents verification, explicitly report the deployment as unverified or failed; do not claim completion.

Useful commands: `gh run list --commit "$SHA"`, `gh run view RUN_ID`, `gh run view RUN_ID --log-failed`, and `gh run watch RUN_ID --exit-status`. Also inspect commit check runs/statuses and any external deployment provider.

## Production boundaries

Legacy executable publication is deliberately disabled in `.github/workflows/standalone-release.yml`. Do not enable it to make a skipped publish job green. Production source deployment and the Postgres cutover follow `POSTGRES_MIGRATION.md`. Never start another scanner against production or send test alerts/orders while checking a deployment. Use a disposable database for tests.
