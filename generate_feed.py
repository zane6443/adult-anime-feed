from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

UA = "Mozilla/5.0 (compatible; adult-anime-feed/stable; +https://github.com/zane6443/adult-anime-feed)"
TIMEOUT = 25
NOW = datetime.now(timezone.utc)
YEAR = NOW.year
translator = GoogleTranslator(source="auto", target="en")

LUNE_BRANDS = {
    "Bunny Walker": "https://www.lune-soft.jp/ova/brand_ova/bunnywalker",
    "Antechinus": "https://www.lune-soft.jp/ova/brand_ova/antechinus",
}
MEDIABANK_LABEL_PAGES = [
    "https://www.mediabank.co.jp/label.php?id=22&pg=1&stat=0",
    "https://www.mediabank.co.jp/label.php?id=14&pg=1&stat=0",
    "https://www.mediabank.co.jp/label.php?id=23&pg=1&stat=0",
]
PINK_META = "https://upcominghentai.com/"

BAD_TEXT = ("500 (server error)", "internal server error", "404 not found", "403 forbidden")
BAD_IMAGE_PARTS = (
    "logo", "favicon", "icon", "placeholder", "noimage", "no-image", "loading", "spinner", "avatar",
    "title_detail.png", "title_detail_sp.png", "/assets/images/330x464.png",
)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def fetch(url: str):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    if "mediabank.co.jp" in url:
        r.encoding = "utf-8"
    elif not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
        r.encoding = r.apparent_encoding or "utf-8"
    return r


def tr(s: str, n: int = 800) -> str:
    s = clean(s)[:n]
    if not s:
        return ""
    try:
        return clean(translator.translate(s))
    except Exception:
        return s


def parse_date(text: str):
    for p in (r"(20\d{2})[./年](\d{1,2})[./月](\d{1,2})", r"(20\d{2})-(\d{1,2})-(\d{1,2})"):
        m = re.search(p, text)
        if m:
            try:
                return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def parse_english_date(text: str):
    m = re.search(r"([A-Z][a-z]{2,8} \d{1,2}, 20\d{2})", text)
    if not m:
        return None
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(m.group(1), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def this_year(dt) -> bool:
    return bool(dt and dt.year == YEAR)


def good_image(url: str | None) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    low = url.lower()
    return not any(x in low for x in BAD_IMAGE_PARTS)


def img_url(img, base: str):
    if not img:
        return None
    src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
    if not src and img.get("srcset"):
        src = img.get("srcset").split(",")[-1].strip().split(" ")[0]
    return urljoin(base, src) if src else None


def image_near_anchor(a, base: str):
    # Prefer the thumbnail that belongs to the release card on the listing page.
    candidates = []
    candidates.extend(a.find_all("img"))
    parent = a
    for _ in range(5):
        parent = parent.parent
        if not parent:
            break
        candidates.extend(parent.find_all("img", limit=4))
    seen = set()
    for img in candidates:
        u = img_url(img, base)
        if u and u not in seen and good_image(u):
            seen.add(u)
            return u
    return None


def image_from_detail(url: str):
    try:
        soup = BeautifulSoup(fetch(url).text, "html.parser")
        # Metadata image first, then content images. Known placeholders are rejected.
        for attrs in ({"property": "og:image"}, {"name": "twitter:image"}):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                u = urljoin(url, tag["content"])
                if good_image(u):
                    return u
        for img in soup.select("article img, main img, .entry-content img, .product img, #contents img"):
            u = img_url(img, url)
            if good_image(u):
                return u
    except Exception:
        pass
    return None


def stable_guid(link: str) -> str:
    # This never changes when title/date/translation changes, so feed readers do not duplicate items.
    return hashlib.sha256(link.encode("utf-8")).hexdigest()


def canonical_title(s: str) -> str:
    s = clean(s).lower()
    s = re.sub(r"^ova\s*", "", s, flags=re.I)
    s = re.sub(r"\s*20\d{2}.*$", "", s)
    s = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠]+", "", s)
    return s


def make_item(source, original_title, link, dt, image=None, summary=""):
    return {
        "source": source,
        "original_title": clean(original_title),
        "title": tr(original_title, 400),
        "link": link,
        "published": dt,
        "image": image,
        "summary": tr(summary, 700),
    }


def parse_lune_brand(label: str, root_url: str):
    out = []
    seen_links = set()
    # Bunny Walker's 2026 releases span at least pages 1 and 2.
    for page in range(1, 4):
        url = root_url if page == 1 else f"{root_url}/page/{page}"
        soup = BeautifulSoup(fetch(url).text, "html.parser")
        found_this_year = 0
        for a in soup.find_all("a", href=True):
            text = clean(a.get_text(" ", strip=True))
            if "OVA" not in text.upper():
                continue
            dt = parse_date(text)
            if not this_year(dt):
                continue
            href = urljoin(url, a["href"])
            if "/ova/" not in href or href in seen_links:
                continue
            seen_links.add(href)
            found_this_year += 1
            title = re.sub(r"\s*20\d{2}年\d{1,2}月\d{1,2}日発売.*$", "", text).strip()
            if not title:
                continue
            image = image_near_anchor(a, url) or image_from_detail(href)
            out.append(make_item(f"Lune Pictures / {label}", title, href, dt, image, text))
        # Once a page has no current-year releases, older pages are unnecessary.
        if page > 1 and found_this_year == 0:
            break
    return out


def detect_mediabank_label(text: str, model: str) -> str:
    m = re.search(r"(?:Label|レーベル)\s*[:：]?\s*(Queen Bee|King Bee|GOLD BEAR|Gold Bear|WHITE BEAR|White Bear|HOT BEAR|Hot Bear)", text, re.I)
    if m:
        name = m.group(1).lower()
        return {
            "queen bee": "Queen Bee", "king bee": "King Bee", "gold bear": "Gold Bear",
            "white bear": "White Bear", "hot bear": "Hot Bear",
        }.get(name, m.group(1))
    prefix = model.upper().split("-")[0]
    return {"QNB": "Queen Bee", "GBR": "Gold Bear", "WBR": "White Bear", "HBR": "Hot Bear", "KBR": "King Bee"}.get(prefix, "MediaBank")


def parse_mediabank_product(model: str):
    url = f"https://www.mediabank.co.jp/product.php?model={model}"
    soup = BeautifulSoup(fetch(url).text, "html.parser")
    text = clean(soup.get_text(" ", strip=True))
    if any(x in text.lower() for x in BAD_TEXT):
        return None
    dt = parse_date(text)
    if not this_year(dt):
        return None
    heading = soup.select_one("h1, h2, .title, .product_title, .product-name")
    title = clean((heading or soup.title).get_text(" ", strip=True)) if (heading or soup.title) else model
    title = re.sub(r"\s*[|｜-]\s*Media\s*Bank.*$", "", title, flags=re.I).strip()
    label = detect_mediabank_label(text, model)
    image = image_from_detail(url)
    summary = ""
    pos = text.find("作品概要")
    if pos >= 0:
        summary = text[pos:pos + 900]
    return make_item(f"MediaBank / {label}", title, url, dt, image, summary)


def parse_mediabank():
    out = []
    models = set()
    for base in MEDIABANK_LABEL_PAGES:
        for page in (1, 2):
            url = re.sub(r"pg=\d+", f"pg={page}", base)
            try:
                soup = BeautifulSoup(fetch(url).text, "html.parser")
            except Exception:
                continue
            # Only explicit product links; no navigation-generated pseudo entries.
            for a in soup.find_all("a", href=re.compile(r"product\.php\?model=")):
                m = re.search(r"product\.php\?model=([^&\"']+)", a.get("href", ""))
                if m:
                    models.add(m.group(1))
    for model in sorted(models):
        try:
            x = parse_mediabank_product(model)
            if x:
                out.append(x)
        except Exception:
            pass
    return out


def parse_pink_pineapple_metadata():
    out = []
    try:
        soup = BeautifulSoup(fetch(PINK_META).text, "html.parser")
    except Exception:
        return out
    seen = set()
    # Start from actual title links, not generic cards/divs. This prevents "View all" entries.
    for a in soup.find_all("a", href=re.compile(r"/title/")):
        link = urljoin(PINK_META, a.get("href", ""))
        if link in seen:
            continue
        node = a
        matched = None
        for _ in range(6):
            node = node.parent
            if not node:
                break
            text = clean(node.get_text(" ", strip=True))
            if "Pink Pineapple" in text and "OVA" in text:
                matched = node
                break
        if matched is None:
            continue
        text = clean(matched.get_text(" ", strip=True))
        dt = parse_english_date(text)
        if not this_year(dt):
            continue
        title = clean(a.get_text(" ", strip=True))
        if not title or title.lower() in ("view all", "more", "details"):
            continue
        image = None
        for img in matched.find_all("img"):
            u = img_url(img, PINK_META)
            if good_image(u):
                image = u
                break
        seen.add(link)
        out.append(make_item("Pink Pineapple / metadata", title, link, dt, image, ""))
    return out


def dedupe(items):
    by_link = {}
    for x in items:
        if x["link"] not in by_link:
            by_link[x["link"]] = x
        else:
            old = by_link[x["link"]]
            if not old.get("image") and x.get("image"):
                by_link[x["link"]] = x
    items = list(by_link.values())

    # Secondary duplicate guard: same release date + normalized title.
    unique = {}
    for x in items:
        k = (x["published"].date(), canonical_title(x["original_title"]))
        old = unique.get(k)
        if old is None:
            unique[k] = x
            continue
        # Prefer official sources to metadata fallbacks; then prefer an item with an image.
        old_meta = "metadata" in old["source"].lower()
        new_meta = "metadata" in x["source"].lower()
        if old_meta and not new_meta:
            unique[k] = x
        elif old_meta == new_meta and not old.get("image") and x.get("image"):
            unique[k] = x
    return list(unique.values())


def build(items):
    rss = ET.Element("rss", version="2.0")
    ch = ET.SubElement(rss, "channel")
    ET.SubElement(ch, "title").text = f"Adult Anime Release Watch {YEAR}"
    ET.SubElement(ch, "link").text = "https://github.com/zane6443/adult-anime-feed"
    ET.SubElement(ch, "description").text = f"Stable English release feed for {YEAR}; one release per item, automatically updated."
    ET.SubElement(ch, "language").text = "en"
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(NOW)

    for x in sorted(items, key=lambda y: (y["published"], y["title"]), reverse=True):
        e = ET.SubElement(ch, "item")
        ET.SubElement(e, "title").text = f"[{x['source']}] {x['title']}"
        ET.SubElement(e, "link").text = x["link"]
        ET.SubElement(e, "guid", isPermaLink="false").text = stable_guid(x["link"])
        status = "Upcoming" if x["published"] > NOW else "Released"
        parts = [
            f"<p><strong>Source:</strong> {html.escape(x['source'])}</p>",
            f"<p><strong>Status:</strong> {status}</p>",
            f"<p><strong>Release date:</strong> {x['published'].date()}</p>",
        ]
        if x.get("image"):
            im = html.escape(x["image"], quote=True)
            parts.append(f'<p><img src="{im}" alt="Preview" style="max-width:100%;height:auto;"></p>')
            ET.SubElement(e, "enclosure", url=x["image"], type="image/jpeg")
        if x.get("summary"):
            parts.append(f"<p>{html.escape(x['summary'])}</p>")
        if x["original_title"] != x["title"]:
            parts.append(f"<p><strong>Original title:</strong> {html.escape(x['original_title'])}</p>")
        ET.SubElement(e, "description").text = "".join(parts)
        ET.SubElement(e, "pubDate").text = format_datetime(x["published"])

    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True)


def main():
    items = []
    errors = []
    for label, url in LUNE_BRANDS.items():
        try:
            items.extend(parse_lune_brand(label, url))
        except Exception as e:
            errors.append(f"Lune {label}: {type(e).__name__}: {e}")
    try:
        items.extend(parse_mediabank())
    except Exception as e:
        errors.append(f"MediaBank: {type(e).__name__}: {e}")
    try:
        items.extend(parse_pink_pineapple_metadata())
    except Exception as e:
        errors.append(f"Pink Pineapple metadata: {type(e).__name__}: {e}")

    items = dedupe(items)
    data = build(items)
    for fn in ("release-feed.xml", "feed.xml"):
        with open(fn, "wb") as f:
            f.write(data)

    counts = Counter(x["source"] for x in items)
    with open("status.txt", "w", encoding="utf-8") as f:
        f.write(f"Updated: {NOW.isoformat()}\n")
        f.write(f"Year: {YEAR}\n")
        f.write(f"Final stable items: {len(items)}\n")
        f.write(f"Items with real images: {sum(bool(x.get('image')) for x in items)}\n")
        f.write(f"Items without images: {sum(not bool(x.get('image')) for x in items)}\n")
        f.write(f"Upcoming: {sum(x['published'] > NOW for x in items)}\n\n")
        f.write("Items by source:\n")
        for source, count in sorted(counts.items()):
            f.write(f"- {source}: {count}\n")
        if errors:
            f.write("\nSource errors:\n")
            for err in errors:
                f.write(f"- {err}\n")


if __name__ == "__main__":
    main()
