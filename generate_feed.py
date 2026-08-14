from __future__ import annotations

import html, hashlib, re, xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urljoin

import feedparser, requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator

UA = "Mozilla/5.0 (compatible; adult-anime-feed/3.0; +https://github.com/zane6443/adult-anime-feed)"
TIMEOUT = 25
NOW = datetime.now(timezone.utc)
YEAR = NOW.year
translator = GoogleTranslator(source="auto", target="en")

LUNE_BRANDS = {
    "Bunny Walker": "https://www.lune-soft.jp/ova/brand_ova/bunnywalker",
    "Antechinus": "https://www.lune-soft.jp/ova/brand_ova/antechinus",
}
MEDIABANK = {
    "Queen Bee": 22,
    "Gold Bear": 14,
    "White Bear": 9,
    "Hot Bear": 8,
    "King Bee": 23,
}
GENERIC = {
    "Pink Pineapple": "https://www.pinkpineapple.co.jp/",
    "PoRO": "https://www.poro.cc/",
}
BAD = ("500 (server error)", "server error", "internal server error", "404 not found", "page not found", "403 forbidden")
BAD_IMG = ("logo", "favicon", "icon", "placeholder", "noimage", "no-image", "loading", "spinner", "avatar", "title_detail.png")


def clean(s): return re.sub(r"\s+", " ", s or "").strip()

def fetch(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    r.raise_for_status()
    # MediaBank serves UTF-8 content with headers that requests can mis-detect,
    # which caused mojibake such as "å¯..." in previous feed versions.
    if "mediabank.co.jp" in url:
        r.encoding = "utf-8"
    elif not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
        r.encoding = r.apparent_encoding or "utf-8"
    return r

def tr(s, n=1000):
    s = clean(s)[:n]
    if not s: return ""
    try: return clean(translator.translate(s))
    except Exception: return s

def date_from(text):
    for p in (r"(20\d{2})[./年](\d{1,2})[./月](\d{1,2})", r"(20\d{2})-(\d{1,2})-(\d{1,2})"):
        m = re.search(p, text)
        if m:
            try: return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
            except ValueError: pass
    return None

def this_year(dt): return bool(dt and dt.year == YEAR)

def good_image(u):
    if not u: return False
    low = u.lower()
    return u.startswith(("http://", "https://")) and not any(x in low for x in BAD_IMG)

def page_image(url):
    try:
        soup = BeautifulSoup(fetch(url).text, "html.parser")
        for sel in (".product_detail img", ".product img", "article img", ".entry-content img", ".post-content img", ".article-body img", "main img", "#contents img"):
            for img in soup.select(sel):
                src = img.get("data-src") or img.get("data-lazy-src") or img.get("src")
                u = urljoin(url, src) if src else None
                if good_image(u): return u
        for attrs in ({"property":"og:image"},{"name":"twitter:image"}):
            tag = soup.find("meta", attrs=attrs)
            if tag and tag.get("content"):
                u = urljoin(url, tag["content"])
                if good_image(u): return u
    except Exception: pass
    return None

def item(source, title, link, summary, dt, image):
    return {"source":source,"original_title":clean(title),"title":tr(title,500),"link":link,
            "summary":tr(summary,1000),"published":dt,"image":image}

def bad_entry(title, summary=""):
    b = f"{title} {summary}".lower(); return any(x in b for x in BAD)


def parse_lune_news():
    out=[]
    f=feedparser.parse(fetch("https://www.lune-soft.jp/feed").content)
    for e in f.entries[:120]:
        title=clean(getattr(e,"title","")); summary=clean(BeautifulSoup(getattr(e,"summary",""),"html.parser").get_text(" "))
        dt=None
        if getattr(e,"published_parsed",None): dt=datetime(*e.published_parsed[:6],tzinfo=timezone.utc)
        if not this_year(dt) or bad_entry(title,summary) or not any(k in f"{title} {summary}" for k in ("OVA","アニメ","発売")): continue
        link=getattr(e,"link","")
        img=None
        for attr in ("media_content","media_thumbnail"):
            for m in getattr(e,attr,[]) or []:
                if good_image(m.get("url")): img=m["url"]; break
            if img: break
        img=img or page_image(link)
        out.append(item("Lune Soft / News",title,link,summary,dt,img))
    return out


def parse_lune_brand(label,url):
    out=[]; soup=BeautifulSoup(fetch(url).text,"html.parser")
    for a in soup.find_all("a", href=True):
        text=clean(a.get_text(" ",strip=True))
        if "OVA" not in text.upper(): continue
        dt=date_from(text)
        if not this_year(dt): continue
        title=re.sub(r"\s*20\d{2}年\d{1,2}月\d{1,2}日発売.*$","",text).strip()
        href=urljoin(url,a["href"])
        if not title or bad_entry(title,text): continue
        img=page_image(href)
        out.append(item(f"Lune Pictures / {label}",title,href,text,dt,img))
    return out


def mediabank_product(model, source):
    url=f"https://www.mediabank.co.jp/product.php?model={model}"
    soup=BeautifulSoup(fetch(url).text,"html.parser")
    # Prefer visible product headings, and strip the site suffix.
    heading = soup.select_one("h1, h2, .title, .product_title, .product-name")
    title=clean((heading or soup.title).get_text(" ",strip=True)) if (heading or soup.title) else model
    title=re.sub(r"\s*[|｜-]\s*Media\s*Bank.*$", "", title, flags=re.I).strip()
    text=clean(soup.get_text(" ",strip=True)); dt=date_from(text)
    if not this_year(dt) or bad_entry(title,text): return None
    img=None
    for tag in soup.select(".product_detail img, .product img, #contents img, main img, img"):
        src=tag.get("data-src") or tag.get("data-lazy-src") or tag.get("src")
        u=urljoin(url,src) if src else None
        if good_image(u): img=u; break
    img=img or page_image(url)
    # Avoid sending the whole navigation/header text through translation.
    summary=text
    for marker in ("作品概要", "STORY", "ストーリー"):
        pos=summary.find(marker)
        if pos >= 0:
            summary=summary[pos:]
            break
    return item(f"MediaBank / {source}",title,url,summary[:1400],dt,img)


def parse_mediabank(source,label_id):
    out=[]; seen=set()
    for pg in (1,2):
        url=f"https://www.mediabank.co.jp/label.php?id={label_id}&pg={pg}&stat=0"
        soup=BeautifulSoup(fetch(url).text,"html.parser")
        text=soup.get_text(" ",strip=True)
        for model in re.findall(r"品番[：:]\s*([A-Z]+(?:-B)?-?\d+|[A-Z]{2,5}-[A-Z]?\d+)", text):
            if model in seen: continue
            seen.add(model)
            try:
                x=mediabank_product(model,source)
                if x: out.append(x)
            except Exception:
                pass
        for a in soup.find_all("a",href=True):
            m=re.search(r"product\.php\?model=([^&\"']+)",a["href"])
            if not m or m[1] in seen: continue
            seen.add(m[1])
            try:
                x=mediabank_product(m[1],source)
                if x: out.append(x)
            except Exception:
                pass
    return out


def parse_generic(source,url):
    out=[]; soup=BeautifulSoup(fetch(url).text,"html.parser")
    for node in (soup.select("article,.post,.news,.item,.product,li") or []):
        a=node.find("a",href=True)
        if not a: continue
        text=clean(node.get_text(" ",strip=True)); dt=date_from(text)
        if not this_year(dt): continue
        title=clean(a.get_text(" ",strip=True)); blob=f"{title} {text}".lower()
        if not title or bad_entry(title,text) or not any(k in blob for k in ("ova","発売","release","anime","アニメ")): continue
        href=urljoin(url,a["href"]); out.append(item(source,title,href,text,dt,page_image(href)))
    return out


def key(x):
    base=re.sub(r"\s+","",x["original_title"].lower())
    base=re.sub(r"20\d{2}.*$","",base)
    return hashlib.sha256(f"{x['published'].date()}|{base}".encode()).hexdigest()


def build(items):
    rss=ET.Element("rss",version="2.0"); ch=ET.SubElement(rss,"channel")
    ET.SubElement(ch,"title").text=f"Adult Anime Release Watch {YEAR} v3"
    ET.SubElement(ch,"link").text="https://zane6443.github.io/adult-anime-feed/"
    ET.SubElement(ch,"description").text=f"English adult-anime releases and announced releases from Jan 1 {YEAR}, automatically updated."
    ET.SubElement(ch,"language").text="en"; ET.SubElement(ch,"lastBuildDate").text=format_datetime(NOW)
    for x in items[:300]:
        e=ET.SubElement(ch,"item"); ET.SubElement(e,"title").text=f"[{x['source']}] {x['title']}"
        ET.SubElement(e,"link").text=x["link"]; ET.SubElement(e,"guid",isPermaLink="false").text=key(x)
        status = "Upcoming" if x["published"] > NOW else "Released"
        p=[f"<p><strong>Source:</strong> {html.escape(x['source'])}</p>",f"<p><strong>Status:</strong> {status}</p>",f"<p><strong>Release date:</strong> {x['published'].date()}</p>"]
        if x.get("image"):
            im=html.escape(x["image"],quote=True); p.append(f'<p><img src="{im}" alt="Preview" style="max-width:100%;height:auto;"></p>')
        if x.get("summary"): p.append(f"<p>{html.escape(x['summary'])}</p>")
        if x["original_title"]!=x["title"]: p.append(f"<p><strong>Original title:</strong> {html.escape(x['original_title'])}</p>")
        ET.SubElement(e,"description").text="".join(p)
        if x.get("image"): ET.SubElement(e,"enclosure",url=x["image"],type="image/jpeg")
        ET.SubElement(e,"pubDate").text=format_datetime(x["published"])
    ET.indent(rss,space="  "); return ET.tostring(rss,encoding="utf-8",xml_declaration=True)


def main():
    all_items=[]; errors=[]
    try: all_items+=parse_lune_news()
    except Exception as e: errors.append(f"Lune news: {type(e).__name__}: {e}")
    for label,url in LUNE_BRANDS.items():
        try: all_items+=parse_lune_brand(label,url)
        except Exception as e: errors.append(f"Lune {label}: {type(e).__name__}: {e}")
    for source,label_id in MEDIABANK.items():
        try: all_items+=parse_mediabank(source,label_id)
        except Exception as e: errors.append(f"MediaBank {source}: {type(e).__name__}: {e}")
    for source,url in GENERIC.items():
        try: all_items+=parse_generic(source,url)
        except Exception as e: errors.append(f"{source}: {type(e).__name__}: {e}")

    uniq={}
    for x in all_items:
        k=key(x); old=uniq.get(k)
        if old is None or (not old.get("image") and x.get("image")) or len(x.get("summary",''))>len(old.get("summary",'')): uniq[k]=x
    items=sorted(uniq.values(),key=lambda x:(x["published"],x["title"]),reverse=True)
    data=build(items)
    for fn in ("feed.xml","feed-v2.xml","feed-v3.xml"):
        open(fn,"wb").write(data)
    counts=Counter(x["source"] for x in items)
    with open("status.txt","w",encoding="utf-8") as f:
        f.write(f"Updated: {NOW.isoformat()}\nYear: {YEAR}\nItems: {len(items)}\nItems with images: {sum(bool(x.get('image')) for x in items)}\nUpcoming: {sum(x['published'] > NOW for x in items)}\n\nItems by source:\n")
        for s,c in sorted(counts.items()): f.write(f"- {s}: {c}\n")
        if errors:
            f.write("\nSource errors:\n")
            for e in errors: f.write(f"- {e}\n")

if __name__=="__main__": main()
