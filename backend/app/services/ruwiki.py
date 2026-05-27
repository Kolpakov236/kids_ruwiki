from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.settings import settings

# ---------------------------------------------------------------------------
# Query pre-processing
# ---------------------------------------------------------------------------

_QUESTION_RE = re.compile(
    r"^(?:"
    r"что такое\s+|что значит\s+|что означает\s+|что из себя представляет\s+|что представляет собой\s+|"
    r"как устроен[аоы]?\s+|как работает\s+|как работают\s+|как называется\s+|как называют\s+|"
    r"как выглядит\s+|как образуется\s+|как возникает\s+|как появился\s+|как появилась\s+|"
    r"почему\s+|зачем\s+|откуда берётся\s+|откуда берется\s+|откуда\s+|"
    r"из чего состоит\s+|из чего сделан[аоы]?\s+|"
    r"расскажи (?:мне )?(?:про |о |об )|объясни (?:мне )?(?:про |о |об |что такое )?|"
    r"кто такой\s+|кто такая\s+|кто такие\s+|кто\s+|"
    r"чем является\s+"
    r")",
    re.IGNORECASE | re.UNICODE,
)

# Leading verb forms in "куда делись X", "как погибли X" — strip to get noun phrase
_VERB_PREFIX_RE = re.compile(
    r"^(?:"
    r"делись|делся|делась|делись|"
    r"появились|появился|появилась|появляются|появляется|"
    r"исчезли|исчез|исчезла|исчезают|исчезает|"
    r"погибли|погиб|погибла|гибнут|гибнет|"
    r"вымерли|вымер|вымерла|вымирают|"
    r"произошло|случилось|случился|случилась|случаются|"
    r"устроен[аоы]?|работает|работают|"
    r"образуется|образуются|возникает|возникают|"
    r"называется|называются|выглядит|выглядят"
    r")\s+",
    re.IGNORECASE | re.UNICODE,
)

# Question-word prefixes not caught by _QUESTION_RE (standalone куда/когда/etc before verb)
_LEADING_QW_RE = re.compile(
    r"^(?:куда|когда|откуда|зачем|почему|сколько|какой|какая|какие|каким|кто)\s+",
    re.IGNORECASE | re.UNICODE,
)

# Russian prepositions — used to strip trailing prepositional phrases in query variants
_RU_PREPOSITIONS = frozenset({
    "в", "на", "по", "из", "за", "без", "для", "до", "об",
    "под", "над", "с", "к", "у", "о", "от", "при", "про", "через",
    "между", "перед", "вокруг", "около", "после", "ради", "вместо",
    "среди", "мимо", "вдоль", "против", "внутри",
})


def _extract_concept(query: str) -> str:
    """Remove question framing, return core encyclopaedic concept."""
    q = query.strip().rstrip("?!.…").strip()
    m = _QUESTION_RE.match(q)
    if m:
        q = q[m.end():].strip()
    return q or query.strip()


def _normalize_to_noun_phrase(text: str) -> str:
    """Strip leading question words and verb forms to get the core noun phrase."""
    t = _LEADING_QW_RE.sub("", text).strip()
    t = _VERB_PREFIX_RE.sub("", t).strip()
    return t


def _strip_prep_tail(phrase: str) -> str:
    """Remove trailing prepositional phrase, e.g. 'лучший футболист в мире' → 'лучший футболист'."""
    words = phrase.split()
    for i in range(len(words) - 1, 0, -1):
        if words[i - 1].lower() in _RU_PREPOSITIONS:
            trimmed = " ".join(words[:i - 1]).strip()
            if trimmed:
                return trimmed
    return phrase


def _extract_search_queries(query: str) -> list[str]:
    """
    Generate an ordered list of search queries (most to least specific).
    Multiple variants improve recall when the primary query finds nothing.
    """
    concept = _extract_concept(query)
    variants: list[str] = [concept]

    # Strip leading question-words + verb forms to get noun phrase
    noun_phrase = _normalize_to_noun_phrase(concept)
    if noun_phrase and noun_phrase.lower() != concept.lower():
        variants.append(noun_phrase)

    base = noun_phrase or concept

    # Strip trailing prepositional phrase ("в мире", "на земле", etc.)
    stripped = _strip_prep_tail(base)
    if stripped and stripped.lower() != base.lower():
        variants.append(stripped)
        base = stripped

    # Last meaningful content word (skip prepositions and very short tokens)
    content_words = [w for w in base.split() if w.lower() not in _RU_PREPOSITIONS and len(w) >= 4]
    if content_words:
        last_word = content_words[-1]
        if last_word.lower() not in {v.lower() for v in variants}:
            variants.append(last_word)

    # De-duplicate, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        v = v.strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------

_DISAMBIGUATION_RE = re.compile(
    r"может означать|может относиться|значения:|другие значения|"
    r"статья — список|это\s+—\s+страница\s+значений",
    re.IGNORECASE,
)


def _relevance_score(concept: str, article_title: str, article_text: str = "") -> float:
    """
    0..1 relevance of an article to the concept.
    Combines title match, content coverage, and penalties for disambiguation pages.
    """
    c = concept.lower().strip()
    t = article_title.lower().strip()

    # Exact match → maximum
    if c == t:
        return 1.0

    # Substring containment
    if c in t or t in c:
        title_score = 0.95
    else:
        c_words = [w for w in re.findall(r"\w{4,}", c)]
        t_words = [w for w in re.findall(r"\w{4,}", t)]
        if c_words and t_words:
            # Prefix-6 matching for Russian inflection
            matched = sum(
                1 for cw in c_words
                if any(tw[:6] == cw[:6] for tw in t_words)
            )
            title_score = matched / len(c_words)
        else:
            title_score = 0.0

    # Boost if title_score is already high
    if title_score >= 0.5:
        score = title_score
    elif article_text:
        # Content coverage: how many concept words appear in the text
        c_words = [w for w in re.findall(r"\w{4,}", c)]
        if c_words:
            sample = article_text[:4000].lower()
            text_matched = sum(1 for cw in c_words if cw[:6] in sample)
            content_score = (text_matched / len(c_words)) * 0.55
            score = max(title_score, content_score)
        else:
            score = title_score
    else:
        score = title_score

    # Penalise disambiguation pages heavily
    if article_text and _DISAMBIGUATION_RE.search(article_text[:600]):
        score *= 0.35

    # Slight bonus for longer articles (more content = more relevant)
    if article_text and score > 0:
        length_bonus = min(0.05, len(article_text) / 200_000)
        score = min(1.0, score + length_bonus)

    return round(score, 4)


def _score_search_snippet(concept: str, title: str, snippet: str) -> float:
    """Quick score from MW search snippet — used to rank titles before fetching."""
    base = _relevance_score(concept, title)
    if not snippet:
        return base
    c_words = [w for w in re.findall(r"\w{4,}", concept.lower())]
    if not c_words:
        return base
    snip = re.sub(r"<[^>]+>", "", snippet).lower()
    matched = sum(1 for cw in c_words if cw[:6] in snip)
    snippet_bonus = (matched / len(c_words)) * 0.2
    return min(1.0, base + snippet_bonus)


# ---------------------------------------------------------------------------
# Article dataclass
# ---------------------------------------------------------------------------

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
    return ("<title>страница ошибки</title>" in low) or ("hmac-token-name" in low)


def _mw_api_candidates() -> list[str]:
    base = (settings.ruwiki_site_base or "https://ruwiki.ru").rstrip("/")
    include_ru_subdomain = "ru.ruwiki.ru" in base
    preferred = "https://ruwiki.ru"
    bases = [preferred, base] if base != preferred else [preferred]
    candidates: list[str] = []
    for b in bases:
        candidates += [f"{b}/w/api.php", f"{b}/api.php"]
    if include_ru_subdomain:
        candidates += ["https://ru.ruwiki.ru/w/api.php", "https://ru.ruwiki.ru/api.php"]
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


# ---------------------------------------------------------------------------
# Main fetch logic
# ---------------------------------------------------------------------------

async def fetch_article(query: str) -> RuwikiArticle:
    q = query.strip()
    if not q:
        raise ValueError("empty_query")

    concept = _extract_concept(q)
    search_queries = _extract_search_queries(q)   # [most_specific, ..., least_specific]
    primary_concept = search_queries[0]            # used for relevance scoring
    encoded = quote(primary_concept)

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=3.0, read=8.0),
        follow_redirects=True,
        headers={
            "User-Agent": "ruwiki-explain/0.1 (+https://github.com; educational; contact: local)",
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.7,en;q=0.6",
        },
    ) as client:
        base_params = {
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "explaintext": "1",
            "redirects": "1",
        }

        last_err: Exception | None = None
        attempts = 0
        max_attempts = 48
        had_network_error = False
        _seen_titles: set[str] = set()

        async def try_mw_extract(api_url: str, requested_title: str) -> RuwikiArticle | None:
            nonlocal last_err, attempts
            if attempts >= max_attempts:
                return None
            attempts += 1
            try:
                api = await client.get(api_url, params={**base_params, "titles": requested_title})
            except Exception as e:
                last_err = e
                return None
            if api.status_code in {403, 404}:
                last_err = httpx.HTTPStatusError(f"mw_api_{api.status_code}", request=api.request, response=api)
                return None
            if 500 <= api.status_code <= 599:
                last_err = httpx.HTTPStatusError("mw_api_5xx", request=api.request, response=api)
                return None
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

        async def mw_search(api_url: str, search_query: str, limit: int = 5) -> list[dict]:
            """Return list of {title, snippet} dicts from MW search, best-first."""
            nonlocal last_err, attempts
            if attempts >= max_attempts:
                return []
            attempts += 1
            try:
                resp = await client.get(
                    api_url,
                    params={
                        "action": "query",
                        "format": "json",
                        "list": "search",
                        "srsearch": search_query,
                        "srlimit": str(limit),
                        "srprop": "snippet|titlesnippet",
                    },
                )
            except Exception as e:
                last_err = e
                return []
            if resp.status_code in {403, 404} or 500 <= resp.status_code <= 599:
                last_err = httpx.HTTPStatusError("mw_search_failed", request=resp.request, response=resp)
                return []
            ct = (resp.headers.get("content-type") or "").lower()
            if "application/json" not in ct or _looks_like_html(resp.text or ""):
                return []
            rows = resp.json().get("query", {}).get("search", [])
            if not rows:
                last_err = ValueError("mw_search_no_results")
                return []
            return [
                {"title": str(r.get("title") or "").strip(), "snippet": str(r.get("snippet") or "")}
                for r in rows if r.get("title")
            ]

        async def find_best_article(api_url: str) -> RuwikiArticle | None:
            """Try direct lookups then ranked search; return the best-scoring article."""
            best_article: RuwikiArticle | None = None
            best_score: float = -1.0

            # --- Phase 1: direct title lookups for all query variants ---
            direct_tasks = [
                try_mw_extract(api_url, sq)
                for sq in search_queries
                if sq.lower() not in _seen_titles
            ]
            for sq in search_queries:
                _seen_titles.add(sq.lower())

            direct_results = await asyncio.gather(*direct_tasks, return_exceptions=True)
            for result in direct_results:
                if isinstance(result, RuwikiArticle):
                    score = _relevance_score(primary_concept, result.title, result.text)
                    if score > best_score:
                        best_score = score
                        best_article = result

            # If direct lookup got a very confident match, return immediately
            if best_article and best_score >= 0.85:
                return best_article

            # --- Phase 2: search with all query variants in parallel ---
            search_tasks = [
                mw_search(api_url, sq, limit=5)
                for sq in search_queries
            ]
            search_results_all = await asyncio.gather(*search_tasks, return_exceptions=True)

            # Collect all candidates with pre-scores, de-duplicated
            candidates: dict[str, float] = {}  # title → best_pre_score
            for sq, results in zip(search_queries, search_results_all):
                if isinstance(results, list):
                    for item in results:
                        title = item["title"]
                        pre_score = _score_search_snippet(primary_concept, title, item["snippet"])
                        if title.lower() not in _seen_titles:
                            candidates[title] = max(candidates.get(title, 0.0), pre_score)

            if not candidates and best_article:
                return best_article  # nothing new from search

            # Sort by pre-score descending, fetch top candidates
            ranked = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
            fetch_tasks = []
            fetch_titles = []
            for title, _pre in ranked[:6]:
                if title.lower() not in _seen_titles:
                    _seen_titles.add(title.lower())
                    fetch_tasks.append(try_mw_extract(api_url, title))
                    fetch_titles.append(title)

            if fetch_tasks:
                fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)
                for result in fetched:
                    if isinstance(result, RuwikiArticle):
                        score = _relevance_score(primary_concept, result.title, result.text)
                        if score > best_score:
                            best_score = score
                            best_article = result

            return best_article

        # Try each API endpoint until we get a good result
        for api_url in _mw_api_candidates() + _wikipedia_api_candidates():
            try:
                article = await find_best_article(api_url)
                if article:
                    return article
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                had_network_error = True
                last_err = e
                continue
            except Exception as e:
                last_err = e
                continue

        # Fallback: REST HTML endpoint
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
                if r.status_code in {403} or 500 <= r.status_code <= 599:
                    last_err = httpx.HTTPStatusError(f"rest_api_{r.status_code}", request=r.request, response=r)
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
            for page_url in _site_page_candidates(encoded)[:2]:
                try:
                    if attempts >= max_attempts:
                        break
                    attempts += 1
                    r = await client.get(page_url, headers={"Accept": "text/html,application/xhtml+xml,*/*"})
                    if r.status_code == 404:
                        continue
                    if r.status_code in {403} or 500 <= r.status_code <= 599:
                        last_err = httpx.HTTPStatusError(f"page_{r.status_code}", request=r.request, response=r)
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
            if isinstance(last_err, httpx.HTTPStatusError) and last_err.response is not None:
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
    return RuwikiArticle(
        title=primary_concept,
        url=f"{settings.ruwiki_site_base.rstrip('/')}/wiki/{encoded}",
        text=text,
    )
