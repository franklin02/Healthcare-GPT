import time
import datetime
import uuid
from pathlib import Path
import sys as _sys
from urllib.parse import urlparse
from bs4 import BeautifulSoup

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))

from src.shared_utils import (
    ai_check_validation,
    extract_fields,
    get_page,
    check_valid_file,
    is_known_article,
    prepend_vuln_csv,
    prepend_noise_csv,
    prepend_json_sources,
)
from src.classes import Vulnerability, SUBSECTOR_DATA_CLASSES


SUBSECTOR_FIELDS = [
    "drug_shortage",
    "medical_device_shortage",
    "cyber_attack",
    "natural_disaster",
    "other",
]
HTML_SITES = [
    {
        "name": "CyberScoop",
        "url": "https://cyberscoop.com/?s=&topic=healthcare&content-type=",
        "pagination_url": "https://cyberscoop.com/page/{page}/?s=&topic=healthcare&content-type=",
        "map": {
            "container": "li.search-results__item",
            "title": None,
            "link_selector": "a.post-item__title-link",
            "body_selector": "div.single-article__content",
            "date_selector": "time[datetime]",
            "starting_page": 1,
            "cap": 10,
        },
    },
    {
        "name": "StateScoop",
        "url": "https://statescoop.com/search/healthcare/page/1/",
        "pagination_url": "https://statescoop.com/search/healthcare/page/{page}/",
        "map": {
            "container": "article.post-item",
            "title": None,
            "link_selector": "a.post-item__title-link",
            "body_selector": "div.single-article__content",
            "date_selector": "time[datetime]",
            "starting_page": 1,
            "cap": 7,
        },
    },
    {
        "name": "FedScoop",
        "url": "https://fedscoop.com/search/healthcare/",
        "pagination_url": "https://fedscoop.com/search/healthcare/page/{page}/",
        "map": {
            "container": "article.post-item",
            "title": None,
            "link_selector": "a.post-item__title-link",
            "body_selector": "div.single-article__content",
            "date_selector": "time[datetime]",
            "starting_page": 1,
            "cap": 18,
        },
    },
    {
        "name": "MedicalNewsToday",
        "url": "https://www.medicalnewstoday.com/news",
        "pagination_url": "https://www.medicalnewstoday.com/news",  # this cite doesnt have pagination
        "map": {
            "container": "ol li",
            "title": None,
            "link_selector": "a:has(h2)",
            "body_selector": "article.article-body",
            "date_selector": "",
            "starting_page": 1,
            "cap": 1,
        },
    },
    {
        "name": "AHA",
        "url": "https://www.aha.org/news",
        "pagination_url": "https://www.aha.org/news?page=%2C{page}",
        "map": {
            "container": "section.views-latest-feed div.views-row",
            "title": None,
            "link_selector": "div.views-field-title span.field-content a",
            "body_selector": "article .body",
            "date_selector": "time[datetime]",
            "starting_page": 0,
            "cap": 10,
        },
    },
    {
        "name": "HealthIT_News",
        "url": "https://www.techtarget.com/news/health-it",
        "pagination_url": "https://www.techtarget.com/news/health-it/page/{page}",
        "map": {
            "container": "div.topic-related-item-info",
            "title": None,
            "link_selector": "h3 a",
            "body_selector": "article#content-columns",
            "date_selector": "",
            "starting_page": 1,
            "cap": 9,
        },
    },
]


def fetch_html_page(site_config, page_url):
    response = get_page(page_url)
    soup = BeautifulSoup(response.content, "html.parser")

    m = site_config["map"]
    link_elements = soup.select(m["container"])
    if not link_elements:
        print(
            f"[SELECTOR MISS] container '{m['container']}' matched 0 elements on {page_url}"
            f" — check your container selector in HTML_SITES"
        )
        return [], False

    # Creates a set of valid articles with their respective links
    seen_urls = set()
    raw_links = []
    for el in link_elements:
        if m.get("link_selector"):
            a_tag = el.select_one(m["link_selector"])
        else:
            a_tag = el if el.name == "a" else el.select_one("a[href]")

        if not a_tag:
            print(
                f"[SELECTOR MISS] link_selector '{m.get('link_selector')}' found no anchor"
                f" in a '{m['container']}' item — skipping"
            )
            continue

        if not a_tag.get("href"):
            continue

        href = a_tag["href"]
        if not href.startswith("http"):
            parsed = urlparse(site_config["url"])
            href = f"{parsed.scheme}://{parsed.netloc}{href}"

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

    body_selector = m[
        "body_selector"
    ]  # NOTE:  this may need to be moved up to the for loop
    date_selector = m.get("date_selector", "")

    # For each article found, we go to that specific link and grab the body and date (if applicable)
    articles = []
    stop = False
    for entry in raw_links:
        try:
            article_resp = get_page(entry["link"])
            article_soup = BeautifulSoup(article_resp.content, "html.parser")

            body_el = article_soup.select_one(body_selector)
            if not body_el:
                print(
                    f"[SELECTOR MISS] body_selector '{body_selector}' matched nothing on"
                    f" {entry['link']} — skipping article. Update body_selector in HTML_SITES config."
                )
                time.sleep(0.5)
                continue

            body = body_el.get_text(separator=" ", strip=True)

            if is_known_article(site_config["name"], entry["title"], body):
                print(
                    f"[FINISH] Reached known article on {site_config['name']}: "
                    f"{entry['title']!r}"
                )
                stop = True
                break

            date_el = article_soup.select_one(date_selector) if date_selector else None
            date = date_el.get("datetime", "") if date_el else ""

            time.sleep(0.5)
        except Exception as e:
            print(f"[WARNING] Could not fetch article body at {entry['link']}: {e}")
            continue

        articles.append(
            {
                "title": entry["title"],
                "link": entry["link"],
                "body": body,
                "date": date,
            }
        )

    return articles, stop


def run_html_scraper(site_config):
    print(f"--- Scraping for {site_config['name']} has started ---")
    check_valid_file(site_config["name"])

    starting_page = site_config["map"]["starting_page"]
    cap = site_config["map"]["cap"]
    current_page = starting_page

    """
    Buffer this run's new vulns + CSV rows so we can prepend them in one shot
    at the end. Order in these lists is newest-first because pagination
    progresses oldest-page-last and each page lists articles newest-first.
    """
    new_vulns: list[Vulnerability] = []
    new_rows: list[list[str]] = []
    new_noise_rows: list[list[str]] = []

    while True:
        if cap != -1 and current_page > cap:
            print(f"[FINISHED] Reached cap of {cap} for {site_config['name']}")
            break

        if current_page == starting_page:
            page_url = site_config["url"]
        else:
            pagination_url = site_config.get("pagination_url")
            if pagination_url:
                page_url = pagination_url.replace("{page}", str(current_page))
            else:
                page_param = site_config["map"].get("page_param", "page")
                sep = "&" if "?" in site_config["url"] else "?"
                page_url = f"{site_config['url']}{sep}{page_param}={current_page}"

        try:
            articles, stop = fetch_html_page(site_config, page_url)
        except Exception as e:
            print(
                f"[ERROR] Fetching {site_config['name']} page {current_page} ({page_url}): {e}"
            )
            return

        if not articles:
            print(
                f"[WARNING] No articles found on page {current_page} — stopping pagination"
            )
            break

        for article in articles:
            is_threat, detail = ai_check_validation(article["title"], article["body"])
            if is_threat:
                if detail not in SUBSECTOR_FIELDS:
                    print(
                        f"[WARNING] Unrecognized subsector '{detail}' — skipping: {article['title']}"
                    )
                    continue
                sector_data, ss_data = extract_fields(
                    detail, article["title"], article["body"]
                )

                # Wrap the raw dict from the LLM in the matching SubsectorData
                # subclass so Vulnerability.to_dict() can call .to_dict() on it.
                subsector_cls = SUBSECTOR_DATA_CLASSES.get(detail)
                subsector_data = (
                    subsector_cls.from_dict(ss_data) if subsector_cls else None
                )

                vuln = Vulnerability(
                    id=str(uuid.uuid4()),
                    title=article["title"],
                    source_name=site_config["name"],
                    direct_link=article["link"],
                    subsector=detail,
                    date_accessed=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                    date_published=article.get("date", ""),
                    content=article["body"],
                    exec_summary=sector_data.get("exec_summary") or "",
                    geography_scope=sector_data.get("geography_scope"),
                    start_date=sector_data.get("start_date"),
                    end_date=sector_data.get("end_date"),
                    resilience_or_mitigation_observed=sector_data.get(
                        "resilience_or_mitigation_observed"
                    ),
                    subsector_data=subsector_data,
                )

                content_preview = (vuln.content or "")[:250].replace("\n", " ")
                new_rows.append(
                    [
                        vuln.date_accessed,
                        vuln.date_published,
                        vuln.source_name,
                        vuln.subsector,
                        vuln.title,
                        vuln.direct_link,
                        vuln.exec_summary,
                        content_preview,
                    ]
                )
                new_vulns.append(vuln)
                print(f"[VALID] ({vuln.subsector}): {vuln.title}")
            else:
                body_preview = (article["body"] or "")[:250].replace("\n", " ")
                new_noise_rows.append(
                    [
                        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        site_config["name"],
                        article["title"],
                        article["link"],
                        detail,
                        body_preview,
                    ]
                )

        if stop:
            break

        current_page += 1
        time.sleep(0.5)

    prepend_vuln_csv(site_config["name"], new_rows)
    prepend_noise_csv(site_config["name"], new_noise_rows)
    prepend_json_sources(site_config["name"], new_vulns)
    print(
        f"--- Finished {site_config['name']}: "
        f"{len(new_vulns)} vuln(s) + {len(new_noise_rows)} noise prepended ---"
    )


if __name__ == "__main__":
    for site in HTML_SITES:
        run_html_scraper(site)
