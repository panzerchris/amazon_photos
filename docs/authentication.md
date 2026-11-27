# Authentication and unattended uploads

## What the sources establish

- Read and upload implementations reuse an authenticated Amazon Photos browser session. Start by signing in at the account's marketplace and completing ordinary MFA or verification. Use the cookies sent on a successful request to the same marketplace. The common UK set is `session-id`, `ubid-acbuk` and `at-acbuk`. The client sends `session-id` both in the cookie header and as `x-amzn-sessionid`. Go and the independent Immich tool use these three cookie types, while Rust includes `x-acb*` and the German archiver reports a role for `session-token`. No controlled UK test has isolated the minimum cookie set. [Go regional definitions](https://github.com/thedavidweng/amazon-photos/blob/b33d1089cdbcc0e3cd4770f8de6da3ec3d89cbbf/pkg/aphotos/region.go), [German rotation observations](https://github.com/CarstenRI/webcam-archiver-multi-storage/blob/fea880d9ffe3f4e59f0493444628d99b02db60b7/app/amazon_session.py)
- The browser is needed to obtain or replace credentials. Several projects make HTTP requests in a separate process using a fixed browser user agent, which supports the feasibility of using copied cookies while the session is valid. UK read access has been reported with one particular browser header profile. There is no proof that device, user agent, network, account security state or browser logout are irrelevant. The library keeps a stable user agent and allows a caller to supply their browser's user agent. [UK report](https://github.com/Exloki/amazon_photos/blob/78e626bb6e9b131cc086911d94aea75d2536e4a5/SAFETY.md#verification), [Immich implementation](https://github.com/vanessa/immich-amazon-sync/blob/6d95d8cfed1f906606df24c7bc7d6b5eadee865d/src/immich_sync.py)
- The reviewed CDProxy clients do not obtain a separate CSRF token. The UK inventory can replay an existing same-marketplace browser header profile, and the German browser's alternative v2 upload was reported to include an access-token header. These observations do not establish that UK uploads require either kind of token. This client accepts optional browser Origin, Referer and CSRF headers in `request_headers`. Capture them only from your authenticated, same-marketplace request if needed. They carry session material and should be kept private. The library neither derives tokens nor guesses their names or values. [Exloki request validation](https://github.com/Exloki/amazon_photos/blob/78e626bb6e9b131cc086911d94aea75d2536e4a5/amazon_photos/_api.py), [German upload discussion](https://github.com/trevorhobenshield/amazon_photos/issues/10#issuecomment-1890381964)

## What can renew

- **Short-lived browser cookies.** The German webcam archiver reports collecting `Set-Cookie` rotations of `session-id` and `session-token` during ordinary Photos requests while the user still has a valid sign-in. Its author treats loss of the underlying `at-acbde` credential as requiring a fresh login. This is one downstream project's German-marketplace evidence. The controlled UK probe below confirms short-session recovery for reads on one UK account. [German rotation implementation](https://github.com/CarstenRI/webcam-archiver-multi-storage/blob/fea880d9ffe3f4e59f0493444628d99b02db60b7/app/amazon_session.py)
- **Amazon's first-party app credentials.** A 2024 study observed refresh tokens in Amazon's Photos and Alexa companion apps, and documented app access to EU Photos metadata using an access-token header. The study covers app-issued credentials and read endpoints. It leaves UK browser-cookie renewal, current upload access and a supported third-party grant unverified. [DFRWS Echo Show 15 study, §§5.1.2, 5.2.1 and 5.2.4](https://dfrws.org/wp-content/uploads/2024/07/dfrws-usa-2024-echo-show-15.pdf)
- **Public developer credentials and the private Photos token route.** Login with Amazon documents OAuth refresh and consumer profile scopes. No Photos upload scope appears in that documented grant. The upstream library's Canadian `/photos/auth/token` experiment failed to update its cookies, and its open refresh issue has no confirmed solution. Credential-free probes of the UK and Canadian token paths returned redirects or 503 responses. With a working UK session, the token route returned HTTP 404 and supplied no access or refresh token. [Login with Amazon grant and scopes](https://developer.amazon.com/docs/login-with-amazon/authorization-code-grant.html), [upstream token probe](https://github.com/trevorhobenshield/amazon_photos/blob/685c965b5a4ba1ac85d418820ad200e12c18a46d/amazon_photos/_api.py), [upstream refresh question](https://github.com/trevorhobenshield/amazon_photos/issues/21)

## Runtime behavior

Keep the cookie JSON outside the checkout under the uploader's private account. Give it mode `0600` on Unix or an equivalent user-only ACL. Provide the path through `AP_COOKIES_FILE` in the examples. A secret manager can also pass the mapping directly. Avoid browser automation that stores the account password, shell commands with secret values as arguments, HTTP debug traces, and sharing response snapshots or signed URLs.

The current process accepts ordinary server cookie updates in its HTTP jar according to their domain and path. The library does not write cookies to disk. Starting a fresh process with the same original file is expected to work only while Amazon accepts those credentials. To replace rejected credentials, open Amazon Photos in the browser and complete sign-in or verification if prompted. Export the current cookies, replace the secret file atomically, then create a new client or restart the uploader. A cookie expiry date from browser storage can give an upper bound for that browser cookie. Remote revocation, access policy and write authorization can change earlier.

`AuthenticationError` on 401, a recognized verification redirect, or a known auth response means the caller should pause uploads and alert the person who controls the Amazon account. A generic 403 is an `APIError`, since access denial can reflect permissions, policy or another requirement. A successful root/list check shows read access only. An `UploadUncertainError` means the bytes may already be stored. Retain the original and reconcile by verified checksum before retrying. All calls use TLS verification by default.

## Opt-in UK verification

Run from the installed checkout with a fresh browser capture.

```sh
export AP_COOKIES_FILE=/path/to/private/uk.cookies.json
export AP_TLD=co.uk
AP_LIVE=1 python -m unittest discover -s tests/live -v
AP_LIVE=1 AP_LIVE_UPLOAD=1 python -m unittest discover -s tests/live -v
```

The read run checks root authentication, a small photo listing and reuse in a fresh Python process. The write run additionally adds one uniquely generated 64×64 PNG to the root, waits for its node and checksum to become visible, and attempts to upload the same bytes through a fresh client. It expects one remote node and no second upload receipt. The PNG remains in the account for inspection. The suite never trashes, purges or changes existing photos. Set `AP_EXPIRED_COOKIES_FILE` to a separate known-expired capture if available. It performs one read attempt and expects `AuthenticationError`. Set `AP_USER_AGENT` to the browser UA for a comparison if useful.

Earlier credential-free checks on 2026-11-27 returned 401 for the UK Drive root GET, 404 for an EU CDProxy HEAD, and 405 for a UK v2 HEAD. These show the hosts answered those requests. Upload compatibility depends on an authenticated POST.

The first run uses EU CDProxy. With `AP_UPLOAD_API=v2`, a separate explicitly enabled write run uses `https://upload-photos.amazon.co.uk/v2/upload`. Use the printed synthetic filename to reconcile the first attempt before changing protocols after a timeout or ambiguous error. The default EU CDProxy format passed the UK test below. V2 remains a source-backed alternative that has not been exercised with this account.

For Echo Show, choose the relevant Amazon Photos source in the device's Photo Display settings, then check whether the named image appears in Amazon Photos web and subsequently on the device without changing its selected source. If the device uses a selected album, root visibility and album membership are separate. Add the synthetic image to that selected album via your normal UI if testing album display specifically. Record marketplace, device model, configured source, elapsed time and whether intervention was needed. [Amazon UK Photo Display setup](https://digprjsurvey.amazon.co.uk/csad/help/node/GX89AZF5HTVZCPT7)

## UK live result, 2026-11-27

On one UK account, fresh browser cookies authenticated the root and supported a photo listing. A fresh Python process reused the same private cookie file. Using the default EU CDProxy endpoint, the test uploaded one generated 64×64 PNG and verified its remote node and MD5. A second upload call with a fresh client skipped the matching bytes, and a subsequent lookup returned exactly one matching node. A separate check resolved the same image by exact filename, downloaded only that node, and independently matched the downloaded bytes against the remote MD5. The remote test image was left in the account for manual display verification.

The known-expired session test was skipped because no expired capture was supplied. The run establishes reuse immediately across a restart. Session longevity, automatic renewal, and appearance on an Echo Show remain open.

## UK cookie recovery probe, 2026-11-27

The probe used the same account as the live upload check. All requests were read-only, took place in disposable in-memory cookie jars, and left the saved cookie file intact.

| Request and starting state | Observed result |
| --- | --- |
| `GET https://www.amazon.co.uk/photos` with the working cookie capture | HTTP 200. Amazon sent `session-id`, `session-token` and `x-acbuk`. The `session-id` value matched the original capture. Amazon did not issue a new `at-acbuk`. |
| `GET https://www.amazon.co.uk/photos/auth/token` with working cookies | HTTP 404. No access token or refresh token was returned. |
| `GET https://www.amazon.co.uk/photos` with valid `at-acbuk` and `ubid-acbuk`, with no initial `session-id` | HTTP 200 and a new `session-id`. Using that new session ID in the cookie and the `x-amzn-sessionid` header allowed `GET /drive/v1/nodes?filters=isRoot:true` to read the account root. |
| `GET https://www.amazon.co.uk/photos` after removing `at-acbuk` from that refreshed jar | HTTP 302. Amazon sent short session cookies and did not issue a new `at-acbuk`. The subsequent root read returned HTTP 401. |

This verifies one way to reconstruct the short session for UK reads while the main authorization cookie is still valid. Uploads were not exercised with the reconstructed session. No naturally expired credential was used, so the probe does not establish whether a revoked or expired `at-acbuk` can be renewed. Treat that as a separate question from short-session recovery and measure it with the longitudinal protocol.

## UK web client inspection, 2026-11-27

The signed-in UK Photos page loaded 16 public JavaScript bundles. The account cookies were not sent to the bundle host. The UK bootstrap identifies `/ap/signin` as the authentication portal. The Drive request adapter uses the bootstrap's session ID and reports a 401 as unauthorized. In the normal web view the user store marks the session unauthorized and the dialog offers a sign-in action. The visible handler does not perform a token exchange. [Drive request adapter](https://dcupkcmoyuvm5.cloudfront.net/clouddrive-flux-adapters-00eb31ab03dd3bfd3725.js), [user store](https://dcupkcmoyuvm5.cloudfront.net/stores-3c3f5fccf6fb7527a252.js), [sign-in dialog](https://dcupkcmoyuvm5.cloudfront.net/dialogs-27609f645f4272803094.js)

References with similar names have separate purposes. The vendor bundle's `renewSession` updates the `cwr_s` web-monitoring session cookie. The app bundle's `refreshToken` hit is in mock cloud-to-cloud transfer code. The storage preference called `setAutoRenew` includes a subscription ID and controls a storage subscription. None demonstrates a renewal flow for `at-acbuk`. [Vendor bundle](https://dcupkcmoyuvm5.cloudfront.net/vendors-9caefaaec8b588347a66.js), [app bundle](https://dcupkcmoyuvm5.cloudfront.net/app-471a2fc5b147f0058fc8.js), [storage adapter](https://dcupkcmoyuvm5.cloudfront.net/clouddrive-flux-adapters-00eb31ab03dd3bfd3725.js)

The selected bundles expose the browser's routes and response handling. Server middleware and code loaded only in other page states remain outside this inspection. During the review a direct root probe briefly returned HTTP 401. The unchanged private cookie file then passed the library root request and both direct variants. One 401 did not establish expiry; the user-facing client still treats a 401 as an authentication failure and lets the caller pause to investigate.

## Measuring session lifetime

For two to four weeks, or until rejection, run a low-frequency read probe from the intended deployment host with the same captured file and fixed user agent. Run a separately authorized synthetic write at a slower interval, because a successful read may outlast successful upload access. Record UTC time, capture age, whether the process is new, endpoint and status category, retry count, whether cookie names rotated, and remote checksum verification. Never store cookie values, user identifiers, response bodies or CDN URLs in the measurements.

Compare a process retaining its in-memory jar with fresh processes reusing the original capture, ideally one cohort at a time to avoid one probe keeping the other's session active. Keep browser use, logout, account changes and deployment-network changes in the log. Report the interval between the last verified success and the first confirmed authentication rejection. A network failure or 429 is an inconclusive sample. Measure before deciding whether occasional attended reauthentication meets the deployment's reliability target.

When a saved UK capture first fails, record its first failure and check whether a later low-frequency read still fails without changing the cookie file. Then check Amazon Photos in the original browser profile. If the browser still works without prompting, take a fresh capture from that profile and repeat the UK read and synthetic write checks. Record whether the fresh capture restored access and whether the browser required each sign-in or verification step. This distinguishes a stale exported copy from a browser profile that also needs reauthentication.

Long-running unattended use is feasible while Amazon accepts the captured session. The UK live run establishes the upload and duplicate path for one fresh session. It does not establish how often a deployed client will need manual reauthentication.
