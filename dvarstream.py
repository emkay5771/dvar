from selenium import webdriver #type: ignore
from selenium.webdriver.chrome.service import Service #type: ignore
from webdriver_manager.chrome import ChromeDriverManager #type: ignore
from selenium.webdriver.common.by import By #type: ignore
from selenium.webdriver.support.ui import WebDriverWait #type: ignore
from selenium.webdriver.support import expected_conditions as EC #type: ignore
import os
import time
import fitz as fitz #type: ignore
from base64 import b64decode
from dateutil.relativedelta import relativedelta #type: ignore
from datetime import date #type: ignore
from datetime import datetime as dt #type: ignore
from datetime import timedelta #type: ignore
import streamlit as st #type: ignore
import markdownlit
from markdownlit import mdlit as mdlit
import streamlit_toggle as stt
from streamlit_pills_multiselect import pills
import PyPDF2 #type: ignore
from PyPDF2 import PdfMerger #type: ignore
import glob
import json
import re
from pyluach import parshios, dates

st.set_page_config(page_title="Dvar Creator (BETA)", page_icon="📚", layout="wide", initial_sidebar_state="collapsed")

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
# A global driver is no longer necessary with the new structure.
# driver = webdriver.Chrome(service=service, options=options)

def clean_chabad_pdf(input_path, output_path):
    """
    Opens a PDF from Chabad.org, finds the page where quizzes or footnotes start,
    and saves a new PDF containing only the pages up to and including that page.
    """
    if not os.path.exists(input_path):
        print(f"Input file not found for cleaning: {input_path}")
        return False

    doc = fitz.open(input_path)
    # Default to all pages if no keywords are found
    last_content_page = len(doc) - 1 

    # Keywords that signify the end of the main content
    stop_keywords = ["Quiz Yourself on", "FOOTNOTES FOR", "Download Rambam Study Schedules"]

    for i, page in enumerate(doc):
        text = page.get_text("text")
        # Check if any of the stop keywords are on the page
        if any(keyword in text for keyword in stop_keywords):
            # We've found the start of the unwanted content.
            # The last good page is this page itself.
            last_content_page = i
            print(f"Found stop keyword on page {i} of {input_path}. Truncating after this page.")
            break
    
    try:
        # Create a new document to save the cleaned pages
        with fitz.open() as clean_doc:
            clean_doc.insert_pdf(doc, from_page=0, to_page=last_content_page)
            clean_doc.save(output_path)
        return True
    except Exception as e:
        print(f"Error while creating cleaned PDF for {input_path}: {e}")
        return False
    finally:
        doc.close()

def dvarget(session2): # attempts to retrieve dvar malchus pdf
    print("Dvarget Running")
    driver = webdriver.Chrome(service=service, options=options)
    print("Driver Opened")
    try:
        # Get list of pdfs before download
        initial_pdfs = set(f for f in os.listdir() if f.endswith('.pdf'))

        driver.get("https://dvarmalchus.org")
        print("Dvar Malchus Opened")
        xpaths = [
            "/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div/div/div/a/span/span[2]",
            "/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div",
            "/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div/div/div",
            '/html/body/div[1]/section[2]/div[3]/div/div/div[3]/div/div/a',
            '/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div[1]/div/div/a',
            "/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div[2]/div/div/a",
            '/html/body/div[1]/section[9]/div/div/div/div[3]/div/div/div/div[1]/div/section/div/div/div/section/div/div/div/div/div/div/a',
            '/html/body/div[1]/section[9]/div/div/div/div[3]/div/div/div/div[2]/div/section/div/div/div/section/div/div/div/div/div/div/a'
        ]
        download_link_found = False
        for each in xpaths:
            try:
                link_text = driver.find_element(By.XPATH, f"{each}/span/span[2]").text
                if link_text == "להורדת החוברת השבועית" or link_text == "להורדת החוברת השבועית - חו״ל":
                    print(f"clicking {link_text} link")
                    url = driver.find_element(By.XPATH, each).get_attribute("href")
                    driver.get(url)
                    print(f"URL: {url}")
                    download_link_found = True
                    break
            except:
                continue
        
        if not download_link_found:
            print("Could not find a download link for Dvar Malchus.")
            return False

        # After clicking the link, wait for the new file
        download_wait_timeout = 30  # seconds
        new_file_path = None
        for _ in range(download_wait_timeout):
            time.sleep(1)
            current_pdfs = set(f for f in os.listdir() if f.endswith('.pdf'))
            new_files = current_pdfs - initial_pdfs
            if new_files:
                new_file_path = new_files.pop()
                break
        
        if not new_file_path:
            print("Download timed out. No new PDF file found.")
            return False

        # Wait a bit more for download to complete fully
        time.sleep(5)
        
        print(f"Found new file: {new_file_path}")
        os.rename(new_file_path, f"dvar{session2}.pdf")
        print(f"Renamed to dvar{session2}.pdf")
        return True

    except Exception as e:
        print(f"An error occurred in dvarget: {e}")
        return False
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
    
    driver = webdriver.Chrome(service=service, options=options)
    try:
        # --- Handle Chumash ---
        if 'Chumash' in opt and not os.path.exists(f"Chumash{session}.pdf"):
            chumash_merger = PdfMerger()
            temp_files_to_delete = []
            for idx, i in enumerate(dor):
                driver.get(f"https://www.chabad.org/dailystudy/torahreading.asp?tdate={i}")
                wait = WebDriverWait(driver, 10)
                wait.until(EC.presence_of_element_located((By.ID, "content")))
                driver.execute_script("co_DailyStudy.SetLanguage('he')")
                time.sleep(3)
                
                pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
                
                day_temp_raw_path = f"chumash_day_raw_{session}_{idx}.pdf"
                with open(day_temp_raw_path, "wb") as f:
                    f.write(b64decode(pdf["data"]))
                temp_files_to_delete.append(day_temp_raw_path)

                day_temp_clean_path = f"chumash_day_clean_{session}_{idx}.pdf"
                print(f"Cleaning Chumash day {idx+1}...")
                if clean_chabad_pdf(day_temp_raw_path, day_temp_clean_path):
                    if os.path.exists(day_temp_clean_path) and os.path.getsize(day_temp_clean_path) > 0:
                        chumash_merger.append(day_temp_clean_path)
                    temp_files_to_delete.append(day_temp_clean_path)
            
            chumash_merger.write(f"Chumash{session}.pdf")
            chumash_merger.close()
            for f_path in temp_files_to_delete:
                if os.path.exists(f_path):
                    os.remove(f_path)

        # --- Handle Tanya ---
        if 'Tanya' in opt and not os.path.exists(f"Tanya{session}.pdf"):
            tanya_merger = PdfMerger()
            temp_files_to_delete = []
            for idx, i in enumerate(dor):
                driver.get(f"https://www.chabad.org/dailystudy/tanya.asp?tdate={i}&commentary=false")
                wait = WebDriverWait(driver, 10)
                wait.until(EC.presence_of_element_located((By.ID, "content")))
                driver.execute_script("co_DailyStudy.SetLanguage('he')")
                time.sleep(3)
                
                pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)

                day_temp_raw_path = f"tanya_day_raw_{session}_{idx}.pdf"
                with open(day_temp_raw_path, "wb") as f:
                    f.write(b64decode(pdf["data"]))
                temp_files_to_delete.append(day_temp_raw_path)

                day_temp_clean_path = f"tanya_day_clean_{session}_{idx}.pdf"
                print(f"Cleaning Tanya day {idx+1}...")
                if clean_chabad_pdf(day_temp_raw_path, day_temp_clean_path):
                     if os.path.exists(day_temp_clean_path) and os.path.getsize(day_temp_clean_path) > 0:
                        tanya_merger.append(day_temp_clean_path)
                     temp_files_to_delete.append(day_temp_clean_path)
            
            tanya_merger.write(f"Tanya{session}.pdf")
            tanya_merger.close()
            for f_path in temp_files_to_delete:
                if os.path.exists(f_path):
                    os.remove(f_path)
    finally:
        driver.quit()

def rambamenglish(dor, session, opt): # retrieves all rambam versions from chabad.org
    pdf_options = {
        'scale': scale2,
        'margin-top': '0.1in',
        'margin-right': '0.1in',
        'margin-bottom': '0.1in',
        'margin-left': '0.1in',
    }
    
    selected_rambam_options = [o for o in opt if 'Rambam' in o]
    
    if not selected_rambam_options:
        return

    for selected_rambam_option in selected_rambam_options:
        unique_filename = f"{selected_rambam_option.replace(' ', '_').replace('(', '').replace(')', '')}_{session}.pdf"
        
        if os.path.exists(unique_filename):
            continue

        merger = PdfMerger()
        temp_files_to_delete = []

        lang = ""
        chapters = ""
        if selected_rambam_option == "Rambam (3)-Bilingual":
                lang = "both"; chapters = "3"
        elif selected_rambam_option == "Rambam (3)-Hebrew":
            lang = "he"; chapters = "3"
        elif selected_rambam_option == "Rambam (3)-English":
            lang = "primary"; chapters = "3"
        elif selected_rambam_option == "Rambam (1)-Bilingual":
            lang = "both"; chapters = "1"
        elif selected_rambam_option == "Rambam (1)-Hebrew":
            lang = "he"; chapters = "1"
        elif selected_rambam_option == "Rambam (1)-English":
            lang = "primary"; chapters = "1"

        driver = webdriver.Chrome(service=service, options=options)
        try:
            for idx, i in enumerate(dor):
                base_url = f"https://www.chabad.org/dailystudy/rambam.asp?rambamchapters={chapters}&tdate={i}"
                driver.get(base_url)
                wait = WebDriverWait(driver, 10)
                wait.until(EC.presence_of_element_located((By.ID, "content")))
                
                print(f"Setting language to '{lang}' for {selected_rambam_option}")
                driver.execute_script(f"co_DailyStudy.SetLanguage('{lang}')")
                time.sleep(3)
                
                pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)

                day_temp_raw_path = f"rambam_day_raw_{session}_{idx}.pdf"
                with open(day_temp_raw_path, "wb") as f:
                    f.write(b64decode(pdf["data"]))
                temp_files_to_delete.append(day_temp_raw_path)

                day_temp_clean_path = f"rambam_day_clean_{session}_{idx}.pdf"
                print(f"Cleaning Rambam day {idx+1} for {selected_rambam_option}...")
                if clean_chabad_pdf(day_temp_raw_path, day_temp_clean_path):
                    if os.path.exists(day_temp_clean_path) and os.path.getsize(day_temp_clean_path) > 0:
                        merger.append(day_temp_clean_path)
                    temp_files_to_delete.append(day_temp_clean_path)
            
            merger.write(unique_filename)
            merger.close()
            for f_path in temp_files_to_delete:
                if os.path.exists(f_path):
                    os.remove(f_path)
        finally:
            driver.quit()

def hayomyom(dor, session): #gets hayom yom from chabad.org
    pdf_options = {
        'scale': scale3,
        'margin-top': '0.1in',
        'margin-right': '0.1in',
        'margin-bottom': '0.1in',
        'margin-left': '0.1in',
    }
    if not os.path.exists(f"Hayom{session}.pdf"):
        merger = PdfMerger()
        temp_files_to_delete = []
        driver = webdriver.Chrome(service=service, options=options)
        try:
            for idx, i in enumerate(dor):
                driver.get(f"https://www.chabad.org/dailystudy/hayomyom.asp?tdate={i}")
                wait = WebDriverWait(driver, 10)
                wait.until(EC.presence_of_element_located((By.ID, "content")))
                
                pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)

                day_temp_raw_path = f"hayomyom_day_raw_{session}_{idx}.pdf"
                with open(day_temp_raw_path, "wb") as f:
                    f.write(b64decode(pdf["data"]))
                temp_files_to_delete.append(day_temp_raw_path)

                day_temp_clean_path = f"hayomyom_day_clean_{session}_{idx}.pdf"
                print(f"Cleaning Hayom Yom day {idx+1}...")
                if clean_chabad_pdf(day_temp_raw_path, day_temp_clean_path):
                    if os.path.exists(day_temp_clean_path) and os.path.getsize(day_temp_clean_path) > 0:
                        merger.append(day_temp_clean_path)
                    temp_files_to_delete.append(day_temp_clean_path)
        finally:
            driver.quit()
            merger.write(f"Hayom{session}.pdf")
            merger.close()
            for f_path in temp_files_to_delete:
                if os.path.exists(f_path):
                    os.remove(f_path)

def parshaget(date1): #get parsha from date for shnayim mikra
    year, date, month = date1.split(", ")
    year, date, month = int(year), int(date), int(month)
    parsha = parshios.getparsha_string(dates.GregorianDate(year, date, month), israel=False, hebrew=True)
    st.write(f"This week's parsha is {parsha}.")
    return parsha

def shnayimget(session2, parsha): #get shnayim mikra from github repo
    pdf_options = {}
    parsha2= parsha.split(" ")
    parshaurl = []
    filename = []
    for parsha in parsha2:
        if parshaurl != []:
            parshaurl.append("%20")
        parshaurl.append(parsha)
        filename.append(parsha)
        parshaurl2 = "".join(parshaurl)
        filename2 = " ".join(filename)
    print(parshaurl2)
    if os.path.exists(f"Shnayim{session2}.pdf") != True:
        if 'Shnayim Mikra' in opt:
            driver = webdriver.Chrome(service=service, options=options)
            try:
                driver.get(f"https://github.com/emkay5771/shnayimfiles/blob/master/{parshaurl2}.pdf?raw=true")
                wait = WebDriverWait(driver, 10)
                time.sleep(2)
                if os.path.exists(f"{filename2}.pdf") == True:
                    print(f"file exists {filename2}")
                    os.rename(f"{filename2}.pdf", f"Shnayim{session2}.pdf")
            finally:
                driver.quit()

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
            optconv.append('רמב"ם - פרק אחד ליום')
        elif i == 'Haftorah' or i == 'Krias Hatorah (includes Haftorah)':
            print("appended haftorah")
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
        print(n)
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
    doc_out = fitz.open()
    final_toc = []
    
    if source:
        try:
            doc = fitz.open(f"dvar{session2}.pdf")
            toc = doc.get_toc()
            if cover:
                final_toc.append([1, "Cover", 1])
                doc_out.insert_pdf(doc, from_page=0, to_page=0)

            # Iterate through selected daily portions for Dvar Malchus
            for q in optconv:
                for z in dow:
                    for i, top_level in enumerate(toc):
                        if not top_level[2] or top_level[1] != q:
                            continue
                        for j, sub_level in enumerate(toc[i+1:], start=i+1):
                            if sub_level[0] != top_level[0] + 1:
                                break
                            if z in sub_level[1]:
                                start_page = sub_level[2] - 1
                                end_page = -1
                                if top_level[1] == "חומש יומי": end_page = toc[j+1][2] - (2 if z == 'שבת קודש' else 1)
                                if top_level[1] == "תניא יומי": end_page = toc[j+1][2] - 2
                                if top_level[1] == 'רמב"ם - שלושה פרקים ליום': end_page = toc[j+1][2] - 1
                                if top_level[1] == 'רמב"ם - פרק אחד ליום': end_page = toc[j+1][2] - 1
                                
                                if end_page == start_page:
                                    next_page_num = end_page + 1
                                    if next_page_num < len(doc):
                                        next_page_text = doc[next_page_num].get_text("text")
                                        top_of_page_text = "\n".join(next_page_text.split('\n')[:10])
                                        other_toc_titles = [item[1] for item in toc if item[0] == 1 and item[1] != top_level[1]]
                                        if not any(title in top_of_page_text for title in other_toc_titles):
                                            end_page += 1
                                
                                bookmark_title = f"{top_level[1]} - {sub_level[1]}"
                                final_toc.append([1, bookmark_title, len(doc_out) + 1])
                                doc_out.insert_pdf(doc, from_page=start_page, to_page=end_page)

            # Handle non-daily Dvar Malchus sections
            # ...
        except Exception as e:
            st.error(f"An error occurred while processing the Dvar Malchus PDF: {e}")
            st.info("Please try switching the source to Chabad.org.")
            st.stop()

    # Iterate through all selected options and append their files
    for option in opt:
        # Generate the expected filename based on the option
        if 'Rambam' in option:
            filepath = f"{option.replace(' ', '_').replace('(', '').replace(')', '')}_{session}.pdf"
        elif option == 'Chumash':
            filepath = f"Chumash{session}.pdf"
        elif option == 'Tanya':
            filepath = f"Tanya{session}.pdf"
        elif option == 'Hayom Yom':
            filepath = f"Hayom{session}.pdf"
        elif option == 'Shnayim Mikra':
            filepath = f"Shnayim{session2}.pdf"
        else:
            filepath = None

        if filepath and os.path.exists(filepath):
            # Avoid re-adding content that was handled by Dvar Malchus logic
            if source and option in ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (1)-Hebrew']:
                continue
            
            print(f"Appending {option} from file {filepath}")
            final_toc.append([1, option, len(doc_out) + 1])
            doc_out.insert_pdf(fitz.open(filepath))

    if len(doc_out) > 0:
        doc_out.set_toc(final_toc)
        doc_out.save(os.path.join(output_dir, f"output_dynamic{session}.pdf"))
    else:
        st.error("Could not find any of the selected content. Please check your selections and the source.")
    
    doc_out.close()


@st.cache_data(ttl="12h")
def dateset():
    session2 = dt.now()
    print(f"Session: {session2}")
    return session2

with st.form(key="dvarform", clear_on_submit=False): #streamlit form for user input
    st.title("Dvar Creator 📚 (BETA)")
    st.info("Need more than 1 week? Check out 📖[Chitas Collator](https://chitas-collator.streamlit.app/)!")
    markdownlit.mdlit("""This app is designed to create a printout for Chitas, Rambam, plus a few other things. To get the materials directly and support the original publishers, go to @(**[blue]Dvar Malchus[/blue]**)(https://dvarmalchus.org/)
    and @(🔥)(**[orange]Chabad.org[/orange]**)(https://www.chabad.org/dailystudy/default_cdo/jewish/Daily-Study.htm/).
    """)
    session2 = dateset()
    print(f"test {session2}")
    date1 = date.today().strftime('%Y, %-m, %-d')
    year, day, month = date1.split(", ")
    year, day, month = int(year), int(day), int(month)
    parsha = parshios.getparsha_string(dates.GregorianDate(year, day, month), israel=False, hebrew=True)
    week = pills("Select which days of the week you would like to print.", options=['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos'], multiselect=True, clearable=True, index=None)
    st.write("**Select which materials you would like to print.** (Select as many as you'd like!)")
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
    try:
        if len(basics) > 0:
            print("appending selected basics")
            opt += basics
    except:
        pass
    try:
        if len(rambamopts) > 0:
            print("appending selected rambam")
            opt += rambamopts
    except:
        pass
    try:
        if len(extras) > 0:
            print("appending selected extras")
            opt += extras
    except:
        pass
    print(opt)
    session = st.session_state['id']
    weekorder = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos']
    optorder = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom', 'Project Likutei Sichos (Hebrew)', 'Maamarim', 'Haftorah', 'Krias Hatorah (includes Haftorah)', 'Shnayim Mikra']
    daydependent = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom']
    chabadoptions = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom']
    dow = []
    optconv = []
    dor = []
    opt = sorted(opt, key=lambda x: optorder.index(x) if x in optorder else len(optorder))
    try:
        week = sorted(week, key=weekorder.index)
        daytoheb(week, dow)
        daytorambam(week, dor)
    except:
        pass
    
    opttouse(opt, optconv)
    
    print(optconv)
    print(source)
    if week == [] and any(x in opt for x in daydependent)==True:
        st.error("Please select at least one day of the week if trying to select anything from the 'Basics' or 'Rambam' sections.")
        st.stop()
    if week == [] and ('חומש לקריאה בציבור' in optconv or 'מאמרים' in optconv or 'לקוטי שיחות' in optconv or 'Shnayim Mikra' in optconv):
        week = ['Sunday']
        print(optconv)
    print(week)
    
    # --- Source Determination and Download ---
    dvar_malchus_needed = source and any(o in opt for o in ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (1)-Hebrew', 'Project Likutei Sichos (Hebrew)', 'Maamarim', 'Krias Hatorah (includes Haftorah)'])
    
    if dvar_malchus_needed:
        if not os.path.exists(f"dvar{session2}.pdf"):
            with st.spinner('Attempting to download Dvar Malchus...'):
                dvar_malchus_succeeded = dvarget(session2)
                if not dvar_malchus_succeeded:
                    st.warning("Could not download Dvar Malchus. Falling back to Chabad.org for available materials.")
                    source = False
                    cover = False
    elif source:
        # User wants Dvar Malchus but no applicable sections selected
        st.write("Dvar Malchus not needed for selected options. Using Chabad.org...")
        source = False
        cover = False

    # --- Main Content Fetching ---
    with st.spinner('Creating PDF...'):
        rambam_from_chabad = [o for o in opt if 'Rambam' in o and o not in ['Rambam (3)-Hebrew', 'Rambam (1)-Hebrew']]
        
        if not source: # If using Chabad.org (either by choice or fallback)
            chabadget(dor, opt, session)
            rambamenglish(dor, session, opt)
        
        # This part is for when Dvar Malchus is the source, but some Rambam versions still come from Chabad.org
        elif source and rambam_from_chabad:
            rambamenglish(dor, session, rambam_from_chabad)
        
        if 'Hayom Yom' in opt:
            hayomyom(dor, session)

        if 'Shnayim Mikra' in opt:
            shnayimget(session2, parsha) 

        # --- Final Compilation ---
        dynamicmake(dow, optconv, opt, source, session)

    if os.path.exists(f"output_dynamic{session}.pdf"):
        st.success("PDF created successfully!")
        st.balloons()
        with open(f"output_dynamic{session}.pdf", "rb") as f:
            st.download_button(label="Download ⬇️", data=f, file_name="Custom_Chitas.pdf", mime="application/pdf")

    # --- File Cleanup ---
    all_pdfs = glob.glob("*.pdf")
    current_session_str = str(session)
    current_session2_str = str(session2) # for dvar and shnayim

    for file in all_pdfs:
        # Don't delete the final output file for the current session
        if file == f"output_dynamic{current_session_str}.pdf":
            continue

        # Extract timestamp from filename using regex
        match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{6})', file)
        if match:
            timestamp_str = match.group(1)
            # If the file is from the current session, don't delete it
            if timestamp_str == current_session_str or timestamp_str == current_session2_str:
                continue
            
            # Otherwise, check its age and delete if old
            try:
                file_datetime = dt.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S.%f")
                
                # Set timeout based on file type
                if file.startswith("dvar") or file.startswith("Shnayim"):
                    timeout = timedelta(hours=14)
                else:
                    timeout = timedelta(minutes=1) # For Rambam, Chumash, Tanya, etc.

                if dt.now() - file_datetime > timeout:
                    print(f"Deleting old file: {file}")
                    os.remove(file)
            except ValueError:
                print(f"Could not parse timestamp from {file}, skipping cleanup for this file.")
                continue

markdownlit.mdlit("**Any major bugs noticed? Features that you'd like to see? Comments? Email me [📧 here!](mailto:mkievman@outlook.com)**")

if not submit_button:
    with st.expander("**Changelog:**"):
        markdownlit.mdlit("""
**New in latest update (7-15-24)**: 
<br/> **[FIX]** Complete refactor of PDF generation logic to be more modular and reliable.
<br/> **[FIX]** Reworked Dvar Malchus extraction to be more robust against weekly formatting changes.
<br/> **[FIX]** Implemented more intelligent PDF cleaning to remove quizzes and footnotes without cutting off main content.
<br/> **[FIX]** Resolved multiple stability issues that could cause crashes when fetching content.
<br/> **[FIX]** Correctly handle multiple Rambam selections and sources.
<br/> **[NEW]** Added a navigable Table of Contents to the final PDF.
<br/> **[NEW]** Added more descriptive error messages to guide users if a source fails.

**Past Changes (1-17-24)**: 
<br/> **[FIX]** Updated location of Dvar Malchus download button.

**Past Changes (7-17-23)**: 
<br/> **1:** Repeated compilations of materials from Dvar Malchus should be considerably faster. 
<br/> **2:** Shnayim mikra gets considerably faster on subsequent reruns. 
<br/> **3:** Fixes to maamarim and sichos to fail less often.
""")
if submit_button:
    if os.path.exists(f"output_dynamic{session}.pdf"):
        with st.expander("NOTE: If you are reciving last weeks materials, please click here."):
            newtime= st.button("Clear Cached Time", on_click=dateset.clear)
