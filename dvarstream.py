from selenium import webdriver #type: ignore
from selenium.webdriver.chrome.service import Service #type: ignore
from selenium.webdriver.common.by import By #type: ignore
from selenium.webdriver.support.ui import WebDriverWait #type: ignore
from selenium.webdriver.support import expected_conditions as EC #type: ignore
import os
import time
import threading
import logging
import fitz as fitz #type: ignore
from base64 import b64decode
from dateutil.relativedelta import relativedelta #type: ignore
from datetime import date #type: ignore
from datetime import datetime as dt #type: ignore
from datetime import timedelta #type: ignore
import requests #type: ignore
import streamlit as st #type: ignore
import markdownlit
from markdownlit import mdlit as mdlit
import streamlit_toggle as stt
from streamlit_pills_multiselect import pills
import PyPDF2 #type: ignore
from PyPDF2 import PdfMerger #type: ignore
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

def dvarget(session2): # attempts to retrieve dvar malchus pdf
    # dvar{session2}.pdf is a cross-user cache (same weekly booklet for everyone within
    # the cache TTL), so guard the check-then-download-then-write sequence with a lock:
    # without it, two concurrent Streamlit sessions in the same process could both see
    # the file missing and race to scrape/write it at the same time.
    with _dvar_cache_lock:
        if os.path.exists(f"dvar{session2}.pdf"):
            logger.info("dvar%s.pdf already fetched by another session, reusing it", session2)
            return
        _dvarget_locked(session2)

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

    driver.save_screenshot("dvar.png")
    logger.info("waiting for download")
    time.sleep(10)
    os.remove("dvar.png")

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
        if 'Chumash' in opt:
            for i in dor:
                driver = webdriver.Chrome(options=options)
                try:
                    driver.get(f"https://www.chabad.org/dailystudy/torahreading.asp?tdate={i}#lt=he")
                    wait = WebDriverWait(driver, 10)
                    element = wait.until(EC.presence_of_element_located((By.ID, "content")))
                    pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
                finally:
                    driver.quit()
                with open(f"temp{session}.pdf", "ab") as f:
                    f.write(b64decode(pdf["data"]))
                merger.append(f"temp{session}.pdf")

            merger.write(f"Chumash{session}.pdf")
            merger.close()
            if os.path.exists(f"temp{session}.pdf"):
                os.remove(f"temp{session}.pdf")
    if os.path.exists(f"Tanya{session}.pdf") != True:
        merger2 = PdfMerger()
        if 'Tanya' in opt:
            for i in dor:
                driver = webdriver.Chrome(options=options)
                try:
                    driver.get(f"https://www.chabad.org/dailystudy/tanya.asp?tdate={i}&commentary=false#lt=he")
                    wait = WebDriverWait(driver, 10)
                    element = wait.until(EC.presence_of_element_located((By.ID, "content")))
                    time.sleep(3)
                    pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
                finally:
                    driver.quit()
                with open(f"temp{session}.pdf", "ab") as f:
                    f.write(b64decode(pdf["data"]))
                merger2.append(f"temp{session}.pdf")

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
        for i in dor:
            driver = webdriver.Chrome(options=options)
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
                driver.get(f"https://www.chabad.org/dailystudy/rambam.asp?rambamchapters={chapters}&tdate={i}#lt={lang}")
                wait = WebDriverWait(driver, 10)
                element = wait.until(EC.presence_of_element_located((By.ID, "content")))
                pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
            finally:
                driver.quit()
            with open(f"temp{session}.pdf", "ab") as f:
                f.write(b64decode(pdf["data"]))

            merger.append(f"temp{session}.pdf")

        merger.write(f"Rambam{session}.pdf")
        merger.close()
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
        for i in dor:
            driver = webdriver.Chrome(options=options)
            try:
                driver.get(f"https://www.chabad.org/dailystudy/hayomyom.asp?tdate={i}")
                wait = WebDriverWait(driver, 10)
                element = wait.until(EC.presence_of_element_located((By.ID, "content")))
                pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
            finally:
                driver.quit()
            with open(f"temp{session}.pdf", "ab") as f:
                f.write(b64decode(pdf["data"]))

            merger3.append(f"temp{session}.pdf")

        merger3.write(f"Hayom{session}.pdf")
        merger3.close()
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

def find_next_top_level_bookmark(toc, current_index):
    for i in range(current_index + 1, len(toc)):
        if toc[i][0] == 1:
            return toc[i][2] - 2
    return None

def dedupe(pages, pages2, pages3, start_page, end_page): #dedupes the pages when appending, to ensure that pages aren't repeated
    pages2.append(start_page)
    pages2.append(end_page)
    if start_page in pages:
        start_page = start_page + 1
        pages.append(start_page)
        pages3.append(start_page)
    if end_page in pages:
        end_page = end_page - 1
        pages.append(end_page)
        pages3.append(end_page)
    if start_page not in pages:
        pages.append(start_page)
    if end_page not in pages:
        pages.append(end_page)
    return start_page,end_page

def dynamicmake(dow, optconv, opt, source, session): #compiles pdf after collecting all the necessary files
    output_dir = ""
    toc = []
    doc_out = fitz.open()
    pages = []
    pages2 = []
    pages3 = []
    kriahattatch = False
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
            chabadget(dor, opt, session)

    if source == False:
            logger.info("Chabad.org")
            logger.info(opt)
            for option in opt:
                if option == 'Chumash':
                    doc_out.insert_pdf(fitz.open(f"Chumash{session}.pdf"))
                elif option == 'Tanya':
                    doc_out.insert_pdf(fitz.open(f"Tanya{session}.pdf"))
                elif 'Rambam' in option:
                    doc_out.insert_pdf(fitz.open(f"Rambam{session}.pdf")) #type: ignore
                elif option == 'Hayom Yom':
                    doc_out.insert_pdf(fitz.open(f"Hayom{session}.pdf"))
                elif option == 'Shnayim Mikra':
                    doc_out.insert_pdf(fitz.open(f"Shnayim{session2}.pdf"))
                if all(option not in chabadoptions for option in opt) and any(option in opt for option in ['Project Likutei Sichos', 'Maamarim', 'Haftorah', 'Krias Hatorah (includes Haftorah)']):
                    st.error("Project Likutei Sichos, Kriah, the Haftorah, and Maamarim are not available from Chabad.org. Please try again.")
                    st.stop()
                
    else:
        for q in optconv:
            for z in dow:
                for i, top_level in enumerate(toc): #type: ignore
                    if not top_level[2]:
                        continue  # skip top-level bookmarks without a page number
                    if top_level[1] == q:
                        for j, sub_level in enumerate(toc[i+1:], start=i+1): #type: ignore
                            if sub_level[0] != top_level[0] + 1:
                                break  # stop when we reach the next top-level bookmark
                            if z in sub_level[1]:
                                start_page = sub_level[2] - 1
                                if top_level[1] == "חומש יומי":
                                    if z == 'שבת קודש':
                                        end_page = toc[j+1][2] - 2
                                    else:
                                        end_page = toc[j+1][2] - 1 #type: ignore
                                    logger.debug("Chumash found")
                                if top_level[1] == "תניא יומי":
                                    end_page = toc[j+1][2] - 2 #type: ignore
                                    logger.debug("Tanya found")
                                if top_level[1] == 'רמב"ם - שלושה פרקים ליום':
                                    end_page = toc[j+1][2] - 1 #type: ignore
                                    logger.debug("Rambam found")
                                logger.debug(f"Current Start Page: {start_page}. Current End Page: {end_page}") #type: ignore
                                start_page, end_page = dedupe(pages, pages2, pages3, start_page, end_page) #type: ignore
                                logger.debug(f"New Start Page: {start_page}. New End Page: {end_page}")
                                doc_out.insert_pdf(doc, from_page=start_page, to_page=end_page) #type: ignore
                                continue

            if q == 'חומש לקריאה בציבור' or q == 'מאמרים' or q == 'לקוטי שיחות':
                for i, item in enumerate(toc): #type: ignore
                    if q == 'לקוטי שיחות':
                        for word in item[1].split():
                            if word == 'לקוטי' and item[1].split()[item[1].split().index(word) + 1] == 'שיחות':
                                logger.debug("Likutei Sichos found")
                                with open(f"dvar{session2}.pdf", "rb") as pdf_file:
                                    pdf_reader = PyPDF2.PdfReader(pdf_file)
                                    page_num_start = item[2] - 1
                                    page_num_end = find_next_top_level_bookmark(toc, i) #type: ignore
                                    doc_out.insert_pdf(doc, from_page=page_num_start, to_page=page_num_end) #type: ignore
                    if q == 'מאמרים':
                        for word in item[1].split():
                            if word == 'מאמר':
                                logger.debug("Maamarim found")
                                with open(f"dvar{session2}.pdf", "rb") as pdf_file:
                                    pdf_reader = PyPDF2.PdfReader(pdf_file)
                                    page_num_start = item[2] - 1
                                    page_num_end = find_next_top_level_bookmark(toc, i) #type: ignore
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
                doc_out.insert_pdf(fitz.open(f"Rambam{session}.pdf"))
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
    markdownlit.mdlit("""This app is designed to create a printout for Chitas, Rambam, plus a few other things. To get the materials directly and support the original publishers, go to @(**[blue]Dvar Malchus[/blue]**)(https://dvarmalchus.org/)
    and @(🔥)(**[orange]Chabad.org[/orange]**)(https://www.chabad.org/dailystudy/default_cdo/jewish/Daily-Study.htm/).
    """)
    session2 = dateset()
    date1 = date.today().strftime('%Y, %-m, %-d')
    year, day, month = date1.split(", ")
    year, day, month = int(year), int(day), int(month)
    parsha = parshios.getparsha_string(dates.GregorianDate(year, day, month), israel=False, hebrew=True)
    week = pills("Select which days of the week you would like to print.", options=['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos'], multiselect=True, clearable=True, index=None)
    st.write("**Select which materials you would like to print.** (Select as many as you'd like!)")
    #TODO: add tehillim and rename to "Chitas"
    basics = pills('Basics:', options=['Chumash', 'Tanya', 'Hayom Yom'], multiselect=True, clearable=True, index=None)
    rambamopts = pills('Rambam:', options=['Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English'], multiselect=True, clearable=True, index=None)
    extras = pills('MISC:', options=['Project Likutei Sichos (Hebrew)', 'Maamarim', 'Krias Hatorah (includes Haftorah)', 'Haftorah', 'Shnayim Mikra'], multiselect=True, clearable=True, index=None)
    source = stt.st_toggle_switch(label ='Try to use Dvar Malchus, or get from Chabad.org? If toggled on (green), it will attempt to get from Dvar Malchus.', default_value=True, label_after=True, inactive_color='#780c21', active_color='#0c7822', track_color='#0c4c78')  
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
    # pills() returns None (not []) when nothing is selected, so guard with truthiness
    # instead of exception-driven control flow.
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
    # pills() returns None when no day is selected. Normalize to [] here (rather than
    # relying on a bare except to swallow the resulting TypeError) so the "week == []"
    # checks below actually fire instead of silently comparing None == [].
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
        if 'Chumash' in opt or 'Tanya' in opt or 'Haftorah' in opt or 'Rambam (3)-Hebrew' in opt or 'Project Likutei Sichos (Hebrew)' in opt or 'Maamarim' in opt or 'Krias Hatorah (includes Haftorah)' in opt:
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
    with st.spinner('Creating PDF...'):
        if source == False:
            chabadget(dor, opt, session)
            if 'Rambam (3)-Hebrew' in opt or 'Rambam (3)-Bilingual' in opt or 'Rambam (3)-English' in opt or 'Rambam (1)-Bilingual' in opt or 'Rambam (1)-English' in opt or 'Rambam (1)-Hebrew' in opt:
                rambamenglish(dor, session, opt)
        if source == True:
            if 'Rambam (3)-Bilingual' in opt or 'Rambam (3)-English' in opt or 'Rambam (1)-Bilingual' in opt or 'Rambam (1)-English' in opt or 'Rambam (1)-Hebrew' in opt:
                rambamenglish(dor, session, opt)
            elif 'Rambam (3)-Hebrew' in opt and os.path.exists(f"dvar{session2}.pdf") == False:
                rambamenglish(dor, session, opt)

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
markdownlit.mdlit("**Any major bugs noticed? Features that you'd like to see? Comments? Email me [📧 here!](mailto:mkievman@outlook.com)**")

if not submit_button:
    with st.expander("**Changelog:**"):
        markdownlit.mdlit("**New in latest update (1-17-24)**: <br/> **[FIX]** Updated location of Dvar Malchus download button.")
        markdownlit.mdlit("**Past Changes (7-17-23)**: <br/> **1:** Repeated compilations of materials from Dvar Malchus should be considerably faster. <br/> **2:** Shnayim mikra gets considerably faster on subsequent reruns. <br/> **3:** Fixes to maamarim and sichos to fail less often.")
if submit_button:
    if os.path.exists(f"output_dynamic{session}.pdf"):
        with st.expander("NOTE: If you are reciving last weeks materials, please click here."):
            newtime= st.button("Clear Cached Time", on_click=dateset.clear)
