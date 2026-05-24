from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.settings import settings

# ---------------------------------------------------------------------------
# Query pre-processing: strip question framing to get core concept
# ---------------------------------------------------------------------------

_QUESTION_RE = re.compile(
    r"^(?:"
    r"что такое\s+|что значит\s+|что означает\s+|что из себя представляет\s+|что представляет собой\s+|"
    r"как устроен[аоы]?\s+|как работает\s+|как работают\s+|как называется\s+|как называют\s+|"
    r"как выглядит\s+|как образуется\s+|как возникает\s+|как появился\s+|как появилась\s+|"
    r"почему\s+|зачем\s+|откуда берётся\s+|откуда берется\s+|откуда\s+|"
    r"из чего состоит\s+|из чего сделан[аоы]?\s+|"
    r"расскажи (?:мне )?(?:про |о |об )|объясни (?:мне )?(?:про |о |об |что такое )?|"
    r"кто такой\s+|кто такая\s+|кто такие\s+|"
    r"чем является\s+"
    r")",
    re.IGNORECASE | re.UNICODE,
)


def _extract_concept(query: str) -> str:
    """Remove question framing to get the core encyclopaedic concept."""
    q = query.strip().rstrip("?!.…").strip()
    m = _QUESTION_RE.match(q)
    if m:
        q = q[m.end():].strip()
    return q or query.strip()


def _relevance_score(concept: str, article_title: str, article_text: str = "") -> float:
    """
    0..1 estimate of how relevant an article is to the concept.
    Uses title substring / word-prefix match; falls back to content check.
    """
    c = concept.lower().strip()
    t = article_title.lower().strip()

    if c in t or t in c:
        return 1.0

    c_words = [w for w in re.findall(r"\w{4,}", c)]
    t_words = [w for w in re.findall(r"\w{4,}", t)]
    if c_words and t_words:
        # Prefix-6 matching handles Russian inflection (e.g. «динозавры» ~ «динозавр»)
        title_matched = sum(
            1 for cw in c_words
            if any(tw[:6] == cw[:6] for tw in t_words)
        )
        title_score = title_matched / len(c_words)
        if title_score >= 0.5:
            return title_score

    # Secondary check: does article text mention concept words?
    if article_text and c_words:
        text_sample = article_text[:3000].lower()
        content_matched = sum(1 for cw in c_words if cw[:6] in text_sample)
        return content_matched / len(c_words) * 0.6  # discounted

    return 0.0


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
    concept = _extract_concept(q)  # core encyclopaedic term, e.g. «фотосинтез» from «Что такое фотосинтез?»

    # Title candidates: prefer concept if it meaningfully differs from raw query
    _seen_titles: set[str] = set()
    titles_to_try: list[str] = []
    for t in ([concept, title] if concept.lower() != title.lower() else [title]):
        if t.lower() not in _seen_titles:
            titles_to_try.append(t)
            _seen_titles.add(t.lower())

    encoded = quote(concept)
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
        max_attempts = 32
        had_network_error = False  # distinguishes "not found" from "connection failed"

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

        async def mw_search_titles(api_url: str, search_query: str, limit: int = 3) -> list[str]:
            """Return up to `limit` article titles matching search_query, best-first."""
            nonlocal last_err, attempts
            if attempts >= max_attempts:
                return []
            attempts += 1
            search = await client.get(
                api_url,
                params={
                    "action": "query",
                    "format": "json",
                    "list": "search",
                    "srsearch": search_query,
                    "srlimit": str(limit),
                },
            )
            if search.status_code in {403, 404} or 500 <= search.status_code <= 599:
                last_err = httpx.HTTPStatusError("mw_search_failed", request=search.request, response=search)
                return []
            search.raise_for_status()
            ct = (search.headers.get("content-type") or "").lower()
            if "application/json" not in ct or _looks_like_html(search.text or ""):
                last_err = ValueError("mw_search_non_json_response")
                return []
            rows = search.json().get("query", {}).get("search", [])
            if not rows:
                last_err = ValueError("mw_search_no_results")
                return []
            return [str(r.get("title") or "").strip() for r in rows if r.get("title")]

        for api_url in _mw_api_candidates() + _wikipedia_api_candidates():
            try:
                # 1. Try direct title lookup for each candidate (concept first, then raw query)
                best_article: RuwikiArticle | None = None
                best_score: float = -1.0
                for t in titles_to_try:
                    article = await try_mw_extract(api_url, t)
                    if article:
                        score = _relevance_score(concept, article.title, article.text)
                        if score > best_score:
                            best_score = score
                            best_article = article

                if best_article and best_score >= 0.3:
                    return best_article

                # 2. Full-text search using extracted concept (top 3 candidates)
                search_results = await mw_search_titles(api_url, concept, limit=3)
                tried_lower = _seen_titles.copy()
                for candidate in search_results:
                    if candidate.lower() in tried_lower:
                        continue
                    tried_lower.add(candidate.lower())
                    article = await try_mw_extract(api_url, candidate)
                    if article:
                        score = _relevance_score(concept, article.title, article.text)
                        if score > best_score:
                            best_score = score
                            best_article = article

                # Return best found so far (even if score is low — LLM can still use it)
                if best_article:
                    return best_article

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                had_network_error = True
                last_err = e
                continue
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
            # Distinguish "content genuinely not found" from "network/server error"
            _not_found_indicators = (
                "mw_search_no_results", "mw_api_empty_or_missing_extract",
                "article_not_found", "mw_api_404",
            )
            err_str = str(last_err) if last_err else ""
            is_not_found = (
                not had_network_error
                and any(ind in err_str for ind in _not_found_indicators)
            )
            if is_not_found:
                raise ValueError("no_relevant_article")
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
        raise ValueError("no_relevant_article")

    return RuwikiArticle(title=concept, url=f"{settings.ruwiki_site_base.rstrip('/')}/wiki/{encoded}", text=text)

