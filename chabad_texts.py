"""Daily study texts from Chabad.org, laid out for printing.

Chabad.org's daily study pages (chabad.org/dailystudy) sit behind a Cloudflare check that
fails every request from Streamlit Cloud (Nov 2026). The same texts are served by the
Torah Texts API, the JSON service behind chabad.org/torah-texts. Chabad.org's robots.txt
blocks /api/ in general but explicitly allows /api/v2/chabadorg/torahtexts/, and that path
isn't behind the check. Which portion is studied on a given day comes from the daily study
RSS feed (/tools/rss/dailystudy_rss.xml?tdate=M/D/YYYY), which is also open.

Each *_section() function returns the HTML for one day of one material; page() wraps one
material's days into a printable document. Everything that can go wrong raises, so the
caller can fall back to another source.
"""
import html as html_mod
import re
from datetime import timedelta

import requests
import streamlit as st
from pyluach import dates, hebrewcal, parshios

API = "https://www.chabad.org/api/v2/chabadorg/torahtexts"
RSS = "https://www.chabad.org/tools/rss/dailystudy_rss.xml"
HEADERS = {"User-Agent": "DvarCreator/1.0 (+https://dvarmaker.streamlit.app)"}

CHUMASH_BOOK = 6504074   # Chumash: Daily Studies in Chitas (Hebrew, English, Rashi and more)
TANYA_BOOK = 6237        # Lessons in Tanya (Hebrew and English)
RAMBAM_BOOK = 682956     # Mishneh Torah, Hebrew with Eliyahu Touger's translation
HAYOM_YOM_BOOK = 5742    # Hayom Yom, English translation by Yitschak Meir Kagan
RASHI = 169              # commentator id for Rashi in the Chumash book

CREDITS = {
    "chumash": "Chumash with Rashi from <i>Daily Studies in Chitas</i>, Chabad House Publications and Kehot Publication Society, via Chabad.org.",
    "tanya": "Tanya from <i>Lessons in Tanya</i> by Rabbi Yosef Wineberg, Kehot Publication Society, via Chabad.org.",
    "rambam": "Mishneh Torah, translated by Rabbi Eliyahu Touger, Moznaim Publications, via Chabad.org.",
    "hayomyom": "Hayom Yom, translated by Yitschak Meir Kagan, Kehot Publication Society, via Chabad.org.",
}

HEBREW_WEEKDAYS = ["יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת קודש", "יום ראשון"]
ALIYOS = {1: ("Rishon", "ראשון"), 2: ("Sheini", "שני"), 3: ("Shlishi", "שלישי"), 4: ("Revi'i", "רביעי"),
          5: ("Chamishi", "חמישי"), 6: ("Shishi", "שישי"), 7: ("Shevi'i", "שביעי")}
_NUMERALS = [(400, "ת"), (300, "ש"), (200, "ר"), (100, "ק"), (90, "צ"), (80, "פ"), (70, "ע"), (60, "ס"),
             (50, "נ"), (40, "מ"), (30, "ל"), (20, "כ"), (10, "י"), (9, "ט"), (8, "ח"), (7, "ז"),
             (6, "ו"), (5, "ה"), (4, "ד"), (3, "ג"), (2, "ב"), (1, "א")]
_LETTER_VALUES = {letter: value for value, letter in _NUMERALS}


# --- fetching --------------------------------------------------------------------------

@st.cache_data(ttl="7d", show_spinner=False)
def api(endpoint, article_id):
    response = requests.get(f"{API}/{endpoint}/{article_id}", headers=HEADERS, timeout=20)
    response.raise_for_status()
    return response.json()

@st.cache_data(ttl="7d", show_spinner=False)
def schedule(day):
    """{"Daily Chumash with Rashi": "Parshat Toldot, 1st Portion (...)", ...} for one date."""
    response = requests.get(RSS, params={"tdate": f"{day.month}/{day.day}/{day.year}"}, headers=HEADERS, timeout=20)
    response.raise_for_status()
    items = {}
    for item in re.findall(r"<item>(.*?)</item>", response.text, re.S):
        title = re.search(r"<title>(.*?)</title>", item, re.S)
        description = re.search(r"<description>(.*?)</description>", item, re.S)
        if title and description:
            items[html_mod.unescape(title.group(1)).strip()] = " ".join(html_mod.unescape(description.group(1)).split())
    return items

def _walk(node, parents=()):
    yield node, parents
    for child in node.get("children") or []:
        yield from _walk(child, parents + (node,))


# --- small helpers -----------------------------------------------------------------------

def hebrew_numeral(number):
    letters = ""
    for value, letter in _NUMERALS:
        while number >= value:
            letters += letter
            number -= value
    return letters.replace("יה", "טו").replace("יו", "טז")

def hebrew_number_value(text):
    return sum(_LETTER_VALUES.get(char, 0) for char in text)

def _clean(text, notes=None):
    """Chabad.org's text HTML, made safe and print-friendly. Footnotes (<co:footnote>) are
    pulled out into `notes` and replaced by a superscript number; links keep their text."""
    text = re.sub(r"<script\b.*?</script>", "", text or "", flags=re.S | re.I)
    text = re.sub(r"</?a\b[^>]*>", "", text)
    text = re.sub(r"</?co:root\b[^>]*>", "", text)
    text = re.sub(r"</?co:instructional\b[^>]*>", "", text)
    def footnote(match):
        if notes is None:
            return ""
        notes.append(match.group(1).strip())
        return f'<sup class="fn">{len(notes)}</sup>'
    text = re.sub(r"<co:footnote\b[^>]*>(.*?)</co:footnote>", footnote, text, flags=re.S)
    text = re.sub(r"</?p\b[^>]*>", " ", text)
    return " ".join(text.split())

def _date_parts(day):
    hebrew = dates.GregorianDate(day.year, day.month, day.day).to_heb()
    return {
        "en": day.strftime("%A, %B %-d, %Y"),
        "he": f"{HEBREW_WEEKDAYS[day.weekday()]}, {hebrew.hebrew_date_string()}",
        "hebrew": hebrew,
    }

def _header(material_en, material_he, day, portion_en="", portion_he=""):
    parts = _date_parts(day)
    return (
        '<header class="day-head">'
        f'<div class="row"><span class="material">{material_en}</span><span class="material he">{material_he}</span></div>'
        f'<div class="row sub"><span>{parts["en"]}</span><span class="he">{parts["he"]}</span></div>'
        + (f'<div class="row portion"><span>{portion_en}</span><span class="he">{portion_he}</span></div>' if portion_en or portion_he else "")
        + "</header>"
    )

def _notes_html(notes):
    if not notes:
        return ""
    return '<ol class="notes">' + "".join(f"<li>{note}</li>" for note in notes) + "</ol>"


# --- Chumash with Rashi ----------------------------------------------------------------

def _chumash_portion(day):
    text = schedule(day).get("Daily Chumash with Rashi")
    if not text:
        raise LookupError(f"No Chumash portion listed for {day}")
    match = re.search(r"\(((?:[^()]|\([^()]*\))+?)\s+(\d+:\d+-\d+:\d+(?:,\s*\d+:\d+-\d+:\d+)*)\)", text)
    aliyah = re.search(r"(\d)(?:st|nd|rd|th) Portion", text)
    parsha = re.search(r"Parshat ([^,]+)", text)
    if not match:
        raise LookupError(f"Can't read the Chumash portion {text!r}")
    ranges = [tuple(int(n) for n in re.split(r"[:-]", r)) for r in re.split(r",\s*", match.group(2))]
    return {"book": match.group(1).strip(), "ranges": ranges, "aliyah": int(aliyah.group(1)) if aliyah else None,
            "parsha": parsha.group(1).strip() if parsha else "", "text": text}

def _rashi(verse, language):
    """The verse's Rashi comments in one language, each as "<b>opening words</b> text"."""
    comments = []
    for commentator in verse.get("commentators") or []:
        if commentator.get("type") != RASHI:
            continue
        for commentary in commentator.get("commentaries") or []:
            for comment in commentary.get("hebrew" if language == "he" else "native") or []:
                title = _clean(comment.get("title"))
                body = _clean(comment.get("text"))
                if title or body:
                    comments.append((f'<b class="dh">{title}</b> ' if title else "") + body)
    return comments

def chumash_section(day, language="he", columns=2):
    """language: "he" or "both". Each pasuk is followed by its own Rashi, as on Chabad.org.
    Hebrew runs in two columns, which takes about a quarter fewer pages than one wide column:
    every pasuk and every Rashi ends in a part-empty line, and those cost less in a narrower
    column. Bilingual pairs the English (left) with the Hebrew (right), pasuk by pasuk, each
    with its Rashi in that language."""
    portion = _chumash_portion(day)
    book = next((node for node in api("book-navigation", CHUMASH_BOOK).get("children") or []
                 if node.get("native-title") == portion["book"]), None)
    if book is None:
        raise LookupError(f"Chumash book {portion['book']!r} not found")
    chapters = {int(re.sub(r"\D", "", c.get("native-title") or "0") or 0): c for c in book.get("children") or []}
    blocks, pesukim = [], 0
    for c1, v1, c2, v2 in portion["ranges"]:
        for chapter in range(c1, c2 + 1):
            node = chapters.get(chapter)
            if node is None:
                raise LookupError(f"{portion['book']} chapter {chapter} not found")
            first = v1 if chapter == c1 else 1
            last = v2 if chapter == c2 else 10 ** 6
            if chapter != c1 or not blocks:
                mark = f"Chapter {chapter} · פרק {hebrew_numeral(chapter)}" if language == "both" else f"פרק {hebrew_numeral(chapter)}"
                blocks.append(f'<div class="chapter-mark">{mark}</div>')
            for verse in api("book-content", node["article-id"]).get("verses") or []:
                number = int(re.sub(r"\D", "", (verse.get("native") or {}).get("number") or "0") or 0)
                if not first <= number <= last:
                    continue
                pesukim += 1
                he_number = (verse.get("hebrew") or {}).get("number") or hebrew_numeral(number)
                he_text = _clean((verse.get("hebrew") or {}).get("text"))
                en_text = _clean((verse.get("native") or {}).get("text"))
                rashi_he = _rashi(verse, "he")
                rashi_en = _rashi(verse, "en") if language == "both" else []
                he = (f'<p class="ptext he"><span class="vnum">{he_number}</span>{he_text}</p>'
                      + (f'<div class="prashi he">{" ".join(rashi_he)}</div>' if rashi_he else ""))
                en = (f'<p class="ptext-en"><span class="vnum">{number}</span>{en_text}</p>'
                      + (f'<div class="prashi-en"><span class="rlabel">Rashi</span> {" ".join(rashi_en)}</div>' if rashi_en else ""))
                if language == "both":
                    blocks.append(f'<div class="crow"><div class="en">{en}</div><div class="he">{he}</div></div>')
                else:
                    blocks.append(f'<div class="pasuk">{he}</div>')
    if not pesukim:
        raise LookupError(f"No verses found for {portion['text']!r}")
    aliyah_en, aliyah_he = ALIYOS.get(portion["aliyah"], ("", ""))
    parsha_he = parshios.getparsha_string(dates.GregorianDate(day.year, day.month, day.day), israel=False, hebrew=True) or ""
    ref = "; ".join(f"{c1}:{v1}–{c2}:{v2}" for c1, v1, c2, v2 in portion["ranges"])
    book_en = re.sub(r"\s*\(.*\)", "", portion["book"])
    portion_en = f"Parshat {portion['parsha']} · {aliyah_en} · {book_en} {ref}"
    portion_he = f"פרשת {parsha_he} · {aliyah_he}" if parsha_he else aliyah_he
    return (
        f'<section class="day chumash lang-{language}">'
        + _header("Chumash with Rashi", "חומש עם פירוש רש״י", day, portion_en, portion_he)
        + f'<div class="chumash-{language}{" one-col" if columns == 1 else ""}">' + "".join(blocks) + "</div>"
        + "</section>"
    )


# --- Tanya ---------------------------------------------------------------------------------

_TANYA_SECTIONS = {"amarim": "Likutei Amarim", "yichud": "Shaar Hayichud Vehaemunah", "teshuv": "Igeret HaTeshuva",
                   "kodesh": "Iggeret HaKodesh", "acharon": "Kuntres Acharon"}

def _tanya_lesson(day):
    """(section title, part title, position) for the day, e.g.
    ("Likutei Amarim", "Chapter 14", "beginning"). position is beginning/middle/end/whole."""
    text = schedule(day).get("Daily Tanya", "")
    match = re.match(r"Today's Lessons?:\s*([^,]+),\s*(?:(beginning|middle|end) of\s+)?(.+?)(\s*\(cont'd\))?$", text)
    if not match:
        raise LookupError(f"Can't read the Tanya lesson {text!r}")
    section = next((name for key, name in _TANYA_SECTIONS.items() if key in match.group(1).lower()), None)
    if section is None:
        raise LookupError(f"Unknown Tanya section {match.group(1)!r}")
    position = match.group(2) or ("middle" if match.group(4) else "whole")
    return section, match.group(3).strip(), position

def _tanya_part(section, part):
    for node, parents in _walk(api("book-navigation", TANYA_BOOK)):
        if node.get("native-title") == part and any(p.get("native-title") == section for p in parents):
            return node, next(p for p in parents if p.get("native-title") == section)
    raise LookupError(f"Tanya {section}, {part} not found")

def _tanya_lessons(article_id):
    """The day-by-day lessons of one chapter. Lessons in Tanya numbers each lesson's
    segments in its own block of 500 (503, 504, ... then 1015, ... then 1506, ...)."""
    lessons = {}
    for verse in api("book-content", article_id).get("verses") or []:
        index = verse.get("index")
        if index is None:
            continue
        lessons.setdefault(int(float(index) // 500), []).append(verse)
    return [lessons[key] for key in sorted(lessons)]

def tanya_section(day, language="he"):
    """language: "he", "en" or "both". The English is Lessons in Tanya: the translation
    with Rabbi Wineberg's explanation woven in, which Chabad.org marks as text-annotation
    and the layout prints in gray."""
    section, part, position = _tanya_lesson(day)
    node, section_node = _tanya_part(section, part)
    lessons = _tanya_lessons(node["article-id"])
    if position == "whole":
        chosen = [segment for lesson in lessons for segment in lesson]
        which = ""
    elif position == "beginning":
        chosen, which = lessons[0], "beginning"
    elif position == "end":
        chosen, which = lessons[-1], "end"
    else:
        # A "middle" day: count back to the day this part began.
        back = 1
        while back < 40:
            earlier = _tanya_lesson(day - timedelta(days=back))
            if earlier[:2] != (section, part) or earlier[2] == "beginning":
                break
            back += 1
        if back >= len(lessons):
            raise LookupError(f"Tanya {section}, {part}: lesson {back + 1} of {len(lessons)}")
        chosen, which = lessons[back], "continued"
    heading_he = (node.get("hebrew-title") or "").strip()
    # Each segment is a phrase with its translation. Group phrases into sentences (for the
    # side-by-side rows) and sentences into paragraphs (a Hebrew phrase ending in ":").
    notes, paragraphs, sentence, paragraph = [], [], {"he": [], "en": []}, []
    for segment in chosen:
        he = _clean((segment.get("hebrew") or {}).get("text"))
        if not he or he == heading_he:
            continue
        sentence["he"].append(he)
        if language != "he":
            sentence["en"].append(_clean((segment.get("native") or {}).get("text"), notes))
        if re.search(r"[.:;?!]['\"״׳)\]]*$", he) or len(sentence["he"]) >= 5:
            paragraph.append(sentence)
            sentence = {"he": [], "en": []}
        if he.endswith(":"):
            paragraphs.append(paragraph)
            paragraph = []
    if sentence["he"]:
        paragraph.append(sentence)
    if paragraph:
        paragraphs.append(paragraph)
    if not paragraphs:
        raise LookupError(f"Tanya {section}, {part} came back empty")
    if language == "both":
        body = '<div class="tanya-both">' + "".join(
            f'<div class="trow"><div class="en">{" ".join(s["en"])}</div><div class="he">{" ".join(s["he"])}</div></div>'
            for paragraph in paragraphs for s in paragraph) + "</div>"
    elif language == "en":
        body = '<div class="tanya-en">' + "".join(
            "<p>" + " ".join(t for s in paragraph for t in s["en"]) + "</p>" for paragraph in paragraphs) + "</div>"
    else:
        body = '<div class="tanya-text he">' + "".join(
            "<p>" + " ".join(t for s in paragraph for t in s["he"]) + "</p>" for paragraph in paragraphs) + "</div>"
    which_he = {"beginning": " (התחלה)", "end": " (סוף)", "continued": " (המשך)"}.get(which, "")
    portion_en = f"{section} · {part}" + (f" ({which})" if which else "")
    portion_he = f"{section_node.get('hebrew-title') or ''} · {heading_he}{which_he}"
    return (
        f'<section class="day tanya lang-{language}">'
        + _header("Tanya", "תניא", day, portion_en, portion_he)
        + body + _notes_html(notes)
        + "</section>"
    )


# --- Rambam --------------------------------------------------------------------------------

# Where the daily schedule names a part of the Mishneh Torah differently from the book.
_RAMBAM_ALIASES = {"Introduction": "Introduction to Mishneh Torah"}

def _rambam_chapters(day, chapters):
    """[(node, parts)] for the day. parts is empty for a normal chapter. At the start of each
    cycle the schedule lists the Introduction and the lists of mitzvos in parts
    ("Positive Commandments: Part 1, ...: Part 2, ...: Part 3"), while the book has each as
    one text; parts holds the part numbers listed for it. Chapter names can themselves
    contain commas ("Shofar, Sukkah, vLulav - Chapter 3"), so match the longest known title
    rather than splitting on commas."""
    title = "Daily Rambam - 3 Chapter Per Day (Hebrew)" if chapters == 3 else "Daily Rambam - 1 Chapter Per Day (Hebrew)"
    rest = re.sub(r"^Today's Lessons?:\s*", "", schedule(day).get(title, "")).strip()
    if not rest:
        raise LookupError(f"No Rambam lesson listed for {day}")
    leaves = {node.get("native-title"): node for node, _ in _walk(api("book-navigation", RAMBAM_BOOK))}
    picked = []
    while rest:
        part = re.match(r"([^,]+?): Part (\d+)\s*(?:,\s*|$)", rest)
        if part:
            name = _RAMBAM_ALIASES.get(part.group(1).strip(), part.group(1).strip())
            if name not in leaves:
                raise LookupError(f"Rambam {name!r} not found")
            existing = next((entry for entry in picked if entry[0] is leaves[name]), None)
            if existing:
                existing[1].append(int(part.group(2)))
            else:
                picked.append((leaves[name], [int(part.group(2))]))
            rest = rest[part.end():]
            continue
        name = max((t for t in leaves if t and rest.startswith(t) and re.match(r"\s*(,|$)", rest[len(t):])), key=len, default=None)
        if name is None:
            raise LookupError(f"Rambam chapter not found at {rest!r}")
        picked.append((leaves[name], []))
        rest = rest[len(name):].lstrip(" ,")
    return picked

def rambam_section(day, chapters, language):
    """language: "he", "en" or "both"."""
    blocks = []
    picked = _rambam_chapters(day, chapters)
    nodes = [node for node, _ in picked]
    for node, parts in picked:
        halachos = {}
        for verse in api("book-content", node["article-id"]).get("verses") or []:
            if verse.get("index") is None:
                continue
            number = int(float(verse["index"]))
            entry = halachos.setdefault(number, {"he": [], "en": []})
            entry["he"].append(_clean((verse.get("hebrew") or {}).get("text")))
            if language != "he":
                # Rabbi Touger's footnotes are left out: they often run longer than the
                # halachos themselves and tripled the length of a day's printout.
                entry["en"].append(_clean((verse.get("native") or {}).get("text")))
        title_en = (node.get("native-title") or "").replace(" - ", " — ")
        hebrew_title = node.get("hebrew-title") or ""
        title_he = ("הלכות " if " - פרק" in hebrew_title else "") + hebrew_title.replace(" - ", " · ")
        if parts and sorted(parts) != [1, 2, 3]:
            # The 1-chapter cycle spreads these over several days; the book doesn't mark
            # where its parts begin, so print the whole text and say so.
            title_en += f" (Part {', '.join(map(str, parts))}; the whole text is shown)"
        rows = []
        for number in sorted(halachos):
            he = " ".join(t for t in halachos[number]["he"] if t)
            en = " ".join(t for t in halachos[number]["en"] if t)
            if language == "both":
                rows.append(f'<div class="hrow"><div class="en"><span class="hnum">{number}</span>{en}</div>'
                            f'<div class="he"><span class="hnum">{hebrew_numeral(number)}</span>{he}</div></div>')
            elif language == "he":
                rows.append(f'<p class="he"><span class="hnum">{hebrew_numeral(number)}</span>{he}</p>')
            else:
                rows.append(f'<p><span class="hnum">{number}</span>{en}</p>')
        if not rows:
            raise LookupError(f"Rambam {title_en} came back empty")
        blocks.append(
            f'<div class="perek perek-{language}"><h3><span>{title_en}</span><span class="he">{title_he}</span></h3>'
            + "".join(rows) + "</div>")
    names_en = ", ".join((n.get("native-title") or "").replace(" - Chapter ", " ") for n in nodes)
    names_he = ", ".join((n.get("hebrew-title") or "").replace(" - פרק ", " ") for n in nodes)
    if all(" - פרק " in (n.get("hebrew-title") or "") for n in nodes):
        names_he = "הלכות " + names_he
    material_en = "Rambam · " + ("3 chapters" if chapters == 3 else "1 chapter")
    material_he = "רמב״ם · " + ("שלושה פרקים ליום" if chapters == 3 else "פרק אחד ליום")
    return (
        f'<section class="day rambam lang-{language}">'
        + _header(material_en, material_he, day, names_en, names_he)
        + "".join(blocks)
        + "</section>"
    )


# --- Hayom Yom -----------------------------------------------------------------------------

_HAYOM_YOM_MONTHS = {1: "Nissan", 2: "Iyar", 3: "Sivan", 4: "Tamuz", 5: "Menachem Av", 6: "Elul",
                     7: "Tishrei", 8: "Cheshvan", 9: "Kislev", 10: "Tevet", 11: "Shevat",
                     12: "Adar I", 13: "Adar II"}

def _hayomyom_index():
    index = {}
    for month in api("book-navigation", HAYOM_YOM_BOOK).get("children") or []:
        name = re.sub(r"\s+\d.*$", "", month.get("native-title") or "").strip()
        for node in month.get("children") or []:
            token = re.split(r"[\s,]", (node.get("hebrew-title") or "").strip(), maxsplit=1)[0]
            number = hebrew_number_value(token)
            if 1 <= number <= 30:
                index.setdefault(name, {})[number] = node
    return index

def hayomyom_entries(day):
    """The entries Chabad.org's own daily page shows for a date: the book follows 5703, a
    leap year, so a regular-year Adar shows both Adar I and Adar II, and a 30th the book
    lacks (30 Cheshvan or Kislev) shows day 29."""
    hebrew = dates.GregorianDate(day.year, day.month, day.day).to_heb()
    if hebrew.month == 12 and not hebrewcal.Year(hebrew.year).leap:
        months = ["Adar I", "Adar II"]
    else:
        months = [_HAYOM_YOM_MONTHS[hebrew.month]]
    index = _hayomyom_index()
    found = []
    for month in months:
        days = index.get(month, {})
        candidates = [number for number in days if number <= hebrew.day]
        if candidates:
            found.append(days[max(candidates)])
    if not found:
        raise LookupError(f"No Hayom Yom entry for {day}")
    return found

def hayomyom_section(day):
    body = []
    for entry in hayomyom_entries(day):
        notes = []
        paragraphs = []
        for verse in api("book-content", entry["article-id"]).get("verses") or []:
            text = _clean((verse.get("native") or {}).get("text"), notes)
            if text:
                paragraphs.append(f"<p>{text}</p>")
        if not paragraphs:
            raise LookupError(f"Hayom Yom entry {entry['article-id']} came back empty")
        title = html_mod.escape(re.sub(r"^Hayom Yom:\s*", "", entry.get("native-title") or ""))
        body.append(f'<div class="entry"><div class="entry-title">{title}</div>{"".join(paragraphs)}{_notes_html(notes)}</div>')
    return '<section class="day hayomyom">' + _header("Hayom Yom", "היום יום", day) + "".join(body) + "</section>"


# --- Hayom Yom in Hebrew, from the Dvar Malchus booklet -----------------------------------
# Chabad.org's Torah Texts API only has the English Hayom Yom; its Hebrew is only on the
# daily study pages, which block the app. The weekly Dvar Malchus booklet prints the Hebrew
# (with nikud) for each day, and dvarstream.booklet_hayomyom() reads it out as text, so it
# can be laid out here like everything else, and paired with Chabad.org's English by date.

_HEBREW_MONTH_KEYS = [("מנחם אב", "Menachem Av"), ("אדר שני", "Adar II"), ("אדר ב", "Adar II"), ("אדר ראשון", "Adar I"),
                      ("אדר א", "Adar I"), ("אדר", "Adar I"), ("מרחשון", "Cheshvan"), ("חשוון", "Cheshvan"), ("חשון", "Cheshvan"),
                      ("תשרי", "Tishrei"), ("כסלו", "Kislev"), ("טבת", "Tevet"), ("שבט", "Shevat"), ("ניסן", "Nissan"),
                      ("אייר", "Iyar"), ("סיוון", "Sivan"), ("סיון", "Sivan"), ("תמוז", "Tamuz"), ("אלול", "Elul"), ("אב", "Menachem Av")]
_MONTH_NUMBERS = {"Nissan": 1, "Iyar": 2, "Sivan": 3, "Tamuz": 4, "Menachem Av": 5, "Elul": 6, "Tishrei": 7,
                  "Cheshvan": 8, "Kislev": 9, "Tevet": 10, "Shevat": 11, "Adar I": 12, "Adar II": 13}

def parse_hebrew_date(text):
    """("Cheshvan", 22) for "כב חשון" (also "כב חשון, שבת ..." and ט"ו-style numerals)."""
    for hebrew, key in _HEBREW_MONTH_KEYS:
        match = re.search(r"([א-ת'\"״׳]{1,4})\s+" + hebrew + r"(?![א-ת])", text)
        if match:
            day = hebrew_number_value(re.sub(r"[^א-ת]", "", match.group(1)))
            if 1 <= day <= 30:
                return key, day
    return None

def _gregorian_near(month_key, day_number, today):
    """The Gregorian date of this Hebrew day and month nearest to today."""
    hebrew_today = dates.GregorianDate(today.year, today.month, today.day).to_heb()
    best = None
    for year in (hebrew_today.year - 1, hebrew_today.year, hebrew_today.year + 1):
        month = _MONTH_NUMBERS[month_key]
        if month == 13 and not hebrewcal.Year(year).leap:
            month = 12
        try:
            candidate = dates.HebrewDate(year, month, min(day_number, 29 if month in (8, 9) else 30)).to_pydate()
        except ValueError:
            continue
        if best is None or abs((candidate - today).days) < abs((best - today).days):
            best = candidate
    return best

def booklet_week_dates(days, today):
    """{"יום ראשון": date, ...} for the booklet's week. Each day's entry date gives a guess at
    that week's Sunday; take the most common, since a day can repeat the previous entry (30
    Cheshvan shows 29) or show a Yom Tov entry."""
    order = ["יום ראשון", "יום שני", "יום שלישי", "יום רביעי", "יום חמישי", "יום שישי", "שבת קודש"]
    votes = {}
    for day in days:
        for entry in day["entries"][:1]:
            parsed = parse_hebrew_date(entry["date"])
            if parsed and day["weekday"] in order:
                gregorian = _gregorian_near(parsed[0], parsed[1], today)
                if gregorian:
                    sunday = gregorian - timedelta(days=order.index(day["weekday"]))
                    votes[sunday] = votes.get(sunday, 0) + 1
    if not votes:
        raise LookupError("Couldn't date the booklet's Hayom Yom")
    sunday = max(votes, key=votes.get)
    return {name: sunday + timedelta(days=n) for n, name in enumerate(order)}

def _hebrew_entry_html(entry):
    parts = []
    for kind, text in entry["paragraphs"]:
        if kind == "label":
            parts.append(f'<div class="tlabel">{html_mod.escape(text)}</div>')
        else:
            parts.append(f"<p>{html_mod.escape(text)}</p>")
    return (f'<div class="entry-title he">{html_mod.escape(entry["date"])}</div>' + "".join(parts))

def hayomyom_booklet_section(day_date, entries, language="he"):
    """One day of Hayom Yom from the booklet's entries. language: "he" or "both"; for
    "both" each entry is paired with Chabad.org's English of the same Hebrew date."""
    if not entries:
        raise LookupError(f"No Hayom Yom entry in the booklet for {day_date}")
    rows = []
    for entry in entries:
        hebrew = f'<div class="hy-he he">{_hebrew_entry_html(entry)}</div>'
        if language != "both":
            rows.append(f'<div class="entry">{hebrew}</div>')
            continue
        parsed = parse_hebrew_date(entry["date"])
        node = _hayomyom_index().get(parsed[0], {}).get(parsed[1]) if parsed else None
        if node is None:
            raise LookupError(f"No English Hayom Yom for {entry['date']!r}")
        notes, paragraphs = [], []
        for verse in api("book-content", node["article-id"]).get("verses") or []:
            text = _clean((verse.get("native") or {}).get("text"), notes)
            if text:
                paragraphs.append(f"<p>{text}</p>")
        title = html_mod.escape(re.sub(r"^Hayom Yom:\s*", "", node.get("native-title") or ""))
        english = f'<div class="hy-en"><div class="entry-title">{title}</div>{"".join(paragraphs)}{_notes_html(notes)}</div>'
        rows.append(f'<div class="entry hyrow">{english}{hebrew}</div>')
    return (f'<section class="day hayomyom lang-{language}">' + _header("Hayom Yom", "היום יום", day_date)
            + "".join(rows) + "</section>")


# --- page ------------------------------------------------------------------------------------

FONTS = ("https://fonts.googleapis.com/css2?family=Noto+Serif+Hebrew:wght@400;600;700"
         "&family=Crimson+Pro:ital,wght@0,400;0,600;1,400&display=block")

CSS = """
@page { size: Letter; margin: 0.55in 0.6in 0.6in; }
:root { --ink: #1c1a17; --muted: #6f675c; --rule: #b89b5e; --soft: #e8dfcc; }
* { box-sizing: border-box; }
body { margin: 0; color: var(--ink); font-family: 'Crimson Pro', Georgia, serif; font-size: 12.5pt; line-height: 1.45; }
.he { font-family: 'Noto Serif Hebrew', 'David', serif; direction: rtl; text-align: right; }
.day { break-before: page; }
.day:first-of-type { break-before: auto; }
.day-head { border-bottom: 1.6pt solid var(--rule); margin-bottom: 12pt; padding-bottom: 5pt; }
.day-head .row { display: flex; justify-content: space-between; align-items: baseline; gap: 16pt; }
.day-head .material { font-size: 17pt; font-weight: 600; letter-spacing: 0.01em; }
.day-head .material.he { font-size: 19pt; font-weight: 700; }
.day-head .sub { color: var(--muted); font-size: 10.5pt; margin-top: 1pt; }
.day-head .sub .he { font-size: 11pt; }
.day-head .portion { font-size: 11pt; margin-top: 3pt; color: #3d382f; }
.day-head .portion .he { font-size: 12pt; font-weight: 600; }
.notes { font-size: 9pt; line-height: 1.35; color: #4a443b; margin: 8pt 0 0; padding-left: 16pt; columns: 2; column-gap: 18pt; border-top: 0.6pt solid var(--soft); padding-top: 5pt; }
.notes li { break-inside: avoid; margin-bottom: 2pt; }
sup.fn { font-size: 7pt; color: var(--rule); font-weight: 600; }
.credit { margin-top: 14pt; font-size: 8.5pt; color: var(--muted); text-align: center; border-top: 0.6pt solid var(--soft); padding-top: 4pt; }

/* Chumash: each pasuk, then its Rashi */
.chapter-mark { text-align: center; font-size: 10.5pt; font-weight: 700; color: var(--muted); margin: 2pt 0 6pt; letter-spacing: 0.04em; break-after: avoid; }
.chapter-mark::before, .chapter-mark::after { content: "—"; color: var(--rule); margin: 0 6pt; }
.chumash-he { direction: rtl; columns: 2; column-gap: 20pt; column-rule: 0.5pt solid var(--soft); }
/* One-column Chumash, for those who prefer full-width lines and larger text. */
.chumash-he.one-col { columns: 1; }
.chumash-he.one-col .ptext { font-size: 17pt; line-height: 1.8; }
.chumash-he.one-col .prashi { font-size: 11pt; padding-right: 10pt; }
.pasuk { margin: 0 0 7pt; }
.ptext { font-size: 15pt; line-height: 1.75; margin: 0; text-align: justify; break-inside: avoid; break-after: avoid; }
.ptext .vnum { font-size: 8.5pt; font-weight: 700; color: var(--rule); margin-left: 4pt; vertical-align: 3pt; }
.prashi { font-family: 'Noto Serif Hebrew', serif; font-size: 10pt; line-height: 1.6; text-align: justify; color: #3a352d; margin: 2pt 0 0; padding: 1pt 8pt 1pt 0; border-right: 2pt solid var(--soft); }
.prashi .dh { font-weight: 700; color: var(--ink); }
.ptext-en { font-size: 12pt; line-height: 1.45; margin: 0; text-align: justify; break-inside: avoid; break-after: avoid; }
.ptext-en .vnum { font-size: 8pt; font-weight: 700; color: var(--rule); margin-right: 4pt; vertical-align: 2pt; }
.prashi-en { font-size: 10pt; line-height: 1.4; text-align: justify; color: #3a352d; margin: 2pt 0 0; padding: 1pt 0 1pt 8pt; border-left: 2pt solid var(--soft); }
.prashi-en .rlabel { font-weight: 600; font-size: 8pt; text-transform: uppercase; letter-spacing: 0.06em; color: var(--rule); margin-right: 2pt; }
.prashi-en .dh { font-weight: 600; color: var(--ink); }
.prashi-en .hebrew { font-family: 'Noto Serif Hebrew', serif; }
.crow { display: grid; grid-template-columns: 1fr 1fr; gap: 16pt; padding: 4pt 0 5pt; border-bottom: 0.4pt solid var(--soft); }
.crow .he .ptext { font-size: 14.5pt; break-after: auto; }
.crow .en .ptext-en { font-size: 11.5pt; break-after: auto; }

/* Tanya */
.tanya-text { font-size: 15pt; line-height: 1.85; text-align: justify; }
.tanya-text p { margin: 0 0 7pt; text-indent: 14pt; }

/* Tanya in English: the translation in ink, Lessons in Tanya's explanation in gray */
.tanya-en p { font-size: 12.5pt; line-height: 1.55; text-align: justify; margin: 0 0 8pt; }
.tanya .text-annotation { color: #7d7468; }
.trow { display: grid; grid-template-columns: 1fr 1fr; gap: 16pt; padding: 4pt 0; border-bottom: 0.4pt solid var(--soft); break-inside: avoid; }
.trow .en { font-size: 11pt; line-height: 1.42; text-align: justify; }
.trow .he { font-size: 13.5pt; line-height: 1.7; text-align: justify; }

/* Rambam */
.perek { margin-bottom: 14pt; }
.perek h3 { display: flex; justify-content: space-between; align-items: baseline; font-size: 13pt; margin: 4pt 0 7pt; padding: 3pt 0; border-bottom: 0.6pt solid var(--soft); }
.perek h3 .he { font-size: 14pt; }
.perek p { margin: 0 0 6pt; }
.perek p.he { font-size: 14pt; line-height: 1.75; text-align: justify; }
.perek-en p { text-align: justify; }
.hnum { display: inline-block; min-width: 16pt; font-weight: 700; color: var(--rule); font-size: 0.82em; }
.he .hnum { margin-left: 4pt; }
.hrow .en .hnum, .perek-en .hnum { margin-right: 4pt; }
.hrow { display: grid; grid-template-columns: 1fr 1fr; gap: 16pt; padding: 4pt 0; border-bottom: 0.4pt solid var(--soft); break-inside: avoid; }
.hrow .en { font-size: 11pt; line-height: 1.4; text-align: justify; }
.hrow .he { font-size: 13pt; line-height: 1.7; text-align: justify; }

/* Short daily lessons (Tanya, Hayom Yom) run on instead of starting a page each; after the
   first day the material's name isn't repeated, only the date. */
.compact .day { break-before: auto; margin-bottom: 16pt; }
.day-head, .entry-title { break-after: avoid; }
.compact .day ~ .day .day-head .row:first-child { display: none; }
.compact .day ~ .day .day-head .sub { color: var(--ink); font-size: 12pt; font-weight: 600; }
.compact .day ~ .day .day-head .sub .he { font-size: 13pt; }
.compact .day ~ .day .day-head { border-bottom-width: 0.8pt; margin-bottom: 8pt; }

/* Hayom Yom */
.hy-he p { font-size: 13.5pt; line-height: 1.75; margin: 0 0 6pt; text-align: justify; }
.hy-he .tlabel { font-size: 9.5pt; font-weight: 700; color: var(--rule); margin: 4pt 0 2pt; letter-spacing: 0.05em; }
.hy-he .entry-title { font-style: normal; font-size: 10.5pt; }
.hyrow { display: grid; grid-template-columns: 1fr 1fr; gap: 16pt; }
.hyrow .hy-en p { font-size: 11.5pt; line-height: 1.45; }
.hyrow .hy-he p { font-size: 13pt; }
.hayomyom .entry { font-size: 13pt; line-height: 1.55; }
.hayomyom .entry + .entry { margin-top: 10pt; padding-top: 8pt; border-top: 0.5pt dashed var(--soft); }
.hayomyom .entry-title { font-size: 10.5pt; color: var(--muted); font-style: italic; margin-bottom: 3pt; }
.hayomyom p { margin: 0 0 6pt; text-align: justify; }
"""

def page(material, sections):
    """A printable HTML document for one material's days. material is one of CREDITS' keys."""
    body_class = f"{material}-page" + (" compact" if material in ("tanya", "hayomyom") else "")
    credit = CREDITS[material]
    if material == "tanya" and any("lang-en" in s or "lang-both" in s for s in sections):
        credit += " In the English, the translation is in black and the explanation woven into it is in gray."
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<link rel='stylesheet' href='{FONTS}'><style>{CSS}</style></head>"
            f"<body class='{body_class}'>{''.join(sections)}"
            f"<div class='credit'>{credit}</div></body></html>")
