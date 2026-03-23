import time
import requests
from html.parser import HTMLParser

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"
MAX_CHARS    = 3000
FETCH_DELAY  = 1.0  # seconds between article fetches

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

PROMPT_TEMPLATE = """\
You are a healthcare cybersecurity analyst reviewing article excerpts.
Answer with ONLY "YES" or "NO".

Does this content provide evidence that a cybersecurity incident (ransomware, \
data breach, hacking, phishing, or malware) targeted a hospital, clinic, \
healthcare organization, or medical device/system used in healthcare, \
anywhere in the United States or US territories?

Consider YES if:
- A named hospital, health system, or medical company was attacked
- Patient data was exposed or stolen
- Hospital operations were disrupted by a cyber incident
- Medical devices or healthcare software were compromised

Consider NO if:
- This is a job posting, graduation announcement, or general security tips
- The attack had no connection to healthcare
- The content is clearly unrelated (sports, politics, entertainment)
- The page appears to be a login wall or error page

URL: {url}
Content:
{text}

Answer (YES or NO only):"""


# ── HTML stripping ────────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "head", "nav", "footer", "aside"}

    def __init__(self):
        super().__init__()
        self._skip  = 0
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self._SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in self._SKIP_TAGS and self._skip > 0:
            self._skip -= 1

    def handle_data(self, data):
        if self._skip == 0:
            stripped = data.strip()
            if stripped:
                self._parts.append(stripped)

    def get_text(self) -> str:
        return " ".join(self._parts)


"""
Fetch a URL and return plain text, truncated to MAX_CHARS.
Returns None on any network or HTTP error.
"""
def fetch_article_text(url: str) -> str | None:

    try:
        resp = requests.get(url, timeout=10, headers=HEADERS, allow_redirects=True)
        if resp.status_code != 200:
            return None
        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            return None
        parser = _TextExtractor()
        parser.feed(resp.text)
        text = parser.get_text()
        return text[:MAX_CHARS] if text else None
    except Exception:
        return None

"""
Ask Ollama (change to chat l8er) whether the article is about a US hospital cyberattack.
Returns True if AI answers YES, False otherwise.
Raises ConnectionError if AI is not running.
"""
def ask_ai(url: str, text: str) -> bool:

    prompt = PROMPT_TEMPLATE.format(url=url, text=text)
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

"""
Filter a list of candidate URLs using Ollama.

For each URL:
    1. Fetch the article text.
    2. Ask Ollama if it is about a US hospital cyberattack.
    3. Keep only the URLs where Ollama answers YES.

Prints progress for every URL processed.
Returns the confirmed list.
"""
def filter_with_ollama(urls: list[str]) -> list[str]:

    if not urls:
        return []

    total     = len(urls)
    confirmed = []

    print(f"\nRunning AI filter on {total} candidate URLs...")
    print(f"Model: {OLLAMA_MODEL}  |  Max chars per article: {MAX_CHARS}\n")

    for i, url in enumerate(urls, start=1):
        prefix = f"[{i:>{len(str(total))}}/{total}]"

        text = fetch_article_text(url)
        if text is None:
            print(f"{prefix} SKIP (fetch failed)  {url}")
            time.sleep(FETCH_DELAY)
            continue
        #print(f"  DEBUG text preview: {text[:200]}") # remove 


        try:
            result = ask_ai(url, text)
        except ConnectionError as e:
            print(f"\n  ERROR: {e}")
            print("  Stopping filter — restart Ollama and try again.\n")
            break

        label = "YES " if result else "NO  "
        print(f"{prefix} {label}  {url}")

        if result:
            confirmed.append(url)

        time.sleep(FETCH_DELAY)

    print(f"\nOllama filter complete: {len(confirmed)} / {total} confirmed.\n")
    return confirmed
