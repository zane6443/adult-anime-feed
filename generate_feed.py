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
from deep_translator import GoogleTranslator

UA = "Mozilla/5.0 (compatible; adult-anime-feed/1.1; +https://github.com/zane6443/adult-anime-feed)"
TIMEOUT = 25
translator = GoogleTranslator(source="auto", target="en")

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


def translate_en(text: str, limit: int = 1200) -> str:
    text = clean_text(text)
    if not text:
        return ""
    text = text[:limit]
    try:
        return clean_text(translator.translate(text))
    except Exception:
        return text


def extract_image_from_html(url: str) -> str | None:
    try:
        r = fetch(url)
        soup = BeautifulSoup(r.text, "html.parser")
        for attrs in [
            {"property": "og:image"},
            {"name": "twitter:image"},
            {"property": "twitter:image"},
        ]:
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                return urljoin(url, tag["content"])
        img = soup.find("img", src=True)
        if img:
            return urljoin(url, img.get("src"))
    except Exception:
        pass
    return None


def rss_image(entry) -> str | None:
    media = getattr(entry, "media_content", None)
    if media:
        for m in media:
            if m.get("url"):
                return m["url"]
    thumbs = getattr(entry, "media_thumbnail", None)
    if thumbs:
        for m in thumbs:
            if m.get("url"):
                return m["url"]
    enc = getattr(entry, "enclosures", None)
    if enc:
        for e in enc:
            href = e.get("href")
            typ = (e.get("type") or "").lower()
            if href and typ.startswith("image/"):
                return href
    return None


def parse_rss(source):
    r = fetch(source["url"])
    feed = feedparser.parse(r.content)
    items = []
    kws = [k.lower() for k in source.get("keywords", [])]
    for e in feed.entries[:40]:
        original_title = clean_text(getattr(e, "title", ""))
        original_summary = clean_text(BeautifulSoup(getattr(e, "summary", ""), "html.parser").get_text(" "))
        blob = f"{original_title} {original_summary}".lower()
        if kws and not any(k in blob for k in kws):
            continue
        link = getattr(e, "link", source["url"])
        published = None
        if getattr(e, "published_parsed", None):
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        image = rss_image(e) or extract_image_from_html(link)
        items.append({
            "title": translate_en(original_title, 500),
            "original_title": original_title,
            "link": link,
            "summary": translate_en(original_summary, 1000),
            "original_summary": original_summary,
            "source": source["name"],
            "published": published,
            "image": image,
        })
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
        original_title = clean_text(a.get_text(" ", strip=True))
        if len(original_title) < 4:
            continue
        original_text = clean_text(node.get_text(" ", strip=True))
        href = urljoin(source["url"], a.get("href"))
        key = (original_title, href)
        if key in seen:
            continue
        seen.add(key)

        blob = f"{original_title} {original_text}".lower()
        likely = any(k in blob for k in ["発売", "release", "ova", "dvd", "bd", "新作", "作品", "anime", "アニメ"])
        if source["name"] == "Lune Soft / Bunny Walker OVA":
            likely = True
        if not likely:
            continue

        img = node.find("img", src=True)
        image = urljoin(source["url"], img.get("src")) if img else extract_image_from_html(href)

        items.append({
            "title": translate_en(original_title, 500),
            "original_title": original_title,
            "link": href,
            "summary": translate_en(original_text[:1000], 1000),
            "original_summary": original_text[:1000],
            "source": source["name"],
            "published": None,
            "image": image,
        })
        if len(items) >= 30:
            break
    return items


def make_guid(item):
    raw = f"{item['source']}|{item['link']}|{item['original_title']}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_feed(items):
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Adult Anime Release Watch"
    ET.SubElement(channel, "link").text = "https://zane6443.github.io/adult-anime-feed/"
    ET.SubElement(channel, "description").text = "Aggregated adult-anime release/news watch feed with English titles and preview images."
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))

    for item in items[:100]:
        el = ET.SubElement(channel, "item")
        ET.SubElement(el, "title").text = f"[{item['source']}] {item['title']}"
        ET.SubElement(el, "link").text = item["link"]
        guid = ET.SubElement(el, "guid", isPermaLink="false")
        guid.text = make_guid(item)

        parts = [f"<p><strong>Source:</strong> {html.escape(item['source'])}</p>"]
        if item.get("image"):
            img = html.escape(item["image"], quote=True)
            parts.append(f'<p><img src="{img}" alt="Preview" style="max-width:100%;height:auto;"></p>')
        if item.get("summary"):
            parts.append(f"<p>{html.escape(item['summary'])}</p>")
        if item.get("original_title") and item["original_title"] != item["title"]:
            parts.append(f"<p><strong>Original title:</strong> {html.escape(item['original_title'])}</p>")
        desc = "".join(parts)
        ET.SubElement(el, "description").text = desc

        if item.get("image"):
            ET.SubElement(el, "enclosure", url=item["image"], type="image/jpeg")
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
        with_images = sum(1 for i in items if i.get("image"))
        f.write(f"Items with images: {with_images}\n")
        if errors:
            f.write("\nSource errors:\n")
            for err in errors:
                f.write(f"- {err}\n")


if __name__ == "__main__":
    main()
