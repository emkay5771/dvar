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
import chabad_texts

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dvarstream"); append_material = lambda doc_out, path, label: doc_out.insert_pdf(fitz.open(path)) if os.path.exists(path) else logger.warning("Skipping %s: %s was not created (all fetch tiers failed).", label, path)

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
        # The Chabad.org layouts load web fonts (Hebrew with cantillation marks).
        # Printing before they arrive falls back to whatever fonts the server has, so wait
        # for them, but never fail the render over it.
        try:
            driver.set_script_timeout(15)
            driver.execute_async_script("document.fonts.ready.then(() => arguments[arguments.length - 1]())")
        except Exception:
            logger.warning("Web fonts did not finish loading before printing", exc_info=True)
        return driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
    finally:
        driver.quit()

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

# Chumash, Tanya, Rambam and Hayom Yom from Chabad.org. chabad_texts.py fetches each day's
# text from Chabad.org's Torah Texts API and lays it out for print; these wrappers render it
# and write the per-tab files the rest of the app expects. If Chabad.org fails for any day
# of Chumash, Tanya or Rambam, no file is written, so ensure_material() falls back to
# Sefaria for the whole material instead of mixing sources.
# 'Chumash' and 'Tanya' are the Hebrew (and come from the Dvar Malchus booklet when that's
# in use); the Bilingual (and English) versions always come from Chabad.org.
CHABAD_CHUMASH_OPTIONS = [("Chumash", "he"), ("Chumash-Bilingual", "both")]
CHUMASH_NAMES = [name for name, _ in CHABAD_CHUMASH_OPTIONS]
CHABAD_TANYA_OPTIONS = [("Tanya", "he"), ("Tanya-Bilingual", "both"), ("Tanya-English", "en")]
TANYA_NAMES = [name for name, _ in CHABAD_TANYA_OPTIONS]
HAYOM_YOM_NAMES = ["Hayom Yom-Hebrew", "Hayom Yom-Bilingual", "Hayom Yom"]  # 'Hayom Yom' is the English
PRINT_SIZES = {"Space saving": 0.8, "Standard": 1.0, "Large print": 1.3}
WEEKLY_LABELS = {'Project Likutei Sichos (Hebrew)': 'Likutei Sichos', 'Krias Hatorah (includes Haftorah)': 'Krias HaTorah (with Haftorah)',
                 'Haftorah': 'Haftorah only'}

def language_label(option):
    # The form shows each material once with its languages as the choices.
    if option in ("Chumash", "Tanya") or option.endswith("-Hebrew"):
        return "Hebrew"
    if option.endswith("-Bilingual"):
        return "Bilingual"
    if option.endswith("-English") or option == "Hayom Yom":
        return "English"
    return option

CHABAD_RAMBAM_OPTIONS = [("Rambam (3)-Hebrew", 3, "he"), ("Rambam (3)-Bilingual", 3, "both"), ("Rambam (3)-English", 3, "en"),
                         ("Rambam (1)-Hebrew", 1, "he"), ("Rambam (1)-Bilingual", 1, "both"), ("Rambam (1)-English", 1, "en")]

def _chabad_dates(dor):
    # dor holds 'MM%2FDD%2FYYYY' strings (see daytorambam()).
    return [date(int(y), int(m), int(d)) for m, d, y in (i.split("%2F") for i in dor)]

def _chabad_pdf(material, sections, scale_value, path):
    pdf = render_html_to_pdf(chabad_texts.page(material, sections),
                             {"scale": scale_value, "printBackground": True, "preferCSSPageSize": True})
    with open(f"{path}.tmp", "wb") as f:
        f.write(b64decode(pdf["data"]))
    os.replace(f"{path}.tmp", path)

def chabadget(dor, opt, session): # Chumash and Tanya from chabad.org
    # Every Chumash or Tanya option picked (Hebrew, Bilingual, English) goes into that
    # material's one file.
    builds = [("Chumash", "chumash", scale, [(lambda language: lambda day: chabad_texts.chumash_section(day, language, chumash_columns))(language)
                                             for name, language in CHABAD_CHUMASH_OPTIONS if name in opt]),
              ("Tanya", "tanya", scale4, [(lambda language: lambda day: chabad_texts.tanya_section(day, language))(language)
                                  for name, language in CHABAD_TANYA_OPTIONS if name in opt])]
    for name, material, size, variants in builds:
        if not variants or os.path.exists(f"{name}{session}.pdf"):
            continue
        try:
            sections = [build(day) for build in variants for day in _chabad_dates(dor)]
        except Exception:
            logger.warning("Chabad.org %s failed; leaving it to the fallback", name, exc_info=True)
            continue
        _chabad_pdf(material, sections, size, f"{name}{session}.pdf")

def rambamenglish(dor, session, opt): # all Rambam versions from chabad.org
    # Every Rambam option picked is included, one after another. This used to fetch only
    # the first one, because each needed its own Chabad.org page.
    if os.path.exists(f"Rambam{session}.pdf"):
        return
    try:
        sections = [chabad_texts.rambam_section(day, chapters, language)
                    for option, chapters, language in CHABAD_RAMBAM_OPTIONS if option in opt
                    for day in _chabad_dates(dor)]
    except Exception:
        logger.warning("Chabad.org Rambam failed; leaving it to the fallback", exc_info=True)
        return
    if sections:
        _chabad_pdf("rambam", sections, scale2, f"Rambam{session}.pdf")

def hayomyom(dor, dow, session, opt, source): # Hayom Yom: English from chabad.org, Hebrew from the booklet
    # No other source has Hayom Yom, so a day that fails is skipped with a warning rather
    # than losing the whole week. Every option picked goes into the one Hayom Yom file.
    if os.path.exists(f"Hayom{session}.pdf"):
        return
    sections = []
    booklet_languages = [language for name, language in (("Hayom Yom-Hebrew", "he"), ("Hayom Yom-Bilingual", "both")) if name in opt]
    if booklet_languages:
        days = None
        if source and os.path.exists(f"dvar{session2}.pdf"):
            try:
                with fitz.open(f"dvar{session2}.pdf") as booklet:
                    days = booklet_hayomyom(booklet, booklet.get_toc())
            except Exception:
                logger.warning("Couldn't read Hayom Yom from the booklet", exc_info=True)
        if not days:
            st.warning("Hebrew Hayom Yom comes from this week's Dvar Malchus, which wasn't used for this PDF, so the Hebrew "
                       "and Bilingual Hayom Yom are not in it. To include them, turn on 'Use Dvar Malchus when possible'.")
        else:
            by_weekday = {found["weekday"]: found for found in days}
            try:
                week_dates = chabad_texts.booklet_week_dates(days, date.today())
            except Exception:
                logger.warning("Couldn't date the booklet's Hayom Yom", exc_info=True)
                week_dates = {}
            for language in booklet_languages:
                for weekday in dow:
                    try:
                        sections.append(chabad_texts.hayomyom_booklet_section(week_dates[weekday], by_weekday[weekday]["entries"], language))
                    except Exception:
                        logger.warning("Booklet Hayom Yom failed for %s", weekday, exc_info=True)
                        st.warning(f"Couldn't read Hayom Yom for {weekday} from this week's Dvar Malchus, so that day is not in this PDF.")
    if "Hayom Yom" in opt:
        for day in _chabad_dates(dor):
            try:
                sections.append(chabad_texts.hayomyom_section(day))
            except Exception:
                logger.warning("Hayom Yom failed for %s", day, exc_info=True)
                st.warning(f"Couldn't get Hayom Yom for {day.strftime('%A')} from Chabad.org, so that day is not in this PDF.")
    if sections:
        _chabad_pdf("hayomyom", sections, scale3, f"Hayom{session}.pdf")

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
.rashi.english { direction: ltr; text-align: left; border-right: none; border-left: 3px solid #999; padding-right: 0; padding-left: 1.5em; }
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

def sefaria_fetch_rashi(ref, language="he"):
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
        text = link.get("he" if language == "he" else "text")
        if not text:
            continue
        comments.append(text if isinstance(text, str) else " ".join(_flatten_sefaria_text(text)))
    return comments

def sefaria_chumash_get(week, session, opt=("Chumash",)): # 3rd-tier Chumash+Rashi fallback via Sefaria
    if os.path.exists(f"Chumash{session}.pdf"):
        return
    # One version covering what was picked, as for Tanya: both languages if both are wanted.
    want_en = "Chumash-Bilingual" in opt
    want_he = True
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
            hebrew_verses = _flatten_sefaria_text(text_data.get("he")) if want_he else []
            english_verses = _flatten_sefaria_text(text_data.get("text")) if want_en else []
            if not hebrew_verses and not english_verses:
                continue
            if want_he and want_en:
                verses_html = _paired_bilingual_html(hebrew_verses, english_verses)
            elif hebrew_verses:
                verses_html = f'<div class="hebrew">{"<br>".join(hebrew_verses)}</div>'
            else:
                verses_html = f'<div class="english">{"<br>".join(english_verses)}</div>'
            rashi_html = ""
            for language, title, css in (("he", 'רש"י', "rashi"), ("en", "Rashi", "rashi english")):
                if not (want_he if language == "he" else want_en):
                    continue
                rashi_comments = sefaria_fetch_rashi(ref, language)
                if rashi_comments:
                    rashi_html += (f'<div class="{css}"><div class="rashi-title">{title}</div>'
                                   + "<br><br>".join(rashi_comments) + "</div>")
            html = _SEFARIA_HTML_HEAD + f"<h1>{ref}</h1>" + verses_html + rashi_html + _SEFARIA_HTML_END
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

def sefaria_tanya_get(week, session, opt=("Tanya",)): # 3rd-tier Tanya fallback via Sefaria
    if os.path.exists(f"Tanya{session}.pdf"):
        return
    # Sefaria has Kehot's English translation as well. One version is made, covering what
    # was picked: side by side if both languages are wanted, else one of them.
    want_en = "Tanya-English" in opt or "Tanya-Bilingual" in opt
    want_he = "Tanya" in opt or "Tanya-Bilingual" in opt
    pdf_options = {'scale': scale4, 'margin-top': '0.1in', 'margin-right': '0.1in',
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
            hebrew_verses = _flatten_sefaria_text(text_data.get("he")) if want_he else []
            english_verses = _flatten_sefaria_text(text_data.get("text")) if want_en else []
            if want_he and want_en and (hebrew_verses or english_verses):
                block = _paired_bilingual_html(hebrew_verses, english_verses)
            elif hebrew_verses:
                block = f'<div class="hebrew">{"<br>".join(hebrew_verses)}</div>'
            elif english_verses:
                block = f'<div class="english">{"<br>".join(english_verses)}</div>'
            else:
                continue
            html = _SEFARIA_HTML_HEAD + f"<h1>{ref}</h1>" + block + _SEFARIA_HTML_END
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
# Weeks where the shnayimfiles repo spells the file differently from pyluach's name.
SHNAYIM_FILE_ALIASES = {
    'אחרי מות, קדושים': 'אחרי, קדושים',
}

def _shnayim_download(name):
    url = f"{SHNAYIM_REPO_RAW_BASE}/{requests.utils.quote(name)}.pdf"
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError(f"{url} did not return a PDF")
    return response.content

def shnayimget(session2, parsha, opt): #get shnayim mikra from github repo
    if 'Shnayim Mikra' not in opt:
        return
    if not parsha:
        # pyluach has no parsha when this Shabbos is Yom Tov (Pesach, Shavuos, Sukkos...).
        # This used to crash on None.split() below and take the whole PDF down with it.
        logger.info("No weekly parsha this Shabbos; skipping Shnayim Mikra")
        st.warning("This Shabbos is Yom Tov, so there is no weekly parsha and no Shnayim Mikra in this PDF.")
        return
    # filename2 intentionally keeps whatever pyluach returned verbatim (including the
    # comma pyluach puts between double-parsha names, e.g. "מטות, מסעי") because the
    # files in the shnayimfiles repo are named that way too.
    filename2 = SHNAYIM_FILE_ALIASES.get(parsha, " ".join(parsha.split(" ")))
    logger.info(f"Shnayim Mikra parsha file: {filename2}")

    # Shnayim{session2}.pdf is a cross-user cache (same file for everyone each week),
    # same reasoning as dvarget's lock above.
    with _shnayim_cache_lock:
        if os.path.exists(f"Shnayim{session2}.pdf"):
            logger.info("Shnayim%s.pdf already fetched by another session, reusing it", session2)
            return
        try:
            content = _shnayim_download(filename2)
        except Exception:
            logger.warning(f"Could not fetch Shnayim Mikra for '{filename2}'", exc_info=True)
            content = None
            parts = [part.strip() for part in filename2.split(",")]
            if len(parts) > 1:
                # Some combined weeks (e.g. נצבים, וילך) only exist in the repo as one file
                # per parsha, so join those instead.
                try:
                    merged = fitz.open()
                    for part in parts:
                        merged.insert_pdf(fitz.open(stream=_shnayim_download(part), filetype="pdf"))
                    content = merged.tobytes()
                    logger.info(f"Built Shnayim Mikra for '{filename2}' from {parts}")
                except Exception:
                    logger.warning(f"Could not build Shnayim Mikra for '{filename2}' from {parts}", exc_info=True)
                    content = None
        if content is None:
            st.warning(f"Couldn't find Shnayim Mikra for {parsha}, so it is not in this PDF.")
            return
        tmp_path = f"Shnayim{session2}.pdf.tmp"
        with open(tmp_path, "wb") as f:
            f.write(content)
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
        elif 'Rambam' in i or 'Hayom Yom' in i or i in ('Tanya-Bilingual', 'Tanya-English', 'Chumash-Bilingual'):
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

def _booklet_earliest_page(toc, index):
    # The earliest 1-based page among a top-level bookmark and its own sub-bookmarks. The
    # publisher's top-level bookmarks have pointed past the real section start before
    # (תניא יומי -> 86 when Tanya starts on 78), while the sub-bookmarks under it were right.
    pages = [toc[index][2]]
    for sub in toc[index + 1:]:
        if sub[0] == 1:
            break
        pages.append(sub[2])
    return min(page for page in pages if page > 0)

def booklet_maamarim_pages(toc, last_page):
    # Returns (first page, last page) as inclusive 0-based indexes, or None if the booklet
    # has no maamarim section. The section's top-level bookmark has had three names:
    # 'מאמר - ד"ה ...', 'מאמר דא"ח' and, nearly every week since mid-2026, 'מאמרים'. The old
    # check looked for the exact word 'מאמר', so the plural matched nothing and the maamar
    # quietly dropped out of the PDF. Match a top-level title whose first word starts with
    # 'מאמר' instead. Staying at the top level keeps out 'ספר המאמרים תרפ״ט', which is a
    # sub-bookmark in the רבותינו נשיאי חב״ד section near the back of the booklet.
    for i, (level, title, page) in enumerate(toc):
        words = _booklet_normalize(title).split()
        if level != 1 or page <= 0 or not words or not words[0].startswith('מאמר'):
            continue
        start = _booklet_earliest_page(toc, i) - 1
        next_index = next((j for j in range(i + 1, len(toc)) if toc[j][0] == 1 and toc[j][2] > 0), None)
        end = last_page if next_index is None else _booklet_earliest_page(toc, next_index) - 2
        return (start, min(max(end, start), last_page))
    return None

BOOKLET_KRIAH_TITLE = 'חומש לקריאה בציבור'
# Footer of the Shabbos Mincha reading, which follows the Kriah (קריאת התורה למנחת שבת קודש).
BOOKLET_MINCHA_FOOTER = 'מנחת שבת'
# Text on the Maftir page (and sometimes the page after it) where the Haftorah begins. The
# second string is how pypdf extracts the booklet's legacy-encoded Hebrew.
BOOKLET_HAFTORAH_MARKERS = ("ברכת הפטורה", "xtd enk dxhtdd renyl")

def _booklet_footer_names(doc, page_number, name):
    # Every booklet page names its section in the footer, next to the page number. The
    # text often comes out of the PDF reversed ("רוביצב האירקל שמוח"), so check both ways.
    rect = doc[page_number].rect
    footer = _booklet_normalize(doc[page_number].get_text(
        clip=fitz.Rect(0, rect.height - BOOKLET_FOOTER_MARGIN, rect.width, rect.height)))
    return name in footer or name in footer[::-1]

def booklet_kriah_pages(doc, toc):
    # Returns (first page, last page) of the Torah reading section as inclusive 0-based
    # indexes, or None if this booklet has none (Yom Tov weeks). The end used to be the
    # next bookmark minus a fixed 3 pages, which assumed exactly one page of Mincha reading
    # sits after the Kriah with no bookmark of its own. Across 20 booklets that dropped the
    # last Haftorah page when Mincha had its own bookmark, and printed a Mincha page when
    # Mincha ran to two pages. So run to the page before the next bookmark and trim the
    # Mincha pages off the end by their footer. Trim by the Mincha footer rather than keep
    # by the Kriah one: the publisher sometimes mislabels Kriah footers (one booklet had
    # נקודה משיחות קודש on eight Kriah pages), but the Mincha pages have been labeled right.
    for i, (level, title, page) in enumerate(toc):
        if level != 1 or page <= 0 or _booklet_title_key(title) != _booklet_title_key(BOOKLET_KRIAH_TITLE):
            continue
        start = _booklet_earliest_page(toc, i) - 1
        next_index = next((j for j in range(i + 1, len(toc)) if toc[j][0] == 1 and toc[j][2] > 0), None)
        end = doc.page_count - 1 if next_index is None else _booklet_earliest_page(toc, next_index) - 2
        while end > start and _booklet_footer_names(doc, end, BOOKLET_MINCHA_FOOTER):
            end -= 1
        return (start, max(end, start))
    return None

def booklet_haftorah_start(pdf_path, first_page, last_page):
    # The first page of the Kriah section that carries a Haftorah marker. Only the first:
    # the marker is often on two pages in a row, and the old loop inserted from each of
    # them, so the Haftorah came out twice.
    reader = PyPDF2.PdfReader(pdf_path)
    for page_num in range(first_page, last_page + 1):
        text = reader.pages[page_num].extract_text() or ""
        if any(marker in text for marker in BOOKLET_HAFTORAH_MARKERS):
            return page_num
    return None

# --- Dvar Malchus day boundaries -------------------------------------------------------
# A day's shiur in the booklet does not stop at a page break: it runs partway down the
# page where the *next* day's heading appears. The original code approximated that with a
# per-section constant (-1 for Chumash/Rambam, -2 for Tanya), which is only right when a
# heading happens to land at the same place every week. Measured across 12 booklets that
# approximation was wrong on ~18% of day ranges -- Tanya's -2 truncated 4-6 of its 7 days
# in *every* booklet tested, silently cutting off the end of each shiur. So rather than
# guessing an offset, find the headings and decide each boundary by looking at the page.

BOOKLET_DAYS = ['יום ראשון', 'יום שני', 'יום שלישי', 'יום רביעי', 'יום חמישי', 'יום שישי', 'שבת קודש']
# Running header sits at y~27 (ending by y~41) and the folio/footer within ~40pt of the
# bottom; both repeat the day name on every page, so they have to be excluded or they
# drown out the real heading.
BOOKLET_HEADER_Y = 45
BOOKLET_FOOTER_MARGIN = 40
# Day headings are looked for from slightly higher up than body text. A day that starts at
# the very top of a page has its heading at y~44.9 -- just above BOOKLET_HEADER_Y -- so a
# single cutoff threw those headings away with the running header (the 11-5-26 booklet's
# Chumash lost Monday and Friday this way and fell back to estimated ranges). The body
# cutoff can't simply come down to match: Rambam pages carry a second header line
# ("מתוך מהדורת חזק...") at y~44.2, and counting that as body text above a heading would
# pull the next day's first page into the day before it.
BOOKLET_HEADING_TOP_Y = 42
# Constants used only by the legacy fallback below, kept so its behaviour is unchanged.
# Its keys are also the day-based sections the app takes from the booklet.
BOOKLET_LEGACY_OFFSETS = {
    'חומש יומי': 1,
    'תניא יומי': 2,
    'רמב"ם - שלושה פרקים ליום': 1,
    'רמב"ם - פרק אחד ליום': 1,
}

def _booklet_body_lines(doc, page_number, top=BOOKLET_HEADER_Y):
    page = doc[page_number]
    height = page.rect.height
    lines = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type"):
            continue  # image block, no text spans
        for line in block["lines"]:
            if line["bbox"][1] > top and line["bbox"][3] < height - BOOKLET_FOOTER_MARGIN:
                lines.append(line)
    lines.sort(key=lambda ln: ln["bbox"][1])
    return lines

def _booklet_has_body_above(doc, page_number, y):
    return any(line["bbox"][3] < y - 2 for line in _booklet_body_lines(doc, page_number))

# Bidi/zero-width marks are invisible but sit inside the extracted text, and whether a
# renderer emits them (and how it spaces runs) varies. Strip them and collapse runs of
# whitespace before matching, so "יום ראשון" is found regardless.
BOOKLET_FORMAT_CHARS = "".join(chr(c) for c in (
    0x200B, 0x200C, 0x200D, 0x200E, 0x200F,
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069, 0xFEFF,
))

def _booklet_normalize(text):
    for char in BOOKLET_FORMAT_CHARS:
        if char in text:
            text = text.replace(char, "")
    return " ".join(text.split())

def _booklet_line_text(line):
    return _booklet_normalize("".join(span["text"] for span in line["spans"]))

def _booklet_line_day(line):
    text = _booklet_line_text(line)
    return next((day for day in BOOKLET_DAYS if day in text), None)

def _booklet_font_key(span):
    # Embedded subset fonts are named "ABCDEF+Real-Name" and the prefix is per-subset, so
    # it can differ between pages of the same booklet. Compare on the real name only.
    font = span["font"]
    if len(font) > 7 and font[6] == "+":
        font = font[7:]
    return (font, round(span["size"], 2))

def _booklet_span_matches(span, signature):
    font, size = _booklet_font_key(span)
    return font == signature[0] and abs(size - signature[1]) < 0.05

# The publisher writes the quote in רמב"ם either as a plain " or as the Hebrew gershayim ״
# (U+05F4). Booklets switched to ״ from Lech Lecha 5787 (Oct 2026) while the titles in
# opttouse() use ", so the exact-match lookup found no Rambam section and those days
# silently dropped out of the PDF for weeks. Compare titles with the quote marks folded to their plain forms.
BOOKLET_TITLE_PUNCTUATION = str.maketrans({
    "\u05f4": '"', "\u05f3": "'",                                  # gershayim, geresh
    "\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'",  # curly quotes
})

def _booklet_title_key(text):
    return _booklet_normalize(text).translate(BOOKLET_TITLE_PUNCTUATION)

def _booklet_day_bookmarks(toc, index):
    # The per-day sub-bookmarks under the top-level bookmark at toc[index].
    days = []
    for sub in toc[index + 1:]:
        if sub[0] == 1:
            break
        if sub[0] == 2 and sub[1] in BOOKLET_DAYS:
            days.append(sub)
    return days

def _booklet_page_has_heading(doc, page_number, day, signature):
    return any(
        _booklet_line_day(line) == day and any(_booklet_span_matches(span, signature) for span in line["spans"])
        for line in _booklet_body_lines(doc, page_number, top=BOOKLET_HEADING_TOP_Y)
    )

def _booklet_section_start(doc, title_page, days):
    # The 1-based page a section really starts on. Normally that's its top-level bookmark,
    # which in a correct booklet is the same page as its first day's bookmark. But the
    # 11-5-26 booklet pointed תניא יומי at page 86 when Tanya starts on 78 (its day
    # bookmarks were right): the scan began mid-Thursday and found 4 of 7 days, and the
    # Chumash section before it was taken to run on through most of Tanya. A section can't
    # start after its own first day, so when the top-level bookmark lands later than the
    # first day's and that day's heading isn't on the top-level page, go by the day
    # bookmark. The heading check keeps the old behaviour when it's the day bookmark that
    # is wrong (day bookmarks have been off by a few pages before).
    first_day, first_day_page = (days[0][1], days[0][2]) if days else (None, 0)
    if not 0 < first_day_page < title_page:
        return title_page
    signature = _booklet_heading_signature(doc, {"days": days})
    if signature is None or _booklet_page_has_heading(doc, title_page - 1, first_day, signature):
        return title_page
    logger.warning("Top-level bookmark at page %d is past its first day (page %d); using the day bookmark",
                   title_page, first_day_page)
    return first_day_page

def _booklet_section(doc, toc, section_title):
    # Locate a top-level section plus its per-day sub-bookmarks, and work out the pages it
    # can possibly occupy: from where it starts to one before the next section starts.
    wanted = _booklet_title_key(section_title)
    for i, entry in enumerate(toc):
        if entry[0] != 1 or _booklet_title_key(entry[1]) != wanted or not entry[2]:
            continue
        days = _booklet_day_bookmarks(toc, i)
        if not days:
            continue
        start = _booklet_section_start(doc, entry[2], days)
        next_index = next((j for j in range(i + 1, len(toc)) if toc[j][0] == 1 and toc[j][2] > 0), None)
        if next_index is None:
            hi = doc.page_count - 1
        else:
            next_start = _booklet_section_start(doc, toc[next_index][2], _booklet_day_bookmarks(toc, next_index))
            hi = next_start - 2
        return {"lo": start - 1, "hi": hi, "days": days}
    return None

def _booklet_heading_signature(doc, section):
    # The day-heading font is not uniform across the booklet -- the Chumash/Tanya/Rambam
    # sections use one face and size, while היום יום uses a different one entirely -- so
    # derive it per section instead of hardcoding. Taking the most common (font, size)
    # across all seven sub-bookmark pages means one wrong bookmark can't skew the result,
    # and a re-typeset booklet adapts instead of silently matching nothing.
    # Matching is done on the whole *line's* text and the largest span on that line, never
    # on the text of one span. How a line gets split into spans is a property of the PDF
    # renderer and varies between PyMuPDF releases -- requiring one span to contain the
    # entire day name worked locally but matched nothing in production, so no signature
    # could be derived and every section silently fell back to the legacy offsets.
    counts = {}
    for _, day, page in section["days"]:
        if not 0 <= page - 1 < doc.page_count:
            continue
        best = None
        for line in _booklet_body_lines(doc, page - 1, top=BOOKLET_HEADING_TOP_Y):
            if day not in _booklet_line_text(line):
                continue
            for span in line["spans"]:
                if best is None or span["size"] > best[1]:
                    best = (_booklet_font_key(span)[0], round(span["size"], 2))
        if best:
            counts[best] = counts.get(best, 0) + 1
    if not counts:
        # Log enough to tell *why* nothing matched without needing the booklet in hand:
        # what the first bookmark page actually yielded. Cheap, and only on the failure path.
        _, day, page = section["days"][0]
        sample = [_booklet_line_text(line)[:60] for line in _booklet_body_lines(doc, page - 1)[:4]]
        logger.warning("No day-heading font derived; looking for %r on page %d. First lines: %r",
                       day, page, sample)
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
        for line in _booklet_body_lines(doc, page_number, top=BOOKLET_HEADING_TOP_Y):
            day = _booklet_line_day(line)
            if day is None or day in seen:
                continue
            # A line is a heading when it names a day and is set in the heading face --
            # tested per span but without requiring that span to hold the day name itself,
            # since the split between spans is not stable across PyMuPDF versions.
            if any(_booklet_span_matches(span, signature) for span in line["spans"]):
                found.append((page_number, line["bbox"][1], day))
                seen.add(day)
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

# --- Hayom Yom in Hebrew -------------------------------------------------------------------
# Chabad.org only serves the English Hayom Yom to the app, so the Hebrew comes from the
# booklet's היום יום section. Its text is real Unicode with nikud, but PyMuPDF's default
# extraction guesses spaces into words around the nikud, so read it with that turned off.
# Each day is found by its parts' look rather than by fixed positions, which vary by week:
# the day's name in the right margin (large Livorna), the entry's date line level with it
# (contains a month name), the שיעורים set short of the right edge (left out here: their
# page numbers come out of the PDF in scrambled order), and the body running to the right
# edge. Checked on 9 booklets, including Sukkos, Simchas Torah and Shavuos weeks.
BOOKLET_HAYOM_YOM_FLAGS = fitz.TEXT_INHIBIT_SPACES
BOOKLET_HEBREW_MONTHS = r"(תשרי|חשון|חשוון|כסלו|טבת|שבט|אדר|ניסן|אייר|סיון|סיוון|תמוז|אב|אלול)"

def _booklet_hayomyom_lines(page):
    lines = []
    for block in page.get_text("dict", flags=BOOKLET_HAYOM_YOM_FLAGS)["blocks"]:
        for line in block.get("lines", []):
            text = " ".join("".join(span["text"] for span in line["spans"]).split())
            x0, y0, x1, y1 = line["bbox"]
            if not text or y0 < BOOKLET_HEADER_Y or y1 > page.rect.height - BOOKLET_FOOTER_MARGIN:
                continue
            span = max(line["spans"], key=lambda sp: len(sp["text"].strip()))
            lines.append({"y": y0, "x0": x0, "x1": x1, "font": _booklet_font_key(span)[0], "size": span["size"], "text": text})
    return sorted(lines, key=lambda line: (round(line["y"]), -line["x1"]))

def _booklet_fix_date(text):
    # A bracketed note at the end of a date line ("[בחו"ל. בא"י - אסרו חג]") can come out of
    # the PDF with its tail moved to the front ("אסרו חג]- כג תשרי ...").
    if text.count("[") == 1 and text.count("]") == 1 and text.index("]") < text.index("["):
        head, rest = text.split("]", 1)
        return rest.strip(" -") + " - " + head.strip() + "]"
    return text

def booklet_hayomyom(doc, toc):
    """[{"weekday": "יום ראשון", "entries": [{"date": "כא חשון", "paragraphs": [("p", text) |
    ("label", "תרגום")]}]}] in booklet order, or None if there's no Hayom Yom section."""
    index = next((i for i, (level, title, page) in enumerate(toc)
                  if level == 1 and "היום יום" in _booklet_title_key(title) and page > 0), None)
    if index is None:
        return None
    start = _booklet_earliest_page(toc, index) - 1
    next_index = next((j for j in range(index + 1, len(toc)) if toc[j][0] == 1 and toc[j][2] > 0), None)
    end = doc.page_count - 1 if next_index is None else _booklet_earliest_page(toc, next_index) - 2
    days, day, entry = [], None, None
    for page_number in range(max(start, 0), min(end, doc.page_count - 1) + 1):
        lines = _booklet_hayomyom_lines(doc[page_number])
        margin, pending = [], []
        for line in sorted((l for l in lines if l["font"].startswith("Livorna") and l["size"] >= 15 and l["x1"] > 440),
                           key=lambda l: l["y"]):
            if pending and line["y"] - pending[-1]["y"] > 25:
                pending = []
            pending.append(line)
            name = " ".join(p["text"] for p in pending)
            if name in BOOKLET_DAYS:
                margin.append({"y": pending[0]["y"], "name": name, "used": False})
                pending = []
        header_rows = []
        for line in lines:
            font, size, text = line["font"], line["size"], line["text"]
            if font.startswith("Livorna"):
                if text == "תרגום" and entry is not None:
                    entry["paragraphs"].append(("label", "תרגום"))
                    entry["open"] = False
                continue
            if not font.startswith("GuttmanFrank"):
                continue  # another section sharing the page
            if size >= 12.3 and re.search(BOOKLET_HEBREW_MONTHS, text):
                label = next((m for m in margin if not m["used"] and abs(m["y"] - line["y"]) <= 8), None)
                if label is not None:
                    label["used"] = True
                    day = {"weekday": label["name"], "entries": []}
                    days.append(day)
                if day is None:
                    continue
                entry = {"date": _booklet_fix_date(text), "paragraphs": [], "open": False}
                day["entries"].append(entry)
                header_rows.append(line["y"])
                continue
            if entry is None or any(abs(line["y"] - y) <= 6 for y in header_rows):
                continue  # the original entry's year and weekday, on the date's row
            if line["x1"] < 380:
                continue  # שיעורים
            if entry["open"] and entry["paragraphs"] and entry["paragraphs"][-1][0] == "p":
                previous = entry["paragraphs"][-1][1]
                entry["paragraphs"][-1] = ("p", previous + ("" if previous.endswith(("־", "-")) else " ") + text)
            else:
                entry["paragraphs"].append(("p", text))
            entry["open"] = line["x0"] < 45  # a paragraph's last line stops short of the left margin
    for found in days:
        for e in found["entries"]:
            e.pop("open", None)
    return days

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
    tanyaattached = False  # same reason: every Tanya option shares one Tanya{session}.pdf
    chumashattached = False  # and every Chumash option one Chumash{session}.pdf
    hayomattached = False  # and every Hayom Yom option one Hayom{session}.pdf
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
            # The booklet-only options are reported by the Chabad.org branch below.
            source = False
            if any(o in opt for o in CHUMASH_NAMES):
                ensure_material("Chumash", session, lambda: chabadget(dor, opt, session),
                                 lambda: sefaria_chumash_get(week, session, opt))
            if any(o in opt for o in TANYA_NAMES):
                ensure_material("Tanya", session, lambda: chabadget(dor, opt, session),
                                 lambda: sefaria_tanya_get(week, session, opt))
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
            # These only exist in Dvar Malchus. The old check listed 'Project Likutei Sichos'
            # without "(Hebrew)", so it never matched that option, and when they were mixed
            # with Chabad.org materials they were dropped without a word.
            booklet_only = [o for o in opt if o in ('Project Likutei Sichos (Hebrew)', 'Maamarim', 'Haftorah', 'Krias Hatorah (includes Haftorah)')]
            if booklet_only and len(booklet_only) == len(opt):
                st.error("Project Likutei Sichos, Kriah, the Haftorah, and Maamarim are not available from Chabad.org. Please try again.")
                st.stop()
            if booklet_only:
                st.warning(f"Dvar Malchus wasn't used for this PDF, and {', '.join(booklet_only)} only come from there, "
                           "so they are not in it.")
            for option in opt:
                if option in CHUMASH_NAMES:
                    if not chumashattached:
                        append_material(doc_out, f"Chumash{session}.pdf", "Chumash")
                        chumashattached = True
                elif option in TANYA_NAMES:
                    if not tanyaattached:
                        append_material(doc_out, f"Tanya{session}.pdf", "Tanya")
                        tanyaattached = True
                elif 'Rambam' in option:
                    if not rambamattached:
                        append_material(doc_out, f"Rambam{session}.pdf", "Rambam") #type: ignore
                        rambamattached = True
                elif option in HAYOM_YOM_NAMES:
                    if not hayomattached:
                        append_material(doc_out, f"Hayom{session}.pdf", "Hayom Yom")
                        hayomattached = True
                elif option == 'Shnayim Mikra':
                    append_material(doc_out, f"Shnayim{session2}.pdf", "Shnayim Mikra")

    else:
        for q in optconv:
            # Day ranges are resolved once per section rather than once per selected day,
            # since the heading scan reads every page of the section.
            day_ranges = booklet_day_ranges(doc, toc, q) #type: ignore
            if day_ranges is None and q in BOOKLET_LEGACY_OFFSETS:
                # A day-based section that isn't in the booklet used to just drop out of the
                # PDF with nothing said -- that is how Rambam went missing for several weeks
                # in Cheshvan 5787. Nothing else below picks these sections up, so tell the user.
                logger.warning("Section %s not found in the Dvar Malchus bookmarks", q)
                st.warning(f"Couldn't find {q} in this week's Dvar Malchus, so it is not in this PDF. "
                           "To get it from Chabad.org instead, turn off the Dvar Malchus toggle and create the PDF again.")
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

            if q == 'מאמרים':
                maamarim = booklet_maamarim_pages(toc, doc.page_count - 1) #type: ignore
                if maamarim is None:
                    logger.warning("Maamarim section not found in the Dvar Malchus bookmarks")
                    st.warning("Couldn't find the Maamarim in this week's Dvar Malchus, so they are not in this PDF.")
                else:
                    logger.debug(f"Maamarim found: pages {maamarim[0]}-{maamarim[1]}")
                    doc_out.insert_pdf(doc, from_page=maamarim[0], to_page=maamarim[1]) #type: ignore

            if q == 'לקוטי שיחות':
                sichos_found = False
                for i, item in enumerate(toc): #type: ignore
                    words = item[1].split()
                    # words[n + 1:n + 2] rather than words[n + 1], so a title that ends in
                    # 'לקוטי' can't raise IndexError.
                    if any(word == 'לקוטי' and words[n + 1:n + 2] == ['שיחות'] for n, word in enumerate(words)):
                        logger.debug("Likutei Sichos found")
                        sichos_found = True
                        page_num_start = item[2] - 1
                        page_num_end = find_next_top_level_bookmark(toc, i, doc.page_count - 1) #type: ignore
                        doc_out.insert_pdf(doc, from_page=page_num_start, to_page=page_num_end) #type: ignore
                if not sichos_found:
                    logger.warning("Likutei Sichos section not found in the Dvar Malchus bookmarks")
                    st.warning("Couldn't find Likutei Sichos in this week's Dvar Malchus, so it is not in this PDF.")

            # opttouse() maps both Kriah and Haftorah to this title, so it can come up twice;
            # kriahattatch makes sure the section is only added once.
            if q == 'חומש לקריאה בציבור' and not kriahattatch:
                kriahattatch = True
                kriah = booklet_kriah_pages(doc, toc) #type: ignore
                if kriah is None:
                    logger.warning("Krias Hatorah section not found in the Dvar Malchus bookmarks")
                    st.warning("Couldn't find Krias Hatorah in this week's Dvar Malchus (Yom Tov booklets may not have it), "
                               "so the Kriah and Haftorah are not in this PDF.")
                elif "Krias Hatorah (includes Haftorah)" in opt:
                    logger.debug(f"Kriah found: pages {kriah[0]}-{kriah[1]}")
                    doc_out.insert_pdf(doc, from_page=kriah[0], to_page=kriah[1]) #type: ignore
                else:
                    haftorah_start = booklet_haftorah_start(f"dvar{session2}.pdf", kriah[0], kriah[1])
                    if haftorah_start is None:
                        logger.warning("Haftorah start not found in pages %d-%d", kriah[0], kriah[1])
                        st.warning("Couldn't find where the Haftorah starts in this week's Dvar Malchus, so it is not in this PDF. "
                                   "Try Krias Hatorah instead, which includes the Haftorah.")
                    else:
                        logger.debug(f"Haftorah found: pages {haftorah_start}-{kriah[1]}")
                        doc_out.insert_pdf(doc, from_page=haftorah_start, to_page=kriah[1]) #type: ignore

            if 'Rambam' in q:
                # Only the non-Hebrew Rambam variants reach here as raw option strings;
                # the Hebrew ones are mapped to their booklet section titles by opttouse()
                # and are handled by the day-range branch above.
                if not rambamattached:
                    append_material(doc_out, f"Rambam{session}.pdf", "Rambam")
                    rambamattached = True
                    
                continue

            if q == 'Chumash-Bilingual':
                # The Hebrew Chumash comes from the booklet above; these come from Chabad.org.
                if not chumashattached:
                    append_material(doc_out, f"Chumash{session}.pdf", "Chumash")
                    chumashattached = True
                continue

            if q in ('Tanya-Bilingual', 'Tanya-English'):
                # The Hebrew Tanya comes from the booklet above; these come from Chabad.org.
                if not tanyaattached:
                    append_material(doc_out, f"Tanya{session}.pdf", "Tanya")
                    tanyaattached = True
                continue

            if q in HAYOM_YOM_NAMES:
                if not hayomattached:
                    append_material(doc_out, f"Hayom{session}.pdf", "Hayom Yom")
                    hayomattached = True
                
                continue

            if q == 'Shnayim Mikra':
                append_material(doc_out, f"Shnayim{session2}.pdf", "Shnayim Mikra")
                
                continue
                       
    _ = doc_out.save(os.path.join(output_dir, f"output_dynamic{session}.pdf")) if doc_out.page_count > 0 else (logger.error("No pages were added to the output PDF (opt=%r, source=%r); refusing to save an empty document.", opt, source), st.error("No content was generated for your selections — nothing could be added to the PDF. Please check your selections and try again."), st.stop())  # NB: assignment, not a bare expression -- keeps Streamlit magic from rendering the None result as "None"
    doc_out.close()


@st.cache_data(ttl="12h")
def dateset():
    session2 = dt.now()
    logger.info(f"Session: {session2}")
    return session2

# Linked choices. These used to sit in a st.form, which only reacts on submit; out of the form,
# each choice can update the settings tied to it as soon as it's made.
ALL_DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos']
SIZE_KEYS = ("size_chumash", "size_tanya", "size_rambam", "size_hayom")
DVAR_ONLY = {"Hayom Yom-Hebrew": "Hebrew Hayom Yom", "Hayom Yom-Bilingual": "Bilingual Hayom Yom",
             'Project Likutei Sichos (Hebrew)': "Likutei Sichos", 'Maamarim': "Maamarim",
             'Krias Hatorah (includes Haftorah)': "Krias HaTorah", 'Haftorah': "the Haftorah"}
for key, value in (("days", []), ("whole_week", False), ("print_size", "Standard"), ("chumash_layout", "Two columns"),
                   ("use_dvar", True), *((k, 100) for k in SIZE_KEYS)):
    st.session_state.setdefault(key, value)

def _whole_week_changed():
    st.session_state.days = list(ALL_DAYS) if st.session_state.whole_week else []

def _days_changed():
    st.session_state.whole_week = set(st.session_state.days or []) == set(ALL_DAYS)

def _print_size_changed():
    # A preset sets every size, and the Chumash layout that suits it.
    size = PRINT_SIZES.get(st.session_state.print_size)
    if size is not None:
        for key in SIZE_KEYS:
            st.session_state[key] = int(size * 100)
        st.session_state.chumash_layout = "One column" if st.session_state.print_size == "Large print" else "Two columns"

def _size_changed():
    sizes = {st.session_state[key] for key in SIZE_KEYS}
    match = next((name for name, size in PRINT_SIZES.items() if sizes == {int(size * 100)}), None)
    st.session_state.print_size = match or "Custom"

def _dvar_only_picked():
    picked = [DVAR_ONLY[o] for o in (st.session_state.get("hayom_pick") or []) + (st.session_state.get("weekly_pick") or []) if o in DVAR_ONLY]
    if picked and not st.session_state.use_dvar:
        st.session_state.use_dvar = True
        st.toast(f"Turned on Dvar Malchus, the only source for {', '.join(picked)}.")

def _use_dvar_changed():
    picked = [DVAR_ONLY[o] for o in (st.session_state.get("hayom_pick") or []) + (st.session_state.get("weekly_pick") or []) if o in DVAR_ONLY]
    if picked and not st.session_state.use_dvar:
        st.toast(f"Without Dvar Malchus, {', '.join(picked)} will be left out.")

# Order of the materials in the PDF. Each group keeps its languages together (in the order
# Hebrew, Bilingual, English); the groups themselves can be moved up and down.
ORDER_GROUPS = {
    "Chumash": CHUMASH_NAMES,
    "Tanya": TANYA_NAMES,
    "Rambam": ['Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English'],
    "Hayom Yom": HAYOM_YOM_NAMES,
    "Likutei Sichos": ['Project Likutei Sichos (Hebrew)'],
    "Maamarim": ['Maamarim'],
    "Krias HaTorah / Haftorah": ['Haftorah', 'Krias Hatorah (includes Haftorah)'],
    "Shnayim Mikra": ['Shnayim Mikra'],
}
PICK_KEYS = ("chumash_pick", "tanya_pick", "hayom_pick", "rambam3_pick", "rambam1_pick", "weekly_pick")
st.session_state.setdefault("pdf_order", list(ORDER_GROUPS))

def _picked_groups():
    picked = {o for key in PICK_KEYS for o in (st.session_state.get(key) or [])}
    return [group for group in st.session_state.pdf_order if picked & set(ORDER_GROUPS[group])]

def _move_group(group, step):
    shown = _picked_groups()
    i = shown.index(group)
    if 0 <= i + step < len(shown):
        order = st.session_state.pdf_order
        a, b = order.index(shown[i]), order.index(shown[i + step])
        order[a], order[b] = order[b], order[a]

def _reset_order():
    st.session_state.pdf_order = list(ORDER_GROUPS)

with st.container(border=True): # the choices; not a st.form, so linked choices update as you click
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
    st.subheader("1. Days")
    week = st.pills("Which days?", options=ALL_DAYS, selection_mode="multi", key="days", on_change=_days_changed)
    whole_week = st.checkbox("Whole week (Sunday to Shabbos)", key="whole_week", on_change=_whole_week_changed)

    st.subheader("2. Daily learning")
    st.caption("Pick one or more languages for each. Hebrew comes from Dvar Malchus when that's on; "
               "Bilingual and English come from Chabad.org.")
    learning_left, learning_right = st.columns(2)
    with learning_left:
        chumashopts = st.pills("Chumash with Rashi", options=CHUMASH_NAMES, selection_mode="multi", format_func=language_label, key="chumash_pick")
        tanyaopts = st.pills("Tanya", options=TANYA_NAMES, selection_mode="multi", format_func=language_label, key="tanya_pick")
        hayomopts = st.pills("Hayom Yom", options=HAYOM_YOM_NAMES, selection_mode="multi", format_func=language_label,
                             key="hayom_pick", on_change=_dvar_only_picked,
                             help="Hebrew and Bilingual Hayom Yom need Dvar Malchus: Chabad.org only has the English.")
    with learning_right:
        rambam3opts = st.pills("Rambam · 3 chapters a day", options=['Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English'],
                               selection_mode="multi", format_func=language_label, key="rambam3_pick")
        rambam1opts = st.pills("Rambam · 1 chapter a day", options=['Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English'],
                               selection_mode="multi", format_func=language_label, key="rambam1_pick")

    st.subheader("3. Weekly")
    extras = st.pills("For the week", help="Shnayim Mikra works either way; the others only come from Dvar Malchus.",
                      options=['Project Likutei Sichos (Hebrew)', 'Maamarim', 'Krias Hatorah (includes Haftorah)', 'Haftorah', 'Shnayim Mikra'],
                      selection_mode="multi", format_func=lambda o: WEEKLY_LABELS.get(o, o), key="weekly_pick", on_change=_dvar_only_picked)

    st.subheader("4. Print")
    print_left, print_right = st.columns(2)
    with print_left:
        print_size = st.segmented_control("Text size", options=list(PRINT_SIZES) + ["Custom"], key="print_size", on_change=_print_size_changed,
                                          help="Space saving prints smaller text on fewer pages; Large print is easier to read (and "
                                               "switches Chumash to one column). Custom sizes are under Advanced options. Pages taken "
                                               "from Dvar Malchus print as they are.")
    with print_right:
        chumash_layout = st.segmented_control("Chumash layout (Hebrew)", options=["Two columns", "One column"], key="chumash_layout",
                                              help="Two columns fits about a quarter more on a page.")
    source = st.toggle("Use Dvar Malchus when possible", key="use_dvar", on_change=_use_dvar_changed,
                       help="On: Hebrew Chumash, Tanya, Rambam and Hayom Yom, and the weekly items, come from this week's Dvar Malchus "
                            "booklet. Off: everything comes from Chabad.org (Sefaria if that fails), and items only Dvar Malchus has are left out.")
    with st.expander("Order in the PDF"):
        shown = _picked_groups()
        if not shown:
            st.caption("Pick some materials above, then set the order they print in here.")
        for n, group in enumerate(shown):
            name_col, up_col, down_col = st.columns([8, 1, 1], vertical_alignment="center")
            name_col.write(f"{n + 1}. {group}")
            up_col.button("↑", key=f"up_{group}", on_click=_move_group, args=(group, -1), disabled=n == 0, help="Move up")
            down_col.button("↓", key=f"down_{group}", on_click=_move_group, args=(group, 1), disabled=n == len(shown) - 1, help="Move down")
        if shown and st.session_state.pdf_order != list(ORDER_GROUPS):
            st.button("Reset to the usual order", on_click=_reset_order)
    with st.expander("Advanced options"):
        cover = st.checkbox('Include the cover page from Dvar Malchus', value=False)
        st.caption("Text size for each material. Picking a Text size above sets these; changing one switches it to Custom.")
        scaleslide = st.slider('Chumash', 30, 150, format="%d%%", key="size_chumash", on_change=_size_changed)
        scaleslide4 = st.slider('Tanya', 30, 150, format="%d%%", key="size_tanya", on_change=_size_changed)
        scaleslide2 = st.slider('Rambam', 30, 150, format="%d%%", key="size_rambam", on_change=_size_changed)
        scaleslide3 = st.slider('Hayom Yom', 30, 150, format="%d%%", key="size_hayom", on_change=_size_changed)
    # Chrome's print scale (Page.printToPDF "scale", 10-200%): text, spacing and line length
    # all scale together on the same page. The sliders always match the chosen Text size.
    scale, scale2, scale3, scale4 = scaleslide / 100, scaleslide2 / 100, scaleslide3 / 100, scaleslide4 / 100
    chumash_columns = 1 if chumash_layout == "One column" else 2

    submit_button = st.button("Generate PDF ▶️", type="primary", use_container_width=True)

if submit_button: #if the user submits the form, run the following code, which will create the pdf using above functions
    if 'id' not in st.session_state:
        st.session_state['id'] = dt.now()
    opt = []
    # st.pills() in multi-select mode returns [] when nothing is selected, but guard
    # with truthiness anyway in case a future Streamlit change reintroduces None.
    for picked in (chumashopts, tanyaopts, hayomopts, rambam3opts, rambam1opts, extras):
        if picked:
            opt += picked
    if whole_week:
        week = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos']
    logger.info(opt)
    session = st.session_state['id']
    # The material files are per browser tab, and every fetcher skips a file that already
    # exists. So a second Generate in the same tab reused the first run's Chumash, Tanya,
    # Rambam and Hayom Yom even after the days, Rambam option or scale had changed. Start
    # each run clean. The page fetches behind them are cached, so this costs little.
    for stale_prefix in ("Chumash", "Tanya", "Rambam", "Hayom", "output_dynamic"):
        try:
            os.remove(f"{stale_prefix}{session}.pdf")
        except FileNotFoundError:
            pass
    weekorder = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos']
    optorder = [name for group in st.session_state.pdf_order for name in ORDER_GROUPS[group]]  # the order picked under Print
    daydependent = ['Chumash', 'Chumash-Bilingual', 'Tanya', 'Tanya-Bilingual', 'Tanya-English', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom', 'Hayom Yom-Hebrew', 'Hayom Yom-Bilingual']
    chabadoptions = ['Chumash', 'Chumash-Bilingual', 'Tanya', 'Tanya-Bilingual', 'Tanya-English', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom', 'Hayom Yom-Hebrew', 'Hayom Yom-Bilingual']
    dow = []
    optconv = []
    dor = []
    opt = sorted(opt, key=optorder.index)
    if not opt: st.error("Please select at least one material to print before generating the PDF."); st.stop()  # st.pills() returns [] when nothing selected
    # defensively so the "week == []" checks below are guaranteed to fire correctly.
    week = week or []
    week = sorted(week, key=weekorder.index)
    daytoheb(week, dow)
    daytorambam(week, dor)

    opttouse(opt, optconv)

    logger.info(optconv)
    logger.info(source)
    if week == [] and any(x in opt for x in daydependent)==True:
        st.error("Please select at least one day of the week if trying to select Chumash, Tanya, Hayom Yom or Rambam.")
        st.stop()
    # Parenthesised so this only fires when no day was picked. Without them it read as
    # "(no days and Kriah) or Maamarim or Sichos or Shnayim", which swapped the user's
    # chosen days for Sunday whenever one of those was selected -- so a Sefaria fallback
    # below fetched Sunday only.
    if week == [] and ('חומש לקריאה בציבור' in optconv or 'מאמרים' in optconv or 'לקוטי שיחות' in optconv or 'Shnayim Mikra' in optconv):
        week = ['Sunday']
        logger.info(optconv)
    logger.info(week)
    if source == True:
        if 'Chumash' in opt or 'Tanya' in opt or 'Haftorah' in opt or 'Rambam (3)-Hebrew' in opt or 'Rambam (1)-Hebrew' in opt or 'Project Likutei Sichos (Hebrew)' in opt or 'Maamarim' in opt or 'Krias Hatorah (includes Haftorah)' in opt or 'Hayom Yom-Hebrew' in opt or 'Hayom Yom-Bilingual' in opt:
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
            if any(o in opt for o in CHUMASH_NAMES):
                ensure_material("Chumash", session, lambda: chabadget(dor, opt, session),
                                 lambda: sefaria_chumash_get(week, session, opt))
            if any(o in opt for o in TANYA_NAMES):
                ensure_material("Tanya", session, lambda: chabadget(dor, opt, session),
                                 lambda: sefaria_tanya_get(week, session, opt))
            if rambam_requested:
                ensure_material("Rambam", session, lambda: rambamenglish(dor, session, opt),
                                 lambda: sefaria_rambam_get(week, session, opt))
        if source == True:
            if 'Chumash-Bilingual' in opt:
                # As with Tanya and Rambam: the Hebrew Chumash comes from the booklet.
                chabad_chumash_opt = ['Chumash-Bilingual']
                ensure_material("Chumash", session, lambda: chabadget(dor, chabad_chumash_opt, session),
                                 lambda: sefaria_chumash_get(week, session, chabad_chumash_opt))
            if 'Tanya-Bilingual' in opt or 'Tanya-English' in opt:
                # As with Rambam below: the Hebrew Tanya comes from the booklet, so only the
                # English and Bilingual versions are fetched from Chabad.org.
                chabad_tanya_opt = [o for o in opt if o in ('Tanya-Bilingual', 'Tanya-English')]
                ensure_material("Tanya", session, lambda: chabadget(dor, chabad_tanya_opt, session),
                                 lambda: sefaria_tanya_get(week, session, chabad_tanya_opt))
            if 'Rambam (3)-Bilingual' in opt or 'Rambam (3)-English' in opt or 'Rambam (1)-Bilingual' in opt or 'Rambam (1)-English' in opt:
                # The Hebrew Rambam comes from the booklet here, so leave it out of what
                # Chabad.org is asked for. rambamenglish() takes the first Rambam option it
                # sees, so with Hebrew + English selected it fetched the Hebrew a second
                # time and the English never came.
                chabad_rambam_opt = [o for o in opt if o not in ('Rambam (3)-Hebrew', 'Rambam (1)-Hebrew')]
                ensure_material("Rambam", session, lambda: rambamenglish(dor, session, chabad_rambam_opt),
                                 lambda: sefaria_rambam_get(week, session, chabad_rambam_opt))
            elif ('Rambam (3)-Hebrew' in opt or 'Rambam (1)-Hebrew' in opt) and os.path.exists(f"dvar{session2}.pdf") == False:
                ensure_material("Rambam", session, lambda: rambamenglish(dor, session, opt),
                                 lambda: sefaria_rambam_get(week, session, opt))

        if any(o in opt for o in HAYOM_YOM_NAMES):
            hayomyom(dor, dow, session, opt, source)

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
        st.markdown("**New in latest update (11-5-26)**: <br/> **[FIX]** Rambam in Hebrew (3 chapters and 1 chapter) was missing from PDFs made from Dvar Malchus for the last few weeks, because the booklet changed how it writes רמב״ם in its titles. It is back. <br/> **[FIX]** Chumash and Tanya no longer fall back to estimated page ranges when a day starts at the very top of a page, or when the booklet's Tanya bookmark points to the wrong page. This week that had added most of Tanya to the end of Chumash for Shabbos. <br/> **[NEW]** If a section you picked can't be found in this week's Dvar Malchus, the app now tells you, instead of quietly leaving it out. <br/> **[FIX]** Maamarim are back. For most weeks since the summer the booklet has titled that section מאמרים instead of מאמר, so the app found nothing and left them out of the PDF. <br/> **[FIX]** The Haftorah no longer prints twice. In some weeks, this one included, it came out twice. <br/> **[FIX]** Krias Hatorah now ends on the right page. Some weeks it cut off the last page of the Haftorah or added a page of the Shabbos Mincha reading. <br/> **[FIX]** Shnayim Mikra no longer crashes the app in weeks when Shabbos is Yom Tov, and it now works for אחרי מות-קדושים and נצבים-וילך. <br/> **[FIX]** Making a second PDF in the same tab with different days or options no longer reuses the Chumash, Tanya, Rambam or Hayom Yom from the first one. <br/> **[FIX]** Picking a Hebrew Rambam together with an English or Bilingual one now gives you both, instead of the Hebrew twice. <br/> **[NEW]** If Likutei Sichos, Krias Hatorah, the Haftorah or Shnayim Mikra can't be found, or you pick something that only comes from Dvar Malchus while it isn't being used, the app now tells you. <br/> **[NEW]** Chumash with Rashi, Tanya, Rambam and Hayom Yom from Chabad.org are back, in a new print layout: Hebrew with full cantillation, Bilingual Rambam side by side, and a header on each day with the English and Hebrew date and the portion. Chabad.org's daily study pages had started blocking the app, so these were coming from Sefaria instead, and Hayom Yom, which only Chabad.org has, was missing from every PDF. They now come from Chabad.org's online library and take a second or two instead of minutes. <br/> **[NEW]** Tanya now has its own row, like Rambam: Hebrew, Bilingual (side by side) and English. The English is Lessons in Tanya, with its explanation printed in gray so the translation stands out. <br/> **[NEW]** Chumash now has its own row too: Hebrew, and Bilingual (English and Hebrew side by side, each with its Rashi). From Chabad.org each pasuk is followed by its Rashi, in block letters, and the Hebrew prints in two columns, which takes about a quarter fewer pages. <br/> **[CHANGE]** English and Bilingual Rambam leave out the translator's footnotes, which more than doubled the length of a day's printout. <br/> **[NEW]** You can now pick more than one Rambam option from Chabad.org; each is included. Before, only the first one came through. <br/> **[NEW]** A clearer form: pick the days (or the whole week), then each material's languages (Hebrew, Bilingual, English), then the weekly items, then how it prints. <br/> **[NEW]** Text size presets: Space saving, Standard and Large print, or Custom sizes per material under Advanced options. <br/> **[NEW]** Hebrew Chumash can print in one column or two. <br/> **[NEW]** Hayom Yom in Hebrew and Bilingual, from this week's Dvar Malchus (the Hebrew isn't available from Chabad.org), next to Chabad.org's English for Bilingual.", unsafe_allow_html=True)
        st.markdown("**Past Changes (7-19-26)**: <br/> **[FIX]** Days taken from Dvar Malchus now include their full shiur. Because a day's learning ends partway down a page rather than at a page break, the app used to cut some days short or add a page of the next day — Tanya was losing the end of most days every week. Day boundaries are now found on the page itself instead of being estimated. <br/> **[NEW]** Rambam (1 chapter) in Hebrew now comes from Dvar Malchus like the 3-chapter cycle, instead of always falling back to Chabad.org. <br/> **[FIX]** Selecting more than one Rambam option at once no longer repeats the same section twice. <br/> **[NEW]** Dvar Malchus downloads are much faster (a couple of seconds instead of around forty) — the app now fetches the booklet directly rather than driving a browser, falling back to the old method if needed.", unsafe_allow_html=True)
        st.markdown("**Past Changes (7-3-26)**: <br/> **[FIX]** Fixed a crash when two people generated a booklet from Dvar Malchus at nearly the same time. <br/> **[NEW]** Modernized the app's dependencies (Streamlit and others) and swapped out a few unmaintained UI components for Streamlit's own native ones. <br/> **[NEW]** Added a custom dark theme. <br/> **[NEW]** Chumash/Tanya/Rambam/Hayom Yom fetches are now shared across everyone requesting the same day, so repeat generations are faster and put less load on Chabad.org.", unsafe_allow_html=True)
        st.markdown("**Past Changes (7-2-26)**: <br/> **[FIX]** Dvar Malchus downloads were silently failing in headless mode; fixed by explicitly allowing Chrome to save the file. <br/> **[FIX]** Chabad.org's daily study pages had started intermittently failing (an anti-bot check); fetches now retry automatically with a fresh session before giving up. <br/> **[NEW]** Added Sefaria as a 3rd fallback source for Chumash (with Rashi), Tanya, and Rambam if both Dvar Malchus and Chabad.org are unavailable, including correct handling of combined/double-parsha weeks. <br/> **[FIX]** Bilingual Rambam now shows Hebrew and English side by side instead of one after the other. <br/> **[FIX]** A single Chabad.org or Sefaria hiccup on one material no longer crashes the whole app.", unsafe_allow_html=True)
        st.markdown("**Past Changes (1-17-24)**: <br/> **[FIX]** Updated location of Dvar Malchus download button.", unsafe_allow_html=True)
        st.markdown("**Past Changes (7-17-23)**: <br/> **1:** Repeated compilations of materials from Dvar Malchus should be considerably faster. <br/> **2:** Shnayim mikra gets considerably faster on subsequent reruns. <br/> **3:** Fixes to maamarim and sichos to fail less often.", unsafe_allow_html=True)
# Not gated on submit_button: clicking the download button triggers a rerun in which
# submit_button is False, which made this escape hatch vanish the moment someone
# downloaded their PDF -- precisely when they'd notice the materials were last week's.
if 'id' in st.session_state and os.path.exists(f"output_dynamic{st.session_state['id']}.pdf"):
    with st.expander("NOTE: If you are reciving last weeks materials, please click here."):
        newtime= st.button("Clear Cached Time", on_click=dateset.clear)
