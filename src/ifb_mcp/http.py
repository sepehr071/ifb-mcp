"""Shared async HTTP client for ifb.ir (Iran Fara Bourse): server-rendered ASP.NET pages and its JSON web service.

Every tool goes through `get_page`, `post_page` or `call`, which cap concurrency, cache answers briefly and turn HTTP
failures into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import copy
import json as json_lib
import os
import random
import re
import time
from typing import Any

import httpx
from curl_cffi.const import CurlOpt
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

SITE = "https://www.ifb.ir"
SERVICE = f"{SITE}/HistoricalInstrumentData.asmx"
HOSTS = {"www.ifb.ir"}  # host allowlist

HEADERS = {
    # User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client).
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
}
PAGE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}
# The site sends `Referrer-Policy: no-referrer`, so Chrome sends no Referer on its XHRs, links or form posts, and a
# form post carries `Origin: null` (checked in Chrome 2026-11-01).
XHR_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json; charset=UTF-8",
    "Origin": SITE,
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "priority": "u=1, i",
}

MAX_CONCURRENCY = 3
JITTER = (0.1, 0.4)  # seconds before each uncached call
MAX_RETRIES = 2
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds
CACHE_TTL = 60  # seconds; quotes move during the session
CACHE_MAX = 64
RETRY_DELAY = 0.5  # seconds, doubled per retry
MAX_BODY = 8_000_000  # bytes; the YTM page is about 2 MB, larger responses are refused


_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_cache: dict[tuple, tuple[float, Any]] = {}


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them on XHR calls."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        # The parent drops "accept: */*" as an httpx default; curl would then send Chrome's page Accept instead.
        accept = req.headers.get("accept")
        p = super()._create_request_params(req)
        extra = [(b"cache-control", None)]  # Chrome sends max-age=0 only on a reload
        if req.headers.get("sec-fetch-mode") != "navigate":
            extra += [(b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        p["headers"] = [*p["headers"], *extra, *([(b"accept", b"*/*")] if accept == "*/*" else [])]
        return p


class ApiError(ToolError):
    """A failed upstream call. `status` is the HTTP status, or None for network errors."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client."""
    global _transport, _client, _limit, _proxy_index, _watch
    _transport, _client, _limit, _proxy_index, _watch = transport, None, None, 0, None
    _cache.clear()


def _proxies() -> list[str]:
    """IFB_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("IFB_MCP_PROXY", "").split(",") if p.strip()]


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate. The old client is closed late because other requests may still use it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(30)  # longer than the request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit
    if _client is None:
        # System proxy settings are ignored; the proxy only comes from IFB_MCP_PROXY. curl_cffi presents
        # Chrome's TLS/HTTP2 fingerprint and default headers. The proxy goes to the transport.
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                curl_options={
                    CurlOpt.NOPROXY: "*"
                },  # httpx-curl-cffi drops its own NOPROXY unless this dict is non-empty
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=httpx.Timeout(30, connect=5),  # fail fast: 3 attempts must not add up to 40 s
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=False,
            trust_env=False,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
    assert _limit is not None
    return _client, _limit


async def get_page(path: str, params: dict[str, Any] | None = None) -> str:
    """GET a server-rendered page (path such as "YTM.aspx") and return its HTML."""
    return await _fetch("GET", path, params=params, headers=PAGE_HEADERS)


async def post_page(path: str, form: dict[str, str], params: dict[str, Any] | None = None) -> str:
    """POST a WebForms form (search button or pager postback) back to a page and return the HTML."""
    headers = {**PAGE_HEADERS, "Origin": "null"}
    return await _fetch("POST", path, params=params, headers=headers, form=form)


async def call(method: str, args: dict[str, Any] | None = None) -> Any:
    """Call one HistoricalInstrumentData.asmx web method with a JSON body; returns the unwrapped `d` value."""
    body = await _fetch("POST", "HistoricalInstrumentData.asmx/" + method, headers=XHR_HEADERS, payload=args or {})
    return body["d"] if isinstance(body, dict) and "d" in body else body


SIGNALR_HEADERS = {
    "Accept": "text/plain, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "priority": "u=1, i",
}
HUB = '[{"name":"myhub"}]'
WATCH_TTL = 30  # seconds; the snapshot is 3 MB and the market moves slowly enough
_watch: tuple[float, list[dict[str, Any]], str | None] | None = None


async def _signalr(method: str, step: str, params: dict[str, Any], form: dict[str, str] | None = None) -> Any:
    host = "www.ifb.ir"
    # jQuery sends a JSON Content-Type even on the bodyless GETs and a charset on the form post
    ctype = "application/x-www-form-urlencoded; charset=UTF-8" if form else "application/json; charset=UTF-8"
    headers = {**SIGNALR_HEADERS, "Content-Type": ctype, **({"Origin": SITE} if form else {})}
    r, content = await _request(method, f"{SITE}/signalr/{step}", params, headers, form, None, host)
    if r.status_code >= 400:
        raise ApiError(_status_message(r, host, content), r.status_code)
    try:
        return json_lib.loads(content)
    except ValueError as e:
        raise ApiError(f"{host} returned a non-JSON SignalR answer (HTTP {r.status_code}).", r.status_code) from e


async def market_watch() -> tuple[list[dict[str, Any]], str | None]:
    """Snapshot of the live market watch (every symbol) read once from the site's SignalR hub, plus its clock text.

    Long polling: negotiate, connect, start, send `letsStart`, then poll until the hub pushes `updateSingleTable`.
    """
    global _watch
    if _watch and time.monotonic() - _watch[0] < WATCH_TTL:
        return copy.deepcopy(_watch[1]), _watch[2]
    n = await _signalr("GET", "negotiate", {"clientProtocol": "1.2", "connectionData": HUB})
    q = {
        "transport": "longPolling",
        "clientProtocol": "1.2",
        "connectionToken": n["ConnectionToken"],
        "connectionData": HUB,
    }
    clock: str | None = None
    rows: list[dict[str, Any]] | None = None

    def scan(answer: dict[str, Any]) -> None:
        nonlocal clock, rows
        for m in answer.get("M") or []:
            if m.get("M") == "updateRow" and isinstance(m["A"][1], str):
                clock = m["A"][1].strip()
            elif m.get("M") == "updateSingleTable":
                rows = m["A"][0]

    try:
        first = await _signalr("GET", "connect", q)
        scan(first)
        await _signalr("GET", "start", q)
        start = {"H": "myhub", "M": "letsStart", "A": [n["ConnectionId"]], "I": 0}
        await _signalr("POST", "send", q, {"data": json_lib.dumps(start, separators=(",", ":"))})
        cursor = first.get("C", "")
        for _ in range(4):
            if rows is not None:
                break
            answer = await _signalr("GET", "poll", {**q, "messageId": cursor})
            cursor = answer.get("C", cursor)
            scan(answer)
    finally:
        try:
            await _signalr("GET", "abort", q)
        except ApiError:
            pass  # the connection times out on the server anyway
    if rows is None:
        raise ApiError("ifb.ir did not push the market watch table. Try again in a moment.")
    _watch = (time.monotonic(), copy.deepcopy(rows), clock)
    return rows, clock


async def _fetch(method: str, path: str, *, params=None, headers=None, form=None, payload=None) -> Any:
    url = f"{SITE}/{path.lstrip('/')}"
    host = httpx.URL(url).host
    if host not in HOSTS:
        raise ApiError(f"Refusing to call unexpected host {host}.")
    key = (method, url, tuple(sorted((params or {}).items())), repr(sorted((form or {}).items())), repr(payload))
    if (hit := _cache.get(key)) and time.monotonic() - hit[0] < CACHE_TTL:
        return copy.deepcopy(hit[1])
    for attempt in range(MAX_RETRIES + 1):
        last = attempt == MAX_RETRIES
        wait = RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
        try:
            r, content = await _request(method, url, params, headers, form, payload, host)
        except ApiError as e:
            if last or e.status is not None:
                raise
            await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
            continue
        if (r.status_code == 429 or r.status_code >= 500) and not last and not _is_service_error(r, content):
            await asyncio.sleep(min(_retry_after(r) or wait, MAX_RETRY_WAIT))
            continue
        break

    if r.status_code >= 400:
        raise ApiError(_status_message(r, host, content), r.status_code)
    if payload is not None:
        try:
            body: Any = json_lib.loads(content)
        except ValueError as e:
            raise ApiError(f"{host} returned a non-JSON response (HTTP {r.status_code}).", r.status_code) from e
    else:
        body = content.decode("utf-8", errors="replace")
        if "<html" not in body[:3000].lower() and "<form" not in body[:3000].lower():
            raise ApiError(f"{host} returned an unexpected response (HTTP {r.status_code}).", r.status_code)
    if len(_cache) >= CACHE_MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), copy.deepcopy(body))
    return body


def _is_service_error(r: httpx.Response, content: bytes) -> bool:
    """asmx answers 500 with a JSON {"Message": ...} for bad arguments; retrying cannot help."""
    return r.status_code == 500 and content.lstrip().startswith(b'{"Message"')


async def _send(client, limit, method, url, params, headers, form, payload, host) -> tuple[httpx.Response, bytes]:
    try:
        async with limit:
            await asyncio.sleep(random.uniform(*JITTER))
            async with client.stream(method, url, params=params, headers=headers, data=form, json=payload) as r:
                return r, await _read_capped(r, host)
    except httpx.TimeoutException as e:
        raise ApiError(f"{host} did not answer in time. Try again in a moment.") from e
    except httpx.RequestError as e:
        if isinstance(e, httpx.ConnectError | httpx.ProxyError):
            _next_proxy(client)
        raise ApiError(
            f"Could not reach {host} ({type(e).__name__}). ifb.ir answers Iranian networks best; check the "
            "internet connection (or the IFB_MCP_PROXY setting if you use one)."
        ) from e


async def _request(method, url, params, headers, form, payload, host) -> tuple[httpx.Response, bytes]:
    client, limit = _get_client()
    r, content = await _send(client, limit, method, url, params, headers, form, payload, host)
    if r.status_code in (403, 429):
        _next_proxy(client)  # blocked: try the next proxy on the following call
    return r, content


async def _read_capped(r: httpx.Response, host: str) -> bytes:
    """Read the body, refusing anything over MAX_BODY so one huge answer cannot flood memory or context."""
    buf = bytearray()
    async for chunk in r.aiter_bytes():
        buf += chunk
        if len(buf) > MAX_BODY:
            raise ApiError(f"{host} sent a response over {MAX_BODY // 1_000_000} MB; refused.", r.status_code)
    return bytes(buf)


def _retry_after(r: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After on a 429/503, else None (HTTP-date form is ignored)."""
    v = r.headers.get("retry-after", "")
    return float(v) if r.status_code in (429, 503) and v.replace(".", "", 1).isdigit() else None


def _status_message(r: httpx.Response, host: str, content: bytes = b"") -> str:
    code = r.status_code
    if code == 403:
        return f"{host} blocked the request (HTTP 403). Try another network or set IFB_MCP_PROXY."
    if code == 404:
        return f"{host} found nothing at that address (HTTP 404). Check the id."
    if code == 429:
        return f"{host} is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if _is_service_error(r, content):
        return f"{host} could not process that request (HTTP 500). Check the arguments."
    if code >= 500:
        return f"{host} had a server error (HTTP {code}). Try again later."
    return f"{host} rejected the request (HTTP {code})."


def hidden_fields(html: str) -> dict[str, str]:
    """The ASP.NET state fields (__VIEWSTATE and friends) a postback must send back."""
    return {
        m.group(1): m.group(2).replace("&amp;", "&").replace("&quot;", '"').replace("&#39;", "'")
        for m in re.finditer(r'<input type="hidden" name="(__[A-Z]+)" id="[^"]*" value="([^"]*)"', html)
    }
