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
from pyluach import parshios, dates
import pandas as pd

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
driver = webdriver.Chrome(service=service, options=options)
#driver = webdriver.Chrome(executable_path=chrome_driver_path, options=options)

def chabadget(dor, opt, session): # retrieves chumash and tanya from chabad.org
    pdf_options = {
    'scale': scale,
    'margin-top': '0.1in',
    'margin-right': '0.1in',
    'margin-bottom': '0.1in',
    'margin-left': '0.1in',
    }
    #st.write(f"{scale}")
    if os.path.exists(f"Chumash{session}.pdf") != True:
        merger = PdfMerger()
        if 'Chumash' in opt:
            for i in dor:
                driver = webdriver.Chrome(options=options)
                driver.get(f"https://www.chabad.org/dailystudy/torahreading.asp?tdate={i}#lt=he")
                wait = WebDriverWait(driver, 10)
                element = wait.until(EC.presence_of_element_located((By.ID, "content")))
                pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
                with open(f"temp{session}.pdf", "ab") as f:
                    f.write(b64decode(pdf["data"]))
                f.close()
                driver.quit()
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
                driver.get(f"https://www.chabad.org/dailystudy/tanya.asp?tdate={i}&commentary=false#lt=he")
                wait = WebDriverWait(driver, 10)
                element = wait.until(EC.presence_of_element_located((By.ID, "content")))
                time.sleep(3)
                pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
                with open(f"temp{session}.pdf", "ab") as f:
                    f.write(b64decode(pdf["data"]))
                f.close()
                driver.quit()
                merger2.append(f"temp{session}.pdf")

            merger2.write(f"Tanya{session}.pdf")
            merger2.close()
            if os.path.exists(f"temp{session}.pdf"):
                os.remove(f"temp{session}.pdf")
            
            #with open(f"Tanya{session}.pdf", "rb") as f:
          #      st.download_button(label="Download Tanya", data=f, file_name=f"Tanya{session}.pdf", mime="application/pdf")

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
            #st.write(dor)
            #st.write("Rambam" + i)
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
            driver.get(f"https://www.chabad.org/dailystudy/rambam.asp?rambamchapters={chapters}&tdate={i}#lt={lang}")
            wait = WebDriverWait(driver, 10)
            element = wait.until(EC.presence_of_element_located((By.ID, "content")))
            pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
            with open(f"temp{session}.pdf", "ab") as f:
                f.write(b64decode(pdf["data"]))
            f.close()
            driver.quit()

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
    #st.write(f"{scale}")
    merger3 = PdfMerger()
    if os.path.exists(f"Hayom{session}.pdf") != True:
        for i in dor:
            #st.write(dor)
            #st.write(i)
            driver = webdriver.Chrome(options=options)
            driver.get(f"https://www.chabad.org/dailystudy/hayomyom.asp?tdate={i}")
            wait = WebDriverWait(driver, 10)
            element = wait.until(EC.presence_of_element_located((By.ID, "content")))
            pdf = driver.execute_cdp_cmd("Page.printToPDF", pdf_options)
            with open(f"temp{session}.pdf", "ab") as f:
                f.write(b64decode(pdf["data"]))
            f.close()
            driver.quit()

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
        #print(parshaurl)
    print(parshaurl2)
    if os.path.exists(f"Shnayim{session2}.pdf") != True:
        if 'Shnayim Mikra' in opt:
            driver = webdriver.Chrome(options=options)
            driver.get(f"https://github.com/emkay5771/shnayimfiles/blob/master/{parshaurl2}.pdf?raw=true")
            wait = WebDriverWait(driver, 10)
            time.sleep(2)
            driver.quit()
            if os.path.exists(f"{filename2}.pdf") == True:
                print(f"file exists {filename2}")
                os.rename(f"{filename2}.pdf", f"Shnayim{session2}.pdf")

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
            print("appended haftorah")
            optconv.append('חומש לקריאה בציבור')
        elif i == 'Project Likutei Sichos (Hebrew)':
            optconv.append('לקוטי שיחות')
        elif i == 'Maamarim':
            optconv.append('מאמרים')
        elif i == 'Shnayim Mikra':
            optconv.append('Shnayim Mikra')
            #print("appeneded maamarim")
        elif 'Rambam' in i or 'Hayom Yom' in i:
            optconv.append(i)
    #st.write(optconv)
    return optconv
        
def daytorambam(week, dor): #converts day of week from week in streamlit to date format for chabad.org, for rambamenglish(), hayonyom(), and chabadget()
    print("Day to Rambam Running")
    today = date.today()
    day_to_n = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Shabbos': 5, 'Sunday': 6}
    if week != []:
        start_date=week[0]
        end_date=week[-1]

        # Calculate the number of days between the start and end dates
        num_days = (end_date - start_date).days

        # Generate the list of dates
        date_list2 = [start_date + timedelta(days=i) for i in range(num_days +  1)]
        print(date_list2)
        for i in date_list2:
            y, m, d = str(i).split("-")
            dor.append(f'{m}%2F{d}%2F{y}')  
            print(dor)
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
    #st.write(optconv)
    if source == True:
        try:
            #st.write(f"opening dvar{session}.pdf")
            doc = fitz.open(f"dvar{session2}.pdf")
            #st.write("opened dvar")
            toc = doc.get_toc()
            #st.write("got toc")
            if cover == True:
                doc_out.insert_pdf(doc, from_page=0, to_page=0)
        except:
            st.write("Something went wrong with Dvar Malchus. Attempting to use Chabad.org.")
            print(opt)
            if all(option not in chabadoptions for option in opt) and any(option in opt for option in ['Project Likutei Sichos', 'Maamarim', 'Haftorah']):
                st.error("Project Likutei Sichos, the Haftorah, and Maamarim are not available from Chabad.org. Please try again.")
                st.stop()
            source = False
            chabadget(dor, opt, session)
            pass
   
    #print(toc)
    if source == False:
            print("Chabad.org")
            print(opt)
            for option in opt:
                #st.write(option)
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
        #st.write(optconv)
        for q in optconv:
            #st.write(q)
            for z in dow:
                for i, top_level in enumerate(toc): #type: ignore
                    #st.write(top_level)
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
                                    print("Chumash found")
                                if top_level[1] == "תניא יומי":
                                    end_page = toc[j+1][2] - 2 #type: ignore
                                    print("Tanya found")
                                if top_level[1] == 'רמב"ם - שלושה פרקים ליום':
                                    end_page = toc[j+1][2] - 1 #type: ignore
                                    print("Rambam found")
                                print(f"Current Start Page: {start_page}. Current End Page: {end_page}") #type: ignore
                                start_page, end_page = dedupe(pages, pages2, pages3, start_page, end_page) #type: ignore
                                print(f"New Start Page: {start_page}. New End Page: {end_page}")
                                doc_out.insert_pdf(doc, from_page=start_page, to_page=end_page) #type: ignore
                                continue
            
            if q == 'חומש לקריאה בציבור' or q == 'מאמרים' or q == 'לקוטי שיחות':
                for i, item in enumerate(toc): #type: ignore
                    #st.write(item)
                    #print(item)
                    if q == 'לקוטי שיחות':
                        for word in item[1].split():
                            if word == 'לקוטי' and item[1].split()[item[1].split().index(word) + 1] == 'שיחות':
                                print("Likutei Sichos found")
                                pdf_file = open(f"dvar{session2}.pdf", "rb")
                                pdf_reader = PyPDF2.PdfReader(pdf_file)
                                page_num_start = item[2] - 1
                                print(page_num_start)
                                page_num_end = find_next_top_level_bookmark(toc, i) #type: ignore
                                print(page_num_end)
                                doc_out.insert_pdf(doc, from_page=page_num_start, to_page=page_num_end) #type: ignore
                    if q == 'מאמרים':
                        for word in item[1].split():
                            if word == 'מאמר':
                                print("Maamarim found")
                                pdf_file = open(f"dvar{session2}.pdf", "rb")
                                pdf_reader = PyPDF2.PdfReader(pdf_file)
                                page_num_start = item[2] - 1
                                print(page_num_start)
                                page_num_end = find_next_top_level_bookmark(toc, i) #type: ignore
                                print(page_num_end)
                                doc_out.insert_pdf(doc, from_page=page_num_start, to_page=page_num_end) #type: ignore
                    if item[1] == 'חומש לקריאה בציבור' and q == 'חומש לקריאה בציבור':
                        pdf_file = open(f"dvar{session2}.pdf", "rb")
                        pdf_reader = PyPDF2.PdfReader(pdf_file)
                        page_num_start = item[2] - 1
                        #print(page_num_start)
                        page_num_end = toc[i+1][2] - 3 #type: ignore
                        #print(page_num_end)
                        print("Torah reading found")
                        if "Krias Hatorah (includes Haftorah)" in opt and kriahattatch == False:
                            print("Kriah found")
                            doc_out.insert_pdf(doc, from_page=page_num_start, to_page=page_num_end)
                            kriahattatch = True
                        elif 'Haftorah' in opt and 'Krias Hatorah (includes Haftorah)' not in opt:    
                            for page_num in range(page_num_start, page_num_end):
                                print("Haftorah found")
                                #print(page_num)
                                page = pdf_reader.pages[page_num]
                                text = page.extract_text()
                                #print(text)
                                if "ברכת הפטורה" in text or "xtd enk dxhtdd renyl" in text:
                                    doc_out.insert_pdf(doc, from_page=page_num, to_page=page_num_end) #type: ignore
                                    continue
                        
            if 'Rambam' in q:
                #st.write("Appending Rambam")
                doc_out.insert_pdf(fitz.open(f"Rambam{session}.pdf")) 
                print("Appended")
                continue
            
            if q == 'Hayom Yom':
                print("Hayom Yom found")
                #st.write("Appending Hayom Yom")
                doc_out.insert_pdf(fitz.open(f"Hayom{session}.pdf")) 
                print("Appended")
                continue

            if q == 'Shnayim Mikra':
                print("Shnayim Mikra found")
                #st.write("Appending Shnayim Mikra")
                doc_out.insert_pdf(fitz.open(f"Shnayim{session2}.pdf")) 
                print("Appended")
                continue
                       
    doc_out.save(os.path.join(output_dir, f"output_dynamic{session}.pdf"))
    doc_out.close()


@st.cache_data(ttl="12h")
def dateset():
    session2 = dt.now()
    print(f"Session: {session2}")
    return session2

with st.form(key="dvarform", clear_on_submit=False): #streamlit form for user input
    st.title("Chitas Collator 📖")
    st.info("Welcome to the initial release of Chitas Collator 🥳! Note that this is less feature-rich than the original Dvar Maker, but is designed to allow for longer periods of time to be printed. If you have any feedback or suggestions, feel free to reach out!")
    markdownlit.mdlit("""This app is designed to create a printout for Chitas and Rambam for extended periods of time. To get the materials directly and support the original publishers, go to @(🔥)(**[orange]Chabad.org[/orange]**)(https://www.chabad.org/dailystudy/default_cdo/jewish/Daily-Study.htm/). 
                      For access to more materials (but only 1 week at a time), check out @(📚)(**Dvar Maker**)(https://dvarmaker.streamlit.app).
    """)
    session2 = dateset()
    print(f"test {session2}")
    date1 = date.today().strftime('%Y, %-m, %-d')
    year, day, month = date1.split(", ")
    year, day, month = int(year), int(day), int(month)
    parsha = parshios.getparsha_string(dates.GregorianDate(year, day, month), israel=False, hebrew=True)
    #st.write(f"Today is {date1}. The parsha is {parsha}.")
    #parshaget(date1)
    #week = pills("Select which days of the week you would like to print.", options=['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos'], multiselect=True, clearable=True, index=None)
    week = st.date_input("Select which dates you would like to print:", value=[dt.today()], min_value=date.today(), max_value=None, key="week")
    st.write("**Select which materials you would like to print.** (Select as many as you'd like!)")
    #TODO: add tehillim and rename to "Chitas"
    basics = pills('Basics:', options=['Chumash', 'Tanya', 'Hayom Yom'], multiselect=True, clearable=True, index=None)
    rambamopts = pills('Rambam:', options=['Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English'], multiselect=True, clearable=True, index=None)
    #extras = pills('MISC:', options=['Shnayim Mikra'], multiselect=True, clearable=True, index=None)
    source = False
    with st.expander("Advanced Options"):
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
    if id not in st.session_state:
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
    #st.write(session2)
    #st.write(session)
    weekorder = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos']
    optorder = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom', 'Project Likutei Sichos (Hebrew)', 'Maamarim', 'Haftorah', 'Krias Hatorah (includes Haftorah)', 'Shnayim Mikra']
    daydependent = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom']
    chabadoptions = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Rambam (3)-English', 'Rambam (1)-Hebrew', 'Rambam (1)-Bilingual', 'Rambam (1)-English', 'Hayom Yom']
    dow = []
    optconv = []
    dor = []
    opt = sorted(opt, key=optorder.index)
    try:
        print("sorting week")
        #week = sorted(week, key=weekorder.index)
        #daytoheb(week, dow)
        daytorambam(week, dor)
        print("week sorted")
    except:
        print("no week")
        pass
    
    #st.write(opt)
    
    
    opttouse(opt, optconv)
    
    print(optconv)
    print(source)
    if week == [] and any(x in opt for x in daydependent)==True:
        st.error("Please select at least one day of the week if trying to select anything from the 'Basics' or 'Rambam' sections.")
        st.stop()
    if week == [] and 'חומש לקריאה בציבור' in optconv or 'מאמרים' in optconv or 'לקוטי שיחות' in optconv or 'Shnayim Mikra' in optconv:
        #st.write("appending sunday")
        week = ['Sunday']
        print(optconv)
    print(week)
  

    if source == True:
        if 'Chumash' in opt or 'Tanya' in opt or 'Haftorah' in opt or 'Rambam (3)-Hebrew' in opt or 'Project Likutei Sichos (Hebrew)' in opt or 'Maamarim' in opt or 'Krias Hatorah (includes Haftorah)' in opt:
            if os.path.exists(f"dvar{session2}.pdf") == False:
                with st.spinner('Getting your Chitas...'):
                    source = False
                    cover = False
        else:
            st.write("Getting your Chitas...")
            source = False
            cover = False
    with st.spinner('Creating PDF...'):
        #st.write(opt)
        if source == False:
            chabadget(dor, opt, session)
            #st.write(opt)
            if 'Rambam (3)-Hebrew' in opt or 'Rambam (3)-Bilingual' in opt or 'Rambam (3)-English' in opt or 'Rambam (1)-Bilingual' in opt or 'Rambam (1)-English' in opt or 'Rambam (1)-Hebrew' in opt:
                #st.write("getting rambam")
                rambamenglish(dor, session, opt)
        if source == True:
            if 'Rambam (3)-Bilingual' in opt or 'Rambam (3)-English' in opt or 'Rambam (1)-Bilingual' in opt or 'Rambam (1)-English' in opt or 'Rambam (1)-Hebrew' in opt:
                #st.write("getting rambam")
                rambamenglish(dor, session, opt)
            elif 'Rambam (3)-Hebrew' in opt and os.path.exists(f"dvar{session2}.pdf") == False:
                rambamenglish(dor, session, opt)
        
        if 'Hayom Yom' in opt:
            #st.write(opt)
            #st.write(optconv)
            hayomyom(dor, session)

        if 'Shnayim Mikra' in opt:
            shnayimget(session2, parsha) 

        dynamicmake(dow, optconv, opt, source, session)

    if os.path.exists(f"output_dynamic{session}.pdf"):
        st.success("PDF created successfully!")
        st.balloons()
        with open(f"output_dynamic{session}.pdf", "rb") as f:
            st.download_button(label="Download ⬇️", data=f, file_name="Custom_Chitas.pdf", mime="application/pdf")


    if glob.glob("Rambam*.pdf"):
        for file in glob.glob("Rambam*.pdf"):
            # remove the prefix "flights" and the suffix ".csv" from the file name
            timestamp = file.lstrip("Rambam").rstrip(".pdf")
            # parse the timestamp using the format string "%Y-%m-%d %H:%M:%S.%f"
            file_datetime = dt.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
            # check if the file is older than 10 minutes
            if dt.now() - file_datetime > timedelta(minutes=1):
                if file != f'Rambam{session}.pdf':
                    os.remove(file)

    if glob.glob("Chumash*.pdf"):
        for file in glob.glob("Chumash*.pdf"):
            # remove the prefix "flights" and the suffix ".csv" from the file name
            timestamp = file.lstrip("Chumash").rstrip(".pdf")
            # parse the timestamp using the format string "%Y-%m-%d %H:%M:%S.%f"
            file_datetime = dt.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
            # check if the file is older than 10 minutes
            if dt.now() - file_datetime > timedelta(minutes=1):
                if file != f'Chumash{session}.pdf':
                    os.remove(file)

    if glob.glob("Tanya*.pdf"):
        for file in glob.glob("Tanya*.pdf"):
            # remove the prefix "flights" and the suffix ".csv" from the file name
            timestamp = file.lstrip("Tanya").rstrip(".pdf")
            # parse the timestamp using the format string "%Y-%m-%d %H:%M:%S.%f"
            file_datetime = dt.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
            # check if the file is older than 10 minutes
            if dt.now() - file_datetime > timedelta(minutes=1):
                if file != f'Tanya{session}.pdf':
                    os.remove(file)

    if glob.glob("dvar*.pdf"):
        for file in glob.glob("dvar*.pdf"):
            # remove the prefix "flights" and the suffix ".csv" from the file name
            timestamp = file.lstrip("dvar").rstrip(".pdf")
            # parse the timestamp using the format string "%Y-%m-%d %H:%M:%S.%f"
            file_datetime = dt.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
            # check if the file is older than 10 minutes
            if dt.now() - file_datetime > timedelta(hours=14):
                if file != f'dvar{session2}.pdf':
                    os.remove(file)
    
    if glob.glob('Shnayim*.pdf'):
        for file in glob.glob('Shnayim*.pdf'):
            # remove the prefix "flights" and the suffix ".csv" from the file name
            timestamp = file.lstrip("Shnayim").rstrip(".pdf")
            # parse the timestamp using the format string "%Y-%m-%d %H:%M:%S.%f"
            file_datetime = dt.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
            # check if the file is older than 10 minutes
            if dt.now() - file_datetime > timedelta(hours=14):
                if file != f'Shnayim{session2}.pdf':
                    os.remove(file)
    
    if glob.glob("output_dynamic*.pdf"):
        for file in glob.glob("output_dynamic*.pdf"):
            # remove the prefix "flights" and the suffix ".csv" from the file name
            timestamp = file.lstrip("output_dynamic").rstrip(".pdf")
            # parse the timestamp using the format string "%Y-%m-%d %H:%M:%S.%f"
            file_datetime = dt.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
            # check if the file is older than 10 minutes
            if dt.now() - file_datetime > timedelta(minutes=1):
                if file != f'output_dynamic{session}.pdf':
                    os.remove(file)
markdownlit.mdlit("**Any major bugs noticed? Features that you'd like to see? Comments? Email me [📧 here!](mailto:mkievman@outlook.com)**")

if not submit_button:
    st.info('Future Roadmap: Add back "Extras" such as Shnayim Mikra and Kriah.')
    with st.expander("**Changelog:**"):
        markdownlit.mdlit("**[LAUNCH]** (2-17-24): Initial Release of Chitas Collator")
if submit_button:
    if os.path.exists(f"output_dynamic{session}.pdf"):
        with st.expander("NOTE: If you are reciving last weeks materials, please click here."):
            newtime= st.button("Clear Cached Time", on_click=dateset.clear)
