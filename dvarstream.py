from selenium import webdriver #type: ignore
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
import PyPDF2 #type: ignore
from PyPDF2 import PdfMerger #type: ignore
import glob

st.set_page_config(page_title="Dvar Creator (BETA)", page_icon="📚", layout="wide", initial_sidebar_state="collapsed")
st.title("Dvar Creator 📚 (BETA)")

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
driver = webdriver.Chrome(executable_path=chrome_driver_path, options=options)
    
def download_wait(path_to_downloads):
    seconds = 0
    dl_wait = True
    while dl_wait and seconds < 20:
        time.sleep(1)
        dl_wait = False
        for fname in os.listdir("/home/mendy/ccscraper/dvarmalchus"):
            if fname.endswith('.crdownload'):
                dl_wait = True
        seconds += 1
    return seconds   

def dvarget2(session):
    driver = webdriver.Chrome(options=options)
    driver.get("https://dvarmalchus.org")
    for each in ["/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div/div/div",
                "/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div[2]/div/div/a/span/span[2]",
                '/html/body/div[1]/section[9]/div/div/div/div[3]/div/div/div/div[1]/div/section/div/div/div/section/div/div/div/div/div/div/a/span/span[2]',
                '/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div[2]/div/div/a']:
        if driver.find_element(By.XPATH, each).text == "להורדת החוברת השבועית":
            print("clicking regular" + each)
            driver.find_element(By.XPATH, each).click()
        else:
            if driver.find_element(By.XPATH, each).text != "להורדת החוברת השבועית - חו״ל":
                print("skipping " + each)
                continue
            elif driver.find_element(By.XPATH, each).text == "להורדת החוברת השבועית - חו״ל":
                print("clicking alternate" + each)
                driver.find_element(By.XPATH, each).click()
                break

    driver.switch_to.window(driver.window_handles[1])
    #driver.save_screenshot("dvar.png")
    download_wait("")
    #os.remove("dvar.png")

    files = os.listdir()
    sessionyear = "2023" # set the session variable to "2023"
    for file in files:
        if file.endswith(".pdf") and sessionyear not in file: # check if the file is a pdf and does not contain the session variable
            print("renaming " + file)
            os.rename(os.path.join("", file), os.path.join("", f"dvar{session}.pdf"))


    driver.quit()

def dvarget3(session):
    driver = webdriver.Chrome(options=options)
    driver.get("https://dvarmalchus.org")
    for each in ["/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div/div/div",
                 "/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div[1]/div/div/a",
                "/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div[2]/div/div/a/span/span[2]",
                '/html/body/div[1]/section[9]/div/div/div/div[3]/div/div/div/div[1]/div/section/div/div/div/section/div/div/div/div/div/div/a/span/span[2]',
                '/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div[2]/div/div/a']:
        button = driver.find_element(By.XPATH, each)
        if button.text == "להורדת החוברת השבועית":
            print("clicking regular" + each)
            url = button.get_attribute("href")
            driver.get(url)
        else:
            if button.text != "להורדת החוברת השבועית - חו״ל":
                print("skipping " + each)
                continue
            elif button.text == "להורדת החוברת השבועית - חו״ל":
                print("clicking alternate" + each)
                url = button.get_attribute("href")
                driver.get(url)
                break

    # driver.save_screenshot("dvar.png")
    #download_wait("")
    # os.remove("dvar.png")

    files = os.listdir()
    sessionyear = "2023"  # set the session variable to "2023"
    for file in files:
        if file.endswith(".pdf") and sessionyear not in file:  # check if the file is a pdf and does not contain the session variable
            print("renaming " + file)
            os.rename(os.path.join("", file), os.path.join("", f"dvar{session}.pdf"))

    driver.quit()

def dvarget(session):
    driver = webdriver.Chrome(options=options)
    driver.get("https://dvarmalchus.org")
    xpaths = [
        "/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div/div/div",
        '/html/body/div[1]/section[2]/div[3]/div/div/div[3]/div/div/a',
        '/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div[1]/div/div/a',
        "/html/body/div[1]/section[2]/div[3]/div/div/div[4]/div/div/section/section/div/div/div/div[2]/div/div/a",
        '/html/body/div[1]/section[9]/div/div/div/div[3]/div/div/div/div[1]/div/section/div/div/div/section/div/div/div/div/div/div/a',
        '/html/body/div[1]/section[9]/div/div/div/div[3]/div/div/div/div[2]/div/section/div/div/div/section/div/div/div/div/div/div/a'
    ]
    for each in xpaths:
        try:
            link_text = driver.find_element(By.XPATH, f"{each}/span/span[2]").text
            #st.write(link_text)
            if link_text == "להורדת החוברת השבועית" :
                #st.write(f"clicking {each}")
                print(f"clicking {each}")
                url = driver.find_element(By.XPATH, each).get_attribute("href")
                driver.get(url)
                break
            else:
                if link_text != "להורדת החוברת השבועית - חו״ל":
                    #st.write("skipping " + each)
                    print("skipping " + each)
                    continue
                elif link_text == "להורדת החוברת השבועית - חו״ל":
                    #st.write(f"clicking alternate {each}")
                    print(f"clicking alternate {each}")
                    url = driver.find_element(By.XPATH, each).get_attribute("href")
                    print(url)
                    driver.get(url)
                    break
        except:
            #st.write("exception")
            continue


    driver.save_screenshot("dvar.png")
    #st.write("screenshot saved")
    time.sleep(7)
    os.remove("dvar.png")
    #st.write("screenshot removed")

    files = os.listdir()
    sessionyear = "2023"  # set the session variable to "2023"
    for file in files:
        if file.endswith(".pdf") and sessionyear not in file:  # check if the file is a pdf and does not contain the session variable
            print("renaming " + file)
            os.rename(os.path.join("", file), os.path.join("", f"dvar{session}.pdf"))

    driver.quit()

def chabadget(dor, opt, session):
    pdf_options = {
    'scale': 0.8,
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
                driver.get(f"https://www.chabad.org/dailystudy/tanya.asp?date={i}&commentary=false#lt=he")
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

def rambamenglish(dor, session):
    pdf_options = {
    'scale': 0.48,
    'margin-top': '0.1in',
    'margin-right': '0.1in',
    'margin-bottom': '0.1in',
    'margin-left': '0.1in',
    }
    merger = PdfMerger()
    if os.path.exists(f"Rambam{session}.pdf") != True:
        for i in dor:
            st.write(dor)
            st.write("Rambam" + i)
            driver = webdriver.Chrome(options=options)
            driver.get(f"https://www.chabad.org/dailystudy/rambam.asp?rambamchapters=3&tdate={i}#lt=both")
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

def hayomyom(dor, session):
    pdf_options = {
    'scale': 0.48,
    'margin-top': '0.1in',
    'margin-right': '0.1in',
    'margin-bottom': '0.1in',
    'margin-left': '0.1in',
    }
    merger3 = PdfMerger()
    if os.path.exists(f"Hayom{session}.pdf") != True:
        for i in dor:
            st.write(dor)
            st.write(i)
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


def daytoheb(week, dow):
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

def opttouse(opt, optconv):
    for i in opt:
        if i == 'Chumash':
            optconv.append('חומש יומי')
        elif i == 'Tanya':
            optconv.append('תניא יומי')
        elif i == 'Rambam (3)-Hebrew':
            optconv.append('רמב"ם - שלושה פרקים ליום')
        elif i == 'Haftorah':
            optconv.append('חומש לקריאה בציבור')
        elif i == 'Rambam (3)-Bilingual':
            optconv.append(i)
    return optconv
        
def daytorambam(week, dor):
    today = date.today()
    day_to_n = {'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 'Friday': 4, 'Shabbos': 5, 'Sunday': 6}
    for i in week:
        n = day_to_n[i]
        print(n)
        linkappend = today + relativedelta(weekday=n)
        y, m, d = str(linkappend).split("-")
        dor.append(f'{m}%2F{d}%2F{y}')
    return dor

def dynamicmake(dow, optconv, opt, source, session):
    output_dir = ""
    toc = []
    if source == True:
        try:
            #st.write(f"opening dvar{session}.pdf")
            doc = fitz.open(f"dvar{session}.pdf")
            #st.write("opened dvar")
            toc = doc.get_toc()
            #st.write("got toc")
        except:
            st.write("Something went wrong with Dvar Malchus. Attempting to use Chabad.org.")
            source = False
            chabadget(dor, opt, session)
            pass
    doc_out = fitz.open()
    #print(toc)
    if source == False:
            print("Chabad.org")
            print(opt)
            for option in opt:
                if option == 'Chumash':
                    doc_out.insert_pdf(fitz.open(f"Chumash{session}.pdf"))
                elif option == 'Tanya':
                    doc_out.insert_pdf(fitz.open(f"Tanya{session}.pdf"))
                elif option == 'Rambam (3)-Bilingual':
                    doc_out.insert_pdf(fitz.open(f"Rambam{session}.pdf")) #type: ignore
                elif option == 'Hayom Yom':
                    doc_out.insert_pdf(fitz.open(f"Hayom{session}.pdf"))
                break
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
                                    print("Chumash found")
                                if top_level[1] == "תניא יומי":
                                    end_page = toc[j+1][2] - 2 #type: ignore
                                    print("Tanya found")
                                if top_level[1] == 'רמב"ם - שלושה פרקים ליום':
                                    end_page = toc[j+1][2] - 1 #type: ignore
                                    print("Rambam found")
                                doc_out.insert_pdf(doc, from_page=start_page, to_page=end_page) #type: ignore
                                continue
            
            if q == 'חומש לקריאה בציבור':
                for i, item in enumerate(toc): #type: ignore
                    #print(item)
                    if item[1] == 'חומש לקריאה בציבור':
                        pdf_file = open(f"dvar{session}.pdf", "rb")
                        pdf_reader = PyPDF2.PdfReader(pdf_file)
                        page_num_start = item[2] - 1
                        #print(page_num_start)
                        page_num_end = toc[i+1][2] - 3 #type: ignore
                        #print(page_num_end)
                        print("Torah reading found")
                        for page_num in range(page_num_start, page_num_end):
                            #print(page_num)
                            page = pdf_reader.pages[page_num]
                            text = page.extract_text()
                            #print(text)
                            if "ברכת הפטורה" in text or "xtd enk dxhtdd renyl" in text:
                                doc_out.insert_pdf(doc, from_page=page_num, to_page=page_num_end) #type: ignore
                                continue
            if q == 'Rambam (3)-Bilingual':
                doc_out.insert_pdf(fitz.open(f"Rambam{session}.pdf")) 
                print("Appended")
                continue
            
            if q == 'Hayom Yom':
                print("Hayom Yom found")
                doc_out.insert_pdf(fitz.open(f"Hayom{session}.pdf")) 
                print("Appended")
                continue

                      
        
                             
    doc_out.save(os.path.join(output_dir, f"output_dynamic{session}.pdf"))
    doc_out.close()


with st.form(key="dvarform", clear_on_submit=False):
    st.title("Printout Creator")
    st.write("(Work in progress... Bugs may occur.)")
    st.write("This app is designed to create a printout for Chitas, Rambam, and Torah reading. It is currently designed to use both Dvar Malchus and Chabad.org as sources.")
    week = st.multiselect('Select which days of the week you would like to print.', options=['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos'])
    opt = st.multiselect('Select which materials you want.', options=['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Hayom Yom', 'Haftorah'])
    source = st.checkbox('Try to use Dvar Malchus, or get from Chabad.org? If checked, sources from Dvar Malchus will attempt to be used.', value=True)
    submit_button = st.form_submit_button(label="Generate PDF ▶️")

if submit_button:
    if id not in st.session_state:
        st.session_state['id'] = dt.now()
    session = st.session_state.id
    weekorder = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Shabbos']
    optorder = ['Chumash', 'Tanya', 'Rambam (3)-Hebrew', 'Rambam (3)-Bilingual', 'Hayom Yom', 'Haftorah']
    dow = []
    optconv = []
    dor = []
    week = sorted(week, key=weekorder.index)
    opt = sorted(opt, key=optorder.index)

    daytoheb(week, dow)
    opttouse(opt, optconv)
    print(optconv)
    if source == True:
        if os.path.exists(f"{session}.pdf") == False:
            try:
                with st.spinner('Attempting to download Dvar Malchus...'):
                    dvarget(session)
            except:
                st.write("Dvar Malchus not found. Using Chabad.org...")
                source = False
    with st.spinner('Creating PDF...'):
        if source == False:
            daytorambam(week, dor)
            chabadget(dor, opt, session)

        if 'Rambam (3)-Bilingual' in opt:
            daytorambam(week, dor)
            rambamenglish(dor, session)
        
        if 'Hayom Yom' in opt:
            daytorambam(week, dor)
            hayomyom(dor, session)

        dynamicmake(dow, optconv, opt, source, session)

    if os.path.exists(f"output_dynamic{session}.pdf"):
        st.success("PDF created successfully!")
        st.balloons()
        with open(f"output_dynamic{session}.pdf", "rb") as f:
            st.download_button(label="Download ⬇️", data=f, file_name="output_dynamic.pdf", mime="application/pdf")


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
            if dt.now() - file_datetime > timedelta(minutes=1):
                if file != f'dvar{session}.pdf':
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


