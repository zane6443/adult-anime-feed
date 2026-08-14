from __future__ import annotations

import html
import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

UA = "Mozilla/5.0 (compatible; adult-anime-feed/1.2; +https://github.com/zane6443/adult-anime-feed)"
TIMEOUT = 25
translator = GoogleTranslator(source="auto", target="en")

SOURCES = [
    {"name": "Lune Soft / Bunny Walker", "kind": "rss", "url": "https://www.lune-soft.jp/feed", "keywords": ["OVA", "アニメ", "発売", "新作", "ばにぃ", "Bunny"]},
    {"name": "Pink Pineapple", "kind": "html", "url": "https://www.pinkpineapple.co.jp/"},
    {"name": "PoRO", "kind": "html", "url": "https://www.poro.cc/"},
]

BAD_IMAGE_HINTS = ("logo", "favicon", "icon", "noimage", "no-image", "placeholder", "loading", "spinner", "avatar", "common/", "header/", "footer/")
BAD_ENTRY_HINTS = ("500 (server error)", "server error", "internal server error", "404 not found", "page not found", "403 forbidden")


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


def good_image(url: str | None) -> bool:
    if not url:
        return False
    low = url.lower()
    if any(h in low for h in BAD_IMAGE_HINTS):
        return False
    return low.startswith("http://") or low.startswith("https://")


def extract_image_from_html(url: str) -> str | None:
    try:
        r = fetch(url)
        soup = BeautifulSoup(r.text, "html.parser")
        for selector in ["article img[src]", ".entry-content img[src]", ".post-content img[src]", ".article-body img[src]", ".news-body img[src]", "main img[src]"]:
            for img in soup.select(selector):
                src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
                candidate = urljoin(url, src) if src else None
                if good_image(candidate):
                    return candidate
        for attrs in [{"property": "og:image"}, {"name": "twitter:image"}, {"property": "twitter:image"}]:
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                candidate = urljoin(url, tag["content"])
                if good_image(candidate):
                    return candidate
    except Exception:
        pass
    return None


def rss_image(entry) -> str | None:
    for attr in ("media_content", "media_thumbnail"):
        vals = getattr(entry, attr, None)
        if vals:
            for m in vals:
                if good_image(m.get("url")):
                    return m["url"]
    enc = getattr(entry, "enclosures", None)
    if enc:
        for e in enc:
            href = e.get("href")
            typ = (e.get("type") or "").lower()
            if href and typ.startswith("image/") and good_image(href):
                return href
    return None


def bad_entry(title: str, summary: str = "") -> bool:
    blob = f"{title} {summary}".lower()
    return any(h in blob for h in BAD_ENTRY_HINTS)


def parse_rss(source):
    r = fetch(source["url"])
    feed = feedparser.parse(r.content)
    items = []
    kws = [k.lower() for k in source.get("keywords", [])]
    for e in feed.entries[:50]:
        original_title = clean_text(getattr(e, "title", ""))
        original_summary = clean_text(BeautifulSoup(getattr(e, "summary", ""), "html.parser").get_text(" "))
        if bad_entry(original_title, original_summary):
            continue
        blob = f"{original_title} {original_summary}".lower()
        if kws and not any(k in blob for k in kws):
            continue
        link = getattr(e, "link", source["url"])
        published = None
        if getattr(e, "published_parsed", None):
            published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        image = rss_image(e) or extract_image_from_html(link)
        items.append({"title": translate_en(original_title, 500), "original_title": original_title, "link": link, "summary": translate_en(original_summary, 1000), "original_summary": original_summary, "source": source["name"], "published": published, "image": image})
    return items


def parse_html(source):
    r = fetch(source["url"])
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    candidates = soup.select("article, .post, .news, .item, .product, li") or soup.find_all(["div", "section"])
    seen = set()
    for node in candidates[:300]:
        a = node.find("a", href=True)
        if not a:
            continue
        original_title = clean_text(a.get_text(" ", strip=True))
        if len(original_title) < 4:
            continue
        original_text = clean_text(node.get_text(" ", strip=True))
        if bad_entry(original_title, original_text):
            continue
        href = urljoin(source["url"], a.get("href"))
        key = (original_title, href)
        if key in seen:
            continue
        seen.add(key)
        blob = f"{original_title} {original_text}".lower()
        if not any(k in blob for k in ["発売", "release", "ova", "dvd", "bd", "新作", "作品", "anime", "アニメ"]):
            continue
        image = None
        for img in node.select("img[src]"):
            src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
            candidate = urljoin(source["url"], src) if src else None
            if good_image(candidate):
                image = candidate
                break
        if not image:
            image = extract_image_from_html(href)
        items.append({"title": translate_en(original_title, 500), "original_title": original_title, "link": href, "summary": translate_en(original_text[:1000], 1000), "original_summary": original_text[:1000], "source": source["name"], "published": None, "image": image})
        if len(items) >= 30:
            break
    return items


def make_guid(item):
    raw = f"{item['source']}|{item['link']}|{item['original_title']}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def clean_repeated_images(items):
    counts = Counter(i.get("image") for i in items if i.get("image"))
    repeated = {url for url, count in counts.items() if count >= 3}
    for item in items:
        if item.get("image") in repeated:
            item["image"] = None
    return repeated


def build_feed(items):
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Adult Anime Release Watch v2"
    ET.SubElement(channel, "link").text = "https://zane6443.github.io/adult-anime-feed/"
    ET.SubElement(channel, "description").text = "Clean English adult-anime release/news feed with preview images."
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(datetime.now(timezone.utc))
    for item in items[:100]:
        el = ET.SubElement(channel, "item")
        ET.SubElement(el, "title").text = f"[{item['source']}] {item['title']}"
        ET.SubElement(el, "link").text = item["link"]
        ET.SubElement(el, "guid", isPermaLink="false").text = make_guid(item)
        parts = [f"<p><strong>Source:</strong> {html.escape(item['source'])}</p>"]
        if item.get("image"):
            img = html.escape(item["image"], quote=True)
            parts.append(f'<p><img src="{img}" alt="Preview" style="max-width:100%;height:auto;"></p>')
        if item.get("summary"):
            parts.append(f"<p>{html.escape(item['summary'])}</p>")
        if item.get("original_title") and item["original_title"] != item["title"]:
            parts.append(f"<p><strong>Original title:</strong> {html.escape(item['original_title'])}</p>")
        ET.SubElement(el, "description").text = "".join(parts)
        if item.get("image"):
            ET.SubElement(el, "enclosure", url=item["image"], type="image/jpeg")
        if item["published"]:
            ET.SubElement(el, "pubDate").text = format_datetime(item["published"])
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def main():
    all_items, errors = [], []
    for source in SOURCES:
        try:
            all_items.extend(parse_rss(source) if source["kind"] == "rss" else parse_html(source))
        except Exception as e:
            errors.append(f"{source['name']}: {type(e).__name__}: {e}")
    uniq = {}
    for item in all_items:
        if not bad_entry(item.get("title", ""), item.get("summary", "")):
            uniq[make_guid(item)] = item
    items = list(uniq.values())
    repeated_images = clean_repeated_images(items)
    items.sort(key=lambda x: (x["published"] is not None, x["published"] or datetime.min.replace(tzinfo=timezone.utc), x["title"]), reverse=True)
    data = build_feed(items)
    for filename in ("feed.xml", "feed-v2.xml"):
        with open(filename, "wb") as f:
            f.write(data)
    with open("status.txt", "w", encoding="utf-8") as f:
        f.write(f"Updated: {datetime.now(timezone.utc).isoformat()}\n")
        f.write(f"Items: {len(items)}\n")
        f.write(f"Items with unique/useful images: {sum(1 for i in items if i.get('image'))}\n")
        f.write(f"Repeated default images removed: {len(repeated_images)}\n")
        if errors:
            f.write("\nSource errors:\n")
            for err in errors:
                f.write(f"- {err}\n")


if __name__ == "__main__":
    main()
