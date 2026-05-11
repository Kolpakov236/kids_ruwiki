from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.settings import settings


@dataclass(frozen=True)
class RuwikiArticle:
    title: str
    url: str
    text: str


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _looks_like_html(text: str) -> bool:
    sample = (text or "").lstrip()[:200].lower()
    return sample.startswith("<!doctype html") or sample.startswith("<html") or "<head" in sample


def _looks_like_error_page(html: str) -> bool:
    low = (html or "").lower()
    # Ruwiki sometimes returns an HTML "error page" shell (often with token scripts)
    return ("<title>страница ошибки</title>" in low) or ("hmac-token-name" in low)


def _mw_api_candidates() -> list[str]:
    # Prefer configured base, but also try both common hosts.
    base = (settings.ruwiki_site_base or "https://ruwiki.ru").rstrip("/")
    include_ru_subdomain = "ru.ruwiki.ru" in base
    preferred = "https://ruwiki.ru"
    bases = [preferred, base] if base != preferred else [preferred]
    candidates: list[str] = []
    for b in bases:
        candidates += [f"{b}/w/api.php", f"{b}/api.php"]
    if include_ru_subdomain:
        candidates += ["https://ru.ruwiki.ru/w/api.php", "https://ru.ruwiki.ru/api.php"]
    # De-duplicate while preserving order
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _site_page_candidates(encoded_title: str) -> list[str]:
    base = (settings.ruwiki_site_base or "https://ruwiki.ru").rstrip("/")
    candidates = [
        f"{base}/wiki/{encoded_title}",
        f"{base}/index.php?title={encoded_title}",
        f"https://ruwiki.ru/wiki/{encoded_title}",
        f"https://ru.ruwiki.ru/wiki/{encoded_title}",
    ]
    seen = set()
    out = []
    for c in candidates:
        if c not in seen:
            out.append(c)
            seen.add(c)
    return out


def _wikipedia_api_candidates() -> list[str]:
    return ["https://ru.wikipedia.org/w/api.php"]


async def fetch_article(query: str) -> RuwikiArticle:
    q = query.strip()
    if not q:
        raise ValueError("empty_query")

    title = q.replace("_", " ")
    encoded = quote(title)
    async with httpx.AsyncClient(
        # Keep article fetch fast; LLM has its own timeout later in the pipeline.
        timeout=httpx.Timeout(8.0, connect=3.0, read=6.0),
        follow_redirects=True,
        headers={
            # Some hosts return an HTML anti-bot page unless the request looks like a browser.
            "User-Agent": "ruwiki-explain/0.1 (+https://github.com; educational; contact: local)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.7,en;q=0.6",
        },
    ) as client:
        params = {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "explaintext": "1",
            "redirects": "1",
            "titles": title,
        }

        last_err: Exception | None = None
        attempts = 0
        # A guard against accidental infinite loops, not a user-facing failure mode.
        max_attempts = 24

        async def try_mw_extract(api_url: str, requested_title: str) -> RuwikiArticle | None:
            nonlocal last_err, attempts
            if attempts >= max_attempts:
                return None
            attempts += 1
            api = await client.get(api_url, params={**params, "titles": requested_title})
            if api.status_code == 404:
                last_err = httpx.HTTPStatusError("mw_api_404", request=api.request, response=api)
                return None
            if api.status_code == 403:
                last_err = httpx.HTTPStatusError("mw_api_403", request=api.request, response=api)
                return None
            if 500 <= api.status_code <= 599:
                last_err = httpx.HTTPStatusError("mw_api_5xx", request=api.request, response=api)
                return None
            api.raise_for_status()
            ct = (api.headers.get("content-type") or "").lower()
            body_text = api.text or ""
            if "application/json" not in ct or _looks_like_html(body_text):
                last_err = ValueError("mw_api_non_json_response")
                return None

            data = api.json()
            pages = data.get("query", {}).get("pages", {})
            for page in pages.values():
                if "missing" in page:
                    continue
                extract = str(page.get("extract", "")).strip()
                if len(extract) >= 50:
                    resolved_title = str(page.get("title") or requested_title)
                    text = re.sub(r"\n{3,}", "\n\n", extract).strip()
                    site = api_url.split("/w/")[0].split("/api.php")[0].split("/w/api.php")[0].rstrip("/")
                    return RuwikiArticle(
                        title=resolved_title,
                        url=f"{site}/wiki/{quote(resolved_title)}",
                        text=text,
                    )
            last_err = ValueError("mw_api_empty_or_missing_extract")
            return None

        async def mw_search_title(api_url: str) -> str | None:
            nonlocal last_err, attempts
            if attempts >= max_attempts:
                return None
            attempts += 1
            search = await client.get(
                api_url,
                params={
                    "action": "query",
                    "format": "json",
                    "list": "search",
                    "srsearch": title,
                    "srlimit": "1",
                },
            )
            if search.status_code in {403, 404} or 500 <= search.status_code <= 599:
                last_err = httpx.HTTPStatusError("mw_search_failed", request=search.request, response=search)
                return None
            search.raise_for_status()
            ct = (search.headers.get("content-type") or "").lower()
            if "application/json" not in ct or _looks_like_html(search.text or ""):
                last_err = ValueError("mw_search_non_json_response")
                return None
            rows = search.json().get("query", {}).get("search", [])
            if not rows:
                last_err = ValueError("mw_search_no_results")
                return None
            return str(rows[0].get("title") or "").strip() or None

        for api_url in _mw_api_candidates() + _wikipedia_api_candidates():
            try:
                article = await try_mw_extract(api_url, title)
                if article:
                    return article
                resolved = await mw_search_title(api_url)
                if resolved and resolved.lower() != title.lower():
                    article = await try_mw_extract(api_url, resolved)
                    if article:
                        return article
            except Exception as e:
                last_err = e
                continue

        # Fallback: REST HTML endpoint (works when extracts are empty).
        configured_rest = (settings.ruwiki_rest_api_base or "https://ruwiki.ru/api/rest_v1").rstrip("/")
        rest_candidates = ["https://ruwiki.ru/api/rest_v1"]
        if configured_rest != rest_candidates[0]:
            rest_candidates.append(configured_rest)
        html = ""
        for rest_base in dict.fromkeys(rest_candidates):
            try:
                if attempts >= max_attempts:
                    break
                attempts += 1
                url = f"{rest_base}/page/html/{encoded}"
                r = await client.get(url, headers={"Accept": "text/html,application/xhtml+xml,*/*"})
                if r.status_code == 404:
                    raise ValueError("article_not_found")
                if r.status_code == 403:
                    last_err = httpx.HTTPStatusError("rest_api_403", request=r.request, response=r)
                    continue
                if 500 <= r.status_code <= 599:
                    last_err = httpx.HTTPStatusError("rest_api_5xx", request=r.request, response=r)
                    continue
                r.raise_for_status()
                candidate = r.text or ""
                if not _looks_like_html(candidate) or _looks_like_error_page(candidate):
                    last_err = ValueError("rest_api_bad_html")
                    continue
                html = candidate
                break
            except Exception as e:
                last_err = e
                continue

        if not html:
            # Last resort: fetch the normal wiki page HTML (often works even when APIs are blocked).
            for page_url in _site_page_candidates(encoded)[:2]:
                try:
                    if attempts >= max_attempts:
                        break
                    attempts += 1
                    r = await client.get(
                        page_url,
                        headers={"Accept": "text/html,application/xhtml+xml,*/*"},
                    )
                    if r.status_code == 404:
                        continue
                    if r.status_code == 403:
                        last_err = httpx.HTTPStatusError("page_403", request=r.request, response=r)
                        continue
                    if 500 <= r.status_code <= 599:
                        last_err = httpx.HTTPStatusError("page_5xx", request=r.request, response=r)
                        continue
                    r.raise_for_status()
                    candidate = r.text or ""
                    if not _looks_like_html(candidate) or _looks_like_error_page(candidate):
                        last_err = ValueError("page_bad_html")
                        continue
                    html = candidate
                    break
                except Exception as e:
                    last_err = e
                    continue

        if not html:
            if isinstance(last_err, httpx.HTTPStatusError) and last_err.response is not None and last_err.request is not None:
                code = last_err.response.status_code
                url = str(last_err.request.url)
                raise ValueError(f"ruwiki_fetch_failed:http_{code}:{url}")
            if isinstance(last_err, httpx.TimeoutException):
                raise ValueError("ruwiki_fetch_failed:timeout")
            if isinstance(last_err, ValueError):
                raise ValueError(f"ruwiki_fetch_failed:{last_err}")
            raise ValueError(f"ruwiki_fetch_failed:{type(last_err).__name__ if last_err else 'no_successful_attempts'}")

    text = _html_to_text(html)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 50:
        raise ValueError("article_too_short")

    return RuwikiArticle(title=title, url=f"{settings.ruwiki_site_base.rstrip('/')}/wiki/{encoded}", text=text)

