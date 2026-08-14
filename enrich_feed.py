from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = "Mozilla/5.0 (compatible; adult-anime-feed/4.0; +https://github.com/zane6443/adult-anime-feed)"
NOW = datetime.now(timezone.utc)
YEAR = NOW.year
META_URL = "https://upcominghentai.com/"

BAD_IMAGE_PARTS = (
    "title_detail.png", "title_detail_sp.png", "logo", "favicon",
    "placeholder", "noimage", "no-image", "loading", "spinner",
    "/assets/images/330x464.png",
)
BAD_TEXT = ("500 (server error)", "internal server error", "404 not found", "403 forbidden")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def bad_image(url: str | None) -> bool:
    if not url:
        return True
    low = url.lower()
    return any(x in low for x in BAD_IMAGE_PARTS)


def guid_for(title: str, link: str, date: datetime) -> str:
    return hashlib.sha256(f"v4|{date.date()}|{title}|{link}".encode("utf-8")).hexdigest()


def parse_date(text: str) -> datetime | None:
    m = re.search(r"([A-Z][a-z]{2,8} \d{1,2}, 20\d{2})", text)
    if not m:
        return None
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(m.group(1), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def add_pink_pineapple_metadata(channel: ET.Element) -> int:
    try:
        r = requests.get(META_URL, headers={"User-Agent": UA}, timeout=25)
        r.raise_for_status()
    except Exception:
        return 0

    soup = BeautifulSoup(r.text, "html.parser")
    existing_links = {clean(x.text) for x in channel.findall("item/link")}
    added = 0
    seen = set()

    for node in soup.select("article, .card, li, div"):
        text = clean(node.get_text(" ", strip=True))
        if "Pink Pineapple" not in text or "OVA" not in text:
            continue
        dt = parse_date(text)
        if not dt or dt.year != YEAR:
            continue
        heading = node.find(["h2", "h3", "h4"])
        a = (heading.find("a", href=True) if heading else None) or node.find("a", href=True)
        if not a:
            continue
        title = clean(a.get_text(" ", strip=True))
        link = urljoin(META_URL, a["href"])
        if not title or link in existing_links or link in seen:
            continue
        seen.add(link)

        image = None
        img = node.find("img")
        if img:
            src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
            if src:
                candidate = urljoin(META_URL, src)
                if not bad_image(candidate):
                    image = candidate

        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = f"[Pink Pineapple / metadata] {title}"
        ET.SubElement(item, "link").text = link
        ET.SubElement(item, "guid", isPermaLink="false").text = guid_for(title, link, dt)
        status = "Upcoming" if dt > NOW else "Released"
        desc = (
            f"<p><strong>Source:</strong> Pink Pineapple (release-calendar metadata)</p>"
            f"<p><strong>Status:</strong> {status}</p>"
            f"<p><strong>Release date:</strong> {dt.date()}</p>"
        )
        if image:
            desc += f'<p><img src="{image}" alt="Preview" style="max-width:100%;height:auto;"></p>'
            ET.SubElement(item, "enclosure", url=image, type="image/jpeg")
        ET.SubElement(item, "description").text = desc
        ET.SubElement(item, "pubDate").text = format_datetime(dt)
        added += 1
    return added


def item_date(item: ET.Element) -> datetime:
    p = item.findtext("pubDate")
    try:
        return parsedate_to_datetime(p) if p else datetime(YEAR, 1, 1, tzinfo=timezone.utc)
    except Exception:
        return datetime(YEAR, 1, 1, tzinfo=timezone.utc)


def cleanup(channel: ET.Element) -> tuple[int, int]:
    removed_bad = 0
    for item in list(channel.findall("item")):
        blob = clean((item.findtext("title") or "") + " " + (item.findtext("description") or "")).lower()
        if any(x in blob for x in BAD_TEXT):
            channel.remove(item)
            removed_bad += 1

    items = list(channel.findall("item"))
    urls = []
    for item in items:
        enc = item.find("enclosure")
        if enc is not None and enc.get("url"):
            urls.append(enc.get("url"))
    counts = Counter(urls)

    stripped = 0
    for item in items:
        enc = item.find("enclosure")
        url = enc.get("url") if enc is not None else None
        if url and (bad_image(url) or counts[url] >= 3):
            item.remove(enc)
            desc = item.find("description")
            if desc is not None and desc.text:
                desc.text = re.sub(r'<p><img src="[^"]+"[^>]*></p>', "", desc.text)
            stripped += 1

    items = list(channel.findall("item"))
    items.sort(key=item_date, reverse=True)
    for item in items:
        channel.remove(item)
    for item in items:
        # New v4 GUIDs force feed readers to import the cleaned cards as fresh entries.
        old_guid = item.find("guid")
        if old_guid is not None:
            title = item.findtext("title") or ""
            link = item.findtext("link") or ""
            old_guid.text = guid_for(title, link, item_date(item))
        channel.append(item)
    return removed_bad, stripped


def main():
    tree = ET.parse("feed-v3.xml")
    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:
        raise RuntimeError("RSS channel missing")

    title = channel.find("title")
    if title is not None:
        title.text = f"Adult Anime Release Watch {YEAR} v4"

    added_pp = add_pink_pineapple_metadata(channel)
    removed_bad, stripped = cleanup(channel)
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    for fn in ("feed.xml", "feed-v2.xml", "feed-v3.xml", "feed-v4.xml"):
        with open(fn, "wb") as f:
            f.write(data)

    with open("status.txt", "a", encoding="utf-8") as f:
        f.write("\nV4 cleanup:\n")
        f.write(f"- Pink Pineapple metadata items added: {added_pp}\n")
        f.write(f"- Error-page entries removed: {removed_bad}\n")
        f.write(f"- Placeholder/repeated images stripped: {stripped}\n")
        f.write(f"- Final v4 items: {len(channel.findall('item'))}\n")


if __name__ == "__main__":
    main()
