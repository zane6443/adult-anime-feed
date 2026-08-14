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

UA = "Mozilla/5.0 (compatible; adult-anime-feed/2.0; +https://github.com/zane6443/adult-anime-feed)"
TIMEOUT = 25
NOW = datetime.now(timezone.utc)
YEAR_START = datetime(NOW.year, 1, 1, tzinfo=timezone.utc)
translator = GoogleTranslator(source="auto", target="en")

BAD_IMAGE_HINTS = ("logo", "favicon", "icon", "noimage", "no-image", "placeholder", "loading", "spinner", "avatar", "common/", "header/", "footer/")
BAD_ENTRY_HINTS = ("500 (server error)", "server error", "internal server error", "404 not found", "page not found", "403 forbidden")

GENERIC_SOURCES = [
    {"name": "Pink Pineapple", "kind": "html", "url": "https://www.pinkpineapple.co.jp/"},
    {"name": "PoRO", "kind": "html", "url": "https://www.poro.cc/"},
]

MEDIABANK_LABELS = [
    {"name": "Queen Bee / MediaBank", "url": "https://www.mediabank.co.jp/label.php?id=22&pg=1&stat=0"},
    {"name": "Gold Bear / MediaBank", "url": "https://www.mediabank.co.jp/label.php?id=14&pg=1&stat=0"},
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
    try:
        return clean_text(translator.translate(text[:limit]))
    except Exception:
        return text[:limit]


def good_image(url: str | None) -> bool:
    if not url:
        return False
    low = url.lower()
    return (low.startswith("http://") or low.startswith("https://")) and not any(h in low for h in BAD_IMAGE_HINTS)


def extract_image_from_html(url: str) -> str | None:
    try:
        soup = BeautifulSoup(fetch(url).text, "html.parser")
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


def bad_entry(title: str, summary: str = "") -> bool:
    blob = f"{title} {summary}".lower()
    return any(h in blob for h in BAD_ENTRY_HINTS)


def parse_date(text: str) -> datetime | None:
    patterns = [
        r"(20\d{2})[./年](\d{1,2})[./月](\d{1,2})",
        r"(20\d{2})-(\d{1,2})-(\d{1,2})",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


def in_current_year(dt: datetime | None) -> bool:
    return bool(dt and dt >= YEAR_START and dt <= NOW.replace(hour=23, minute=59, second=59))


def make_item(source, original_title, link, summary, published, image):
    return {
        "title": translate_en(original_title, 500),
        "original_title": clean_text(original_title),
        "link": link,
        "summary": translate_en(summary, 1000),
        "original_summary": clean_text(summary)[:1000],
        "source": source,
        "published": published,
        "image": image,
    }


def parse_lune_brand_pages():
    index_url = "https://www.lune-soft.jp/brand"
    soup = BeautifulSoup(fetch(index_url).text, "html.parser")
    brand_urls = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(index_url, a["href"])
        if "/ova/brand_ova/" in href:
            brand_urls.add(href.split("#")[0])
    # Guaranteed currently active animation labels, even if brand index markup changes.
    brand_urls.update({
        "https://www.lune-soft.jp/ova/brand_ova/bunnywalker",
        "https://www.lune-soft.jp/ova/brand_ova/antechinus",
    })

    items = []
    for url in sorted(brand_urls):
        page = BeautifulSoup(fetch(url).text, "html.parser")
        heading = clean_text((page.find("h1") or page.title).get_text(" ", strip=True)) if (page.find("h1") or page.title) else "Lune Pictures"
        label = heading.replace("アニメブランド：", "").replace("一覧", "").strip(" -") or "Lune Pictures"
        for node in page.select("article, li, .item, .product, .ova_item, .works-item, .archive-item, div"):
            text = clean_text(node.get_text(" ", strip=True))
            if "OVA" not in text.upper():
                continue
            dt = parse_date(text)
            if not in_current_year(dt):
                continue
            a = node.find("a", href=True)
            if not a:
                continue
            href = urljoin(url, a["href"])
            title = clean_text(a.get_text(" ", strip=True))
            if not title or "OVA" not in title.upper():
                m = re.search(r"(OVA.+?)(?:20\d{2}[年./-]|$)", text, re.I)
                title = clean_text(m.group(1)) if m else title
            if not title or bad_entry(title, text):
                continue
            img = node.find("img")
            src = (img.get("data-src") or img.get("data-lazy-src") or img.get("src")) if img else None
            image = urljoin(url, src) if src and good_image(urljoin(url, src)) else extract_image_from_html(href)
            items.append(make_item(f"Lune Pictures / {label}", title, href, text, dt, image))
    return items


def parse_mediabank_label(source):
    items = []
    # First two pages are enough for current-year backfill on these labels.
    for pg in (1, 2):
        url = re.sub(r"[?&]pg=\d+", f"&pg={pg}", source["url"])
        soup = BeautifulSoup(fetch(url).text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            if "product.php?model=" not in href:
                continue
            node = a.find_parent(["li", "article", "div", "section"]) or a.parent
            text = clean_text(node.get_text(" ", strip=True)) if node else clean_text(a.get_text(" ", strip=True))
            dt = parse_date(text)
            if not in_current_year(dt):
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if len(title) < 3:
                continue
            img = node.find("img") if node else None
            src = (img.get("data-src") or img.get("src")) if img else None
            image = urljoin(url, src) if src and good_image(urljoin(url, src)) else extract_image_from_html(href)
            items.append(make_item(source["name"], title, href, text, dt, image))
    return items


def parse_generic_html(source):
    soup = BeautifulSoup(fetch(source["url"]).text, "html.parser")
    items = []
    seen = set()
    for node in (soup.select("article, .post, .news, .item, .product, li") or soup.find_all(["div", "section"]))[:500]:
        a = node.find("a", href=True)
        if not a:
            continue
        title = clean_text(a.get_text(" ", strip=True))
        text = clean_text(node.get_text(" ", strip=True))
        dt = parse_date(text)
        if not in_current_year(dt):
            continue
        blob = f"{title} {text}".lower()
        if not any(k in blob for k in ["発売", "release", "ova", "dvd", "bd", "新作", "作品", "anime", "アニメ"]):
            continue
        href = urljoin(source["url"], a["href"])
        if href in seen or bad_entry(title, text):
            continue
        seen.add(href)
        img = node.find("img")
        src = (img.get("data-src") or img.get("data-lazy-src") or img.get("src")) if img else None
        image = urljoin(source["url"], src) if src and good_image(urljoin(source["url"], src)) else extract_image_from_html(href)
        items.append(make_item(source["name"], title, href, text, dt, image))
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
    ET.SubElement(channel, "title").text = f"Adult Anime Release Watch {NOW.year}"
    ET.SubElement(channel, "link").text = "https://zane6443.github.io/adult-anime-feed/"
    ET.SubElement(channel, "description").text = f"English release feed, backfilled from Jan 1 {NOW.year}, then updated automatically."
    ET.SubElement(channel, "language").text = "en"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(NOW)
    for item in items[:250]:
        el = ET.SubElement(channel, "item")
        ET.SubElement(el, "title").text = f"[{item['source']}] {item['title']}"
        ET.SubElement(el, "link").text = item["link"]
        ET.SubElement(el, "guid", isPermaLink="false").text = make_guid(item)
        parts = [f"<p><strong>Source:</strong> {html.escape(item['source'])}</p>"]
        if item.get("published"):
            parts.append(f"<p><strong>Release date:</strong> {item['published'].date().isoformat()}</p>")
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
        if item.get("published"):
            ET.SubElement(el, "pubDate").text = format_datetime(item["published"])
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def main():
    all_items, errors = [], []
    try:
        all_items.extend(parse_lune_brand_pages())
    except Exception as e:
        errors.append(f"Lune Pictures labels: {type(e).__name__}: {e}")

    for source in MEDIABANK_LABELS:
        try:
            all_items.extend(parse_mediabank_label(source))
        except Exception as e:
            errors.append(f"{source['name']}: {type(e).__name__}: {e}")

    for source in GENERIC_SOURCES:
        try:
            all_items.extend(parse_generic_html(source))
        except Exception as e:
            errors.append(f"{source['name']}: {type(e).__name__}: {e}")

    uniq = {}
    for item in all_items:
        if in_current_year(item.get("published")) and not bad_entry(item.get("title", ""), item.get("summary", "")):
            uniq[make_guid(item)] = item
    items = list(uniq.values())
    repeated_images = clean_repeated_images(items)
    items.sort(key=lambda x: (x.get("published") or YEAR_START, x["title"]), reverse=True)

    data = build_feed(items)
    for filename in ("feed.xml", "feed-v2.xml"):
        with open(filename, "wb") as f:
            f.write(data)

    by_source = Counter(i["source"] for i in items)
    with open("status.txt", "w", encoding="utf-8") as f:
        f.write(f"Updated: {NOW.isoformat()}\n")
        f.write(f"Backfill start: {YEAR_START.date().isoformat()}\n")
        f.write(f"Items: {len(items)}\n")
        f.write(f"Items with useful images: {sum(1 for i in items if i.get('image'))}\n")
        f.write(f"Repeated default images removed: {len(repeated_images)}\n\n")
        f.write("Items by source:\n")
        for source, count in sorted(by_source.items()):
            f.write(f"- {source}: {count}\n")
        if errors:
            f.write("\nSource errors:\n")
            for err in errors:
                f.write(f"- {err}\n")


if __name__ == "__main__":
    main()
