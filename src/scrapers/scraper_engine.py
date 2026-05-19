"""Streamlines the scraping and processing of data

This module is the engine responsible for web scraping and data extraction from various sources, particularly websites and RSS feeds.
It handles fetching, parsing, and processing of data, while organizing outputs into structured formats suitable for further use or analysis.

Attributes:
    - 'SITES_TO_SCRAPE': A list of dictionaries defining the websites to be scraped, including their type (rss or html), name, URL, and mapping configuration.
    - 'READY_FOR_RAG_DIR': The directory where processed data is stored, organized by subdirectories for each website.
    - 'NOISE_DIR': The directory where scraping errors and other non-critical data is stored.
    - 'VULNERABILITIES_DIR': The directory where vulnerabilities are stored, organized by subdirectories for each website.
    - 'HEADERS': HTTP headers to be used in HTTP requests.
    - 'SUBSECTOR_FIELDS': A dictionary mapping subsector names to a list of fields that should be extracted from the scraped data.
    - 'Fetchers': A dictionary mapping fetcher types (rss or html) to their corresponding fetcher functions.

Functions:
    - '_get_page': Fetches the content of a webpage using an HTTP GET request.
    - 'fetch_rss_page': Fetches and parses the RSS feed for a given page URL based on the provided site configuration.
    - 'fetch_rss_external_page': Fetches and processes articles from an external RSS feed.
    - 'fetch_html_page': Fetches and processes HTML pages based on the given site configuration and page URL.
    - 'build_page_url': Generates complete URLs for scraping by combining base URLs and query parameters.
    - 'run_scraper': Orchestrates the scraping process by iterating over the list of websites defined in SITES_TO_SCRAPE.
    - 'check_valid_file': Ensures that necessary directory structures and files exist for organizing data.
    - 'json_output': Appends processed data into a JSON file located in READY_FOR_RAG_DIR.
    - 'report_output': Generates a report summarizing the results of the scraping process, including metrics or other detailed information.
"""

import json
import uuid
import datetime
import csv
import requests
import time
from pathlib import Path
from bs4 import BeautifulSoup
from .ask_llm import ai_check_validation, find_subsector_fields

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
    # {
    #     "type": "rss",
    #     "name": "NPR_Shots",
    #     "url": "https://feeds.npr.org/1128/rss.xml",
    #     "map": {
    #         "container": "item",
    #         "title": "title",
    #         "link": "link",
    #         "body": "encoded",
    #         "published_date": "pubDate",
    #         "starting_page": 1,
    #         "cap": -1,
    #     },
    # },
    # {
    #     "type": "rss", #pagination doesnt work
    #     "name": "EndPoints_News",
    #     "url": "https://endpoints.news/feed/", #html https://endpoints.news/news/
    #     "map": {
    #         "container": "item",
    #         "title": "title",
    #         "link": "link",
    #         "body": "description",  # NOTE: full articles require a subscription
    #         "published_date": "pubDate",
    #         "starting_page": 1,
    #         "cap": -1,
    #     },
    # },
    # {
    #     "type": "rss",
    #     "name": "AIScoop",
    #     "url": "https://aiscoop.com/feed/",
    #     "map": {
    #         "container": "item",
    #         "title": "title",
    #         "link": "link",
    #         "body": "encoded",
    #         "published_date": "pubDate",
    #         "starting_page": 1,
    #         "cap": -1,
    #     },
    # },
    # {
    #     "type": "html",
    #     "name": "MedicalNewsToday",
    #     "url": "https://www.medicalnewstoday.com/news",
    #     "map": {
    #         "container": "a[href^='https://www.medicalnewstoday.com/articles/']",
    #         "title": None,
    #         "link_selector": None,
    #         "body_selector": "article",
    #         "page_param": "page",
    #         "starting_page": 1,
    #         "cap": -1,
    #     },
    # },
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


def _get_page(url):
    """
    Fetches the content of a webpage using an HTTP GET request.

    Parameters:
        url (str): The URL of the webpage to fetch.

    Returns:
        requests.Response: The response object containing the webpage content.

    Raises:
        requests.exceptions.RequestException: If the request failed or the server returned a bad status code.
    """
    resp = requests.get(url, timeout=15, headers=HEADERS)
    resp.raise_for_status()
    return resp


def fetch_rss_page(site_config, page_url):
    """
    Fetches and parses the RSS feed for a given page URL based on the provided site configuration.
    Runs once and grabs all articles from the single page.

    Arguments:
        site_config (dict): A dictionary containing the mapping configuration for the RSS feed. The configuration should specify how to extract title, link, body, and optionally the published date from the RSS feed.
        page_url (str): The URL of the page whose RSS feed is to be fetched and parsed.

    Returns:
        list: A list of dictionaries where each dictionary represents an article or item extracted from the RSS feed. Each dictionary contains the following keys:
            - "title": The title of the article.
            - "link": The URL of the article.
            - "body": The plain text representation of the article content.
            - "date": The published date of the article, if available. Returns an empty string if the date is not found in the RSS feed.
    """
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


def fetch_rss_external_page(site_config, page_url):
    """
    Fetches and processes articles from an external RSS feed.

    This function retrieves an external RSS page based on the provided site configuration and page URL, parses the content,
    and extracts articles' details such as title, link, publication date, and body text.
    Articles with available body content are collected and returned in a structured format.

    Parameters:
        site_config (dict): A dictionary containing the site configuration, specifically the mapping information ('map') used to locate and extract elements from the RSS feed and article content.
        page_url (str): The URL of the RSS page to fetch.

    Returns:
        list: A list of dictionaries where each dictionary represents an article extracted from the RSS feed. Each dictionary contains the following keys:
            - "title": The title of the article.
            - "link": The URL of the article.
            - "body": The plain text representation of the article content, fetched from the linked page.
            - "date": The published date of the article, if available. Returns an empty string if the date is not found in the RSS feed.

    Raises:
        Exception: May raise exceptions during network requests, parsing, or processing individual articles.
        Certain exceptions related to article body fetching will only trigger warnings instead of halting execution.
    """
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


def fetch_html_page(site_config, page_url):
    """
    Fetches and processes HTML pages based on the given site configuration and page URL.

    This function scrapes a given web page for article links, their titles, and corresponding content.
    It uses the specified configurations to locate and filter the required elements on the page.
    For each identified article, it fetches the link, extracts its title, and retrieves its body content from the respective page.

    Args:
        site_config (dict): Configuration dictionary containing the mapping and site-specific selectors:
            - "map": Dictionary with keys for "container", "link_selector", "title", and "body_selector" to specify the HTML structure.
            - "url": Base URL of the site, used for resolving relative links.
        page_url (str): URL of the page to scrape for article links.

    Returns:
        list: A list of dictionaries representing scraped articles, where each dictionary contains:
            - "title" (str): The title of the article.
            - "link" (str): The full URL of the article.
            - "body" (str): Text content of the article's body.
            - "date" (str): Placeholder for article publication date (currently always an empty string).

    Notes:
        - Links without 'http' are treated as relative and resolved using the base URL.
        - If no valid links are found in the specified container, returns an empty list.
        - Includes a delay (0.5 seconds) between fetching individual article pages to respect server load.
        - Exceptions or errors during fetching a particular article's body are logged as warnings, and the article body is left empty.
    """
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


def build_page_url(site_config, current_page, starting_page):
    """
    Builds a URL for a page based on the site configuration and current pagination.

    Parameters:
        site_config (dict): A dictionary containing the site configuration.
        Must include "url" key. Optional keys include "type" ("rss" or "rss_external"), and "map" (dict with "page_param").
        current_page (int): The page number of the current request.
        starting_page (int): The starting page number for the site (usually 1).

    Returns:
        str: The constructed URL for the given page based on the site configuration.
    """
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


def run_scraper(site_config):
    """
    Executes the scraping process for a given site configuration.

    site_config (dict): A dictionary containing the site's configuration, which includes:
        - type: The type of the site to scrape (default is "rss").
        - name: The name of the site.
        - map: A dictionary with keys:
            - starting_page: The starting page number for scraping.
            - cap: The maximum page number to scrape, or -1 for unlimited.

    The function retrieves the appropriate fetcher for the site type and validates the site's configuration file.
    It iterates through pages starting from 'starting_page' until the 'cap' is reached or no articles are found.

    For each page:
    - Fetches articles using the provided fetcher.
    - Processes each article by checking for potential threats using an AI model.
    - Outputs the article details and validation results in both JSON and report formats.

    If an error occurs during fetching or no articles are retrieved from the current page, the function will terminate or proceed accordingly.

    Sleep is applied between each page fetch to mitigate overloading external services.
    """
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


def check_valid_file(title):
    """
    Checks and ensures the necessary directory and file structures exist for a given title.
    It creates JSON and CSV files for organizing and storing data if they do not already exist.
    [title].json under the valid data/valid and [title].txt under the invalid data/invalid.

    Parameters:
        title (str): The title used to construct the names of the files to be checked and created.

    Behavior:
    - Ensures the directories `READY_FOR_RAG_DIR`, `NOISE_DIR`, and `VULNERABILITIES_DIR` exist.
    - Checks if a JSON file with the specified title exists in `READY_FOR_RAG_DIR`. If absent, creates the file with a default structure.
    - Checks if a CSV file with the specified title exists in each of `NOISE_DIR` and `VULNERABILITIES_DIR`. If absent, creates the files with predefined headers.

    Side Effects:
    - Creates directories if they do not exist.
    - Creates and writes to files as necessary.
    - Prints messages when a file is newly created.
    """
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


def json_output(site_name, title, url, body, subsector):
    """
    Generates and appends structured JSON data to a file for a given site.

    This function takes in information about a source, such as its site name, title, URL, body content, and subsector, and appends it to a pre-existing JSON file.
    The JSON data includes metadata like publication and access dates, unique identifiers, and processed subsector-related information.

    Parameters:
        site_name (str): Name of the source site as a string
        title (str): Title of the source content as a string
        url (str): URL of the source content as a string
        body (str): The main content of the source as a string
        subsector (str): Specific subsector associated with the source as a string

    Intermediate actions:
    - Parses an existing JSON file associated with the given source site.
    - Appends a newly created dictionary with metadata and processed data fields.
    - Handles subsector-related fields through specific processing logic.
    - Writes back the updated JSON data to the respective file.

    Notes:
    - Each entry is appended with a unique ID generated using UUID.
    - The access and publication dates are set to the current date and time.

    Exceptional scenarios:
    - Ensures proper handling of subsector data using pre-defined logic.
    - Writes the updated JSON data back to the file with proper encoding and formatting.
    """
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


def report_output(is_threat, site_name, title, url, body, reason):
    """
    Saves a report of a detected issue or non-issue to a CSV file in the appropriate directory.

    Parameters:
        is_threat (bool): Indicates if the detected issue is a threat.
        If True, the report is saved in the VULNERABILITIES_DIR, otherwise in NOISE_DIR.
        site_name (str): The name of the site related to the issue or non-issue.
        title (str): The title of the issue or non-issue report.
        url (str): The URL of the detected issue or non-issue.
        body (str): The body content of the detected issue or non-issue. It is truncated to the first 200 characters and newlines are replaced by spaces.
        reason (str): The reason or description explaining the detection.

    The function creates or appends to a CSV file named after the site_name in the designated directory.
    The CSV row contains a timestamp, threat indicator, reason, title, URL, and truncated body content.
    """
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
