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


async def fetch_article(query: str) -> RuwikiArticle:
    q = query.strip()
    if not q:
        raise ValueError("empty_query")

    title = q.replace("_", " ")
    encoded = quote(title)
    async with httpx.AsyncClient(
        timeout=12.0,
        follow_redirects=True,
        headers={"User-Agent": "RuwikSimplifierMVP/0.1"},
    ) as client:
        api = await client.get(
            "https://ru.ruwiki.ru/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "prop": "extracts",
                "explaintext": "1",
                "redirects": "1",
                "titles": title,
            },
        )
        api.raise_for_status()
        data = api.json()
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if "missing" in page:
                continue
            extract = str(page.get("extract", "")).strip()
            if len(extract) >= 50:
                resolved_title = str(page.get("title") or title)
                text = re.sub(r"\n{3,}", "\n\n", extract).strip()
                return RuwikiArticle(
                    title=resolved_title,
                    url=f"https://ru.ruwiki.ru/wiki/{quote(resolved_title)}",
                    text=text,
                )

        url = f"{settings.ruwiki_api_base}/page/html/{encoded}"
        r = await client.get(url)
        if r.status_code == 404:
            raise ValueError("article_not_found")
        r.raise_for_status()
        html = r.text

    text = _html_to_text(html)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 50:
        raise ValueError("article_too_short")

    return RuwikiArticle(title=title, url=f"https://ru.ruwiki.ru/wiki/{encoded}", text=text)

