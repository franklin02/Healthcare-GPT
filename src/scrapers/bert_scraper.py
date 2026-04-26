import sys
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

MAX_WORDS = 300  # change if needed

_NOISE_PATTERNS = (
    "ad",
    "advert",
    "promo",
    "sidebar",
    "related",
    "newsletter",
    "subscribe",
    "comment",
    "social",
    "share",
    "cookie",
)

_NOISE_ID_RE = re.compile("|".join(_NOISE_PATTERNS), re.IGNORECASE)


def _get_page(url: str) -> requests.Response:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    resp = requests.get(url, timeout=15, headers=HEADERS)
    resp.raise_for_status()
    return resp


def _extract_title(soup: BeautifulSoup) -> str:
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""


def _extract_main(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "iframe",
            "form",
            "header",
            "footer",
            "nav",
            "aside",
        ]
    ):
        tag.decompose()

    selector = ",".join(f'[class*="{p}"]' for p in _NOISE_PATTERNS)
    for el in soup.select(selector):
        el.decompose()

    for el in soup.find_all(id=_NOISE_ID_RE):
        el.decompose()

    for candidate in (
        soup.find("article"),
        soup.find("main"),
        soup.find(attrs={"role": "main"}),
        soup.body,
    ):
        if candidate and candidate.get_text(strip=True):
            return candidate

    return soup


def bert_scraper(url: str) -> dict:
    resp = _get_page(url)
    soup = BeautifulSoup(resp.text, "html.parser")

    title = _extract_title(soup)
    main = _extract_main(soup)

    raw_text = main.get_text(separator=" ", strip=True)
    words = raw_text.split()[:MAX_WORDS]
    body = " ".join(words)

    return {
        "title": title,
        "body": body,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python bert_scraper.py <url>", file=sys.stderr)
        sys.exit(1)

    result = bert_scraper(sys.argv[1])
    print(f"TITLE: {result['title']}")
    print("=" * 72)
    print(result["body"])
