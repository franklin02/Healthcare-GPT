import re
import json
import time
import requests
from pathlib import Path
from bs4 import BeautifulSoup

PAGES = 25
BASE_URL = "https://www.aha.org/cybersecurity-news?page=%2C{page}"
ARTICLE_BASE = "https://www.aha.org"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"

OUTPUT_DIR = Path(__file__).parent / "aha_cybersecurity_news"

OUTPUT_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def clean_text(text: str) -> str:
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.replace('\x0c', '\n')
    lines = [line.rstrip() for line in text.splitlines()]
    text = '\n'.join(lines)
    return text.strip()


def slugify(title: str, max_len: int = 80) -> str:
    """Convert a title to a safe filename stem."""
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")[:max_len]



def scrape_page(page_num: int) -> list[dict]:
    """
    Fetch one AHA listing page and return article stubs.
    Reads only what is visible on the listing card: title, blurb, url.
    Returns [] on error or when the page has no articles.
    """
    url = BASE_URL.format(page=page_num)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [ERR] Could not fetch page {page_num}: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    articles = []

    # AHA renders each news item as an <article> tag or a heading+link pattern.
    # Try <article> elements first, then fall back to headings with links.
    cards = soup.find_all("div", class_="views-row")
    for card in cards:
        a = card.find("div", class_="views-field-title") and \
            card.find("div", class_="views-field-title").find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True)
        if not title:
            continue
        body_div = card.find("div", class_="views-field-body")
        blurb = body_div.find("div", class_="field-content").get_text(strip=True) \
                if body_div else ""
        href = a["href"]
        articles.append({
            "title": title,
            "body":  blurb,
            "url":   href if href.startswith("http") else ARTICLE_BASE + href,
        })

    return articles



def is_attack(article: dict) -> bool:
    """
    Ask Ollama whether this article describes a real cyberattack or breach.

    ISOLATED — only this function needs to change when you swap Ollama for a
    different AI provider (OpenAI, Anthropic, etc.).

    Returns True  if the article is about a real attack/breach.
    Returns False for advisories, warnings, tips, podcasts, policy updates, etc.
    Raises ConnectionError if Ollama is not running.
    """
    prompt = f"""\
You are a healthcare cybersecurity analyst.
Read the article title and excerpt below and answer with ONLY "YES" or "NO".

Does this article report that a real cybersecurity attack, ransomware incident,
data breach, or hacking event ACTUALLY OCCURRED at a hospital, health system,
or healthcare-related organization?

Answer NO if the article is any of the following:
- A general security warning, advisory, or alert
- A tip, best-practice guide, or policy recommendation
- A podcast episode, interview, or opinion piece
- A government or agency announcement not tied to a specific incident
- News about legislation, regulations, or organizational updates

TITLE: {article.get('title', '')}
EXCERPT: {article.get('body', '')}

Answer (YES or NO only):"""

    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
    except requests.exceptions.ConnectionError:
        raise ConnectionError(
            "Could not reach Ollama. Make sure it is running: ollama serve"
        )

    if resp.status_code != 200:
        return False

    answer = resp.json().get("response", "").strip().upper()
    return answer.startswith("YES")



def flush_outputs(confirmed: list[dict], skipped: list[str]) -> None:
    """
    Write all collected articles to three consolidated files.
    Called once at the end of run() after all pages are processed.
    """
    # confirmed_articles.txt — one block per article, separated by dividers
    txt_path = OUTPUT_DIR / "confirmed_articles.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        for article in confirmed:
            header = (
                f"SOURCE: AHA Cybersecurity News\n"
                f"TITLE:  {article['title']}\n"
                f"URL:    {article['url']}\n"
                f"{'=' * 72}\n\n"
            )
            f.write(header + clean_text(article.get("body", "")) + "\n\n")
            f.write("-" * 72 + "\n\n")
    print(f"  [TXT]  {len(confirmed)} confirmed articles → {txt_path.name}")

    # confirmed_articles.json — JSON array of all confirmed dicts
    json_path = OUTPUT_DIR / "confirmed_articles.json"
    docs = [
        {
            "source":      "AHA Cybersecurity News",
            "title":       a["title"],
            "url":         a["url"],
            "body":        a.get("body", ""),
            "char_count":  len(a.get("body", "")),
            "ai_verified": True,
        }
        for a in confirmed
    ]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2, ensure_ascii=False)
    print(f"  [JSON] {len(confirmed)} confirmed articles → {json_path.name}")

    # skipped_titles.txt — one title per line
    skip_path = OUTPUT_DIR / "skipped_titles.txt"
    with open(skip_path, "w", encoding="utf-8") as f:
        f.write("\n".join(skipped))
    print(f"  [SKIP] {len(skipped)} skipped titles → {skip_path.name}")



def run():
    manifest          = []
    confirmed_articles: list[dict] = []
    skipped_titles:     list[str]  = []

    print(f"\n{'=' * 60}")
    print("AHA Cybersecurity News — Scraper + AI Classifier")
    print(f"Output: {OUTPUT_DIR.resolve()}")
    print(f"{'=' * 60}\n")

    for page_num in range(PAGES):           # outer loop: 25 listing pages
        print(f"\n── Page {page_num} ──────────────────────────────────────")
        articles = scrape_page(page_num)

        if not articles:
            print(f"  No articles found. Stopping early.")
            break

        print(f"  Found {len(articles)} articles.")

        for article in articles:            # inner loop: each card on the page
            print(f"\n  {article['title'][:70]}")

            try:
                result = is_attack(article)
            except ConnectionError as e:
                print(f"\n  ERROR: {e}")
                print("  Stopping — restart Ollama and try again.\n")
                break

            if result:
                confirmed_articles.append(article)
                print(f"  [YES]  Confirmed attack")
                manifest.append({
                    "title":  article["title"],
                    "url":    article["url"],
                    "status": "confirmed",
                })
            else:
                skipped_titles.append(article["title"])
                print(f"  [NO]   Skipped")
                manifest.append({
                    "title":  article["title"],
                    "url":    article["url"],
                    "status": "skipped",
                })

        time.sleep(1)                       # one pause per page, not per article

    # Write all consolidated output files at once
    flush_outputs(confirmed_articles, skipped_titles)

    # Write manifest
    manifest_path = OUTPUT_DIR / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n{'=' * 60}")
    print(f"Done.")
    print(f"  Confirmed : {len(confirmed_articles)}")
    print(f"  Skipped   : {len(skipped_titles)}")
    print(f"  Manifest  : {manifest_path}")
    print(f"{'=' * 60}\n")

    return manifest


if __name__ == "__main__":
    run()
