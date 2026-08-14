from __future__ import annotations

import html
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; adult-anime-feed/1.0; +https://github.com/zane6443/adult-anime-feed)"
TIMEOUT = 25

SOURCES = [
    {
        "name": "Lune Soft / Bunny Walker",
        "kind": "rss",
        "url": "https://www.lune-soft.jp/feed",
        "keywords": ["OVA", "アニメ", "発売", "新作", "ばにぃ", "Bunny"],
    },
    {
        "name": "Lune Soft / Bunny Walker OVA",
        "kind": "html",
        "url": "https://www.lune-soft.jp/ova/brand_ova/bunnywalker",
    },
    {
        "name": "Pink Pineapple",
        "kind": "html",
        "url": "https://www.pinkpineapple.co.jp/",
    },
    {
        "name": "PoRO",
        "kind": "html",
        "url": "https://www.poro.cc/",
    },
]


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def fetch(url: str) -> requests.Response:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def parse_rss(source):
    r = fetch(source["url"])
    feed = feedparser.parse(r.content)
    items = []
    kws = [k.lower() for k in source.get("keywords", [])]
    for e in feed.entries[:40]:
        title = clean_text(getattr(e, "title", ""))
        summary = clean_text(BeautifulSoup(getattr(e, "summary", ""), "html.parser").get_text(" "))
        blob = f"{title} {summary}".lower()
        if kws and not any(k.lower() in blob for k in kws):
            continue
        link = getattr(e, "link", source["url"])
        published = None
        if getattr(e, "published_parsed", None):
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        items.append({"title": title, "link": link, "summary": summary, "source": source["name"], "published": published})
    return items


def parse_html(source):
    r = fetch(source["url"])
    soup = BeautifulSoup(r.text, "html.parser")
    items = []

    candidates = soup.select("article, .post, .news, .item, .product, li")
    if not candidates:
        candidates = soup.find_all(["div", "section"])

    seen = set()
    for node in candidates[:300]:
        a = node.find("a", href=True)
        if not a:
            continue
        title = clean_text(a.get_text(" ", strip=True))
        if len(title) < 4:
            continue
        text = clean_text(node.get_text(" ", strip=True))
        href = urljoin(source["url"], a.get("href"))
        key = (title, href)
        if key in seen:
            continue
        seen.add(key)

        # Keep likely product/news/release entries and drop generic nav links.
        blob = f"{title} {text}".lower()
        likely = any(k in blob for k in ["発売", "release", "ova", "dvd", "bd", "新作", "作品", "anime", "アニメ"])
        if source["name"] == "Lune Soft / Bunny Walker OVA":
            likely = True
        if not likely:
            continue

        items.append({
            "title": title,
            "link": href,
            "summary": text[:700],
            "source": source["name"],
            "published": None,
        })
        if len(items) >= 30:
            break
    return items


def make_guid(item):
    raw = f"{item['source']}|{item['link']}|{item['title']}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_feed(items):
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Adult Anime Release Watch"
    ET.SubElement(channel, "link").text = "https://zane6443.github.io/adult-anime-feed/"
    ET.SubElement(channel, "description").text = "Aggregated release/news watch feed for selected adult-anime labels and sources."
    ET.SubElement(channel, "language").text = "de"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    for item in items[:100]:
        el = ET.SubElement(channel, "item")
        ET.SubElement(el, "title").text = f"[{item['source']}] {item['title']}"
        ET.SubElement(el, "link").text = item["link"]
        guid = ET.SubElement(el, "guid", isPermaLink="false")
        guid.text = make_guid(item)
        desc = f"<p><strong>Quelle:</strong> {html.escape(item['source'])}</p><p>{html.escape(item['summary'])}</p>"
        ET.SubElement(el, "description").text = desc
        if item["published"]:
            ET.SubElement(el, "pubDate").text = format_datetime(item["published"])

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def main():
    all_items = []
    errors = []
    for source in SOURCES:
        try:
            if source["kind"] == "rss":
                all_items.extend(parse_rss(source))
            else:
                all_items.extend(parse_html(source))
        except Exception as e:
            errors.append(f"{source['name']}: {type(e).__name__}: {e}")

    # de-duplicate, deterministic order; dated entries first
    uniq = {}
    for item in all_items:
        uniq[make_guid(item)] = item
    items = list(uniq.values())
    items.sort(key=lambda x: (x["published"] is not None, x["published"] or datetime.min.replace(tzinfo=timezone.utc), x["title"]), reverse=True)

    with open("feed.xml", "wb") as f:
        f.write(build_feed(items))

    with open("status.txt", "w", encoding="utf-8") as f:
        f.write(f"Updated: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"Items: {len(items)}\n")
        if errors:
            f.write("\nSource errors:\n")
            for err in errors:
                f.write(f"- {err}\n")


if __name__ == "__main__":
    main()
