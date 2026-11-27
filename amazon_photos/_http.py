"""HTTP errors, regional settings and retry policy for the Photos client."""

import random
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx


class AmazonPhotosError(Exception):
    """Base class for failures reported by Amazon Photos."""


class AuthenticationError(AmazonPhotosError, PermissionError):
    """Amazon rejected the session or requested browser sign-in."""


class APIError(AmazonPhotosError):
    """The Amazon Photos endpoint rejected a request."""

    def __init__(self, status_code: int, method: str, retry_after: str | None = None):
        self.status_code = status_code
        self.method = method
        self.retry_after = retry_after
        super().__init__(f"Amazon Photos rejected {method} (HTTP {status_code}).")


class TransportError(AmazonPhotosError):
    """An HTTP connection or transfer failed."""

    def __init__(self, error: httpx.RequestError):
        self.before_request = isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
        super().__init__(f"Amazon Photos HTTP transfer failed ({type(error).__name__}).")


class ProtocolError(AmazonPhotosError):
    """The endpoint returned an unexpected response."""


class UploadUncertainError(AmazonPhotosError):
    """Amazon might have committed the upload before its response failed."""


def cookie_marketplace(name: str) -> str | None:
    match = re.fullmatch(r"(?:at|ubid)[-_](main|acb([a-z]{2}))", name)
    if match:
        return match[2] or "com"
    return None


def normalize_tld(tld: str) -> str:
    tld = tld.lower().strip().removeprefix("www.").removeprefix("amazon.")
    tld = {"us": "com", "co.uk": "uk", "co.jp": "jp", "com.br": "br", "com.au": "au",
           "com.mx": "mx", "com.be": "be"}.get(tld, tld)
    if not re.fullmatch(r"com|[a-z]{2}", tld):
        raise ValueError("Use an Amazon marketplace suffix such as co.uk, com or de.")
    return tld


def check_response(response: httpx.Response) -> None:
    request = response.request
    status = response.status_code
    location = response.headers.get("location")
    if location and response.is_redirect:
        path = request.url.join(location).path.lower()
        if path.startswith(("/ap/signin", "/ap/cvf", "/ap/mfa", "/errors/validatecaptcha")):
            raise AuthenticationError(
                "Amazon requires browser sign-in or verification. Complete it in the browser and load fresh cookies."
            )
    trusted = not request.extensions.get("photos_untrusted")
    html = "text/html" in response.headers.get("content-type", "").lower()
    if trusted and response.is_stream_consumed and (status == 403 or (response.is_success and html)):
        body = response.content[:65536].lower()
        if any(marker in body for marker in (
            b"badauthenticationdata", b"no auth method provided", b"ap_signin_form", b"validatecaptcha",
        )):
            raise AuthenticationError(
                "Amazon requires browser sign-in or verification. Complete it in the browser and load fresh cookies."
            )
    if status == 401 and trusted:
        raise AuthenticationError(
            "Amazon rejected the session (HTTP 401). Sign in to Amazon Photos and load fresh marketplace cookies."
        )
    if status == 409 and request.extensions.get("photos_allow_conflict"):
        return
    if response.is_redirect and request.extensions.get("photos_download"):
        if location and request.url.join(location).scheme == "https":
            return
    if not response.is_success:
        raise APIError(status, request.method, response.headers.get("retry-after"))
    if html and not request.extensions.get("photos_download"):
        raise ProtocolError("Expected an Amazon Photos API response. Check the endpoint and browser session.")


def retry_delay(error: APIError | httpx.RequestError, attempt: int, cap: float, base: float) -> float:
    delay = min(random.random() * base ** attempt, cap)
    if not isinstance(error, APIError) or not error.retry_after:
        return delay
    try:
        server_delay = float(error.retry_after)
    except ValueError:
        try:
            expires = parsedate_to_datetime(error.retry_after)
            server_delay = (expires - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return delay
    # If the server asks for longer than the budget, surface the rate limit.
    if server_delay > cap:
        raise error
    return max(delay, server_delay)


def is_retryable(error: APIError | httpx.RequestError, read: bool) -> bool:
    if isinstance(error, APIError):
        return error.status_code == 429 or (read and error.status_code in {408, 500, 502, 503, 504})
    if isinstance(error, (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout)):
        return True
    return read and isinstance(error, (httpx.NetworkError, httpx.TimeoutException, httpx.RemoteProtocolError))


def response_json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        raise ProtocolError("Expected Amazon Photos JSON. Check the endpoint and browser session.") from None
    if not isinstance(payload, dict):
        raise ProtocolError("Expected an Amazon Photos JSON object.")
    return payload
