"""
One HTTP session to rule them all.

Key trick for Indian government/exchange sites: they reject requests that don't
look like a browser, and NSE additionally requires you to "prime" cookies by
hitting the homepage first. This module handles both.
"""
import time
import random
import httpx

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# Worth another attempt: rate-limit and transient server faults. Everything else
# in the 4xx range is a permanent answer.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class Fetcher:
    def __init__(self, base_headers: dict | None = None, cookies: dict | None = None,
                 timeout: float = 30.0, min_delay: float = 0.8, max_retries: int = 4):
        self.client = httpx.Client(
            headers={**BROWSER_HEADERS, **(base_headers or {})},
            cookies=cookies,
            timeout=timeout,
            follow_redirects=True,
            http2=True,
        )
        self.min_delay = min_delay
        self.max_retries = max_retries
        self._last = 0.0

    def _throttle(self):
        """
        Be a good citizen. Also: hammering gets you IP-banned within a day.

        Jitter is proportional to min_delay, not a flat 0.3s. A flat value more
        than doubled SEC's intended 0.12s pace (0.12 -> ~0.27s average) while
        adding nothing: SEC publishes a rate limit and does not ban on pattern.
        NSE, on a 0.8s delay, still gets meaningful jitter.
        """
        gap = time.time() - self._last
        wait = self.min_delay - gap
        if wait > 0:
            time.sleep(wait + random.uniform(0, self.min_delay * 0.25))
        self._last = time.time()

    def get(self, url, **kw) -> httpx.Response:
        last_err = None
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                r = self.client.get(url, **kw)
                if r.status_code in RETRYABLE_STATUS:
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError as e:
                # 404/403/400 mean "this will never work" -- retrying wastes the
                # rate-limit budget (NSE) and turns speculative probes into
                # minutes of backoff. Only 429/5xx are worth another attempt.
                if e.response is not None and e.response.status_code not in RETRYABLE_STATUS:
                    raise
                last_err = e
                time.sleep((2 ** attempt) + random.uniform(0, 1))
            except Exception as e:  # network/timeout -- genuinely transient
                last_err = e
                time.sleep((2 ** attempt) + random.uniform(0, 1))
        raise RuntimeError(f"GET failed after {self.max_retries} tries: {url}") from last_err

    def post(self, url, **kw) -> httpx.Response:
        self._throttle()
        r = self.client.post(url, **kw)
        r.raise_for_status()
        return r


def nse_session() -> Fetcher:
    """
    NSE blocks bare requests. You must load the homepage first so the WAF hands
    you cookies, then reuse that session for the JSON endpoints.
    """
    f = Fetcher(base_headers={"Referer": "https://www.nseindia.com/"})
    f.client.get("https://www.nseindia.com", timeout=30)
    time.sleep(1)
    f.client.get("https://www.nseindia.com/market-data/live-equity-market", timeout=30)
    return f


def bse_session() -> Fetcher:
    """BSE's API host only needs a matching Referer + Origin."""
    return Fetcher(base_headers={
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
    })


def sec_session(contact_email: str) -> Fetcher:
    """
    SEC REQUIRES a User-Agent identifying you with a contact email.
    Without it you get 403. Rate limit is 10 requests/second -- respect it.
    """
    return Fetcher(
        base_headers={"User-Agent": f"HedgeFundResearch/1.0 ({contact_email})"},
        min_delay=0.12,
    )


def crisil_session() -> Fetcher:
    """
    CRISIL's rating-rationale JSON endpoint (ratingresultlisting.results.json)
    needs no cookie priming -- verified stateless against a bare client, unlike
    NSE. A Referer matching the real search page is sent anyway since that is
    what a browser actually sends and costs nothing.
    """
    return Fetcher(base_headers={
        "Referer": "https://www.crisilratings.com/en/home/our-business/ratings/rating-rationale.html",
        "Accept": "application/json",
    })


def icra_session() -> Fetcher:
    """
    icra.in's listing endpoint is a session-based POST, not a stateless GET
    like CRISIL's: it needs an ASP.NET anti-forgery token read off the search
    page's own HTML (see connectors/credit_ratings.py's _icra_token()) and
    sent back on every POST. The cookie jar (ASP.NET_SessionId etc.) is
    handled automatically as long as this same Fetcher's client is reused
    across the priming GET and the POSTs that follow.
    """
    return Fetcher(base_headers={"Referer": "https://www.icra.in/Rating/AllRatingRationales"})


def care_session() -> Fetcher:
    """CARE's rrcompany endpoint (careratings.com) needs no cookie priming -- verified
    stateless against a bare client, same as CRISIL."""
    return Fetcher(base_headers={
        "Referer": "https://www.careratings.com/find-ratings",
        "Accept": "application/json",
    })


def screener_session() -> Fetcher:
    """
    Logged-in screener.in session -- a deliberate, scoped exception to
    CLAUDE.md's no-third-party-vendors rule (see connectors/screener.py's
    module docstring for why). Needs SCREENER_CSRFTOKEN/SCREENER_SESSIONID
    in .env, captured from a real logged-in browser session (DevTools >
    Network > Copy as cURL on an "Export to Excel" click) -- these are
    session cookies, not an API key, and expire; when they do, every request
    302s to /login/ instead of erroring, so check for that specifically.
    """
    from core.config import require
    return Fetcher(
        base_headers={"Referer": "https://www.screener.in/"},
        cookies={
            "csrftoken": require("SCREENER_CSRFTOKEN"),
            "sessionid": require("SCREENER_SESSIONID"),
        },
    )


_SEC_SESSIONS: dict[str, Fetcher] = {}


def cached_sec_session(contact_email: str) -> Fetcher:
    """
    One long-lived SEC session per contact address.

    Building a Fetcher per request throws away the connection pool, so every
    fetch paid a fresh TCP + TLS handshake -- measured at ~0.95 files/sec
    against SEC's 10/sec ceiling when walking 2,786 filings. Connection reuse is
    the reason this project uses httpx rather than requests; a per-call session
    defeats it entirely.

    Use this for any loop over many filings. sec_session() remains available
    when a caller genuinely wants an isolated session.
    """
    if contact_email not in _SEC_SESSIONS:
        _SEC_SESSIONS[contact_email] = sec_session(contact_email)
    return _SEC_SESSIONS[contact_email]
