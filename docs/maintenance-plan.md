# Amazon Photos fork maintenance

Started 2026-11-27 from fork main `7cf3f5e` on `codex/amazon-photos-maintenance`. Upstream main was `685c965`. The fork was eight commits ahead and zero behind, with UK Drive routing and the commits from upstream PRs #16–19. The integration branch targets the fork’s main branch for review.

## Integration sequence

1. Establish the baseline and review the complete public upstream PR inventory, issues and alternative clients.
2. Preserve regional overrides, make authentication failure actionable, and test request construction and credential handling.
3. Make upload success verifiable and retry behavior conservative. Preserve the existing CDProxy contract and expose alternate request formats only as explicit choices supported by source evidence.
4. Fix bounded pagination, folder traversal, cache writes, streaming downloads, and propagation of failures from the remaining public API.
5. Verify package installation, supported Python versions and mocked tests. Provide a separate, opt-in UK account check and a longitudinal protocol.

## Gate for unattended operation

Browser session reuse is the working model. Sustained UK upload success, lifetime, and Echo Show display require account/device verification. Public reports of renewal or expiry are recorded with their scope in the comparative and authentication audits.

## Progress and verification

| Phase | Result |
| --- | --- |
| 1. Baseline and upstream | Configured the local `upstream` remote and fetched all 16 PR heads. Verified the fork's public default branch and preserved it as the branch base. A Python 3.12 baseline install succeeded, discovered zero tests, and reproduced the missing Parquet engine despite a passing `pip check`. [PR inventory](upstream-audit.md) |
| 2. Alternative implementations | Reviewed source snapshots of the independent Go, Python and Rust clients, the MCP server and recent forks. The independent Go project's offline `go test ./...` passed. It independently corroborates raw CDProxy routing and transfer checks, with its UK route still marked experimental. [Comparative audit](alternative-implementations.md) |
| 3–4. Consolidation and code review | Consolidated regional and cookie fixes with the fork's original UK override. Implemented checked session and HTTP handling, bounded retries, streaming uploads, remote deduplication, incomplete-download handling, pagination, safe worker cleanup and atomic cache writes. [Code audit](code-audit.md) |
| 5. Authentication | Traced cookie acquisition and cross-process reuse. A read-only UK probe recovered a new `session-id` from `GET /photos` using valid `at-acbuk` and `ubid-acbuk`. The new short session could read the account root. Removing `at-acbuk` yielded a 401 on the root check, and `/photos/auth/token` returned 404 with valid cookies. The web bundle shows a sign-in action on 401 and uses a separate analytics session timer. Renewal of the main UK credential and uploads using a reconstructed short session remain unverified. [Authentication](authentication.md) |
| 6. Automated tests | 30 credential-free tests pass with warnings treated as errors on macOS CPython 3.11, 3.12, 3.13 and 3.14. The suite also passes on 3.11 with Pandas 2.2.0, PyArrow 16.0.0, HTTPX 0.27.0, aiofiles 23.2.1, orjson 3.9.0, tqdm 4.66.0 and NumPy 1.26.4. Ruff and `git diff --check` pass. GitHub Actions is configured for the four versions on Linux. |
| 7. Live API | An opt-in UK account run on 2026-11-27 passed root authentication, photo listing and reuse after restart. One synthetic PNG uploaded through EU CDProxy, matched its remote MD5, was skipped as a duplicate by a fresh client, and downloaded with the same checksum. No known-expired capture was supplied. V2 upload, session lifetime and Echo Show display remain unverified. [Live result](authentication.md#uk-live-result-2026-11-27) |
| 8. Packaging and documentation | PEP 517 build, source distribution, wheel install, import outside the checkout and `pip check` succeeded. Added a private-cookie-file upload example, live test instructions and migration details. |

Python 3.11 is the minimum because CPython 3.10 reached upstream end of life in October 2026. The integration package version is `0.0.97.post1`.

## Open validation and trade-offs

1. Check whether the synthetic PNG left by the live UK run appears on the configured Echo Show.
2. Run the session longevity protocol on the deployment host. Decide the acceptable reauthentication interval from the results. Browser sign-in remains the recoverable path when credentials expire.
3. Verify the reported Japan and other marketplace routes as accounts are available. UK CDProxy remains the default. V2 is an explicit alternative for a separate trial.
4. Reconcile uncertain writes by checksum before replay. Concurrent writers and eventual indexing mean that remote exactly-once delivery cannot be guaranteed. Keep one Parquet writer per snapshot.
5. Track live endpoint and cookie behavior using sanitized evidence. Never attach cookie files, private media, signed URLs or raw HTTP captures.

The package remains unpublished. The repository maintenance excludes downstream service implementation.
