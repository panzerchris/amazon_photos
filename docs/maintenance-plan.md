# Amazon Photos fork maintenance

Started 2026-11-27 from fork main `7cf3f5e` on `codex/amazon-photos-maintenance`. Upstream main was `685c965`. The fork was eight commits ahead and zero behind, with UK Drive routing and the commits from upstream PRs #16–19. The work stays on the local integration branch.

## Integration sequence

1. Establish the baseline and review the complete public upstream PR inventory, issues and alternative clients.
2. Preserve regional overrides, make authentication failure actionable, and test request construction and credential handling.
3. Make upload success verifiable and retry behavior conservative. Preserve the existing CDProxy contract and expose alternate request formats only as explicit choices supported by source evidence.
4. Fix bounded pagination, folder traversal, cache writes, streaming downloads, and propagation of failures from the remaining public API.
5. Verify package installation, supported Python versions and mocked tests. Provide a separate, opt-in UK account check and a longitudinal protocol.

## Gate for unattended operation

Browser session reuse is the working model. Sustained UK upload success, lifetime, and Echo Show display require account/device verification. Public reports of renewal or expiry are recorded with their scope in the comparative and authentication audits.

## Progress and decisions

| Phase | Result |
| --- | --- |
| 1. Baseline and upstream | Configured `upstream`, compared default branches, and retrieved all 16 public PR heads and their discussions or public conversation pages. On an isolated Python 3.12 install, the baseline imported and `pip check` passed. Unittest discovered zero tests. A direct Parquet write reproduced the missing-engine failure. |
| 2. Other implementations | Compared independent Go, Rust and Python clients, the MCP server, and active downstream forks and applications. The Go tests passed, but its authors mark UK routing experimental. Keep the Python fork and prioritize validation from the intended account and deployment host. |
| 3. Integration | Kept every fork commit and its UK fix. Consolidated overlapping auth and cancellation proposals, added Japan routing, and integrated the PyArrow, aggregation, list deduplication and favorite fixes. Left the FastAPI web UI outside the library. |
| 4. Correctness | Reviewed all public HTTP methods. Added checked responses, scoped sessions, conservative retries, bounded workers, atomic download/cache IO, verified conflict handling and corrected folder/list behavior. See [code audit](code-audit.md). |
| 5. Authentication | Browser-cookie reuse is feasible while accepted. We accept normal server cookie rotation in memory. No reviewed client demonstrated supported renewal for UK, and write access may end before read access. The longitudinal procedure and manual reauthentication steps are recorded in [authentication](authentication.md). |
| 6. Automated tests | 28 offline tests passed on CPython 3.11, 3.12, 3.13 and 3.14 on macOS, with Python warnings treated as errors. They also passed on Python 3.11 with Pandas 2.2.0, PyArrow 16.0.0, HTTPX 0.27.0, aiofiles 23.2.1, orjson 3.9.0 and tqdm 4.66.0. GitHub Actions is configured for the same Python matrix plus lint and builds. |
| 7. Live validation | The four opt-in tests skip without opt-in. Account auth, upload, duplicate behavior, credential age and Echo Show display remain unverified by this branch. The tests use no personal images and perform no remote deletion. |
| 8. Packaging and documentation | Added PEP 517/621 packaging and explicit dependencies. The wheel and source distribution build, the wheel installs and imports outside the checkout, and `pip check` passes. The README and single-file upload example document the new settings and exit codes. |

The supported range starts at Python 3.11 because CPython 3.10 reached upstream end of life in October 2026. The integration package version is `0.0.97.post1`. The EU CDProxy format remains the UK default and the marketplace v2 format is opt-in. No branch or package has been published.

## Remaining verification

1. Run the authorized UK read and synthetic write suites in [authentication](authentication.md).
2. Confirm the generated image in Amazon Photos web and on the configured Echo Show.
3. Measure both read and write session lifetime from the intended host before treating the pipeline as unattended. Confirm that an invalid session pauses the caller and preserves its local queue.
4. GitHub Actions will report the hosted checks after this branch is pushed for review. Current verification was local. UK upload and date/filter behavior, exact credential requirements across regions, and the private API's future availability remain open.

## Progress and verification

| Phase | Result |
| --- | --- |
| 1. Baseline and upstream | Configured the local `upstream` remote and fetched all 16 PR heads. Verified the fork's public default branch and preserved it as the branch base. A Python 3.12 baseline install succeeded, discovered zero tests, and reproduced the missing Parquet engine despite a passing `pip check`. [PR inventory](upstream-audit.md) |
| 2. Alternative implementations | Reviewed source snapshots of the independent Go, Python and Rust clients, the MCP server and recent forks. The independent Go project's offline `go test ./...` passed. It independently corroborates raw CDProxy routing and transfer checks, with its UK route still marked experimental. [Comparative audit](alternative-implementations.md) |
| 3–4. Consolidation and code review | Consolidated regional and cookie fixes with the fork's original UK override. Implemented checked session and HTTP handling, bounded retries, streaming uploads, remote deduplication, incomplete-download handling, pagination, safe worker cleanup and atomic cache writes. [Code audit](code-audit.md) |
| 5. Authentication | Traced cookie acquisition and cross-process reuse. No supported UK renewal or established lifetime was found. Documented manual rotation and a longitudinal protocol. [Authentication](authentication.md) |
| 6. Automated tests | 28 credential-free tests pass with warnings treated as errors on macOS CPython 3.11, 3.12, 3.13 and 3.14. The suite also passes on 3.11 with the declared dependency floors and a compatible NumPy 1.26.4. Ruff and `git diff --check` pass. GitHub Actions is configured for the four versions on Linux. |
| 7. Live API | Credential-free probes reached UK Drive (GET 401), EU CDProxy (HEAD 404) and UK v2 (HEAD 405). Those observations establish only HTTP reachability and expected protection of the metadata request. The four live checks discover and skip by default. Authenticated execution requires an opting-in account owner, and Echo Show display requires a configured device. |
| 8. Packaging and documentation | PEP 517 build, source distribution, wheel install, import outside the checkout and `pip check` succeeded. Added a private-cookie-file upload example, live test instructions and migration details. |

## Open validation and trade-offs

1. Run the opt-in UK read and write suite with a fresh browser session. Capture status classes and times, then verify the named synthetic PNG's checksum and Echo Show display. Leave the test artifact for manual inspection.
2. Run the session longevity protocol on the deployment host. Decide the acceptable reauthentication interval from the results. Browser sign-in remains the recoverable path when credentials expire.
3. Verify the reported Japan and other marketplace routes as accounts are available. UK CDProxy remains the default. V2 is an explicit alternative for a separate trial.
4. Reconcile uncertain writes by checksum before replay. Concurrent writers and eventual indexing mean that remote exactly-once delivery cannot be guaranteed. Keep one Parquet writer per snapshot.
5. Track live endpoint and cookie behavior using sanitized evidence. Never attach cookie files, private media, signed URLs or raw HTTP captures.

No changes have been pushed or published. The repository maintenance excludes downstream service implementation.
