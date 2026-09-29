from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
import re
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup


SOURCE_URL = "https://www.theblockbeats.info/"
OUTPUT_FILE = Path(__file__).with_name("feed.xml")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/134.0 Safari/537.36"
    )
}
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
ET.register_namespace("content", CONTENT_NS)


def text(element, default=""):
    return element.get_text(strip=True) if element else default


def absolutize_links(container, page_url):
    for tag in container.find_all(href=True):
        tag["href"] = urljoin(page_url, tag["href"])
    for tag in container.find_all(src=True):
        tag["src"] = urljoin(page_url, tag["src"])


def fetch_article_content(session, url, fallback):
    try:
        response = session.get(url, timeout=60)
        response.raise_for_status()
        response.encoding = "utf-8"
        article_soup = BeautifulSoup(response.text, "html.parser")
        content = article_soup.select_one(".flash-content, .news-content")
        if content:
            for unwanted in content.select("script, style, noscript"):
                unwanted.decompose()
            absolutize_links(content, url)
            body = content.decode_contents().strip()
            if body:
                return body

        description_tag = article_soup.find("meta", attrs={"name": "description"})
        if description_tag and description_tag.get("content"):
            return f"<p>{description_tag['content']}</p>"
    except requests.RequestException as exc:
        print(f"Could not fetch article content from {url}: {exc}")

    return f"<p>{fallback}</p>"


def build_feed():
    session = requests.Session()
    session.headers.update(HEADERS)
    response = session.get(SOURCE_URL, timeout=60)
    response.raise_for_status()
    response.encoding = "utf-8"
    page_html = response.text
    soup = BeautifulSoup(page_html, "html.parser")

    published_by_id = {
        article_id: int(timestamp)
        for article_id, timestamp in re.findall(
            r"article_id:(\d+)[^{}]{0,600}?add_time:(\d+)",
            page_html,
        )
    }

    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = text(soup.title, "BlockBeats")
    ET.SubElement(channel, "link").text = SOURCE_URL
    description_tag = soup.find("meta", attrs={"name": "description"})
    description = (
        description_tag.get("content", "") if description_tag else ""
    ) or "BlockBeats 律动新闻"
    ET.SubElement(channel, "description").text = description
    ET.SubElement(channel, "language").text = "zh-CN"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        datetime.now(timezone.utc)
    )
    ET.SubElement(channel, "generator").text = "blockbeats-rss"

    seen_links = set()
    entries = soup.select(".home-news-item-wrapper, .home-flow-item-item")
    for entry in entries:
        anchor = entry.select_one("h2 a") or entry.find("a")
        if not anchor or not anchor.get("href"):
            continue

        link = urljoin(SOURCE_URL, anchor["href"])
        if link in seen_links:
            continue
        seen_links.add(link)

        title = (anchor.get("title") or text(anchor)).strip()
        if not title:
            continue

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = title
        ET.SubElement(item, "link").text = link
        ET.SubElement(item, "guid", {"isPermaLink": "true"}).text = link
        article_html = fetch_article_content(session, link, title)
        article_html += (
            f'<p><a href="{link}" target="_blank" rel="noopener">查看原文</a></p>'
        )
        ET.SubElement(item, "description").text = article_html
        ET.SubElement(item, f"{{{CONTENT_NS}}}encoded").text = article_html

        article_match = re.search(r"/(?:flash|news)/(\d+)", anchor["href"])
        if article_match:
            timestamp = published_by_id.get(article_match.group(1))
            if timestamp:
                published = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                ET.SubElement(item, "pubDate").text = format_datetime(published)

    if not seen_links:
        raise RuntimeError("No BlockBeats articles were found")

    ET.indent(rss, space="  ")
    document = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        rss,
        encoding="unicode",
    )
    OUTPUT_FILE.write_text(document + "\n", encoding="utf-8")
    print(f"Updated {OUTPUT_FILE} with {len(seen_links)} items")


if __name__ == "__main__":
    build_feed()
