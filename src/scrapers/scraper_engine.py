import json
import uuid
import datetime
import csv
import requests
import time
from pathlib import Path
from bs4 import BeautifulSoup
from ask_llm import ai_check_validation, find_subsector_fields

AI_URL = "http://localhost:11434/api/generate"
AI_MODEL = "llama3.2"
READY_FOR_RAG_DIR = Path(__file__).parent.parent / "data" / "Ready_for_RAG"
NOISE_DIR = Path(__file__).parent.parent / "data" / "Noise"
VULNERABILITIES_DIR = Path(__file__).parent.parent / "data" / "Vulnerabilities"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
SUBSECTOR_FIELDS = {
    "drug_shortage": [
        "drug_name",
        "generic_name",
        "manufacturer",
        "dosage_form",
        "shortage_reason",
        "estimated_resolution_date",
        "affected_regions",
    ],
    "medical_device_shortage": [
        "device_name",
        "device_category",
        "manufacturer",
        "manufacturer_country",
        "shortage_reason",
        "fda_recall_number",
        "recall_class",
        "affected_specialties",
        "alternatives_available",
        "estimated_resolution_date",
    ],
    "cyber_attack": [
        "attack_type",
        "threat_actor",
        "individuals_affected",
        "data_types_exposed",
        "systems_affected",
        "ransom_demanded_usd",
        "ransom_paid",
        "downtime_days",
        "services_disrupted",
        "law_enforcement_involved",
        "hhs_breach_portal_listed",
    ],
    "natural_disaster": [
        "disaster_type",
        "disaster_name",
        "fema_declaration_id",
        "category_magnitude",
        "affected_facilities_count",
        "evacuation_ordered",
        "field_hospitals",
        "beds_offline",
        "facility_status",
        "estimated_damage_usd",
        "infrastructure_damage",
        "services_disrupted",
    ],
    "other": [
        "event_type",
        "event_description",
        "severity",
        "departments_affected",
        "staff_type_affected",
        "beds_offline",
        "services_disrupted",
        "regulatory_response",
    ],
}
SITES_TO_SCRAPE = [
    {
        "type": "rss",
        "name": "CyberScoop",
        "url": "https://cyberscoop.com/news/healthcare/feed/",
        "map": {
            "container": "item",
            "title": "title",
            "link": "link",
            "body": "encoded",
            "published_date": "pubDate",
            "starting_page": 1,
            "cap": 3,
        },
    },
    {
        "type": "rss_external",
        "name": "StateScoop",
        "url": "https://statescoop.com/tag/healthcare/feed/",
        "map": {
            "container": "item",
            "title": "title",
            "link": "link",
            "published_date": "pubDate",
            "body_selector": "main article",
            "starting_page": 1,
            "cap": -1,
        },
    },
    {
        "type": "rss",
        "name": "FedScoop",
        "url": "https://fedscoop.com/tag/healthcare/feed/",
        "map": {
            "container": "item",
            "title": "title",
            "link": "link",
            "body": "encoded",
            "published_date": "pubDate",
            "starting_page": 1,
            "cap": -1,
        },
    },
    {
        "type": "html",
        "name": "HealthITSecurity",
        "url": "https://www.techtarget.com/healthtechsecurity/",
        "map": {
            "container": "a[href*='/healthtechsecurity/news/']",
            "title": None,
            "link_selector": None,
            "body_selector": "article",
            "starting_page": 1,
            "cap": 1,
        },
    },
]


"""
This function is a shared HTTP GET with a User-Agent header to blend 
in with normal traffic and avoid getting blocked by the website.
"""


def _get_page(url):
    resp = requests.get(url, timeout=15, headers=HEADERS)
    resp.raise_for_status()
    return resp


"""
This function parses a full XML/RSS feed and retuns a list of normalised articles.
A normalized article is {"title": title, "link": link, "body": body, "date": date}
This runs once and grabs all articles from the single page. 
"""


def fetch_rss_page(site_config, page_url):
    response = _get_page(page_url)
    soup = BeautifulSoup(response.content, "lxml-xml")
    items = soup.find_all(site_config["map"]["container"])
    if not items:
        return []

    articles = []
    m = site_config["map"]
    for item in items:
        title = item.find(m["title"]).text
        link = item.find(m["link"]).text
        raw_body = item.find(m["body"]).text
        body = BeautifulSoup(raw_body, "lxml").get_text(separator=" ", strip=True)
        date = (
            item.find(m.get("published_date", "")).text
            if item.find(m.get("published_date", ""))
            else ""
        )
        articles.append({"title": title, "link": link, "body": body, "date": date})
    return articles


"""
Similar to fetch_rss_page, but for external RSS feeds. This would be for pages who have an RSS page
that has a title and link, but the body is on a different page. This then grabs the html body from the link
and returns a list of normalised articles. Same concept {"title": title, "link": link, "body": body, "date": date}
"""


def fetch_rss_external_page(site_config, page_url):
    response = _get_page(page_url)
    soup = BeautifulSoup(response.content, "lxml-xml")
    items = soup.find_all(site_config["map"]["container"])
    if not items:
        return []

    body_selector = site_config["map"]["body_selector"]
    articles = []
    m = site_config["map"]
    for item in items:
        title = item.find(m["title"]).text
        link = item.find(m["link"]).text
        date = (
            item.find(m.get("published_date", "")).text
            if item.find(m.get("published_date", ""))
            else ""
        )

        body = ""
        try:
            article_resp = _get_page(link)
            article_soup = BeautifulSoup(article_resp.content, "html.parser")
            body_el = article_soup.select_one(body_selector)
            if body_el:
                body = body_el.get_text(separator=" ", strip=True)
            time.sleep(0.5)
        except Exception as e:
            print(f"  Warning: could not fetch article body at {link}: {e}")

        if body:
            articles.append({"title": title, "link": link, "body": body, "date": date})
    return articles


"""
This function scrapes a pure-HTML listing page for article links, then fetches each body.
Same concept {"title": title, "link": link, "body": body, "date": date}
"""


def fetch_html_page(site_config, page_url):
    response = _get_page(page_url)
    soup = BeautifulSoup(response.content, "html.parser")

    m = site_config["map"]
    link_elements = soup.select(m["container"])
    if not link_elements:
        return []

    seen_urls = set()
    raw_links = []
    for el in link_elements:
        if m.get("link_selector"):
            a_tag = el.select_one(m["link_selector"])
        else:
            a_tag = el if el.name == "a" else el.select_one("a[href]")

        if not a_tag or not a_tag.get("href"):
            continue

        href = a_tag["href"]
        if not href.startswith("http"):
            href = site_config["url"].rstrip("/") + href

        if href in seen_urls:
            continue
        seen_urls.add(href)

        title_text = ""
        if m.get("title"):
            title_el = el.select_one(m["title"])
            title_text = title_el.get_text(strip=True) if title_el else ""
        if not title_text:
            title_text = a_tag.get_text(strip=True)

        if title_text:
            raw_links.append({"title": title_text, "link": href})

    body_selector = m["body_selector"]
    articles = []
    for entry in raw_links:
        try:
            article_resp = _get_page(entry["link"])
            article_soup = BeautifulSoup(article_resp.content, "html.parser")
            body_el = article_soup.select_one(body_selector)
            body = body_el.get_text(separator=" ", strip=True) if body_el else ""
            time.sleep(0.5)
        except Exception as e:
            print(f"  Warning: could not fetch article body at {entry['link']}: {e}")
            body = ""

        if body:
            articles.append(
                {
                    "title": entry["title"],
                    "link": entry["link"],
                    "body": body,
                    "date": "",
                }
            )
    return articles


"""
This function builds the page url for the site.
It handles the pagination differences between types 
eg, it might go from RSS to HTML, this handles that pagination differnce
"""


def build_page_url(site_config, current_page, starting_page):
    if current_page == starting_page:
        return site_config["url"]

    site_type = site_config.get("type", "rss")
    if site_type in ("rss", "rss_external"):
        return f"{site_config['url']}?paged={current_page}"

    page_param = site_config["map"].get("page_param", "page")
    return f"{site_config['url']}?{page_param}={current_page}"


FETCHERS = {
    "rss": fetch_rss_page,
    "rss_external": fetch_rss_external_page,
    "html": fetch_html_page,
}


"""
This function reads the type field from the config, picks the right fetcher function from the FETCHERS dict, 
then runs a page-by-page while True loop. Each iteration it builds a URL, calls the fetcher, gets back a list 
of article dicts, and runs each one through AI validation and output.
"""


def run_scraper(site_config):
    site_type = site_config.get("type", "rss")
    fetcher = FETCHERS.get(site_type)
    if not fetcher:
        print(f"Unknown site type '{site_type}' for {site_config['name']}")
        return

    print(f"--- Scraping for {site_config['name']} ({site_type}) has started ---")
    check_valid_file(site_config["name"])

    starting_page = site_config["map"]["starting_page"]
    cap = site_config["map"]["cap"]
    current_page = starting_page

    while True:
        if cap != -1 and current_page > cap:
            print(f"Reached cap of {cap} for {site_config['name']}")
            break

        page_url = build_page_url(site_config, current_page, starting_page)

        try:
            articles = fetcher(site_config, page_url)
        except Exception as e:
            print(f"Error fetching {site_config['name']} page {current_page}: {e}")
            return

        if not articles:
            print(f"No articles found on page {current_page}")
            break

        for article in articles:
            # NOTE: Eventually we will need to see if the article is something we have already seen
            is_threat, detail = ai_check_validation(article["title"], article["body"])
            if is_threat:
                json_output(
                    site_config["name"],
                    article["title"],
                    article["link"],
                    article["body"],
                    detail,
                )
            report_output(
                is_threat,
                site_config["name"],
                article["title"],
                article["link"],
                article["body"],
                detail,
            )

        current_page += 1
        time.sleep(1)


"""
This function checks to see that we have 2 valid files in the data directory.
One being [title].json under the valid data/valid and the other being [title].txt under the invalid data/invalid.
This will automatically add the files if they are not found
"""


def check_valid_file(title):
    READY_FOR_RAG_DIR.mkdir(parents=True, exist_ok=True)
    NOISE_DIR.mkdir(parents=True, exist_ok=True)
    VULNERABILITIES_DIR.mkdir(parents=True, exist_ok=True)

    json_path = READY_FOR_RAG_DIR / f"{title.strip()}.json"
    if not json_path.exists():
        json_path.write_text(json.dumps({"sources": []}, indent=4), encoding="utf-8")
        print(f"Created {json_path}")

    # these are the columns that will be written to the CSV files, we may need to add more later
    headers = [
        "date_accessed",
        "is_threat",
        "subsector",
        "title",
        "url",
        "body_preview",
    ]

    noise_path = NOISE_DIR / f"{title.strip()}.csv"
    if not noise_path.exists():
        with open(noise_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)
        print(f"Created {noise_path}")

    vulnerabilities_path = VULNERABILITIES_DIR / f"{title.strip()}.csv"
    if not vulnerabilities_path.exists():
        with open(vulnerabilities_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)
        print(f"Created {vulnerabilities_path}")


"""
This function will write to src/data/Ready_for_RAG/[site_name].json
It will write all fields that we have found and leave a "" for all other fields not found.
"""


def json_output(site_name, title, url, body, subsector):
    print(f"[VALID] {title} | {url}")  # makes easy to see, delete later
    json_path = READY_FOR_RAG_DIR / f"{site_name.lower()}.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["sources"].append(
        {
            "id": str(uuid.uuid4()),
            "title": title,
            "source_name": site_name,
            "direct_link": url,
            "subsector": subsector,
            "date_accessed": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "date_published": datetime.datetime.now().strftime(
                "%Y-%m-%d %H:%M"
            ),  # TODO: add a published date to the SITES_TO_SCRAPE
            "content": body,
            "exec_summary": "",  # TODO: add a fucntion for this in ask_llm.py
            # if there is no subsector just insert an empty dict {}
            # TODO: make subsector_data more peaceful when empty
            "subsector_data": find_subsector_fields(subsector, title, body)
            if subsector in SUBSECTOR_FIELDS
            else {},
        }
    )
    json_path.write_text(json.dumps(data, indent=4), encoding="utf-8")
    print(f"[VALID] ([{subsector}]: {title}")


"""
This function writes to a CSV file in src/data/[Noise OR Vulnerabilities]/[site_name].csv
It reports everything it sees here, to make it easier for us to review and evaluate the accuracy 
of the AI later. 
"""


def report_output(is_threat, site_name, title, url, body, reason):
    target_dir = VULNERABILITIES_DIR if is_threat else NOISE_DIR
    csv_path = target_dir / f"{site_name}.csv"
    row = [
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "YES" if is_threat else "NO",
        reason,
        title,
        url,
        body[:200].replace("\n", " "),
    ]
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(row)


# Run everything in one go
for site in SITES_TO_SCRAPE:
    run_scraper(site)
