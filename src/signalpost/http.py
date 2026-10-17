"""Polite, provenance-capturing HTTP client.

Every fetch records the requested URL, the final URL, the redirect chain, the HTTP
status, the retrieval time, the byte count and a SHA-256 of the body, because a
claim is only publishable if the retrieval behind it can be replayed and checked.

robots.txt is honoured for every non-API host. The official Brreg and Nav data
endpoints are allow-listed as documented open-data APIs.
"""
from __future__ import annotations

import gzip
import hashlib
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zlib
from dataclasses import dataclass, field
from typing import Final

from .envelope import utcnow

USER_AGENT: Final = (
    "SignalpostAgent/1.0 (+https://builderr.ai/signalpost; "
    "Norwegian company research; contact submit@builderr.ai)"
)
MAX_BYTES: Final = 2_000_000
ROBOTS_EXEMPT_HOSTS: Final = frozenset({
    "data.brreg.no", "pam-stilling-feed.nav.no", "api.ted.europa.eu",
})

# Measured politeness floors. arbeidsplassen.nav.no answers 429 under sustained
# load, so it gets a wide default and the runtime prefers the prebuilt index.
HOST_MIN_DELAY: Final[dict[str, float]] = {
    "arbeidsplassen.nav.no": 1.5,
    "pam-stilling-feed.nav.no": 0.3,
    "data.brreg.no": 0.15,
    "api.ted.europa.eu": 0.5,
}


@dataclass(slots=True)
class Response:
    """Outcome of exactly one HTTP attempt."""

    url: str
    status: int | None
    body: bytes = b""
    text: str = ""
    final_url: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    content_type: str = ""
    retrieved_at: str = field(default_factory=utcnow)
    elapsed_ms: int = 0
    error: str | None = None
    from_cache: bool = False
    retry_after: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.body)

    @property
    def content_sha256(self) -> str | None:
        return hashlib.sha256(self.body).hexdigest() if self.body else None

    @property
    def blocked(self) -> bool:
        """Distinguish 'the source refused' from 'there is nothing there'."""
        return self.status in {401, 403, 429} or self.error == "robots_disallowed"


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        self.chain: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        self.chain.append(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    """Shared HTTP client with per-host throttling and a robots cache."""

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        per_host_delay: float = 0.4,
        respect_robots: bool = True,
        max_bytes: int = MAX_BYTES,
        verify_tls: bool = False,
    ) -> None:
        self.timeout = timeout
        self.per_host_delay = per_host_delay
        self.respect_robots = respect_robots
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._last_hit: dict[str, float] = {}
        self._penalty: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.requests = 0
        self.bytes_downloaded = 0
        # Many small Norwegian sites have expired or mismatched certificates.
        # Content is verified by organisation number rather than by TLS identity,
        # so a certificate failure must not silently erase a real company.
        self._ctx = ssl.create_default_context()
        if not verify_tls:
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    # -- throttling -------------------------------------------------------
    def _throttle(self, host: str) -> None:
        """Serialise per host at the politeness floor, plus any 429 penalty."""
        with self._lock:
            now = time.monotonic()
            delay = max(self.per_host_delay, HOST_MIN_DELAY.get(host, 0.0),
                        self._penalty.get(host, 0.0))
            wait = delay - (now - self._last_hit.get(host, 0.0))
            self._last_hit[host] = now + max(wait, 0.0)
        if wait > 0:
            time.sleep(wait)

    def _note_rate_limit(self, host: str, retry_after: str | None) -> float:
        """Widen this host's floor after a 429 and report how long to wait now."""
        with self._lock:
            current = self._penalty.get(host, 0.0)
            self._penalty[host] = min(max(current * 2, 4.0), 60.0)
            penalty = self._penalty[host]
        try:
            if retry_after and retry_after.strip().isdigit():
                return min(float(retry_after.strip()), 60.0)
        except (TypeError, ValueError):
            pass
        return penalty

    def _note_success(self, host: str) -> None:
        with self._lock:
            if host in self._penalty:
                relaxed = self._penalty[host] / 2
                if relaxed < 0.5:
                    del self._penalty[host]
                else:
                    self._penalty[host] = relaxed

    # -- robots -----------------------------------------------------------
    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        host = urllib.parse.urlsplit(url).netloc.lower()
        if host in ROBOTS_EXEMPT_HOSTS:
            return True
        with self._lock:
            known = host in self._robots
            parser = self._robots.get(host)
        if not known:
            parser = self._load_robots(url, host)
        if parser is None:
            return True                       # no robots.txt means no restriction
        try:
            return parser.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    def _load_robots(self, url: str, host: str) -> urllib.robotparser.RobotFileParser | None:
        scheme = urllib.parse.urlsplit(url).scheme or "https"
        parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
        try:
            self._throttle(host)
            req = urllib.request.Request(
                f"{scheme}://{host}/robots.txt", headers={"User-Agent": USER_AGENT}
            )
            # Counted before the attempt: a robots.txt fetch that times out still
            # consumed a request, and the reported total must not understate it.
            self.requests += 1
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                raw = r.read(200_000).decode("utf-8", "replace")
            parser.parse(raw.splitlines())
        except Exception:
            parser = None                     # unreachable robots.txt is not a block
        with self._lock:
            self._robots[host] = parser
        return parser

    # -- fetching ---------------------------------------------------------
    RETRYABLE: Final = frozenset({"timeout", "connection_refused", "dns_failure"})

    def fetch(self, url: str, *, accept: str = "text/html,application/xhtml+xml,*/*",
              headers: dict[str, str] | None = None, method: str = "GET",
              data: bytes | None = None, retries: int = 1) -> Response:
        """Fetch with bounded retries on transient faults.

        A dropped result costs the whole official run, so a timeout is retried once
        with a short backoff. Permanent outcomes (404, 403, robots) are never retried.
        """
        attempt = 0
        while True:
            response = self._fetch_once(url, accept=accept, headers=headers,
                                        method=method, data=data)
            host = urllib.parse.urlsplit(url).netloc.lower()
            if response.status == 429:
                wait = self._note_rate_limit(host, response.retry_after)
                if attempt >= retries:
                    return response
                attempt += 1
                time.sleep(wait)
                continue
            if response.ok:
                self._note_success(host)
            transient = response.error in self.RETRYABLE or (response.status or 0) in {502, 503, 504}
            if not transient or attempt >= retries:
                return response
            attempt += 1
            time.sleep(0.6 * attempt)

    def _fetch_once(self, url: str, *, accept: str,
                    headers: dict[str, str] | None, method: str,
                    data: bytes | None) -> Response:
        started = time.monotonic()
        parts = urllib.parse.urlsplit(url)
        host = parts.netloc.lower()
        if parts.scheme not in {"http", "https"} or not host:
            return Response(url=url, status=None, error="unsupported_url")
        if not self.allowed(url):
            return Response(url=url, status=None, error="robots_disallowed",
                            elapsed_ms=int((time.monotonic() - started) * 1000))

        self._throttle(host)
        recorder = _RedirectRecorder()
        opener = urllib.request.build_opener(
            recorder, urllib.request.HTTPSHandler(context=self._ctx)
        )
        hdrs = {
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "nb-NO,no;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            **(headers or {}),
        }
        req = urllib.request.Request(url, headers=hdrs, method=method, data=data)
        try:
            with opener.open(req, timeout=self.timeout) as r:
                raw = r.read(self.max_bytes + 1)
                self.requests += 1
                self.bytes_downloaded += len(raw)
                body = _decode_body(raw[: self.max_bytes], r.headers.get("Content-Encoding", ""))
                ctype = r.headers.get("Content-Type", "")
                return Response(
                    url=url, status=r.status, body=body,
                    text=_to_text(body, ctype, r.headers.get_content_charset()),
                    final_url=r.url, redirect_chain=recorder.chain, content_type=ctype,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
        except urllib.error.HTTPError as e:
            self.requests += 1
            try:
                raw = e.read(65_536)
            except Exception:
                raw = b""
            return Response(url=url, status=e.code, body=raw, final_url=e.url or url,
                            redirect_chain=recorder.chain, error=f"http_{e.code}",
                            retry_after=e.headers.get("Retry-After") if e.headers else None,
                            elapsed_ms=int((time.monotonic() - started) * 1000))
        except (urllib.error.URLError, socket.timeout, ssl.SSLError, ConnectionError, OSError) as e:
            self.requests += 1
            return Response(url=url, status=None, error=_classify(e),
                            elapsed_ms=int((time.monotonic() - started) * 1000))
        except Exception as e:  # pragma: no cover - defensive
            self.requests += 1
            return Response(url=url, status=None, error=f"unexpected:{type(e).__name__}",
                            elapsed_ms=int((time.monotonic() - started) * 1000))

    def resolves(self, host: str) -> bool:
        try:
            socket.getaddrinfo(host, None)
            return True
        except OSError:
            return False


def _decode_body(raw: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    try:
        if "gzip" in enc:
            return gzip.decompress(raw)
        if "deflate" in enc:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        return raw
    return raw


def _to_text(body: bytes, content_type: str, charset: str | None) -> str:
    if not body:
        return ""
    ct = (content_type or "").lower()
    if not any(t in ct for t in ("text", "html", "json", "xml", "javascript")) and ct:
        return ""
    for enc in filter(None, (charset, "utf-8", "cp1252", "latin-1")):
        try:
            return body.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return body.decode("utf-8", "replace")


def _classify(exc: Exception) -> str:
    text = str(getattr(exc, "reason", exc)).lower()
    if "name or service not known" in text or "nodename nor servname" in text or "getaddrinfo" in text:
        return "dns_failure"
    if "timed out" in text or isinstance(exc, socket.timeout):
        return "timeout"
    if "certificate" in text or isinstance(exc, ssl.SSLError):
        return "tls_error"
    if "refused" in text:
        return "connection_refused"
    return f"network:{type(exc).__name__}"
