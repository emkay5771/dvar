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
from datetime import date #type: ignore
from datetime import datetime as dt #type: ignore
from datetime import timedelta #type: ignore
import requests #type: ignore
import streamlit as st #type: ignore
import pypdf as PyPDF2 #type: ignore
import glob
from pyluach import parshios, dates
import importlib
import chabad_texts
# Streamlit Cloud re-reads this script after a deploy but keeps modules it has already
# imported, so a deploy that changed chabad_texts.py went on running the old copy next to the
# new dvarstream.py. On Nov 5 2026 that made every Chumash from Chabad.org fail (the old
# chumash_section() took no column setting) and fall back to Sefaria without a word, and
# Bilingual Hayom Yom fail outright (the old module lacked its functions). Reload the module
# whenever its file is newer than the copy in memory.
if getattr(chabad_texts, "_loaded_mtime", None) != os.path.getmtime(chabad_texts.__file__):
    chabad_texts = importlib.reload(chabad_texts)
    chabad_texts._loaded_mtime = os.path.getmtime(chabad_texts.__file__)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dvarstream")

st.set_page_config(page_title="Dvar Creator (BETA)", page_icon="📚", layout="wide", initial_sidebar_state="collapsed")
# On a phone the four Text size choices were 9px too wide for one line; tighten their padding
# there only. st-key-<key> is the class Streamlit documents for keyed widgets.
st.markdown("<style>@media (max-width: 640px) { .st-key-print_size button { padding-left: 11px; padding-right: 11px; } }</style>",
            unsafe_allow_html=True)

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

# Guards the shared, cross-user booklet file (dvar{session2}.pdf) so two concurrent Streamlit
# sessions in the same process can't both start a redundant download and race each other
# writing the same filename. Kept in st.cache_resource: a plain module-level Lock was made anew
# on every run of this script, so each session had its own and nothing was guarded.
@st.cache_resource
def _booklet_lock():
    return threading.Lock()
_dvar_cache_lock = _booklet_lock()

@st.cache_data(ttl="24h", max_entries=60, show_spinner=False)
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
# text from Chabad.org's Torah Texts API and lays it out for print. The PDF is put together
# one option and one day at a time (see PdfBuilder below), so a day Chabad.org can't supply
# falls back to Sefaria, or for Hayom Yom is left out, without taking the other days with it.
# 'Chumash' and 'Tanya' are the Hebrew, and come from the Dvar Malchus booklet for the
# booklet's own week when that's in use; Bilingual and English always come from Chabad.org.
CHABAD_CHUMASH_OPTIONS = [("Chumash", "he"), ("Chumash-Bilingual", "both")]
CHUMASH_NAMES = [name for name, _ in CHABAD_CHUMASH_OPTIONS]
CHABAD_TANYA_OPTIONS = [("Tanya", "he"), ("Tanya-Bilingual", "both"), ("Tanya-English", "en")]
TANYA_NAMES = [name for name, _ in CHABAD_TANYA_OPTIONS]
HAYOM_YOM_NAMES = ["Hayom Yom-Hebrew", "Hayom Yom-Bilingual", "Hayom Yom"]  # 'Hayom Yom' is the English
PRINT_SIZES = {"Compact": 0.8, "Standard": 1.0, "Large": 1.3}  # short names so the choices fit one line, even on a phone
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
RAMBAM_NAMES = [name for name, _, _ in CHABAD_RAMBAM_OPTIONS]
OPTION_LANGUAGE = {**dict(CHABAD_CHUMASH_OPTIONS), **dict(CHABAD_TANYA_OPTIONS),
                   **{name: language for name, _, language in CHABAD_RAMBAM_OPTIONS}}

def weekday_index(day):
    return (day.weekday() + 1) % 7  # Sunday 0 ... Shabbos 6, the booklet's order

def option_material(option):
    """The chabad_texts.page() layout an option prints in, which is also the size slider it
    follows; None for the weekly items."""
    if option in CHUMASH_NAMES:
        return "chumash"
    if option in TANYA_NAMES:
        return "tanya"
    if option in RAMBAM_NAMES:
        return "rambam"
    if option in HAYOM_YOM_NAMES:
        return "hayomyom"
    return None

def option_title(option):
    if option in CHUMASH_NAMES:
        base = "Chumash with Rashi"
    elif option in TANYA_NAMES:
        base = "Tanya"
    elif option in HAYOM_YOM_NAMES:
        base = "Hayom Yom"
    elif option in RAMBAM_NAMES:
        base = "Rambam · 3 chapters" if option.startswith("Rambam (3)") else "Rambam · 1 chapter"
    else:
        return WEEKLY_LABELS.get(option, option)
    return f"{base} · {language_label(option)}"

def chabad_section(option, day, chumash_columns):
    """One day of one option from Chabad.org, as HTML for chabad_texts.page(). Raises when
    Chabad.org can't supply it."""
    if option in CHUMASH_NAMES:
        return chabad_texts.chumash_section(day, OPTION_LANGUAGE[option], chumash_columns)
    if option in TANYA_NAMES:
        return chabad_texts.tanya_section(day, OPTION_LANGUAGE[option])
    if option == "Hayom Yom":
        return chabad_texts.hayomyom_section(day)
    for name, chapters, language in CHABAD_RAMBAM_OPTIONS:
        if name == option:
            return chabad_texts.rambam_section(day, chapters, language)
    raise ValueError(f"{option} doesn't come from Chabad.org")

SEFARIA_PDF_MARGINS = {'margin-top': '0.1in', 'margin-right': '0.1in', 'margin-bottom': '0.1in', 'margin-left': '0.1in'}

def render_pdf_bytes(html, size, sefaria=False):
    options = {"scale": size, **SEFARIA_PDF_MARGINS} if sefaria else {"scale": size, "printBackground": True, "preferCSSPageSize": True}
    return b64decode(render_html_to_pdf(html, options)["data"])

SEFARIA_API_BASE = "https://www.sefaria.org/api"
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
.sefaria-day + .sefaria-day { break-before: page; }
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

def _sefaria_block(hebrew_verses, english_verses):
    if hebrew_verses and english_verses:
        return _paired_bilingual_html(hebrew_verses, english_verses)
    if hebrew_verses:
        return f'<div class="hebrew">{"<br>".join(hebrew_verses)}</div>'
    if english_verses:
        return f'<div class="english">{"<br>".join(english_verses)}</div>'
    return ""

def _sefaria_calendar_items(day, title):
    return [item for item in sefaria_calendar(day.strftime("%Y-%m-%d")) if item.get("title", {}).get("en") == title]

def sefaria_day_html(option, day):
    """One day of a Chumash, Tanya or Rambam option from Sefaria, the 3rd-tier fallback: a
    heading and the text, for one page of a Sefaria printout. None when Sefaria has nothing for
    that day. The option's language decides Hebrew, English or both side by side."""
    language = OPTION_LANGUAGE[option]
    want_he, want_en = language in ("he", "both"), language in ("en", "both")

    def texts(ref):
        data = sefaria_fetch_text(ref) or {}
        return (_flatten_sefaria_text(data.get("he")) if want_he else [],
                _flatten_sefaria_text(data.get("text")) if want_en else [])

    if option in CHUMASH_NAMES:
        # Sefaria's calendar gives every date the aliyos of the coming Shabbos's parsha,
        # combined weeks included, so the date's own weekday picks its aliyah.
        parsha = next(iter(_sefaria_calendar_items(day, "Parashat Hashavua")), None)
        aliyot = ((parsha or {}).get("extraDetails") or {}).get("aliyot", [])
        if len(aliyot) <= weekday_index(day):
            logger.warning("No Sefaria aliyah for %s", day)
            return None
        refs = [aliyot[weekday_index(day)]]
        body = _sefaria_block(*texts(refs[0]))
        for wanted, rashi_language, title, css in ((want_he, "he", 'רש"י', "rashi"), (want_en, "en", "Rashi", "rashi english")):
            comments = sefaria_fetch_rashi(refs[0], rashi_language) if wanted else []
            if comments:
                body += f'<div class="{css}"><div class="rashi-title">{title}</div>' + "<br><br>".join(comments) + "</div>"
    else:
        if option in TANYA_NAMES:
            calendar_title = "Tanya Yomi"
        else:
            calendar_title = "Daily Rambam (3 Chapters)" if option.startswith("Rambam (3)") else "Daily Rambam"
        # The 3-chapter cycle can appear as two calendar entries on days where it crosses a
        # Mishneh Torah sub-book boundary -- include every match.
        refs = [item["ref"] for item in _sefaria_calendar_items(day, calendar_title)]
        body = "<hr>".join(block for block in (_sefaria_block(*texts(ref)) for ref in refs) if block)
    if not body:
        logger.warning("Sefaria had nothing for %s on %s", option, day)
        return None
    return f'<div class="sefaria-day"><h1>{" / ".join(refs)}</h1>{body}</div>'

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

@st.cache_data(ttl="7d", max_entries=12, show_spinner=False)
def shnayim_pdf(parsha):
    """Shnayim Mikra for one week, by pyluach's Hebrew parsha name, as PDF bytes. Raises when
    the shnayimfiles repo doesn't have it."""
    # The name is kept as pyluach gives it, including the comma between double-parsha names
    # ("מטות, מסעי"), because the files in the repo are named that way too.
    filename = SHNAYIM_FILE_ALIASES.get(parsha, parsha)
    try:
        return _shnayim_download(filename)
    except Exception:
        parts = [part.strip() for part in filename.split(",")]
        if len(parts) < 2:
            raise
        # Some combined weeks (e.g. נצבים, וילך) only exist in the repo as one file per
        # parsha, so join those instead.
        logger.info("No file for '%s'; joining %s", filename, parts)
        merged = fitz.open()
        for part in parts:
            with fitz.open(stream=_shnayim_download(part), filetype="pdf") as piece:
                merged.insert_pdf(piece)
        return merged.tobytes()

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
            # But an unbookmarked page can sit between the two sections (the 11-5-26 booklet has
            # a לוח תהלים after Shabbos's Chumash), and it came out as the end of Shabbos. Every
            # page of a day's shiur names the day in its footer, so drop trailing pages that
            # don't -- only when the day's first page does, in case a booklet stops printing it.
            if _booklet_footer_names(doc, page_number, day):
                while end > page_number and not _booklet_footer_names(doc, end, day):
                    end -= 1
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
    """[{"weekday": "יום ראשון", "pages": [0-based booklet pages the day is printed on],
    "entries": [{"date": "כא חשון", "paragraphs": [("p", text) | ("label", "תרגום")]}]}] in
    booklet order, or None if there's no Hayom Yom section."""
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
                    day["pages"].add(page_number)
                continue
            if not font.startswith("GuttmanFrank"):
                continue  # another section sharing the page
            if size >= 12.3 and re.search(BOOKLET_HEBREW_MONTHS, text):
                label = next((m for m in margin if not m["used"] and abs(m["y"] - line["y"]) <= 8), None)
                if label is not None:
                    label["used"] = True
                    day = {"weekday": label["name"], "entries": [], "pages": set()}
                    days.append(day)
                if day is None:
                    continue
                day["pages"].add(page_number)
                entry = {"date": _booklet_fix_date(text), "paragraphs": [], "open": False}
                day["entries"].append(entry)
                header_rows.append(line["y"])
                continue
            if entry is None or any(abs(line["y"] - y) <= 6 for y in header_rows):
                continue  # the original entry's year and weekday, on the date's row
            day["pages"].add(page_number)
            if line["x1"] < 380:
                continue  # שיעורים
            if entry["open"] and entry["paragraphs"] and entry["paragraphs"][-1][0] == "p":
                previous = entry["paragraphs"][-1][1]
                entry["paragraphs"][-1] = ("p", previous + ("" if previous.endswith(("־", "-")) else " ") + text)
            else:
                entry["paragraphs"].append(("p", text))
            entry["open"] = line["x0"] < 45  # a paragraph's last line stops short of the left margin
    for found in days:
        found["pages"] = sorted(found["pages"])
        for e in found["entries"]:
            e.pop("open", None)
    return days

# --- Which week, and where each day comes from -------------------------------------------------
# The app used to work on weekday names only, each taken as its next date from today, while
# the booklet covers one fixed week: on a Thursday, "Sunday" was this week's Sunday from Dvar
# Malchus but next Sunday from Chabad.org. Now everything works on dates. "This week" is the
# booklet's own week, read off the dates on its Hayom Yom pages; "Pick dates" is any range.
# day_sources() decides where each picked option comes from on each date, and both the summary
# under Generate and the build use it, so the summary shows what the PDF will really have.
WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos']
SHORT_WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Shabbos']
MAX_DAYS = 60
BOOKLET_SECTIONS = {'Chumash': 'חומש יומי', 'Tanya': 'תניא יומי', 'Rambam (3)-Hebrew': 'רמב"ם - שלושה פרקים ליום',
                    'Rambam (1)-Hebrew': 'רמב"ם - פרק אחד ליום'}
BOOKLET_HAYOM_YOM = ('Hayom Yom-Hebrew', 'Hayom Yom-Bilingual')
WEEKLY_DVAR = ['Project Likutei Sichos (Hebrew)', 'Maamarim', 'Krias Hatorah (includes Haftorah)', 'Haftorah']

@st.cache_resource
def _booklet_fetch_failures():
    return {}  # session2 -> when the form last failed to reach dvarmalchus.org, shared by every visitor

def booklet_now(session2):
    """This week's booklet on disk, downloaded over plain HTTP if it isn't there yet (a couple of
    seconds, once for everyone). None if dvarmalchus.org can't be reached; that isn't retried for
    ten minutes, and Generate still tries the browser path."""
    path = f"dvar{session2}.pdf"
    if os.path.exists(path):
        return path
    failures = _booklet_fetch_failures()
    if time.time() - failures.get(session2, 0) < 600:
        return None
    with _dvar_cache_lock:
        if not os.path.exists(path) and not _dvarget_http(session2):
            failures[session2] = time.time()
            return None
    return path

@st.cache_data(ttl="12h", show_spinner=False)
def booklet_sunday(path, mtime):
    """The Sunday that the booklet's week starts on, from the dates on its Hayom Yom, or None."""
    try:
        with fitz.open(path) as doc:
            days = booklet_hayomyom(doc, doc.get_toc())
        sunday = chabad_texts.booklet_week_dates(days or [], date.today())["יום ראשון"]
    except Exception:
        logger.warning("Couldn't tell which week %s is for", path, exc_info=True)
        return None
    if abs((sunday - date.today()).days) > 14:
        logger.warning("%s seems to be for the week of %s; not using that", path, sunday)
        return None
    return sunday

def this_week(session2):
    """(the seven dates Sunday to Shabbos of this week's booklet, True if read from the booklet).
    Without the booklet it's the calendar week we're in."""
    path = booklet_now(session2)
    sunday = booklet_sunday(path, os.path.getmtime(path)) if path else None
    from_booklet = sunday is not None
    if sunday is None:
        sunday = date.today() - timedelta(days=weekday_index(date.today()))
    return [sunday + timedelta(days=n) for n in range(7)], from_booklet

def week_parsha(day, hebrew=False):
    """The parsha of the Shabbos ending day's week, or None when that Shabbos is Yom Tov."""
    shabbos = day + timedelta(days=6 - weekday_index(day))
    return parshios.getparsha_string(dates.GregorianDate(shabbos.year, shabbos.month, shabbos.day), israel=False, hebrew=hebrew)

def chosen_days(day_mode, weekday_picks, date_range, week):
    if day_mode == "Pick dates":
        if not isinstance(date_range, (tuple, list)) or len(date_range) < 2:
            return []  # the end date isn't picked yet
        start, end = date_range[0], date_range[1]
        return [start + timedelta(days=n) for n in range(min((end - start).days + 1, MAX_DAYS))]
    return [week[WEEKDAYS.index(name)] for name in sorted(weekday_picks or [], key=WEEKDAYS.index)]

def day_sources(option, days, booklet_days):
    """[(source, [dates])] in date order, runs of consecutive picked dates with one source:
    'booklet' (Dvar Malchus), 'chabad' (Chabad.org, Sefaria as backup) or 'none'.
    booklet_days holds the booklet week's dates when Dvar Malchus is in use, else nothing."""
    runs = []
    for day in days:
        if option in BOOKLET_SECTIONS:
            source = "booklet" if day in booklet_days else "chabad"
        elif option in BOOKLET_HAYOM_YOM:
            source = "booklet" if day in booklet_days else "none"  # Chabad.org has no Hebrew Hayom Yom
        else:
            source = "chabad"
        if runs and runs[-1][0] == source:
            runs[-1][1].append(day)
        else:
            runs.append((source, [day]))
    return runs

def describe_days(days, by_weekday):
    """'Nov 1–7, Nov 9' for sorted dates, or 'Sun–Tue, Thu' in This week mode, where the form
    doesn't show dates."""
    runs = []
    for day in days:
        if runs and day == runs[-1][-1] + timedelta(days=1):
            runs[-1].append(day)
        else:
            runs.append([day])
    parts = []
    for run in runs:
        first, last = run[0], run[-1]
        if by_weekday:
            name = lambda d: SHORT_WEEKDAYS[weekday_index(d)]
            parts.append(name(first) if first == last else f"{name(first)}–{name(last)}")
        elif first == last:
            parts.append(f"{first:%b %-d}")
        elif first.month == last.month:
            parts.append(f"{first:%b %-d}–{last.day}")
        else:
            parts.append(f"{first:%b %-d} – {last:%b %-d}")
    return ", ".join(parts)

def shnayim_shabbosim(day_mode, days, week):
    """The Shabbosim whose parsha goes in: this week's, or one for each week the dates touch."""
    if day_mode != "Pick dates":
        return [week[6]]
    return sorted({day + timedelta(days=6 - weekday_index(day)) for day in days})

def weekly_unavailable(use_dvar, day_mode, days, week):
    """Why the weekly Dvar Malchus items can't go in, or None if they can."""
    if not use_dvar:
        return "Dvar Malchus is off"
    if day_mode == "Pick dates" and not any(day in week for day in days):
        return f"your dates don't include this week's Dvar Malchus ({describe_days(week, False)})"
    return None

def pdf_plan(opt, days, booklet_days, weekly_reason, shabbosim, describe):
    """[(title, [lines])] for the summary under Generate, in the PDF's order."""
    items = []
    for option in opt:
        if option in WEEKLY_DVAR:
            if option == 'Haftorah' and 'Krias Hatorah (includes Haftorah)' in opt:
                continue  # the Kriah includes it
            line = (":material/menu_book: Dvar Malchus · this week" if weekly_reason is None
                    else f":material/block: Not available · {weekly_reason}")
            items.append((WEEKLY_LABELS.get(option, option), [line]))
        elif option == 'Shnayim Mikra':
            names = [f"Parshas {week_parsha(s)}" if week_parsha(s) else f"none for Shabbos {s:%b %-d} (Yom Tov)" for s in shabbosim]
            items.append(("Shnayim Mikra", [f":material/description: Shnayim Mikra files · {', '.join(names)}"]))
        elif days:
            lines = []
            for source, run in day_sources(option, days, booklet_days):
                if source == "booklet":
                    how = {"Hayom Yom-Hebrew": " (the booklet's pages)", "Hayom Yom-Bilingual": " (with Chabad.org's English)"}.get(option, "")
                    lines.append(f":material/menu_book: Dvar Malchus · {describe(run)}{how}")
                elif source == "chabad":
                    lines.append(f":material/public: Chabad.org · {describe(run)}")
                else:
                    lines.append(f":material/block: Not available · {describe(run)}")
            items.append((option_title(option), lines))
    return items

# --- Building the PDF ---------------------------------------------------------------------------
def _page_runs(pages):
    runs = []
    for page in pages:
        if runs and page == runs[-1][1] + 1:
            runs[-1][1] = page
        else:
            runs.append([page, page])
    return runs

class PdfBuilder:
    """Collects the PDF's parts in order -- booklet pages, Chabad.org days, Sefaria days and whole
    PDFs -- then prints and joins them. Neighbouring Chabad.org days in the same layout print
    together, so a range of dates costs one browser render, not one per day."""

    def __init__(self, booklet_path, week, use_dvar, describe, sizes, chumash_columns):
        self.booklet_path = booklet_path
        self.week, self.use_dvar = week, use_dvar
        self.booklet = fitz.open(booklet_path) if booklet_path else None
        self.toc = self.booklet.get_toc() if self.booklet else []
        self.describe = describe
        self.sizes = sizes
        self.columns = chumash_columns
        self.parts = []
        self.taken = set()      # booklet pages already in: neighbouring days share the page they break on
        self.ranges = {}        # booklet section title -> its day ranges (None if it isn't there)
        self.hayomyom = None    # the booklet's Hayom Yom, by Hebrew weekday
        self.network_failures = 0
        self.kriah_done = False
        self.said = set()

    def say(self, kind, text):
        if text not in self.said:
            self.said.add(text)
            (st.warning if kind == "warning" else st.info)(text)

    def add_pages(self, pages):
        pages = [page for page in pages if page not in self.taken]
        self.taken.update(pages)
        if not pages:
            return
        if self.parts and self.parts[-1][0] == "pages":
            self.parts[-1][1].extend(pages)
        else:
            self.parts.append(["pages", pages])

    def _add_html(self, kind, material, html):
        size = self.sizes[material]
        last = self.parts[-1] if self.parts else None
        if last and last[0] == kind and last[1] == material and last[2] == size:
            last[3].append(html)
        else:
            self.parts.append([kind, material, size, [html]])

    def daily(self, option, days, booklet_days):
        # What happened to which days, gathered for the whole option so the notes come out as
        # one line each rather than one per run of days.
        self.moved = {"chabad": [], "sefaria": [], "lost": [], "none": []}
        for source, run in day_sources(option, days, booklet_days):
            if source == "booklet" and option in BOOKLET_HAYOM_YOM:
                self._booklet_hayomyom(option, run)
            elif source == "booklet":
                self._booklet_days(option, run)
            elif source == "chabad":
                self._chabad_days(option, run)
            else:
                self.moved["none"] += run
        title, moved = option_title(option), {kind: self.describe(sorted(run)) for kind, run in self.moved.items() if run}
        if "chabad" in moved:
            # A section or day missing from the booklet used to drop out of the PDF; that's how
            # Rambam went missing for weeks in Cheshvan 5787.
            self.say("info", f"Couldn't find {title} for {moved['chabad']} in this week's Dvar Malchus, so for those days it "
                             "comes from Chabad.org.")
        # Say so: a silent fallback looks like a layout bug, and hid a real failure for a while.
        if "sefaria" in moved:
            self.say("info", f"Chabad.org didn't work for {title} on {moved['sefaria']}, so for those days it comes from Sefaria, "
                             "in a simpler layout.")
        if "lost" in moved:
            source_note = "Chabad.org, the only source for it" if option_material(option) == "hayomyom" else "Chabad.org or Sefaria"
            self.say("warning", f"Couldn't get {title} for {moved['lost']} from {source_note}, so it isn't in this PDF for those days.")
        if "none" in moved:
            if not self.use_dvar:
                why = "which is turned off, so it isn't in this PDF."
            elif not booklet_days and any(day in self.week for day in self.moved["none"]):
                why = "which couldn't be downloaded, so it isn't in this PDF."
            else:
                why = f"which covers {describe_days(self.week, False)}, so it isn't in this PDF for {moved['none']}."
            self.say("warning", f"{title} only comes from this week's Dvar Malchus, {why}")

    def _booklet_days(self, option, days):
        title = BOOKLET_SECTIONS[option]
        if title not in self.ranges:
            self.ranges[title] = booklet_day_ranges(self.booklet, self.toc, title)
        ranges = self.ranges[title] or {}
        pending = []  # days the booklet doesn't have, for Chabad.org, kept in date order
        for day in days:
            span = ranges.get(BOOKLET_DAYS[weekday_index(day)])
            if span is None:
                logger.warning("%s not found in the booklet for %s", title, day)
                self.moved["chabad"].append(day)
                pending.append(day)
                continue
            if pending:
                self._chabad_days(option, pending)
                pending = []
            self.add_pages(range(span[0], span[1] + 1))
        if pending:
            self._chabad_days(option, pending)

    def _chabad_days(self, option, days):
        material = option_material(option)
        fallback = []  # a run of days Chabad.org couldn't supply
        for day in days:
            section = None
            # After a few network failures in a row Chabad.org is taken to be down, so the
            # rest goes straight to the fallback instead of waiting on each day's timeouts.
            if self.network_failures < 3:
                try:
                    section = chabad_section(option, day, self.columns)
                    self.network_failures = 0
                except requests.RequestException:
                    logger.warning("Chabad.org unreachable for %s on %s", option, day, exc_info=True)
                    self.network_failures += 1
                except Exception:
                    logger.warning("Chabad.org %s failed for %s", option, day, exc_info=True)
            if section is None:
                fallback.append(day)
                continue
            if fallback:
                self._fallback(option, fallback)
                fallback = []
            self._add_html("chabad", material, section)
        if fallback:
            self._fallback(option, fallback)

    def _fallback(self, option, days):
        if option_material(option) == "hayomyom":
            self.moved["lost"] += days  # no other source has Hayom Yom
            return
        for day in days:
            try:
                html = sefaria_day_html(option, day)
            except Exception:
                logger.warning("Sefaria %s failed for %s", option, day, exc_info=True)
                html = None
            if html:
                self._add_html("sefaria", option_material(option), html)
                self.moved["sefaria"].append(day)
            else:
                self.moved["lost"].append(day)

    def _booklet_hayomyom(self, option, days):
        # Hebrew is the booklet's own pages (a page can carry the neighbouring days too);
        # Bilingual pairs the booklet's Hebrew text with Chabad.org's English for the same date.
        if self.hayomyom is None:
            try:
                found = booklet_hayomyom(self.booklet, self.toc) or []
            except Exception:
                logger.warning("Couldn't read Hayom Yom from the booklet", exc_info=True)
                found = []
            self.hayomyom = {day["weekday"]: day for day in found}
        missing, failed = [], []
        for day in days:
            found = self.hayomyom.get(BOOKLET_DAYS[weekday_index(day)])
            if not found:
                missing.append(day)
            elif option == "Hayom Yom-Hebrew":
                self.add_pages(found["pages"])
            else:
                try:
                    self._add_html("chabad", "hayomyom", chabad_texts.hayomyom_booklet_section(day, found["entries"], "both"))
                except Exception:
                    logger.warning("Bilingual Hayom Yom failed for %s", day, exc_info=True)
                    failed.append(day)
        if missing:
            self.say("warning", f"Couldn't find Hayom Yom for {self.describe(missing)} in this week's Dvar Malchus, "
                                f"so {language_label(option)} Hayom Yom for those days isn't in this PDF.")
        if failed:
            self.say("warning", f"Couldn't build Bilingual Hayom Yom for {self.describe(failed)}, so it isn't in this PDF.")

    def weekly(self, option, opt):
        toc, last_page = self.toc, self.booklet.page_count - 1
        if option == 'Maamarim':
            maamarim = booklet_maamarim_pages(toc, last_page)
            if maamarim is None:
                logger.warning("Maamarim section not found in the Dvar Malchus bookmarks")
                self.say("warning", "Couldn't find the Maamarim in this week's Dvar Malchus, so they aren't in this PDF.")
            else:
                self.add_pages(range(maamarim[0], maamarim[1] + 1))
        elif option == 'Project Likutei Sichos (Hebrew)':
            found = False
            for i, item in enumerate(toc):
                words = item[1].split()
                # words[n + 1:n + 2] rather than words[n + 1], so a title that ends in 'לקוטי'
                # can't raise IndexError.
                if any(word == 'לקוטי' and words[n + 1:n + 2] == ['שיחות'] for n, word in enumerate(words)):
                    found = True
                    self.add_pages(range(item[2] - 1, find_next_top_level_bookmark(toc, i, last_page) + 1))
            if not found:
                logger.warning("Likutei Sichos section not found in the Dvar Malchus bookmarks")
                self.say("warning", "Couldn't find Likutei Sichos in this week's Dvar Malchus, so it isn't in this PDF.")
        elif not self.kriah_done:  # Kriah and Haftorah are one section; add it once
            self.kriah_done = True
            kriah = booklet_kriah_pages(self.booklet, toc)
            if kriah is None:
                logger.warning("Krias Hatorah section not found in the Dvar Malchus bookmarks")
                self.say("warning", "Couldn't find Krias Hatorah in this week's Dvar Malchus (Yom Tov booklets may not have it), "
                                    "so the Kriah and Haftorah aren't in this PDF.")
            elif 'Krias Hatorah (includes Haftorah)' in opt:
                self.add_pages(range(kriah[0], kriah[1] + 1))
            else:
                haftorah_start = booklet_haftorah_start(self.booklet_path, kriah[0], kriah[1])
                if haftorah_start is None:
                    logger.warning("Haftorah start not found in pages %d-%d", kriah[0], kriah[1])
                    self.say("warning", "Couldn't find where the Haftorah starts in this week's Dvar Malchus, so it isn't in "
                                        "this PDF. Try Krias Hatorah instead, which includes the Haftorah.")
                else:
                    self.add_pages(range(haftorah_start, kriah[1] + 1))

    def shnayim(self, shabbosim):
        for shabbos in shabbosim:
            parsha = week_parsha(shabbos, hebrew=True)
            if not parsha:
                # pyluach has no parsha when Shabbos is Yom Tov (Pesach, Shavuos, Sukkos...).
                self.say("warning", f"Shabbos {shabbos:%b %-d} is Yom Tov, so there's no weekly parsha and no Shnayim Mikra "
                                    "for that week.")
                continue
            try:
                self.parts.append(["pdf", shnayim_pdf(parsha)])
            except Exception:
                logger.warning("Could not fetch Shnayim Mikra for '%s'", parsha, exc_info=True)
                self.say("warning", f"Couldn't find Shnayim Mikra for {parsha}, so it isn't in this PDF.")

    def assemble(self):
        out = fitz.open()
        for part in self.parts:
            try:
                if part[0] == "pages":
                    for first, last in _page_runs(part[1]):
                        out.insert_pdf(self.booklet, from_page=first, to_page=last)
                    continue
                if part[0] == "chabad":
                    data = render_pdf_bytes(chabad_texts.page(part[1], part[3]), part[2])
                elif part[0] == "sefaria":
                    data = render_pdf_bytes(_SEFARIA_HTML_HEAD + "".join(part[3]) + _SEFARIA_HTML_END, part[2], sefaria=True)
                else:
                    data = part[1]
                with fitz.open(stream=data, filetype="pdf") as piece:
                    out.insert_pdf(piece)
            except Exception:
                logger.warning("Couldn't print part of the PDF (%s)", part[0], exc_info=True)
                self.say("warning", "Part of this PDF couldn't be printed, so it's missing. Please try again.")
        if self.booklet:
            self.booklet.close()
        return out

def generate(opt, day_mode, weekday_picks, date_range, use_dvar, cover, sizes, chumash_columns, session2):
    """Builds the PDF for the form's choices and offers it for download."""
    session = st.session_state.setdefault('id', dt.now())
    try:
        os.remove(f"output_dynamic{session}.pdf")  # last run's, so a failed run can't leave it looking like this one's
    except FileNotFoundError:
        pass
    if not opt:
        st.error("Please select at least one material to print before generating the PDF.")
        return
    week, _ = this_week(session2)
    days = chosen_days(day_mode, weekday_picks, date_range, week)
    daily = [o for o in opt if option_material(o)]
    if daily and not days:
        st.error("Please pick a start and an end date." if day_mode == "Pick dates" else
                 "Please select at least one day of the week if trying to select Chumash, Tanya, Hayom Yom or Rambam.")
        return
    logger.info("Generating %s for %s (%s, Dvar Malchus %s)", opt, days, day_mode, use_dvar)
    weekly = [o for o in opt if o in WEEKLY_DVAR]
    weekly_ok = weekly_unavailable(use_dvar, day_mode, days, week) is None
    from_booklet_needed = any(o in BOOKLET_SECTIONS or o in BOOKLET_HAYOM_YOM for o in daily) and any(d in week for d in days)
    booklet_path = None
    if use_dvar and (cover or (weekly and weekly_ok) or from_booklet_needed):
        try:
            with st.spinner("Getting this week's Dvar Malchus..."):
                dvarget(session2)
        except Exception:
            logger.warning("Dvar Malchus fetch failed, falling back to Chabad.org", exc_info=True)
        if os.path.exists(f"dvar{session2}.pdf"):
            booklet_path = f"dvar{session2}.pdf"
            # If the form couldn't reach dvarmalchus.org, its week was the calendar week; now
            # that the booklet is here, go by its own week.
            week, _ = this_week(session2)
            days = chosen_days(day_mode, weekday_picks, date_range, week)
        else:
            st.warning("Couldn't download this week's Dvar Malchus, so the Hebrew comes from Chabad.org instead, "
                       "and what only Dvar Malchus has is left out.")
    booklet_days = set(week) if booklet_path else set()
    describe = lambda run: describe_days(run, by_weekday=day_mode != "Pick dates")
    builder = PdfBuilder(booklet_path, week, use_dvar, describe, sizes, chumash_columns)
    if cover and booklet_path:
        builder.add_pages([0])
    for option in opt:
        with st.spinner(f"Getting {option_title(option)}..."):
            if option in WEEKLY_DVAR:
                if booklet_path and weekly_ok:
                    builder.weekly(option, opt)
                else:
                    reason = weekly_unavailable(use_dvar, day_mode, days, week) or "it couldn't be downloaded"
                    builder.say("warning", f"{WEEKLY_LABELS.get(option, option)} only comes from this week's Dvar Malchus, "
                                           f"and {reason}, so it isn't in this PDF.")
            elif option == 'Shnayim Mikra':
                builder.shnayim(shnayim_shabbosim(day_mode, days, week))
            else:
                builder.daily(option, days, booklet_days)
    with st.spinner("Putting the PDF together..."):
        out = builder.assemble()
    if out.page_count == 0:
        logger.error("No pages were added to the output PDF (opt=%r, days=%r)", opt, days)
        st.error("No content was generated for your selections — nothing could be added to the PDF. "
                 "Please check your selections and try again.")
        return
    out.save(f"output_dynamic{session}.pdf")
    out.close()
    st.success("PDF created successfully!")
    st.balloons()
    with open(f"output_dynamic{session}.pdf", "rb") as f:
        # on_click="ignore": downloading doesn't rerun the app, so the page stays as it is.
        st.download_button(label="Download ⬇️", data=f.read(), file_name="Custom_Chitas.pdf", mime="application/pdf",
                           on_click="ignore")

@st.cache_data(ttl="12h")
def dateset():
    session2 = dt.now()
    logger.info(f"Session: {session2}")
    return session2

# Linked choices. These used to sit in a st.form, which only reacts on submit; out of the form,
# each choice can update the settings tied to it as soon as it's made.
ALL_DAYS = WEEKDAYS
SIZE_KEYS = ("size_chumash", "size_tanya", "size_rambam", "size_hayom")
CHOICE_KEYS = ("day_mode", "print_size", "chumash_layout")  # single choices that must always have one picked
DVAR_ONLY = {"Hayom Yom-Hebrew": "Hebrew Hayom Yom", "Hayom Yom-Bilingual": "Bilingual Hayom Yom",
             'Project Likutei Sichos (Hebrew)': "Likutei Sichos", 'Maamarim': "Maamarim",
             'Krias Hatorah (includes Haftorah)': "Krias HaTorah", 'Haftorah': "the Haftorah"}
TODAY = date.today()
# ?days=dates opens with Pick dates on; the old Chitas Collator app links here that way.
for key, value in (("days", []), ("whole_week", False), ("print_size", "Standard"), ("chumash_layout", "Two columns"),
                   ("day_mode", "Pick dates" if st.query_params.get("days") == "dates" else "This week"),
                   ("date_range", (TODAY, TODAY + timedelta(days=13))),
                   ("use_dvar", True), ("advanced_open", False), *((k, 100) for k in SIZE_KEYS)):
    st.session_state.setdefault(key, value)
# Streamlit forgets a widget's value on a run where it isn't drawn, so the weekday picks would be
# lost while Pick dates is showing, and the dates while This week is. Keep them.
for key in ("days", "whole_week", "date_range"):
    st.session_state[key] = st.session_state[key]
for key in CHOICE_KEYS:
    if st.session_state.get(key) is not None:
        st.session_state[f"_last_{key}"] = st.session_state[key]

def _keep_choice(key):
    # Clicking the picked choice of a segmented control unpicks it; put it back.
    if st.session_state[key] is None:
        st.session_state[key] = st.session_state.get(f"_last_{key}")

def _whole_week_changed():
    st.session_state.days = list(ALL_DAYS) if st.session_state.whole_week else []

def _days_changed():
    st.session_state.whole_week = set(st.session_state.days or []) == set(ALL_DAYS)

def _quick_range(count):
    st.session_state.date_range = (TODAY, TODAY + timedelta(days=count - 1))

def _print_size_changed():
    # A preset sets every size, and the Chumash layout that suits it. Custom opens Advanced
    # options, where the sizes are.
    _keep_choice("print_size")
    if st.session_state.print_size == "Custom":
        st.session_state.advanced_open = True
    size = PRINT_SIZES.get(st.session_state.print_size)
    if size is not None:
        for key in SIZE_KEYS:
            st.session_state[key] = int(size * 100)
        st.session_state.chumash_layout = "One column" if st.session_state.print_size == "Large" else "Two columns"

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
    "Rambam": RAMBAM_NAMES,
    "Hayom Yom": HAYOM_YOM_NAMES,
    "Likutei Sichos": ['Project Likutei Sichos (Hebrew)'],
    "Maamarim": ['Maamarim'],
    "Krias HaTorah / Haftorah": ['Haftorah', 'Krias Hatorah (includes Haftorah)'],
    "Shnayim Mikra": ['Shnayim Mikra'],
}
PICK_KEYS = ("chumash_pick", "tanya_pick", "hayom_pick", "rambam3_pick", "rambam1_pick", "weekly_pick", "shnayim_pick")
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
    st.markdown("""This app is designed to create a printout for Chitas, Rambam, plus a few other things, for this week or any dates you pick. To get the materials directly and support the original publishers, go to ![](https://www.google.com/s2/favicons?domain=dvarmalchus.org&sz=16) :blue[**[Dvar Malchus](https://dvarmalchus.org/)**]
    and ![](https://www.google.com/s2/favicons?domain=chabad.org&sz=16) :orange[**[Chabad.org](https://www.chabad.org/dailystudy/default_cdo/jewish/Daily-Study.htm/)**].
    For Chumash, Tanya, and Rambam, if both of those are unavailable this app will fall back to ![](https://www.google.com/s2/favicons?domain=sefaria.org&sz=16) :green[**[Sefaria](https://www.sefaria.org/)**].
    """, unsafe_allow_html=True)
    session2 = dateset()
    if os.path.exists(f"dvar{session2}.pdf"):
        week, week_from_booklet = this_week(session2)
    else:
        with st.spinner("Checking this week's Dvar Malchus..."):
            week, week_from_booklet = this_week(session2)
    use_dvar = st.session_state.use_dvar  # the toggle itself is under Print

    st.subheader("1. Days")
    day_mode = st.segmented_control("Days", ["This week", "Pick dates"], key="day_mode", on_change=_keep_choice, args=("day_mode",),
                                    label_visibility="collapsed")
    if day_mode != "Pick dates":
        parsha_name = week_parsha(week[0])
        week_name = f"Parshas {parsha_name}" if parsha_name else "a Yom Tov week"
        st.caption(f"This week's Dvar Malchus: {week_name}" if week_from_booklet else
                   f"This week: {week_name}. (Couldn't check Dvar Malchus just now.)")
        st.pills("Which days?", options=ALL_DAYS, selection_mode="multi", key="days", on_change=_days_changed)
        st.checkbox("Whole week (Sunday to Shabbos)", key="whole_week", on_change=_whole_week_changed)
    else:
        st.date_input("From – to", key="date_range", min_value=TODAY - timedelta(days=30), max_value=TODAY + timedelta(days=365),
                      format="MM/DD/YYYY")
        with st.container(horizontal=True):  # a row, even on a phone, where columns would stack
            st.button("Next 7 days", on_click=_quick_range, args=(7,))
            st.button("Next 2 weeks", on_click=_quick_range, args=(14,))
            st.button("Next 30 days", on_click=_quick_range, args=(30,))
        picked_range = st.session_state.date_range
        if isinstance(picked_range, (tuple, list)) and len(picked_range) == 2 and (picked_range[1] - picked_range[0]).days + 1 > MAX_DAYS:
            st.warning(f"That's more than {MAX_DAYS} days. Only the first {MAX_DAYS} will print.")
    days = chosen_days(day_mode, st.session_state.days, st.session_state.date_range, week)
    in_week = [day for day in days if day in week]
    describe = lambda run: describe_days(run, by_weekday=day_mode != "Pick dates")
    if day_mode == "Pick dates" and days:
        count = f"{len(days)} day{'s' if len(days) != 1 else ''}"
        if not use_dvar:
            st.caption(f"{count}, all from Chabad.org.")
        elif in_week:
            st.caption(f"{count}. This week's Dvar Malchus covers {describe(in_week)}; for the other dates, "
                       "the Hebrew comes from Chabad.org.")
        else:
            st.caption(f"{count}, none in this week's Dvar Malchus ({describe_days(week, False)}), so all from Chabad.org.")

    st.subheader("2. Daily learning")
    st.caption("Pick one or more languages for each. Hebrew comes from Dvar Malchus for its week when that's on; "
               "Bilingual and English come from Chabad.org.")
    learning_left, learning_right = st.columns(2)
    with learning_left:
        chumashopts = st.pills("Chumash with Rashi", options=CHUMASH_NAMES, selection_mode="multi", format_func=language_label, key="chumash_pick")
        tanyaopts = st.pills("Tanya", options=TANYA_NAMES, selection_mode="multi", format_func=language_label, key="tanya_pick")
        hayomopts = st.pills("Hayom Yom", options=HAYOM_YOM_NAMES, selection_mode="multi", format_func=language_label,
                             key="hayom_pick", on_change=_dvar_only_picked,
                             help="Hebrew and Bilingual Hayom Yom come from Dvar Malchus, so only for its week (Chabad.org only has "
                                  "the English). Hebrew is the booklet's own pages, so a page can show the days next to yours too.")
        if [o for o in hayomopts or [] if o in BOOKLET_HAYOM_YOM] and days:
            if not (use_dvar and in_week):
                st.caption(":orange[Hebrew and Bilingual Hayom Yom need dates in this week's Dvar Malchus, with it on.]")
            elif len(in_week) < len(days):
                st.caption(f":orange[Hebrew and Bilingual Hayom Yom only cover {describe(in_week)}.]")
    with learning_right:
        rambam3opts = st.pills("Rambam · 3 chapters a day", options=RAMBAM_NAMES[:3],
                               selection_mode="multi", format_func=language_label, key="rambam3_pick")
        rambam1opts = st.pills("Rambam · 1 chapter a day", options=RAMBAM_NAMES[3:],
                               selection_mode="multi", format_func=language_label, key="rambam1_pick")

    st.subheader("3. Weekly")
    outside_week = day_mode == "Pick dates" and not in_week
    extras = st.pills("From Dvar Malchus", options=WEEKLY_DVAR, selection_mode="multi", format_func=lambda o: WEEKLY_LABELS.get(o, o),
                      key="weekly_pick", on_change=_dvar_only_picked, disabled=outside_week,
                      help="These only come from this week's Dvar Malchus.")
    if outside_week:
        st.caption(f"Only when your dates include this week's Dvar Malchus ({describe_days(week, False)}).")
    shnayimopts = st.pills("Other", options=['Shnayim Mikra'], selection_mode="multi", key="shnayim_pick",
                           help="This week's parsha, or with Pick dates, the parsha of each week your dates touch.")

    st.subheader("4. Print")
    # One per row: side by side, the four Text size choices didn't fit half the width and
    # "Custom" wrapped onto a line of its own.
    print_size = st.segmented_control("Text size", options=list(PRINT_SIZES) + ["Custom"], key="print_size", on_change=_print_size_changed,
                                      help="Compact: smaller text (80%) on fewer pages. Standard: 100%. Large: 130%, easier to read, "
                                           "and switches Chumash to one column. Custom: set each material's size under Advanced "
                                           "options. Pages taken from Dvar Malchus print as they are.")
    chumash_layout = st.segmented_control("Chumash layout (Hebrew)", options=["Two columns", "One column"], key="chumash_layout",
                                          on_change=_keep_choice, args=("chumash_layout",),
                                          help="Two columns fits about a quarter more on a page.")
    source = st.toggle("Use Dvar Malchus when possible", key="use_dvar", on_change=_use_dvar_changed,
                       help="On: for the days in this week's Dvar Malchus, Hebrew Chumash, Tanya, Rambam and Hayom Yom come from the "
                            "booklet, and so do the weekly items. Off: everything comes from Chabad.org (Sefaria if that fails), "
                            "and items only Dvar Malchus has are left out.")
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
    with st.expander("Advanced options", key="advanced_open", on_change="rerun"):
        cover = st.checkbox('Include the cover page from Dvar Malchus', value=False)
        st.caption("Text size for each material. Picking a Text size above sets these; changing one switches it to Custom.")
        scaleslide = st.slider('Chumash', 30, 150, format="%d%%", key="size_chumash", on_change=_size_changed)
        scaleslide4 = st.slider('Tanya', 30, 150, format="%d%%", key="size_tanya", on_change=_size_changed)
        scaleslide2 = st.slider('Rambam', 30, 150, format="%d%%", key="size_rambam", on_change=_size_changed)
        scaleslide3 = st.slider('Hayom Yom', 30, 150, format="%d%%", key="size_hayom", on_change=_size_changed)
    # Chrome's print scale (Page.printToPDF "scale", 10-200%): text, spacing and line length
    # all scale together on the same page. The sliders always match the chosen Text size.
    sizes = {"chumash": scaleslide / 100, "tanya": scaleslide4 / 100, "rambam": scaleslide2 / 100, "hayomyom": scaleslide3 / 100}
    chumash_columns = 1 if chumash_layout == "One column" else 2

    optorder = [name for group in st.session_state.pdf_order for name in ORDER_GROUPS[group]]  # the order picked under Print
    opt = sorted({o for picked in (chumashopts, tanyaopts, hayomopts, rambam3opts, rambam1opts, extras, shnayimopts) for o in picked or []},
                 key=optorder.index)

    submit_button = st.button("Generate PDF ▶️", type="primary", use_container_width=True)
    result_area = st.container()  # the build's progress, notes and download, right under the button

    # A recap of what the PDF will have and where each part comes from, from the same rules the
    # build uses.
    st.divider()
    st.subheader("What the PDF will have")
    shabbosim = shnayim_shabbosim(day_mode, days, week)
    items = pdf_plan(opt, days, set(week) if source else set(), weekly_unavailable(source, day_mode, days, week), shabbosim, describe)
    if days:
        st.caption(f"{len(days)} day{'s' if len(days) != 1 else ''}: {describe(days)} · {len(items)} item{'s' if len(items) != 1 else ''} · "
                   f"text {(print_size or 'Standard').lower()}, Chumash in {(chumash_layout or 'Two columns').lower()}")
    else:
        st.caption("No days picked yet." if day_mode != "Pick dates" else "Pick a start and an end date.")
    for title, lines in items:
        st.markdown(f"**{title}**  \n" + "  \n".join(lines))
    if not opt:
        st.caption("Pick some materials to see where each comes from.")

if submit_button: # build the PDF from the choices above
    with result_area:
        generate(opt, day_mode, st.session_state.days, st.session_state.date_range, source, cover, sizes, chumash_columns, session2)

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

    session = st.session_state.get('id')
    cleanup_stale("dvar", f'dvar{session2}.pdf', timedelta(hours=14))
    cleanup_stale("output_dynamic", f'output_dynamic{session}.pdf', timedelta(minutes=1))
    # The app no longer writes these per-material files; clear any an older version left behind.
    for prefix in ("Chumash", "Tanya", "Rambam", "Hayom", "Shnayim", "temp"):
        cleanup_stale(prefix, None, timedelta(minutes=1))
st.markdown("**Any major bugs noticed? Features that you'd like to see? Comments? Email me [📧 here!](mailto:mkievman@outlook.com)**", unsafe_allow_html=True)

if not submit_button:
    with st.expander("**Changelog:**"):
        st.markdown("**New in latest update (11-5-26)**: <br/> **[NEW]** Pick dates: make a PDF for any dates, up to 60 days, not just this week. Chitas Collator is now part of Dvar Creator. Hebrew comes from Dvar Malchus for the days in its week and from Chabad.org for the others. <br/> **[NEW]** A summary under Generate shows what the PDF will have and where each part comes from. <br/> **[FIX]** Days from Chabad.org now match this week's Dvar Malchus. Later in the week, picking Sunday gave this Sunday's Hebrew from Dvar Malchus but next Sunday's Bilingual or English from Chabad.org. <br/> **[CHANGE]** A day or section that can't be found in Dvar Malchus now comes from Chabad.org instead of being left out. If Chabad.org fails for some days, only those days come from Sefaria. <br/> **[NEW]** With Pick dates, Shnayim Mikra has the parsha of each week your dates touch. <br/> **[FIX]** Downloading the PDF no longer clears it from the page. <br/> **[FIX]** Shabbos's Chumash from Dvar Malchus no longer ends with the booklet's לוח תהלים page. <br/> **[FIX]** Rambam in Hebrew (3 chapters and 1 chapter) was missing from PDFs made from Dvar Malchus for the last few weeks, because the booklet changed how it writes רמב״ם in its titles. It is back. <br/> **[FIX]** Chumash and Tanya no longer fall back to estimated page ranges when a day starts at the very top of a page, or when the booklet's Tanya bookmark points to the wrong page. This week that had added most of Tanya to the end of Chumash for Shabbos. <br/> **[NEW]** If a section you picked can't be found in this week's Dvar Malchus, the app now tells you, instead of quietly leaving it out. <br/> **[FIX]** Maamarim are back. For most weeks since the summer the booklet has titled that section מאמרים instead of מאמר, so the app found nothing and left them out of the PDF. <br/> **[FIX]** The Haftorah no longer prints twice. In some weeks, this one included, it came out twice. <br/> **[FIX]** Krias Hatorah now ends on the right page. Some weeks it cut off the last page of the Haftorah or added a page of the Shabbos Mincha reading. <br/> **[FIX]** Shnayim Mikra no longer crashes the app in weeks when Shabbos is Yom Tov, and it now works for אחרי מות-קדושים and נצבים-וילך. <br/> **[FIX]** Making a second PDF in the same tab with different days or options no longer reuses the Chumash, Tanya, Rambam or Hayom Yom from the first one. <br/> **[FIX]** Picking a Hebrew Rambam together with an English or Bilingual one now gives you both, instead of the Hebrew twice. <br/> **[NEW]** If Likutei Sichos, Krias Hatorah, the Haftorah or Shnayim Mikra can't be found, or you pick something that only comes from Dvar Malchus while it isn't being used, the app now tells you. <br/> **[NEW]** Chumash with Rashi, Tanya, Rambam and Hayom Yom from Chabad.org are back, in a new print layout: Hebrew with full cantillation, Bilingual Rambam side by side, and a header on each day with the English and Hebrew date and the portion. Chabad.org's daily study pages had started blocking the app, so these were coming from Sefaria instead, and Hayom Yom, which only Chabad.org has, was missing from every PDF. They now come from Chabad.org's online library and take a second or two instead of minutes. <br/> **[NEW]** Tanya now has its own row, like Rambam: Hebrew, Bilingual (side by side) and English. The English is Lessons in Tanya, with its explanation printed in gray so the translation stands out. <br/> **[NEW]** Chumash now has its own row too: Hebrew, and Bilingual (English and Hebrew side by side, each with its Rashi). From Chabad.org each pasuk is followed by its Rashi, in block letters, and the Hebrew prints in two columns, which takes about a quarter fewer pages. <br/> **[CHANGE]** English and Bilingual Rambam leave out the translator's footnotes, which more than doubled the length of a day's printout. <br/> **[NEW]** You can now pick more than one Rambam option from Chabad.org; each is included. Before, only the first one came through. <br/> **[NEW]** A clearer form: pick the days (or the whole week), then each material's languages (Hebrew, Bilingual, English), then the weekly items, then how it prints. <br/> **[NEW]** Text size presets: Compact, Standard and Large, or Custom sizes per material under Advanced options. <br/> **[NEW]** Hebrew Chumash can print in one column or two. <br/> **[NEW]** Hayom Yom in Hebrew and Bilingual, from this week's Dvar Malchus (the Hebrew isn't available from Chabad.org). Hebrew is the booklet's own pages; Bilingual sets the booklet's Hebrew next to Chabad.org's English.", unsafe_allow_html=True)
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
