"""A web scraper that extracts the title and a limited portion of the main body text from a webpage.

This module provides a function `bert_scraper` that scrapes the content of a web page and extracts the title and a limited portion of the main body text.

Functions:
    - `bert_scraper`: Scrapes the content of a web page and extracts the title and a limited portion of the main body text.
    - `_get_page`: Retrieves the content of a webpage using its URL.
    - `_extract_title`: Extracts the title of a web page from the provided BeautifulSoup object.
    - `_extract_main`: Extracts the main content from the given BeautifulSoup object by removing extraneous elements such as scripts, styles, and other identified noise based on predefined patterns and heuristics.
"""

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
    """
    Retrieves the content of a webpage using its URL.

    This function takes a URL as input and ensures it starts with the correct
    HTTP/HTTPS protocol. If a valid protocol is not provided, it prepends
    "https://" to the URL. It then sends a GET request to the specified URL
    and returns the server's response. A timeout of 15 seconds is configured
    to limit the request duration.

    Parameters:
    url (str): A string representing the URL of the website to access. If the protocol is missing, "https://" is prepended automatically.

    Returns:
        The response object from the HTTP GET request that contains the content and metadata of the requested webpage.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    resp = requests.get(url, timeout=15, headers=HEADERS)
    resp.raise_for_status()
    return resp


def _extract_title(soup: BeautifulSoup) -> str:
    """
    Extracts the title of a web page from the provided BeautifulSoup object.
    This function first checks for a meta tag with the property `og:title`
    and retrieves its `content` attribute if available. If the `og:title`
    meta tag is not present, it falls back to the `<title>` tag's content.
    In case neither are found, it returns an empty string.

    Parameters:
    soup: The BeautifulSoup object representing the parsed HTML of the web page.

    Returns:
        The extracted title of the web page. If no title is found, returns an empty string.
    """
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return ""


def _extract_main(soup: BeautifulSoup) -> BeautifulSoup:
    """
    Extracts the main content from the given BeautifulSoup object by removing extraneous
    elements such as scripts, styles, and other identified noise based on predefined patterns
    and heuristics. The function processes the provided soup object to retain only the
    most relevant content, attempting to identify the main article or central text
    segment while filtering out unnecessary or distracting markup.

    Parameters:
        soup: A BeautifulSoup object representing the HTML document to process.

    Returns:
        A BeautifulSoup object containing the extracted main content.
    """
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
    """
    Scrapes the content of a web page and extracts the title and a limited portion
    of the main body text. This function fetches the HTML content of the URL, parses
    it, extracts the title, and retrieves up to a defined number of words from
    the main content body.

    Parameters:
        url (str):  The URL of the web page to scrape, provided as a string.

    Returns:
        A dictionary containing the extracted title as a string and the main body text as a string limited to a certain number of words.
    """
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
