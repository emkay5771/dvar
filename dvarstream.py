from selenium import webdriver #type: ignore
from selenium.webdriver.chrome.service import Service #type: ignore
from selenium.webdriver.common.by import By #type: ignore
from selenium.webdriver.support.ui import WebDriverWait #type: ignore
from selenium.webdriver.support import expected_conditions as EC #type: ignore
import os
import re
import time
import threading
import logging
import html as html_mod #type: ignore
from urllib.parse import urljoin
import fitz as fitz #type: ignore
from base64 import b64decode, b64encode
from dateutil.relativedelta import relativedelta #type: ignore
from datetime import date #type: ignore
from datetime import datetime as dt #type: ignore
from datetime import timedelta #type: ignore
import requests #type: ignore
import streamlit as st #type: ignore
import pypdf as PyPDF2 #type: ignore
from pypdf import PdfWriter as PdfMerger #type: ignore
import glob
from pyluach import parshios, dates

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dvarstream")

st.set_page_config(page_title="Dvar Creator (BETA)", page_icon="📚", layout="wide", initial_sidebar_state="collapsed")

# Chrome flags below were tuned specifically for how Streamlit Cloud needs headless
# Chrome configured (sandboxing, /dev/shm size, download prefs). Do not change these
# without validating against a real Streamlit Cloud deployment.
options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--disable-gpu')
options.add_argument('--disable-dev-shm-usage')
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/87.0.4280.88 Safari/537.36")
options.add_experimental_option('prefs', {
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "plugins.always_open_pdf_externally": True
})
chrome_driver_path = "/usr/bin/chromedriver"
service = Service(chrome_driver_path)

# Guards the shared, cross-user cache files (dvar{session2}.pdf, Shnayim{session2}.pdf)
# so two concurrent Streamlit sessions in the same process can't both start a redundant
# download and race each other writing the same filename.
_dvar_cache_lock = threading.Lock()
_shnayim_cache_lock = threading.Lock()

@st.cache_data(ttl="24h", show_spinner=False)
def render_html_to_pdf(html, pdf_options):
    # Shared by the Sefaria-sourced builders below: renders a locally-authored HTML string
    # (rather than a scraped page) through the same headless-Chrome print-to-PDF pipeline
    # already used for chabad.org, so Hebrew RTL layout/fonts come from the browser for
    # free and we don't need a second PDF-generation library.
    # Cached cross-session by (html, pdf_options): 'html' already bakes in the date/content
    # and 'pdf_options' bakes in the user's scale setting, so this naturally caches at
    # exactly the right granularity (same day + same scale = same render, shared across
    # everyone) without caching across genuinely different content or scales.
    driver = webdriver.Chrome(options=options)
    try:
        encoded = b64encode(html.encode("utf-8")).decode("ascii")
        driver.get(f"data:text/html;charset=utf-8;base64,{encoded}")
        return driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
    finally:
        driver.quit()

@st.cache_data(ttl="24h", show_spinner=False)
def fetch_chabad_page(url, pdf_options, attempts=3, wait_seconds=10, extra_sleep=0):
    # Chabad.org's Cloudflare bot check is intermittent, not a hard block -- observed live,
    # the exact same page succeeds outright on one request and times out on the very next.
    # A fresh browser session on retry often gets through, so retry a few times with a
    # fresh driver before giving up and letting the caller fall through to the next tier.
    # Cached cross-session by (url, pdf_options) -- url already encodes the date and any
    # material-specific params (chapters/language), pdf_options encodes the user's scale
    # setting, so different scales or days naturally get separate cache entries. A failed
    # fetch raises rather than returns, and st.cache_data never caches a raised exception,
    # so failures are always retried fresh rather than "cached as broken". Fewer redundant
    # hits against chabad.org across all users also means less chance of tripping whatever
    # triggers its intermittent Cloudflare check in the first place.
    last_exc = None
    for attempt in range(1, attempts + 1):
        driver = webdriver.Chrome(options=options)
        try:
            driver.get(url)
            WebDriverWait(driver, wait_seconds).until(EC.presence_of_element_located((By.ID, "content")))
            if extra_sleep:
                time.sleep(extra_sleep)
            return driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
        except Exception as exc:
            last_exc = exc
            logger.warning("Chabad.org fetch attempt %d/%d failed for %s", attempt, attempts, url, exc_info=True)
        finally:
            driver.quit()
    raise last_exc

def dvarget(session2): # attempts to retrieve dvar malchus pdf
    # dvar{session2}.pdf is a cross-user cache (same weekly booklet for everyone within
    # the cache TTL), so guard the check-then-download-then-write sequence with a lock:
    # without it, two concurrent Streamlit sessions in the same process could both see
    # the file missing and race to scrape/write it at the same time.
    with _dvar_cache_lock:
        if os.path.exists(f"dvar{session2}.pdf"):
            logger.info("dvar%s.pdf already fetched by another session, reusing it", session2)
            return
        # Try the plain-HTTP path first and only spin up Chrome if it fails, so a change
        # at dvarmalchus.org that breaks the HTML scrape still degrades to the old
        # (slower but independently written) browser path instead of losing the booklet.
        if _dvarget_http(session2):
            return
        _dvarget_locked(session2)

DVAR_LINK_TEXTS = ["להורדת החוברת השבועית", "להורדת החוברת השבועית - חו״ל"]
DVAR_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"

def _dvarget_find_url(page_html, base_url):
    # Same idea as the XPath below -- match the button by its visible text rather than by
    # position, because the Elementor markup around it has drifted before while the text
    # has not. Anchors are matched with their tags stripped so nested <span> wrappers
    # don't matter, and the link texts are tried in order so the Israel edition wins over
    # the חו״ל one when the page happens to carry both.
    for link_text in DVAR_LINK_TEXTS:
        for href, inner in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', page_html, re.S | re.I):
            text = " ".join(re.sub(r"<[^>]+>", " ", html_mod.unescape(inner)).split())
            if link_text in text:
                return urljoin(base_url, href)
    return None

def _dvarget_http(session2):
    # dvarmalchus.org is plain WordPress behind Apache with no bot mitigation (unlike
    # chabad.org, which is why that one still needs a real browser), and the download
    # button's href is present in the served HTML. The Selenium path below was therefore
    # only ever launching Chrome to read one attribute and then follow it -- this does the
    # same job in ~2s instead of ~40s (driver launch + page load + the fixed 10s download
    # wait) with no Chrome involved, and was verified to return a byte-identical booklet.
    # It also removes the need for the CDP download-behaviour call and the mtime-based
    # "which file did the browser just drop in the working directory?" guesswork.
    # Returns True only if a real PDF landed on disk; every failure returns False so the
    # caller falls back to Selenium rather than raising.
    try:
        http = requests.Session()
        http.headers.update({"User-Agent": DVAR_USER_AGENT})
        home = http.get("https://dvarmalchus.org", timeout=30)
        home.raise_for_status()
        url = _dvarget_find_url(home.text, "https://dvarmalchus.org")
        if url is None:
            logger.warning("Weekly booklet link not found in dvarmalchus.org HTML")
            return False
        logger.info(f"fetching booklet over http -> {url}")
        booklet = http.get(url, timeout=120, allow_redirects=True)
        booklet.raise_for_status()
        if not booklet.content.startswith(b"%PDF-"):
            logger.warning("dvarmalchus.org returned %s, not a PDF, for %s",
                           booklet.headers.get("content-type"), url)
            return False
        # Write to a temp name and rename, so a partial download can never be mistaken
        # for a complete cached booklet by another session checking os.path.exists().
        tmp_path = f"dvar{session2}.pdf.tmp"
        with open(tmp_path, "wb") as f:
            f.write(booklet.content)
        os.replace(tmp_path, f"dvar{session2}.pdf")
        logger.info(f"Saved dvar{session2}.pdf over http")
        return True
    except Exception:
        logger.warning("HTTP fetch of dvarmalchus.org failed, falling back to Selenium", exc_info=True)
        return False

def _dvarget_button_xpath(link_text):
    # Match on the Elementor button's class + visible text instead of an absolute,
    # deeply-nested positional path: the site is WordPress/Elementor, and its exact div
    # nesting has drifted before (this is why the old absolute XPaths kept breaking),
    # but the button's class and text are far more likely to stay stable across
    # layout tweaks. Verified live: dvarmalchus.org currently renders this button
    # (identical href) in six different places on the page (header, footer, body).
    return (
        "//a[.//span[contains(@class,'elementor-button-text')]"
        f"[normalize-space(text())='{link_text}']]"
    )

def _dvarget_locked(session2):
    logger.info("Dvarget Running")
    driver = webdriver.Chrome(options=options)
    logger.info("Driver Opened")
    # Headless Chrome blocks file downloads by default regardless of the
    # download.* prefs above; this CDP call is what actually allows the
    # browser to save the booklet PDF to the working directory.
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": os.getcwd(),
    })
    driver.get("https://dvarmalchus.org")
    logger.info("Dvar Malchus Opened")
    download_started_at = time.time()

    link_texts = ["להורדת החוברת השבועית", "להורדת החוברת השבועית - חו״ל"]
    url = None
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, _dvarget_button_xpath(link_texts[0])))
        )
    except Exception:
        logger.warning("Weekly booklet button never appeared on dvarmalchus.org", exc_info=True)

    for link_text in link_texts:
        try:
            element = driver.find_element(By.XPATH, _dvarget_button_xpath(link_text))
            url = element.get_attribute("href")
            logger.info(f"clicking '{link_text}' -> {url}")
            driver.get(url)
            break
        except Exception:
            logger.debug(f"button with text '{link_text}' not found, trying next", exc_info=True)
            continue

    if url is None:
        logger.warning("Could not find the weekly booklet download button on dvarmalchus.org")
        driver.quit()
        raise RuntimeError("dvarmalchus.org download button not found")

    logger.info("waiting for download")
    time.sleep(10)

    try:
        # Our own app-managed files all use one of these known prefixes; anything else
        # that just landed in the working directory during this download is assumed to
        # be the booklet the browser just fetched. This replaces a hardcoded year string
        # ("2023") that went stale and could misidentify other users' files as ours.
        managed_prefixes = ("dvar", "Chumash", "Tanya", "Rambam", "Hayom", "Shnayim", "output_dynamic", "temp")
        for file in os.listdir():
            if not file.endswith(".pdf"):
                continue
            if file.startswith(managed_prefixes):
                continue
            if os.path.getmtime(file) < download_started_at:
                continue
            logger.info("renaming " + file)
            os.replace(file, f"dvar{session2}.pdf")
            break
    finally:
        driver.quit()

def chabadget(dor, opt, session): # retrieves chumash and tanya from chabad.org
    pdf_options = {
    'scale': scale,
    'margin-top': '0.1in',
    'margin-right': '0.1in',
    'margin-bottom': '0.1in',
    'margin-left': '0.1in',
    }
    if os.path.exists(f"Chumash{session}.pdf") != True:
        merger = PdfMerger()
        appended_any = False
        if 'Chumash' in opt:
            for i in dor:
                try:
                    url = f"https://www.chabad.org/dailystudy/torahreading.asp?tdate={i}#lt=he"
                    pdf = fetch_chabad_page(url, pdf_options)
                except Exception:
                    # Leave Chumash{session}.pdf absent on failure (rather than crash the
                    # whole Streamlit run) so the caller can fall through to the next tier.
                    logger.warning("Chabad.org Chumash fetch failed for %s", i, exc_info=True)
                    continue
                with open(f"temp{session}.pdf", "ab") as f:
                    f.write(b64decode(pdf["data"]))
                merger.append(f"temp{session}.pdf")
                appended_any = True

            if appended_any:
                merger.write(f"Chumash{session}.pdf")
            merger.close()
            if os.path.exists(f"temp{session}.pdf"):
                os.remove(f"temp{session}.pdf")
    if os.path.exists(f"Tanya{session}.pdf") != True:
        merger2 = PdfMerger()
        appended_any = False
        if 'Tanya' in opt:
            for i in dor:
                try:
                    url = f"https://www.chabad.org/dailystudy/tanya.asp?tdate={i}&commentary=false#lt=he"
                    pdf = fetch_chabad_page(url, pdf_options, extra_sleep=3)
                except Exception:
                    logger.warning("Chabad.org Tanya fetch failed for %s", i, exc_info=True)
                    continue
                with open(f"temp{session}.pdf", "ab") as f:
                    f.write(b64decode(pdf["data"]))
                merger2.append(f"temp{session}.pdf")
                appended_any = True

            if appended_any:
                merger2.write(f"Tanya{session}.pdf")
            merger2.close()
            if os.path.exists(f"temp{session}.pdf"):
                os.remove(f"temp{session}.pdf")

def rambamenglish(dor, session, opt): # retrieves all rambam versions from chabad.org
    pdf_options = {
    'scale': scale2,
    'margin-top': '0.1in',
    'margin-right': '0.1in',
    'margin-bottom': '0.1in',
    'margin-left': '0.1in',
    }
    merger = PdfMerger()
    if os.path.exists(f"Rambam{session}.pdf") != True:
        appended_any = False
        for i in dor:
            lang = ""
            chapters = ""
            if "Rambam (3)-Bilingual" in opt:
                    lang = "both"
                    chapters = "3"
            elif "Rambam (3)-Hebrew" in opt:
                lang = "he"
                chapters = "3"
            elif "Rambam (3)-English" in opt:
                lang = "primary"
                chapters = "3"
            elif "Rambam (1)-Bilingual" in opt:
                lang = "both"
                chapters = "1"
            elif "Rambam (1)-Hebrew" in opt:
                lang = "he"
                chapters = "1"
            elif "Rambam (1)-English" in opt:
                lang = "primary"
                chapters = "1"
            try:
                url = f"https://www.chabad.org/dailystudy/rambam.asp?rambamchapters={chapters}&tdate={i}#lt={lang}"
                pdf = fetch_chabad_page(url, pdf_options)
            except Exception:
                logger.warning("Chabad.org Rambam fetch failed for %s", i, exc_info=True)
                continue
            with open(f"temp{session}.pdf", "ab") as f:
                f.write(b64decode(pdf["data"]))

            merger.append(f"temp{session}.pdf")
            appended_any = True

        if appended_any:
            merger.write(f"Rambam{session}.pdf")
        merger.close()
        if os.path.exists(f"temp{session}.pdf"):
            os.remove(f"temp{session}.pdf")

def hayomyom(dor, session): #gets hayom yom from chabad.org
    pdf_options = {
    'scale': scale3,
    'margin-top': '0.1in',
    'margin-right': '0.1in',
    'margin-bottom': '0.1in',
    'margin-left': '0.1in',
    }
    merger3 = PdfMerger()
    if os.path.exists(f"Hayom{session}.pdf") != True:
        appended_any = False
        for i in dor:
            try:
                url = f"https://www.chabad.org/dailystudy/hayomyom.asp?tdate={i}"
                pdf = fetch_chabad_page(url, pdf_options)
            except Exception:
                # No further fallback exists for Hayom Yom (Sefaria doesn't have it and
                # it's not part of Dvar Malchus's contents either) -- surface a warning
                # instead of crashing, since there's nothing else to try.
                logger.warning("Chabad.org Hayom Yom fetch failed for %s; no fallback source available", i, exc_info=True)
                st.warning("Could not fetch Hayom Yom from Chabad.org, and no alternative source is available for it. Skipping.")
                continue
            with open(f"temp{session}.pdf", "ab") as f:
                f.write(b64decode(pdf["data"]))

            merger3.append(f"temp{session}.pdf")
            appended_any = True

        if appended_any:
            merger3.write(f"Hayom{session}.pdf")
        merger3.close()
        if os.path.exists(f"temp{session}.pdf"):
            os.remove(f"temp{session}.pdf")

SEFARIA_API_BASE = "https://www.sefaria.org/api"
WEEKDAY_TO_NUM = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Shabbos': 5, 'Sunday': 6}
ALIYAH_WEEKDAY_ORDER = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos']

_SEFARIA_HTML_HEAD = """<html><head><meta charset="utf-8"><style>
body { font-family: 'Noto Sans Hebrew', 'David', 'Times New Roman', serif; direction: rtl; padding: 2em; }
.hebrew { font-size: 1.3em; line-height: 1.8; margin-bottom: 1.2em; }
.english { font-size: 1.1em; line-height: 1.6; direction: ltr; text-align: left; margin-bottom: 1.2em; }
.bilingual-row { display: flex; gap: 1.5em; margin-bottom: 0.8em; align-items: flex-start; }
.bilingual-row .hebrew { flex: 1; margin-bottom: 0; }
.bilingual-row .english { flex: 1; margin-bottom: 0; }
.rashi { margin-top: 0.8em; padding-right: 1.5em; font-size: 1em; color: #333; border-right: 3px solid #999; }
.rashi-title { font-weight: bold; margin-bottom: 0.4em; }
h1 { text-align: center; font-size: 1.4em; }
</style></head><body>
"""
_SEFARIA_HTML_END = "</body></html>"

def _paired_bilingual_html(hebrew_verses, english_verses):
    # Side-by-side (Hebrew right column, English left column) per corresponding
    # verse/segment, rather than one long Hebrew block followed by one long English
    # block -- Sefaria returns 'he'/'text' as parallel, index-aligned lists.
    rows = max(len(hebrew_verses), len(english_verses))
    parts = []
    for i in range(rows):
        he = hebrew_verses[i] if i < len(hebrew_verses) else ""
        en = english_verses[i] if i < len(english_verses) else ""
        parts.append(
            '<div class="bilingual-row">'
            f'<div class="hebrew">{he}</div>'
            f'<div class="english">{en}</div>'
            '</div>'
        )
    return "".join(parts)

def _flatten_sefaria_text(value):
    # Sefaria's 'he'/'text' fields can be a plain string, or nested lists of strings
    # across chapter boundaries when a ref spans more than one chapter; flatten to one
    # ordered list of verse strings either way.
    if isinstance(value, str):
        return [value] if value else []
    flat = []
    for item in value or []:
        flat.extend(_flatten_sefaria_text(item))
    return flat

@st.cache_data(ttl="12h")
def sefaria_calendar(date_str):
    # Sefaria's own calendar already includes the correct per-day aliyah breakdown for
    # Parashat Hashavua in 'extraDetails.aliyot' -- including combined/double-parsha weeks
    # (verified live: it reflects the standard *combined* 7-aliyah division for weeks like
    # Vayakhel-Pekudei, not each portion's independent split), so no separate double-parsha
    # table or calendar library is needed on our end.
    try:
        y, m, d = date_str.split("-")
        resp = requests.get(f"{SEFARIA_API_BASE}/calendars", params={"year": y, "month": m, "day": d}, timeout=15)
        resp.raise_for_status()
        return resp.json().get("calendar_items", [])
    except Exception:
        logger.warning("Could not fetch Sefaria calendar for %s", date_str, exc_info=True)
        return []

@st.cache_data(ttl="24h", show_spinner=False)
def sefaria_fetch_text(ref):
    # Raises on failure (rather than returning None) so st.cache_data doesn't cache a
    # transient failure -- callers already wrap this call in a per-day try/except that
    # treats any exception as "skip this day", identical to the old None-check behavior.
    resp = requests.get(f"{SEFARIA_API_BASE}/texts/{requests.utils.quote(ref)}", params={"context": 0}, timeout=15)
    resp.raise_for_status()
    return resp.json()

def sefaria_fetch_rashi(ref):
    # A full aliyah's link list can be large (thousands of commentary links across many
    # verses), which is more prone to a dropped connection mid-download than the smaller
    # /texts calls -- retry once before giving up rather than silently losing Rashi.
    links = None
    for attempt in range(2):
        try:
            resp = requests.get(f"{SEFARIA_API_BASE}/links/{requests.utils.quote(ref)}", timeout=30)
            resp.raise_for_status()
            links = resp.json()
            break
        except Exception:
            logger.warning("Could not fetch Sefaria Rashi links for ref '%s' (attempt %d)", ref, attempt + 1, exc_info=True)
    if links is None:
        return []
    comments = []
    for link in links or []:
        collective_title = (link.get("collectiveTitle") or {}).get("en", "")
        if "Rashi" not in collective_title:
            continue
        he = link.get("he")
        if not he:
            continue
        comments.append(he if isinstance(he, str) else " ".join(_flatten_sefaria_text(he)))
    return comments

def sefaria_chumash_get(week, session): # 3rd-tier Chumash+Rashi fallback via Sefaria
    if os.path.exists(f"Chumash{session}.pdf"):
        return
    pdf_options = {'scale': scale, 'margin-top': '0.1in', 'margin-right': '0.1in',
                   'margin-bottom': '0.1in', 'margin-left': '0.1in'}
    merger = PdfMerger()
    appended_any = False
    for weekday_name in week:
        try:
            day_date = date.today() + relativedelta(weekday=WEEKDAY_TO_NUM[weekday_name])
            calendar_items = sefaria_calendar(day_date.strftime("%Y-%m-%d"))
            parsha_item = next((i for i in calendar_items if i.get("title", {}).get("en") == "Parashat Hashavua"), None)
            aliyot = ((parsha_item or {}).get("extraDetails") or {}).get("aliyot", [])
            aliyah_index = ALIYAH_WEEKDAY_ORDER.index(weekday_name)
            if len(aliyot) <= aliyah_index:
                logger.warning("No Sefaria aliyah data for %s on %s", weekday_name, day_date)
                continue
            ref = aliyot[aliyah_index]
            text_data = sefaria_fetch_text(ref)
            if not text_data:
                continue
            hebrew_verses = _flatten_sefaria_text(text_data.get("he"))
            if not hebrew_verses:
                continue
            rashi_comments = sefaria_fetch_rashi(ref)
            rashi_html = ""
            if rashi_comments:
                rashi_html = (
                    '<div class="rashi"><div class="rashi-title">רש"י</div>'
                    + "<br><br>".join(rashi_comments) + "</div>"
                )
            html = (
                _SEFARIA_HTML_HEAD + f"<h1>{ref}</h1>"
                + f'<div class="hebrew">{"<br>".join(hebrew_verses)}</div>'
                + rashi_html + _SEFARIA_HTML_END
            )
            pdf = render_html_to_pdf(html, pdf_options)
            with open(f"temp{session}.pdf", "ab") as f:
                f.write(b64decode(pdf["data"]))
            merger.append(f"temp{session}.pdf")
            appended_any = True
        except Exception:
            logger.warning("Sefaria Chumash fetch failed for %s", weekday_name, exc_info=True)
            continue
    if appended_any:
        merger.write(f"Chumash{session}.pdf")
    merger.close()
    if os.path.exists(f"temp{session}.pdf"):
        os.remove(f"temp{session}.pdf")

def sefaria_tanya_get(week, session): # 3rd-tier Tanya fallback via Sefaria
    if os.path.exists(f"Tanya{session}.pdf"):
        return
    pdf_options = {'scale': scale, 'margin-top': '0.1in', 'margin-right': '0.1in',
                   'margin-bottom': '0.1in', 'margin-left': '0.1in'}
    merger = PdfMerger()
    appended_any = False
    for weekday_name in week:
        try:
            day_date = date.today() + relativedelta(weekday=WEEKDAY_TO_NUM[weekday_name])
            calendar_items = sefaria_calendar(day_date.strftime("%Y-%m-%d"))
            tanya_item = next((i for i in calendar_items if i.get("title", {}).get("en") == "Tanya Yomi"), None)
            if not tanya_item:
                logger.warning("No Sefaria Tanya Yomi entry for %s", day_date)
                continue
            ref = tanya_item["ref"]
            text_data = sefaria_fetch_text(ref)
            if not text_data:
                continue
            hebrew_verses = _flatten_sefaria_text(text_data.get("he"))
            if not hebrew_verses:
                continue
            html = (
                _SEFARIA_HTML_HEAD + f"<h1>{ref}</h1>"
                + f'<div class="hebrew">{"<br>".join(hebrew_verses)}</div>' + _SEFARIA_HTML_END
            )
            pdf = render_html_to_pdf(html, pdf_options)
            with open(f"temp{session}.pdf", "ab") as f:
                f.write(b64decode(pdf["data"]))
            merger.append(f"temp{session}.pdf")
            appended_any = True
        except Exception:
            logger.warning("Sefaria Tanya fetch failed for %s", weekday_name, exc_info=True)
            continue
    if appended_any:
        merger.write(f"Tanya{session}.pdf")
    merger.close()
    if os.path.exists(f"temp{session}.pdf"):
        os.remove(f"temp{session}.pdf")

def sefaria_rambam_get(week, session, opt): # 3rd-tier Rambam fallback via Sefaria
    if os.path.exists(f"Rambam{session}.pdf"):
        return
    pdf_options = {'scale': scale2, 'margin-top': '0.1in', 'margin-right': '0.1in',
                   'margin-bottom': '0.1in', 'margin-left': '0.1in'}
    three_chapters = any(o in opt for o in ["Rambam (3)-Bilingual", "Rambam (3)-Hebrew", "Rambam (3)-English"])
    calendar_title = "Daily Rambam (3 Chapters)" if three_chapters else "Daily Rambam"
    lang_both = any(o in opt for o in ["Rambam (3)-Bilingual", "Rambam (1)-Bilingual"])
    lang_en_only = any(o in opt for o in ["Rambam (3)-English", "Rambam (1)-English"])
    merger = PdfMerger()
    appended_any = False
    for weekday_name in week:
        try:
            day_date = date.today() + relativedelta(weekday=WEEKDAY_TO_NUM[weekday_name])
            calendar_items = sefaria_calendar(day_date.strftime("%Y-%m-%d"))
            # The 3-chapter cycle can appear as two calendar entries on days where it
            # crosses a Mishneh Torah sub-book boundary -- include every match.
            rambam_items = [i for i in calendar_items if i.get("title", {}).get("en") == calendar_title]
            if not rambam_items:
                logger.warning("No Sefaria '%s' entry for %s", calendar_title, day_date)
                continue
            title_parts = []
            sections_html = []
            for item in rambam_items:
                ref = item["ref"]
                title_parts.append(ref)
                text_data = sefaria_fetch_text(ref)
                if not text_data:
                    continue
                hebrew_verses = _flatten_sefaria_text(text_data.get("he")) if not lang_en_only else []
                english_verses = _flatten_sefaria_text(text_data.get("text")) if (lang_both or lang_en_only) else []
                block = ""
                if lang_both and (hebrew_verses or english_verses):
                    block = _paired_bilingual_html(hebrew_verses, english_verses)
                elif hebrew_verses:
                    block = f'<div class="hebrew">{"<br>".join(hebrew_verses)}</div>'
                elif english_verses:
                    block = f'<div class="english">{"<br>".join(english_verses)}</div>'
                if block:
                    sections_html.append(block)
            if not sections_html:
                continue
            html = (
                _SEFARIA_HTML_HEAD + f"<h1>{' / '.join(title_parts)}</h1>"
                + "<hr>".join(sections_html) + _SEFARIA_HTML_END
            )
            pdf = render_html_to_pdf(html, pdf_options)
            with open(f"temp{session}.pdf", "ab") as f:
                f.write(b64decode(pdf["data"]))
            merger.append(f"temp{session}.pdf")
            appended_any = True
        except Exception:
            logger.warning("Sefaria Rambam fetch failed for %s", weekday_name, exc_info=True)
            continue
    if appended_any:
        merger.write(f"Rambam{session}.pdf")
    merger.close()
    if os.path.exists(f"temp{session}.pdf"):
        os.remove(f"temp{session}.pdf")

def parshaget(date1): #get parsha from date for shnayim mikra
    year, date, month = date1.split(", ")
    year, date, month = int(year), int(date), int(month)
    parsha = parshios.getparsha_string(dates.GregorianDate(year, date, month), israel=False, hebrew=True)
    st.write(f"This week's parsha is {parsha}.")
    return parsha

SHNAYIM_REPO_RAW_BASE = "https://raw.githubusercontent.com/emkay5771/shnayimfiles/master"

def shnayimget(session2, parsha, opt): #get shnayim mikra from github repo
    if 'Shnayim Mikra' not in opt:
        return
    # filename2 intentionally keeps whatever pyluach returned verbatim (including the
    # comma pyluach puts between double-parsha names, e.g. "מטות, מסעי") because the
    # files in the shnayimfiles repo are named that way too.
    filename2 = " ".join(parsha.split(" "))
    logger.info(f"Shnayim Mikra parsha file: {filename2}")

    # Shnayim{session2}.pdf is a cross-user cache (same file for everyone each week),
    # same reasoning as dvarget's lock above.
    with _shnayim_cache_lock:
        if os.path.exists(f"Shnayim{session2}.pdf"):
            logger.info("Shnayim%s.pdf already fetched by another session, reusing it", session2)
            return
        url = f"{SHNAYIM_REPO_RAW_BASE}/{requests.utils.quote(filename2)}.pdf"
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise ValueError("downloaded file does not look like a PDF")
        except Exception:
            logger.warning(f"Could not fetch Shnayim Mikra for '{filename2}' from {url}", exc_info=True)
            return
        tmp_path = f"Shnayim{session2}.pdf.tmp"
        with open(tmp_path, "wb") as f:
            f.write(response.content)
        os.replace(tmp_path, f"Shnayim{session2}.pdf")
        logger.info(f"Saved Shnayim{session2}.pdf")

def daytoheb(week, dow): #converts day of week from week in streamlit to hebrew date, to be used when parsing dvar malchus
    for i in week:
        if i == 'Sunday':
            dow.append('יום ראשון')
        elif i == 'Monday':
            dow.append('יום שני')
        elif i == 'Tuesday':
            dow.append('יום שלישי')
        elif i == 'Wednesday':
            dow.append('יום רביעי')
        elif i == 'Thursday':
            dow.append('יום חמישי')
        elif i == 'Friday':
            dow.append('יום שישי')
        elif i == 'Shabbos':
            dow.append('שבת קודש')
    return dow

def opttouse(opt, optconv): #sorts through options from opt to optconv, converting some options to hebrew for dvar malchus, to be used when compiling pdf 
    for i in opt:
        if i == 'Chumash':
            optconv.append('חומש יומי')
        elif i == 'Tanya':
            optconv.append('תניא יומי')
        elif i == 'Rambam (3)-Hebrew':
            optconv.append('רמב"ם - שלושה פרקים ליום')
        elif i == 'Rambam (1)-Hebrew':
            # The booklet carries a one-chapter-a-day Rambam section alongside the
            # three-chapter one, with the same per-day sub-bookmarks. It simply was never
            # mapped here, so this option always fell through to the catch-all below and
            # was fetched from Chabad.org even when the booklet was already downloaded.
            optconv.append('רמב"ם - פרק אחד ליום')
        elif i == 'Haftorah' or i == 'Krias Hatorah (includes Haftorah)':
            logger.debug("appended haftorah")
            optconv.append('חומש לקריאה בציבור')
        elif i == 'Project Likutei Sichos (Hebrew)':
            optconv.append('לקוטי שיחות')
        elif i == 'Maamarim':
            optconv.append('מאמרים')
        elif i == 'Shnayim Mikra':
            optconv.append('Shnayim Mikra')
        elif 'Rambam' in i or 'Hayom Yom' in i:
            optconv.append(i)
    return optconv

def daytorambam(week, dor): #converts day of week from week in streamlit to date format for chabad.org, for rambamenglish(), hayonyom(), and chabadget()
    today = date.today()
    day_to_n = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Shabbos': 5, 'Sunday': 6}
    for i in week:
        n = day_to_n[i]
        logger.debug(f"day offset: {n}")
        linkappend = today + relativedelta(weekday=n)
        y, m, d = str(linkappend).split("-")
        dor.append(f'{m}%2F{d}%2F{y}')
    return dor

def find_next_top_level_bookmark(toc, current_index, last_page):
    for i in range(current_index + 1, len(toc)):
        if toc[i][0] == 1:
            return toc[i][2] - 2
    # No further top-level bookmark means this section runs to the end of the booklet.
    # Returning None here used to flow straight into insert_pdf(to_page=None), which
    # raises instead of meaning "to the end" -- so hand back the real last page.
    return last_page

# --- Dvar Malchus day boundaries -------------------------------------------------------
# A day's shiur in the booklet does not stop at a page break: it runs partway down the
# page where the *next* day's heading appears. The original code approximated that with a
# per-section constant (-1 for Chumash/Rambam, -2 for Tanya), which is only right when a
# heading happens to land at the same place every week. Measured across 12 booklets that
# approximation was wrong on ~18% of day ranges -- Tanya's -2 truncated 4-6 of its 7 days
# in *every* booklet tested, silently cutting off the end of each shiur. So rather than
# guessing an offset, find the headings and decide each boundary by looking at the page.

BOOKLET_DAYS = ['יום ראשון', 'יום שני', 'יום שלישי', 'יום רביעי', 'יום חמישי', 'יום שישי', 'שבת קודש']
# Running header sits at y~27 and the folio/footer within ~40pt of the bottom; both repeat
# the day name on every page, so they have to be excluded or they drown out the real heading.
BOOKLET_HEADER_Y = 45
BOOKLET_FOOTER_MARGIN = 40
# Constants used only by the legacy fallback below, kept so its behaviour is unchanged.
BOOKLET_LEGACY_OFFSETS = {
    'חומש יומי': 1,
    'תניא יומי': 2,
    'רמב"ם - שלושה פרקים ליום': 1,
    'רמב"ם - פרק אחד ליום': 1,
}

def _booklet_body_lines(doc, page_number):
    page = doc[page_number]
    height = page.rect.height
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type"):
            continue  # image block, no text spans
        for line in block["lines"]:
            if line["bbox"][1] > BOOKLET_HEADER_Y and line["bbox"][3] < height - BOOKLET_FOOTER_MARGIN:
                lines.append(line)
    lines.sort(key=lambda ln: ln["bbox"][1])
    return lines

def _booklet_has_body_above(doc, page_number, y):
    return any(line["bbox"][3] < y - 2 for line in _booklet_body_lines(doc, page_number))

def _booklet_section(doc, toc, section_title):
    # Locate a top-level section plus its per-day sub-bookmarks, and work out the last
    # page the section can possibly occupy (one before the next top-level bookmark).
    for i, entry in enumerate(toc):
        if entry[0] != 1 or entry[1] != section_title or not entry[2]:
            continue
        days = []
        for sub in toc[i + 1:]:
            if sub[0] == 1:
                break
            if sub[0] == 2 and sub[1] in BOOKLET_DAYS:
                days.append(sub)
        if not days:
            continue
        next_top = next((s[2] for s in toc[i + 1:] if s[0] == 1), None)
        hi = (next_top - 2) if next_top else doc.page_count - 1
        return {"lo": entry[2] - 1, "hi": hi, "days": days}
    return None

def _booklet_heading_signature(doc, section):
    # The day-heading font is not uniform across the booklet -- the Chumash/Tanya/Rambam
    # sections use one face and size, while היום יום uses a different one entirely -- so
    # derive it per section instead of hardcoding. Taking the most common (font, size)
    # across all seven sub-bookmark pages means one wrong bookmark can't skew the result,
    # and a re-typeset booklet adapts instead of silently matching nothing.
    counts = {}
    for _, day, page in section["days"]:
        best = None
        if not 0 <= page - 1 < doc.page_count:
            continue
        for line in _booklet_body_lines(doc, page - 1):
            for span in line["spans"]:
                if day in span["text"] and (best is None or span["size"] > best[1]):
                    best = (span["font"], round(span["size"], 2))
        if best:
            counts[best] = counts.get(best, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: item[1])[0]

def _booklet_scan_day_headings(doc, section, signature):
    # Scan the section's pages and ignore the sub-bookmark page numbers entirely. The
    # publisher's own bookmarks are sometimes wrong -- of 12 booklets checked, 3 had a day
    # bookmark off by up to 4 pages, and in one case the offset maths therefore gave that
    # day a single page instead of six. Scanning finds the heading wherever it actually
    # is, so a bad bookmark costs nothing. Matching on the font signature rather than the
    # day text alone also avoids false hits on ordinary prose that happens to mention a
    # day name (e.g. "שהחג חל ביום שני" inside a commentary).
    found = []
    seen = set()
    for page_number in range(max(section["lo"], 0), min(section["hi"], doc.page_count - 1) + 1):
        for line in _booklet_body_lines(doc, page_number):
            text = "".join(span["text"] for span in line["spans"])
            day = next((d for d in BOOKLET_DAYS if d in text), None)
            if day is None or day in seen:
                continue
            for span in line["spans"]:
                if (span["font"] == signature[0] and abs(span["size"] - signature[1]) < 0.05
                        and day in span["text"]):
                    found.append((page_number, line["bbox"][1], day))
                    seen.add(day)
                    break
    return found

def _booklet_day_ranges_by_heading(doc, section):
    signature = _booklet_heading_signature(doc, section)
    if signature is None:
        return None
    headings = _booklet_scan_day_headings(doc, section, signature)
    # Completeness gate: only trust the scan when it found exactly the days the section's
    # own sub-bookmarks advertise. A partial scan is worse than no scan, because the days
    # it missed would silently disappear from the output rather than merely being wrong --
    # so anything short of a full match falls back wholesale.
    if {day for _, _, day in headings} != {day for _, day, _ in section["days"]}:
        logger.warning("Heading scan found %d/%d days", len(headings), len(section["days"]))
        return None
    ranges = {}
    for index, (page_number, _, day) in enumerate(headings):
        if index + 1 < len(headings):
            next_page, next_y, _ = headings[index + 1]
            # Include the page the next heading sits on only when this day's text actually
            # continues onto it, i.e. when there is body content above that heading. When
            # the next heading starts at the top of its page, this day ended on the page
            # before it.
            end = next_page if _booklet_has_body_above(doc, next_page, next_y) else next_page - 1
        else:
            # Last day of the section: a new section always opens on a fresh page with its
            # own banner, so this day never continues into it.
            end = section["hi"]
        ranges[day] = (page_number, min(max(end, page_number), section["hi"]))
    return ranges

def _booklet_day_ranges_by_offset(section, section_title):
    # The original constant-offset behaviour, kept as a fallback for the case where the
    # heading scan can't identify every day (a re-typeset booklet, say). It is measurably
    # wrong on a fair share of days -- see the note above -- so it is a safety net, not an
    # equivalent method, and the caller warns the user when it is used.
    days = section["days"]
    ranges = {}
    for index, (_, day, page) in enumerate(days):
        start = page - 1
        is_last = index + 1 == len(days)
        next_bookmark = days[index + 1][2] if not is_last else section["hi"] + 2
        offset = 2 if (section_title == 'חומש יומי' and is_last) else BOOKLET_LEGACY_OFFSETS.get(section_title, 1)
        end = next_bookmark - offset
        ranges[day] = (start, min(max(end, start), section["hi"]))
    return ranges

def booklet_day_ranges(doc, toc, section_title):
    # Returns {hebrew day name: (first page, last page)} as inclusive 0-based page indexes,
    # or None if this section isn't a day-based one in this booklet.
    section = _booklet_section(doc, toc, section_title)
    if section is None:
        return None
    ranges = _booklet_day_ranges_by_heading(doc, section)
    if ranges is not None:
        return ranges
    logger.warning("Falling back to legacy page offsets for %s", section_title)
    st.warning(f"Couldn't read the day boundaries for {section_title} in this week's Dvar Malchus, "
               "so its page ranges were estimated and may be slightly short or long. "
               "Please check that section against the original booklet.")
    return _booklet_day_ranges_by_offset(section, section_title)

def dedupe(pages, pages2, pages3, start_page, end_page): #dedupes the pages when appending, to ensure that pages aren't repeated
    pages2.append(start_page)
    pages2.append(end_page)
    # Consecutive days legitimately share the page they break on, so a day's range can
    # overlap the previous one. Trim this range down to the part not already inserted.
    #
    # This tracks every page taken, not just the two endpoints, and only records pages
    # once the trimmed range is known to be non-empty. The endpoint-only version could
    # adjust an endpoint by a single page (not enough when several are already taken),
    # and it recorded the adjusted endpoints even when the range came out empty -- which
    # claimed a page nothing had actually inserted and left a hole in the output.
    seen = set(pages)
    while start_page <= end_page and start_page in seen:
        start_page = start_page + 1
    while start_page <= end_page and end_page in seen:
        end_page = end_page - 1
    if start_page > end_page:
        return start_page, end_page  # fully covered already; caller skips, `pages` untouched
    for page_number in range(start_page, end_page + 1):
        if page_number not in seen:
            pages.append(page_number)
            pages3.append(page_number)
    return start_page,end_page

def ensure_material(name, session, chabad_fetch, sefaria_fetch):
    # Shared 3-tier trigger: dvar malchus is handled by callers before this point, so this
    # just tries chabad.org, then falls through to Sefaria only if chabad.org didn't
    # produce the file. Both fetchers already leave the file absent on failure instead of
    # raising, so "file still missing" is the sole signal to try the next tier.
    if os.path.exists(f"{name}{session}.pdf"):
        return
    chabad_fetch()
    if os.path.exists(f"{name}{session}.pdf"):
        return
    logger.info("%s not available from Chabad.org, trying Sefaria...", name)
    sefaria_fetch()

def dynamicmake(dow, optconv, opt, source, session): #compiles pdf after collecting all the necessary files
    output_dir = ""
    toc = []
    doc_out = fitz.open()
    pages = []
    pages2 = []
    pages3 = []
    kriahattatch = False
    # The Rambam pills are multi-select but rambamenglish() only ever writes a single
    # Rambam{session}.pdf, so without this guard selecting two Chabad-sourced Rambam
    # variants at once inserted the same file twice.
    rambamattached = False
    if source == True:
        try:
            doc = fitz.open(f"dvar{session2}.pdf")
            toc = doc.get_toc()
            if cover == True:
                doc_out.insert_pdf(doc, from_page=0, to_page=0)
        except Exception:
            logger.warning("Something went wrong opening Dvar Malchus PDF, falling back to Chabad.org", exc_info=True)
            st.write("Something went wrong with Dvar Malchus. Attempting to use Chabad.org.")
            logger.info(opt)
            if all(option not in chabadoptions for option in opt) and any(option in opt for option in ['Project Likutei Sichos', 'Maamarim', 'Haftorah']):
                st.error("Project Likutei Sichos, the Haftorah, and Maamarim are not available from Chabad.org. Please try again.")
                st.stop()
            source = False
            if 'Chumash' in opt:
                ensure_material("Chumash", session, lambda: chabadget(dor, opt, session),
                                 lambda: sefaria_chumash_get(week, session))
            if 'Tanya' in opt:
                ensure_material("Tanya", session, lambda: chabadget(dor, opt, session),
                                 lambda: sefaria_tanya_get(week, session))
            if rambam_requested:
                # Closes a latent gap: this inline fallback used to only call chabadget()
                # and never rambamenglish(), so if Dvar Malchus's file existed but was
                # corrupt/unreadable (caught here), Rambam silently never got fetched and
                # the script crashed later trying to open a Rambam file that never existed.
                ensure_material("Rambam", session, lambda: rambamenglish(dor, session, opt),
                                 lambda: sefaria_rambam_get(week, session, opt))

    if source == False:
            logger.info("Chabad.org")
            logger.info(opt)
            for option in opt:
                if option == 'Chumash':
                    doc_out.insert_pdf(fitz.open(f"Chumash{session}.pdf"))
                elif option == 'Tanya':
                    doc_out.insert_pdf(fitz.open(f"Tanya{session}.pdf"))
                elif 'Rambam' in option:
                    if not rambamattached:
                        doc_out.insert_pdf(fitz.open(f"Rambam{session}.pdf")) #type: ignore
                        rambamattached = True
                elif option == 'Hayom Yom':
                    doc_out.insert_pdf(fitz.open(f"Hayom{session}.pdf"))
                elif option == 'Shnayim Mikra':
                    doc_out.insert_pdf(fitz.open(f"Shnayim{session2}.pdf"))
                if all(option not in chabadoptions for option in opt) and any(option in opt for option in ['Project Likutei Sichos', 'Maamarim', 'Haftorah', 'Krias Hatorah (includes Haftorah)']):
                    st.error("Project Likutei Sichos, Kriah, the Haftorah, and Maamarim are not available from Chabad.org. Please try again.")
                    st.stop()
                
    else:
        for q in optconv:
            # Day ranges are resolved once per section rather than once per selected day,
            # since the heading scan reads every page of the section.
            day_ranges = booklet_day_ranges(doc, toc, q) #type: ignore
            if day_ranges:
                for z in dow:
                    if z not in day_ranges:
                        continue
                    start_page, end_page = day_ranges[z]
                    logger.debug(f"{q} {z}: Current Start Page: {start_page}. Current End Page: {end_page}")
                    # Consecutive days legitimately share the page they break on, so the
                    # dedupe below still earns its keep: it keeps that shared page from
                    # being inserted twice when both days are selected.
                    start_page, end_page = dedupe(pages, pages2, pages3, start_page, end_page)
                    logger.debug(f"New Start Page: {start_page}. New End Page: {end_page}")
                    if start_page > end_page:
                        # Everything unique to this day sits on a page an earlier selected
                        # day already contributed, so there is nothing left to add.
                        continue
                    doc_out.insert_pdf(doc, from_page=start_page, to_page=end_page) #type: ignore

            if q == 'חומש לקריאה בציבור' or q == 'מאמרים' or q == 'לקוטי שיחות':
                for i, item in enumerate(toc): #type: ignore
                    if q == 'לקוטי שיחות':
                        for word in item[1].split():
                            if word == 'לקוטי' and item[1].split()[item[1].split().index(word) + 1] == 'שיחות':
                                logger.debug("Likutei Sichos found")
                                with open(f"dvar{session2}.pdf", "rb") as pdf_file:
                                    pdf_reader = PyPDF2.PdfReader(pdf_file)
                                    page_num_start = item[2] - 1
                                    page_num_end = find_next_top_level_bookmark(toc, i, doc.page_count - 1) #type: ignore
                                    doc_out.insert_pdf(doc, from_page=page_num_start, to_page=page_num_end) #type: ignore
                    if q == 'מאמרים':
                        for word in item[1].split():
                            if word == 'מאמר':
                                logger.debug("Maamarim found")
                                with open(f"dvar{session2}.pdf", "rb") as pdf_file:
                                    pdf_reader = PyPDF2.PdfReader(pdf_file)
                                    page_num_start = item[2] - 1
                                    page_num_end = find_next_top_level_bookmark(toc, i, doc.page_count - 1) #type: ignore
                                    doc_out.insert_pdf(doc, from_page=page_num_start, to_page=page_num_end) #type: ignore
                    if item[1] == 'חומש לקריאה בציבור' and q == 'חומש לקריאה בציבור':
                        with open(f"dvar{session2}.pdf", "rb") as pdf_file:
                            pdf_reader = PyPDF2.PdfReader(pdf_file)
                            page_num_start = item[2] - 1
                            page_num_end = toc[i+1][2] - 3 #type: ignore
                            logger.debug("Torah reading found")
                            if "Krias Hatorah (includes Haftorah)" in opt and kriahattatch == False:
                                logger.debug("Kriah found")
                                doc_out.insert_pdf(doc, from_page=page_num_start, to_page=page_num_end)
                                kriahattatch = True
                            elif 'Haftorah' in opt and 'Krias Hatorah (includes Haftorah)' not in opt:
                                for page_num in range(page_num_start, page_num_end):
                                    logger.debug("Haftorah found")
                                    page = pdf_reader.pages[page_num]
                                    text = page.extract_text()
                                    if "ברכת הפטורה" in text or "xtd enk dxhtdd renyl" in text:
                                        doc_out.insert_pdf(doc, from_page=page_num, to_page=page_num_end) #type: ignore
                                        continue

            if 'Rambam' in q:
                # Only the non-Hebrew Rambam variants reach here as raw option strings;
                # the Hebrew ones are mapped to their booklet section titles by opttouse()
                # and are handled by the day-range branch above.
                if not rambamattached:
                    doc_out.insert_pdf(fitz.open(f"Rambam{session}.pdf"))
                    rambamattached = True
                    logger.debug("Appended Rambam")
                continue

            if q == 'Hayom Yom':
                doc_out.insert_pdf(fitz.open(f"Hayom{session}.pdf"))
                logger.debug("Appended Hayom Yom")
                continue

            if q == 'Shnayim Mikra':
                doc_out.insert_pdf(fitz.open(f"Shnayim{session2}.pdf"))
                logger.debug("Appended Shnayim Mikra")
                continue
                       
    doc_out.save(os.path.join(output_dir, f"output_dynamic{session}.pdf"))
    doc_out.close()


@st.cache_data(ttl="12h")
def dateset():
    session2 = dt.now()
    logger.info(f"Session: {session2}")
    return session2

with st.form(key="dvarform", clear_on_submit=False): #streamlit form for user input
    st.title("Dvar Creator 📚 (BETA)")
    st.info("Need more than 1 week? Check out 📖[Chitas Collator](https://chitas-collator.streamlit.app/)!")
    st.markdown("""This app is designed to create a printout for Chitas, Rambam, plus a few other things. To get the materials directly and support the original publishers, go to ![](https://www.google.com/s2/favicons?domain=dvarmalchus.org&sz=16) :blue[**[Dvar Malchus](https://dvarmalchus.org/)**]
    and ![](https://www.google.com/s2/favicons?domain=chabad.org&sz=16) :orange[**[Chabad.org](https://www.chabad.org/dailystudy/default_cdo/jewish/Daily-Study.htm/)**].
    For Chumash, Tanya, and Rambam, if both of those are unavailable this app will fall back to ![](https://www.google.com/s2/favicons?domain=sefaria.org&sz=16) :green[**[Sefaria](https://www.sefaria.org/)**].
    """, unsafe_allow_html=True)
    session2 = dateset()
    date1 = date.today().strftime('%Y, %-m, %-d')
    year, day, month = date1.split(", ")
    year, day, month = int(year), int(day), int(month)
    parsha = parshios.getparsha_string(dates.GregorianDate(year, day, month), israel=False, hebrew=True)
    week = st.pills("Select which days of the week you would like to print.", options=['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos'], selection_mode="multi", default=None)
    st.write("**Select which materials you would like to print.** (Select as many as you'd like!)")
    #TODO: add tehillim and rename to "Chitas"
    basics = st.pills('Basics:', options=['Chumash', 'Tanya', 'Hayom Yom'], selection_mode="multi", default=None)
    rambamopts = st.pills('Rambam:', options=['Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English'], selection_mode="multi", default=None)
    extras = st.pills('MISC:', options=['Project Likutei Sichos (Hebrew)', 'Maamarim', 'Krias Hatorah (includes Haftorah)', 'Haftorah', 'Shnayim Mikra'], selection_mode="multi", default=None)
    source = st.toggle(label='Try to use Dvar Malchus, or get from Chabad.org? If toggled on, it will attempt to get from Dvar Malchus.', value=True)
    with st.expander("Advanced Options"):
        cover = st.checkbox('Include the cover page from Dvar Malchus?', value=False)
        scaleslide = st.slider('Change the scale of Chumash and Tanya from Chabad.Org. Default is 100%.', 30, 100, 100)
        st.write("Scale is", scaleslide,"%")
        scale = scaleslide/100
        scaleslide2 = st.slider('Change the scale of Rambam from Chabad.Org. Default is 50%.', 30, 100, 50)
        st.write("Scale is", scaleslide2,"%")
        scale2 = scaleslide2/100
        scaleslide3 = st.slider('Change the scale of Hayom Yom from Chabad.Org. Default is 80%.', 30, 100, 80)
        st.write("Scale is", scaleslide3,"%")
        scale3 = scaleslide3/100
        

    submit_button = st.form_submit_button(label="Generate PDF ▶️")

if submit_button: #if the user submits the form, run the following code, which will create the pdf using above functions
    if 'id' not in st.session_state:
        st.session_state['id'] = dt.now()
    opt = []
    # st.pills() in multi-select mode returns [] when nothing is selected, but guard
    # with truthiness anyway in case a future Streamlit change reintroduces None.
    if basics:
        logger.info("appending selected basics")
        opt += basics
    if rambamopts:
        logger.info("appending selected rambam")
        opt += rambamopts
    if extras:
        logger.info("appending selected extras")
        opt += extras
    logger.info(opt)
    session = st.session_state['id']
    weekorder = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos']
    optorder = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom', 'Project Likutei Sichos (Hebrew)', 'Maamarim', 'Haftorah', 'Krias Hatorah (includes Haftorah)', 'Shnayim Mikra']
    daydependent = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom']
    chabadoptions = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom']
    dow = []
    optconv = []
    dor = []
    opt = sorted(opt, key=optorder.index)
    # st.pills() already returns [] (not None) when no day is selected, but normalize
    # defensively so the "week == []" checks below are guaranteed to fire correctly.
    week = week or []
    week = sorted(week, key=weekorder.index)
    daytoheb(week, dow)
    daytorambam(week, dor)

    opttouse(opt, optconv)

    logger.info(optconv)
    logger.info(source)
    if week == [] and any(x in opt for x in daydependent)==True:
        st.error("Please select at least one day of the week if trying to select anything from the 'Basics' or 'Rambam' sections.")
        st.stop()
    if week == [] and 'חומש לקריאה בציבור' in optconv or 'מאמרים' in optconv or 'לקוטי שיחות' in optconv or 'Shnayim Mikra' in optconv:
        week = ['Sunday']
        logger.info(optconv)
    logger.info(week)
    if source == True:
        if 'Chumash' in opt or 'Tanya' in opt or 'Haftorah' in opt or 'Rambam (3)-Hebrew' in opt or 'Rambam (1)-Hebrew' in opt or 'Project Likutei Sichos (Hebrew)' in opt or 'Maamarim' in opt or 'Krias Hatorah (includes Haftorah)' in opt:
            if os.path.exists(f"dvar{session2}.pdf") == False:
                try:
                    with st.spinner('Attempting to download Dvar Malchus...'):
                        dvarget(session2)
                except Exception:
                    logger.warning("Dvar Malchus fetch failed, falling back to Chabad.org", exc_info=True)
                    st.write("Dvar Malchus not found. Using Chabad.org...")
                    source = False
                    cover = False
        else:
            st.write("Dvar Malchus not needed. Using Chabad.org...")
            source = False
            cover = False
    rambam_requested = ('Rambam (3)-Hebrew' in opt or 'Rambam (3)-Bilingual' in opt or 'Rambam (3)-English' in opt
                        or 'Rambam (1)-Bilingual' in opt or 'Rambam (1)-English' in opt or 'Rambam (1)-Hebrew' in opt)
    with st.spinner('Creating PDF...'):
        if source == False:
            if 'Chumash' in opt:
                ensure_material("Chumash", session, lambda: chabadget(dor, opt, session),
                                 lambda: sefaria_chumash_get(week, session))
            if 'Tanya' in opt:
                ensure_material("Tanya", session, lambda: chabadget(dor, opt, session),
                                 lambda: sefaria_tanya_get(week, session))
            if rambam_requested:
                ensure_material("Rambam", session, lambda: rambamenglish(dor, session, opt),
                                 lambda: sefaria_rambam_get(week, session, opt))
        if source == True:
            if 'Rambam (3)-Bilingual' in opt or 'Rambam (3)-English' in opt or 'Rambam (1)-Bilingual' in opt or 'Rambam (1)-English' in opt:
                ensure_material("Rambam", session, lambda: rambamenglish(dor, session, opt),
                                 lambda: sefaria_rambam_get(week, session, opt))
            elif ('Rambam (3)-Hebrew' in opt or 'Rambam (1)-Hebrew' in opt) and os.path.exists(f"dvar{session2}.pdf") == False:
                ensure_material("Rambam", session, lambda: rambamenglish(dor, session, opt),
                                 lambda: sefaria_rambam_get(week, session, opt))

        if 'Hayom Yom' in opt:
            hayomyom(dor, session)

        if 'Shnayim Mikra' in opt:
            shnayimget(session2, parsha, opt)

        dynamicmake(dow, optconv, opt, source, session)

    if os.path.exists(f"output_dynamic{session}.pdf"):
        st.success("PDF created successfully!")
        st.balloons()
        with open(f"output_dynamic{session}.pdf", "rb") as f:
            st.download_button(label="Download ⬇️", data=f, file_name="Custom_Chitas.pdf", mime="application/pdf")


    def cleanup_stale(prefix, keep_filename, max_age):
        # Filenames are "{prefix}{timestamp}.pdf". Use an actual prefix/suffix strip
        # (not str.lstrip/rstrip, which strip character sets and can mangle the
        # timestamp) to recover the timestamp and decide what's safe to delete.
        for file in glob.glob(f"{prefix}*.pdf"):
            if file == keep_filename:
                continue
            timestamp = file.removeprefix(prefix).removesuffix(".pdf")
            try:
                file_datetime = dt.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                logger.debug(f"Skipping cleanup of unrecognized file: {file}")
                continue
            if dt.now() - file_datetime > max_age:
                try:
                    os.remove(file)
                except OSError:
                    logger.debug(f"Could not remove stale file {file}", exc_info=True)

    cleanup_stale("Rambam", f'Rambam{session}.pdf', timedelta(minutes=1))
    cleanup_stale("Chumash", f'Chumash{session}.pdf', timedelta(minutes=1))
    cleanup_stale("Tanya", f'Tanya{session}.pdf', timedelta(minutes=1))
    cleanup_stale("dvar", f'dvar{session2}.pdf', timedelta(hours=14))
    cleanup_stale("Shnayim", f'Shnayim{session2}.pdf', timedelta(hours=14))
    cleanup_stale("output_dynamic", f'output_dynamic{session}.pdf', timedelta(minutes=1))
st.markdown("**Any major bugs noticed? Features that you'd like to see? Comments? Email me [📧 here!](mailto:mkievman@outlook.com)**", unsafe_allow_html=True)

if not submit_button:
    with st.expander("**Changelog:**"):
        st.markdown("**New in latest update (7-19-26)**: <br/> **[FIX]** Days taken from Dvar Malchus now include their full shiur. Because a day's learning ends partway down a page rather than at a page break, the app used to cut some days short or add a page of the next day — Tanya was losing the end of most days every week. Day boundaries are now found on the page itself instead of being estimated. <br/> **[NEW]** Rambam (1 chapter) in Hebrew now comes from Dvar Malchus like the 3-chapter cycle, instead of always falling back to Chabad.org. <br/> **[FIX]** Selecting more than one Rambam option at once no longer repeats the same section twice. <br/> **[NEW]** Dvar Malchus downloads are much faster (a couple of seconds instead of around forty) — the app now fetches the booklet directly rather than driving a browser, falling back to the old method if needed.", unsafe_allow_html=True)
        st.markdown("**Past Changes (7-3-26)**: <br/> **[FIX]** Fixed a crash when two people generated a booklet from Dvar Malchus at nearly the same time. <br/> **[NEW]** Modernized the app's dependencies (Streamlit and others) and swapped out a few unmaintained UI components for Streamlit's own native ones. <br/> **[NEW]** Added a custom dark theme. <br/> **[NEW]** Chumash/Tanya/Rambam/Hayom Yom fetches are now shared across everyone requesting the same day, so repeat generations are faster and put less load on Chabad.org.", unsafe_allow_html=True)
        st.markdown("**Past Changes (7-2-26)**: <br/> **[FIX]** Dvar Malchus downloads were silently failing in headless mode; fixed by explicitly allowing Chrome to save the file. <br/> **[FIX]** Chabad.org's daily study pages had started intermittently failing (an anti-bot check); fetches now retry automatically with a fresh session before giving up. <br/> **[NEW]** Added Sefaria as a 3rd fallback source for Chumash (with Rashi), Tanya, and Rambam if both Dvar Malchus and Chabad.org are unavailable, including correct handling of combined/double-parsha weeks. <br/> **[FIX]** Bilingual Rambam now shows Hebrew and English side by side instead of one after the other. <br/> **[FIX]** A single Chabad.org or Sefaria hiccup on one material no longer crashes the whole app.", unsafe_allow_html=True)
        st.markdown("**Past Changes (1-17-24)**: <br/> **[FIX]** Updated location of Dvar Malchus download button.", unsafe_allow_html=True)
        st.markdown("**Past Changes (7-17-23)**: <br/> **1:** Repeated compilations of materials from Dvar Malchus should be considerably faster. <br/> **2:** Shnayim mikra gets considerably faster on subsequent reruns. <br/> **3:** Fixes to maamarim and sichos to fail less often.", unsafe_allow_html=True)
if submit_button:
    if os.path.exists(f"output_dynamic{session}.pdf"):
        with st.expander("NOTE: If you are reciving last weeks materials, please click here."):
            newtime= st.button("Clear Cached Time", on_click=dateset.clear)
