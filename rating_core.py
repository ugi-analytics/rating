# -*- coding: utf-8 -*-
"""
Рейтинг тилових міст України за кількістю влучань / вибухів
за повідомленнями Telegram-каналів (ОВА, міських голів, моніторингових).

Що рахує:
  * events  — унікальні пари (місто, дата), коли в будь-якому каналі
              було повідомлення про влучання/вибух/уламки в цьому місті
              (дублікати з різних каналів за ту саму добу = 1 подія);
  * share_% — частка міста в сумі подій усіх тилових міст;
  * msgs    — сирова кількість повідомлень (для контролю).

Запуск:
  pip install telethon pandas openpyxl
  python rating_vluchan_mist.py --from 2026-09-01 --to 2026-11-15
  (перший запуск попросить номер телефону і код з Telegram)

api_id / api_hash: https://my.telegram.org -> API development tools.
"""
import argparse
import re
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- НАЛАШТУВАННЯ
API_ID = 0            # <- ваш api_id
API_HASH = ""         # <- ваш api_hash
GITHUB_TOKEN = ""     # публікація на GitHub Pages (необов'язково)
GITHUB_REPO = "ugi-analytics/rating"
GITHUB_PATH = "index.html"

# Канали для аналізу: @username або посилання t.me/...
# ПЕРЕВІРТЕ та доповніть юзернейми — офіційні канали ОВА й міських голів
# тилових областей. Чим більше джерел, тим повніша картина.
CHANNELS = [
    "kpszsu",          # Повітряні сили ЗСУ
    # "<канал Полтавської ОВА>",
    # "<канал Київської ОВА>",
    # "<канал КМВА>",
    # "<канал Кіровоградської ОВА>",
    # "<канал Черкаської ОВА>",
    # "<канал Вінницької ОВА>",
    # "<канал Житомирської ОВА>",
    # "<канал Хмельницької ОВА>",
    # "<канал Рівненської ОВА>",
    # "<канал Волинської ОВА>",
    # "<канал Львівської ОВА>",
    # "<канал Тернопільської ОВА>",
    # "<канал Івано-Франківської ОВА>",
    # "<канал Закарпатської ОВА>",
    # "<канал Чернівецької ОВА>",
    # "<канал міського голови Кременчука>",
]

# Ознаки влучання/вибуху (корені слів, нижній регістр)
HIT_PATTERNS = [
    r"влучан", r"влучил", r"влучив", r"поцілив", r"приліт", r"прильот",
    r"вибух", r"уламк", r"падінн[ яi] (?:бпла|дрона|ракет)", r"пошкоджен",
    r"руйнуван", r"пожеж", r"загин", r"поранен", r"травмован", r"постраждал",
]
# Повідомлення, які НЕ є фактом влучання (рух цілей, загальні попередження)
NOISE_PATTERNS = [
    r"курсом на", r"курс\w* (?:на|західн|північн|південн|східн)",
    r"у напрямку", r"в напрямку", r"загроза застосування", r"відбій",
    r"повітряна тривога", r"рухаються",
]

# Тилові міста: назва -> регулярний вираз з відмінковими формами.
# \b у Python для кирилиці працює (re.UNICODE за замовчуванням).
CITIES = {
    "Київ":              r"\bКи(?:їв|єв[іау]|євом)\b",
    "Біла Церква":       r"\bБіл(?:а|ій|у|ої|ою) Церкв(?:а|і|у|ою)\b",
    "Бровари":           r"\bБровар(?:и|ах|ам|ами)\b",
    "Бориспіль":         r"\bБорисп(?:іль|олі|оля|олем|олю)\b",
    "Фастів":            r"\bФаст(?:ів|ові|ова|овом|ову)\b",
    "Обухів":            r"\bОбух(?:ів|ові|ова|овом|ову)\b",
    "Вишгород":          r"\bВишгород(?:і|а|ом|у)?\b",
    "Полтава":           r"\bПолтав(?:а|і|и|у|ою)\b",
    "Кременчук":         r"\bКременчу(?:к|ці|ка|ком|ку|кові)\b",
    "Горішні Плавні":    r"\bГоріш(?:ні|ніх|нім|німи) Плав(?:ні|нях|ням|нями)\b",
    "Миргород":          r"\bМиргород(?:і|а|ом|у)?\b",
    "Лубни":             r"\bЛуб(?:ни|нах|нам|нями)\b",
    "Кропивницький":     r"\bКропивницьк(?:ий|ому|ого|им)\b",
    "Олександрія":       r"\bОлександрі(?:я|ї|ю|єю)\b",
    "Знам'янка":         r"\bЗнам['’ʼ]?ян(?:ка|ці|ки|ку|кою)\b",
    "Світловодськ":      r"\bСвітловодськ(?:у|а|ом)?\b",
    "Черкаси":           r"\bЧерка(?:си|сах|сам|сами)\b",
    "Умань":             r"\bУман(?:ь|і|ню)\b",
    "Сміла":             r"\bСміл(?:а|і|у|ою)\b",
    "Вінниця":           r"\bВінниц(?:я|і|ю|ею)\b",
    "Козятин":           r"\bКозятин(?:і|а|ом|у)?\b",
    "Житомир":           r"\bЖитомир(?:і|а|ом|у|ові)?\b",
    "Бердичів":          r"\bБердич(?:ів|еві|ева|евом|еву)\b",
    "Коростень":         r"\bКоростен(?:ь|і|я|ем|ю)\b",
    "Хмельницький":      r"\bХмельницьк(?:ий|ому|ого|им)\b",
    "Старокостянтинів":  r"\bСтарокостянтин(?:ів|ові|ова|овом|ову)\b",
    "Шепетівка":         r"\bШепетів(?:ка|ці|ки|ку|кою)\b",
    "Рівне":             r"\bРівн(?:е|ому|ого|им)\b",
    "Луцьк":             r"\bЛуцьк(?:у|а|ом)?\b",
    "Ковель":            r"\bКовел(?:ь|і|я|ем|ю)\b",
    "Тернопіль":         r"\bТерноп(?:іль|олі|оля|олем|олю)\b",
    "Львів":             r"\bЛьв(?:ів|ові|ова|овом|ову)\b",
    "Стрий":             r"\bСтри(?:й|ю|ї|єм)\b",
    "Дрогобич":          r"\bДрогобич(?:і|а|ем|у)?\b",
    "Івано-Франківськ":  r"\bІвано-Франківськ(?:у|а|ом|ові)?\b",
    "Калуш":             r"\bКалуш(?:і|а|ем|у)?\b",
    "Ужгород":           r"\bУжгород(?:і|а|ом|у)?\b",
    "Мукачево":          r"\bМукачев(?:о|і|а|ом|у)\b",
    "Чернівці":          r"\bЧернівц(?:і|ях|ям|ями)\b",
}
# Міста ближче до фронту — вмикаються ключем --include-border
BORDER_CITIES = {
    "Дніпро":     r"\bДніпр(?:о|і|а|ом|у)\b",
    "Одеса":      r"\bОдес(?:а|і|и|у|ою)\b",
    "Миколаїв":   r"\bМикола(?:їв|єві|єва|євом|єву)\b",
    "Харків":     r"\bХарк(?:ів|ові|ова|овом|ову)\b",
    "Запоріжжя":  r"\bЗапоріжж(?:я|і|ям)\b",
    "Кривий Ріг": r"\bКрив(?:ий|ому|ого|им) Р(?:іг|озі|огу|огом)\b",
}
# ---------------------------------------------------------------------------

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "налаштування.txt"


def load_config():
    """Читає api_id, api_hash і канали з налаштування.txt (якщо файл є)."""
    global API_ID, API_HASH, CHANNELS, GITHUB_TOKEN, GITHUB_REPO, GITHUB_PATH
    if not CONFIG.exists():
        return
    chans, in_ch = [], False
    for line in CONFIG.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("[канали]"):
            in_ch = True
            continue
        if not in_ch and "=" in line:
            k, v = [x.strip() for x in line.split("=", 1)]
            if k.lower() == "api_id" and v.isdigit():
                API_ID = int(v)
            elif k.lower() == "api_hash" and v:
                API_HASH = v.strip('"\'')
            elif k.lower() == "github_token" and v:
                GITHUB_TOKEN = v.strip('"\'')
            elif k.lower() == "github_repo" and v:
                GITHUB_REPO = v.strip('"\'')
            elif k.lower() == "github_path" and v:
                GITHUB_PATH = v.strip('"\'').lstrip("/")
        elif in_ch:
            ch = line.split()[0]
            ch = re.sub(r"^(https?://)?t\.me/", "", ch).lstrip("@").strip("/")
            if ch:
                chans.append(ch)
    if chans:
        CHANNELS = chans

HIT_RE = re.compile("|".join(HIT_PATTERNS), re.I)
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.I)


def compile_cities(include_border: bool):
    d = dict(CITIES)
    if include_border:
        d.update(BORDER_CITIES)
    d.update(EXTRA_CITIES)              # усі інші міста України (cities_all.py)
    tail = r"(?![-\w]*\s+(?:район|област|ОВА|ОДА|МВА))"
    return {name: re.compile("(?:" + rx + ")" + tail) for name, rx in d.items()}


ATTACK_RE = re.compile(
    r"атак|обстріл|удар|бпла|безпілот|дрон|ракет|шахед|влучан|приліт|прильот|вибух|уламк|балістик|авіабомб|\bкаб\b", re.I)
EXCLUDE_RE = re.compile(
    r"прощається|прощання|хвилин\w* мовчання|загинув за україну|загинула за україну|"
    r"загибл\w* (?:захисник|воїн|військов)|на фронті|у бою|під час виконання бойового|"
    r"на щиті|вічная пам|світла пам", re.I)


def classify(text: str, city_res: dict) -> list:
    """Повертає список міст, де повідомлення фіксує влучання/вибух."""
    if not text:
        return []
    t = text.lower()
    if not HIT_RE.search(t) or not ATTACK_RE.search(t):
        return []
    if EXCLUDE_RE.search(t):
        return []
    # повідомлення лише про рух цілей без факту наслідків — відкидаємо
    if NOISE_RE.search(t) and not re.search(r"влучан|пошкоджен|загин|поранен|уламк", t):
        return []
    return [name for name, rx in city_res.items() if rx.search(text)]


# ------------------------------------------------------------ ОБЛАСТІ
# Місто зараховується лише з каналів своєї області — це відсікає тезок-сіл
# в інших областях і загальнонаціональні зведення («атака на Київ, Харків...»).
# Канали, яких тут немає (напр. Повітряні сили), можуть зараховувати будь-яке місто.
CITY_REGION = {
    **dict.fromkeys(["Київ", "Біла Церква", "Бровари", "Бориспіль", "Фастів", "Обухів", "Вишгород"], "Київщина"),
    **dict.fromkeys(["Полтава", "Кременчук", "Горішні Плавні", "Миргород", "Лубни"], "Полтавщина"),
    **dict.fromkeys(["Кропивницький", "Олександрія", "Знам'янка", "Світловодськ"], "Кіровоградщина"),
    **dict.fromkeys(["Черкаси", "Умань", "Сміла"], "Черкащина"),
    **dict.fromkeys(["Вінниця", "Козятин"], "Вінниччина"),
    **dict.fromkeys(["Житомир", "Бердичів", "Коростень"], "Житомирщина"),
    **dict.fromkeys(["Хмельницький", "Старокостянтинів", "Шепетівка"], "Хмельниччина"),
    "Рівне": "Рівненщина",
    **dict.fromkeys(["Луцьк", "Ковель"], "Волинь"),
    "Тернопіль": "Тернопільщина",
    **dict.fromkeys(["Львів", "Стрий", "Дрогобич"], "Львівщина"),
    **dict.fromkeys(["Івано-Франківськ", "Калуш"], "Прикарпаття"),
    **dict.fromkeys(["Ужгород", "Мукачево"], "Закарпаття"),
    "Чернівці": "Буковина",
    **dict.fromkeys(["Дніпро", "Кривий Ріг"], "Дніпропетровщина"),
    "Одеса": "Одещина", "Миколаїв": "Миколаївщина", "Харків": "Харківщина", "Запоріжжя": "Запоріжжя",
}
CHANNEL_REGION = {k.lower(): v for k, v in {
    "vinnytskaODA": "Вінниччина", "s_morgunov": "Вінниччина",
    "volynskaODA": "Волинь", "igopolishchuk": "Волинь",
    "dnipropetrovskaODA": "Дніпропетровщина", "borys_filatovv": "Дніпропетровщина", "vilkul": "Дніпропетровщина",
    "zhytomyrskaODA": "Житомирщина",
    "zakarpatskaODA": "Закарпаття",
    "ivan_fedorov_zp": "Запоріжжя",
    "onyshchuksvitlana": "Прикарпаття", "martsinkiv": "Прикарпаття",
    "kyivoda": "Київщина", "VA_Kyiv": "Київщина", "vitaliy_klitschko": "Київщина",
    "kirovohradskaODA": "Кіровоградщина", "kr_rada": "Кіровоградщина",
    "kozytskyy_maksym_official": "Львівщина", "andriysadovyi": "Львівщина",
    "mykolaivskaODA": "Миколаївщина", "senkevichonline": "Миколаївщина",
    "odeskaODA": "Одещина",
    "poltavskaOVA": "Полтавщина", "kateryna_yamshchykova": "Полтавщина", "vitalii_maletskyi": "Полтавщина",
    "oleksandrkoval_rv": "Рівненщина", "shakyrzian_viktor": "Рівненщина",
    "ternopilskaODA": "Тернопільщина", "Nadal_Sergii": "Тернопільщина",
    "synegubov": "Харківщина", "ihor_terekhov": "Харківщина",
    "khmelnytskaODA": "Хмельниччина", "symchyshyn_oleksandr": "Хмельниччина",
    "cherkaskaODA": "Черкащина", "bondarenko_mayor": "Черкащина",
    "chernivetskaODA": "Буковина", "klichuk_roman": "Буковина",
    "Sumy_news_ODA": "Сумщина", "chernigivskaODA": "Чернігівщина", "olexandrprokudin": "Херсонщина",
    "VadymFilashkin": "Донеччина", "luhanskaVTSA": "Луганщина",
}.items()}


# ------------------------------------------------------------ ЖЕРТВИ (приблизно)
_APOS = "['’ʼ]"
_UNITS = {
    "один": 1, "одна": 1, "одну": 1, "одного": 1, "двоє": 2, "два": 2, "дві": 2,
    "троє": 3, "три": 3, "четверо": 4, "чотири": 4, "п'ятеро": 5, "п'ять": 5,
    "шестеро": 6, "шість": 6, "семеро": 7, "сім": 7, "восьмеро": 8, "вісім": 8,
    "дев'ятеро": 9, "дев'ять": 9, "двох": 2, "трьох": 3, "чотирьох": 4, "п'ятьох": 5,
    "шістьох": 6, "сімох": 7, "вісьмох": 8, "дев'ятьох": 9,
}
_TEENS = {
    "десятеро": 10, "десять": 10, "одинадцятеро": 11, "одинадцять": 11, "дванадцятеро": 12, "дванадцять": 12,
    "тринадцятеро": 13, "тринадцять": 13, "чотирнадцятеро": 14, "чотирнадцять": 14,
    "п'ятнадцятеро": 15, "п'ятнадцять": 15, "шістнадцятеро": 16, "шістнадцять": 16,
    "сімнадцятеро": 17, "сімнадцять": 17, "вісімнадцятеро": 18, "вісімнадцять": 18,
    "дев'ятнадцятеро": 19, "дев'ятнадцять": 19, "двадцятеро": 20, "тридцятеро": 30,
}
_TENS = {"двадцять": 20, "тридцять": 30, "сорок": 40, "п'ятдесят": 50, "шістдесят": 60,
         "сімдесят": 70, "вісімдесят": 80, "дев'яносто": 90}
NUM_WORDS = {**_UNITS, **_TEENS, **_TENS}


def _w(word):
    return r"\b" + re.escape(word).replace("'", _APOS) + r"\b"


def normalize_numbers(t: str) -> str:
    """«тридцять двоє» -> «32», «п'ятеро» -> «5» (текст уже в нижньому регістрі)."""
    for tw, tv in _TENS.items():
        for uw, uv in _UNITS.items():
            t = re.sub(_w(tw) + r"\s+" + _w(uw), str(tv + uv), t)
    for w, v in sorted(NUM_WORDS.items(), key=lambda kv: -len(kv[0])):
        t = re.sub(_w(w), str(v), t)
    return t


_NUM = r"(\d{1,3}(?!\d))"
# не вік: «41 і 60 років», «71-річна»
_NUM += r"(?!(?:\s*(?:і|та|,)\s*\d{1,3})*[\s-]*(?:річ|рок|рік|р\.))"
_WHO = r"(?:(?:цивільн\w*|мирн\w*)\s+)?(?:людин\w*|людей|люди|осіб|особ\w*|мешкан\w*|чоловік\w*|жін\w*|дит\w*|дітей|працівник\w*|енергетик\w*|рятувальник\w*|цивільн\w*)"
KILLED_RES = [
    re.compile(_NUM + r"\s+(?:" + _WHO + r"\s+)?загинул", re.I),
    re.compile(_NUM + r"\s+" + _WHO + r"[^.;]{0,60}?\s(?:загинул|загибл)", re.I),
    re.compile(r"загинул[аио]?\s+(?:щонайменше\s+)?" + _NUM, re.I),
    re.compile(r"загиблих\s+(?:зросла\s+)?до\s+" + _NUM, re.I),
]
KILLED_ONE_RE = re.compile(r"загинул[аи]?\s+(?:одна\s+)?(?:людина|жінка|чоловік|дитина|особа)|є\s+загибл", re.I)
INJ_RES = [
    re.compile(_NUM + r"\s+(?:" + _WHO + r"\s+)?(?:(?:отримал\w*|дістал\w*|зазнал\w*)\s+)?(?:поранен|травм|постраждал)", re.I),
    re.compile(_NUM + r"\s+" + _WHO + r"[^.;]{0,60}?\s(?:поранен|травмован|постраждал)", re.I),
    re.compile(r"(?:ще|також)\s+" + _NUM + r"[^.;]{0,50}?\s(?:поранен|травмован|постраждал)", re.I),
    re.compile(r"(?:поранен|травмован|постраждал)\w*\s+(?:щонайменше\s+|ще\s+)?" + _NUM, re.I),
    re.compile(r"(?:поранених|постраждалих|травмованих)\s+(?:\w+\s+){0,2}до\s+" + _NUM, re.I),
    re.compile(r"(?:поранен\w*|травм\w*)\s+(?:отримал\w*|дістал\w*|зазнал\w*)\s+(?:щонайменше\s+)?" + _NUM, re.I),
]
INJ_ONE_RE = re.compile(r"(?:поранен\w*|травмован\w*|постраждал\w*)\s+(?:одна\s+)?(?:людина|жінка|чоловік|дитина|особа)|є\s+(?:поранен|постраждал)", re.I)


def _to_int(tok):
    tok = tok.lower()
    if tok.isdigit():
        v = int(tok)
        return v if 0 < v <= 300 else 0
    return NUM_WORDS.get(tok, 0)


def casualties(text: str):
    """(загиблі, поранені) з тексту повідомлення — грубо, максимум згаданих чисел."""
    t = normalize_numbers((text or "").lower())
    k = max([_to_int(m.group(1)) for rx in KILLED_RES for m in rx.finditer(t)] or [0])
    if not k and KILLED_ONE_RE.search(t):
        k = 1
    i = max([_to_int(m.group(1)) for rx in INJ_RES for m in rx.finditer(t)] or [0])
    if not i and INJ_ONE_RE.search(t):
        i = 1
    return k, i


_SENT_SPLIT = re.compile(r"(?<=[.!?;\n])\s+")
_AREA_RE = re.compile(r"област|районі|районах|населених пункт|громад|по регіону", re.I)


def casualties_for_city(text, city_rx, n_cities):
    """Жертви, що стосуються саме цього міста: беремо речення, де воно згадане.
    Якщо місто в повідомленні одне і в ньому немає обласних підсумків — увесь текст."""
    sents = [x for x in _SENT_SPLIT.split(text or "") if city_rx.search(x)]
    k, i = casualties(" ".join(sents)) if sents else (0, 0)
    if not (k or i) and n_cities == 1 and not _AREA_RE.search(text or ""):
        k, i = casualties(text)
    return k, i


# ------------------------------------------------------------ КООРДИНАТИ МІСТ
COORDS = {
    "Київ": (50.45, 30.52), "Біла Церква": (49.80, 30.11), "Бровари": (50.51, 30.79),
    "Бориспіль": (50.35, 30.95), "Фастів": (50.08, 29.92), "Обухів": (50.11, 30.62),
    "Вишгород": (50.58, 30.49), "Полтава": (49.59, 34.55), "Кременчук": (49.07, 33.42),
    "Горішні Плавні": (49.01, 33.65), "Миргород": (49.97, 33.61), "Лубни": (50.02, 33.00),
    "Кропивницький": (48.51, 32.26), "Олександрія": (48.67, 33.12), "Знам'янка": (48.72, 32.67),
    "Світловодськ": (49.05, 33.24), "Черкаси": (49.44, 32.06), "Умань": (48.75, 30.22),
    "Сміла": (49.22, 31.87), "Вінниця": (49.23, 28.47), "Козятин": (49.71, 28.84),
    "Житомир": (50.25, 28.66), "Бердичів": (49.90, 28.60), "Коростень": (50.95, 28.64),
    "Хмельницький": (49.42, 26.99), "Старокостянтинів": (49.76, 27.21), "Шепетівка": (50.18, 27.06),
    "Рівне": (50.62, 26.25), "Луцьк": (50.75, 25.33), "Ковель": (51.21, 24.71),
    "Тернопіль": (49.55, 25.59), "Львів": (49.84, 24.03), "Стрий": (49.26, 23.85),
    "Дрогобич": (49.35, 23.51), "Івано-Франківськ": (48.92, 24.71), "Калуш": (49.03, 24.37),
    "Ужгород": (48.62, 22.29), "Мукачево": (48.44, 22.72), "Чернівці": (48.29, 25.94),
    "Дніпро": (48.46, 35.05), "Одеса": (46.48, 30.72), "Миколаїв": (46.98, 32.00),
    "Харків": (49.99, 36.23), "Запоріжжя": (47.84, 35.14), "Кривий Ріг": (47.91, 33.39),
}

# ------------------------------------------------------------ УСІ МІСТА УКРАЇНИ
try:
    import cities_all as _CA
    _ALL = _CA.load()
except Exception:                       # на комп'ютері без data_src — лише основний список
    _CA, _ALL = None, []
EXTRA_CITIES = {}
CITY_POP = {}
RU_NAMES = {}
for _c in _ALL:
    _n = _c["name"]
    CITY_POP[_n] = _c["pop"]
    if _c["ru"] and _c["ru"] != _n:
        RU_NAMES[_n] = _c["ru"]
    if _n not in CITIES and _n not in BORDER_CITIES:
        EXTRA_CITIES[_n] = _CA.city_regex(_n)
    CITY_REGION.setdefault(_n, _c["region"])
    COORDS.setdefault(_n, (_c["lat"], _c["lon"]))
AMBIGUOUS = set(_CA.AMBIGUOUS) if _CA else set()


# ------------------------------------------------------------ АГРЕГАЦІЯ
def aggregate(raw):
    """raw: список dict(дата, канал, місто, текст, id, загиблі, поранені).
    Повертає (рейтинг DataFrame, тижні DataFrame, dict для HTML)."""
    import pandas as pd
    events, msgs, chans = defaultdict(set), defaultdict(int), defaultdict(set)
    kd, idd = defaultdict(int), defaultdict(int)   # max за (місто, доба)
    for r in raw:
        c, day = r["місто"], r["дата"]
        events[c].add(day)
        msgs[c] += 1
        chans[c].add(r["канал"])
        kd[(c, day)] = max(kd[(c, day)], r.get("загиблі", 0))
        idd[(c, day)] = max(idd[(c, day)], r.get("поранені", 0))
    killed, injured = defaultdict(int), defaultdict(int)
    for (c, _), v in kd.items():
        killed[c] += v
    for (c, _), v in idd.items():
        injured[c] += v

    df = pd.DataFrame({
        "місто": list(events),
        "події (місто×доба)": [len(events[c]) for c in events],
        "повідомлень": [msgs[c] for c in events],
        "джерел": [len(chans[c]) for c in events],
        "загиблі (≈)": [killed[c] for c in events],
        "поранені (≈)": [injured[c] for c in events],
    })
    weekly = pd.DataFrame()
    data = {"cities": [], "weeks": [], "weekly": {}, "log": []}
    if df.empty:
        return df, weekly, data
    total = df["події (місто×доба)"].sum()
    df["частка_%"] = (df["події (місто×доба)"] / total * 100).round(1)
    df = df.sort_values(["події (місто×доба)", "повідомлень"], ascending=False).reset_index(drop=True)
    df.insert(0, "№", range(1, len(df) + 1))

    # тижні (понеділок)
    wk = defaultdict(lambda: defaultdict(int))
    all_weeks = set()
    for c, days in events.items():
        for d in days:
            w = d - timedelta(days=d.weekday())
            wk[c][w] += 1
            all_weeks.add(w)
    if all_weeks:
        w0, w1 = min(all_weeks), max(all_weeks)
        weeks = [w0 + timedelta(weeks=i) for i in range((w1 - w0).days // 7 + 1)]
    else:
        weeks = []
    weekly = pd.DataFrame({c: [wk[c].get(w, 0) for w in weeks] for c in df["місто"]},
                          index=[w.isoformat() for w in weeks])
    weekly.index.name = "тиждень з"

    for _, row in df.iterrows():
        c = row["місто"]
        lat, lon = COORDS.get(c, (None, None))
        data["cities"].append({
            "name": c, "events": int(row["події (місто×доба)"]), "share": float(row["частка_%"]),
            "msgs": int(row["повідомлень"]), "sources": int(row["джерел"]),
            "killed": int(row["загиблі (≈)"]), "injured": int(row["поранені (≈)"]),
            "lat": lat, "lon": lon, "border": c in BORDER_CITIES,
        })
    data["weeks"] = [w.isoformat() for w in weeks]
    data["weekly"] = {c: [wk[c].get(w, 0) for w in weeks] for c in df["місто"]}
    for r in sorted(raw, key=lambda x: (x["дата"], x["id"]), reverse=True)[:3000]:
        data["log"].append({"d": r["дата"].isoformat(), "c": r["місто"], "ch": r["канал"],
                            "t": r["текст"][:400], "id": r["id"],
                            "k": r.get("загиблі", 0), "i": r.get("поранені", 0)})
    return df, weekly, data


def static_html(data):
    """Проста таблиця без скриптів — видно навіть у переглядачах месенджерів."""
    from html import escape
    m = data.get("meta", {})
    cs = data.get("cities", [])
    fd = lambda x: "{2}.{1}.{0}".format(*x.split("-")) if x else ""
    total = sum(c["events"] for c in cs)
    kill = sum(c["killed"] for c in cs)
    inj = sum(c["injured"] for c in cs)
    rows = "".join(
        f"<tr><td class='num' style='text-align:left'>{i}</td><td>{escape(c['name'])}{' *' if c.get('border') else ''}</td>"
        f"<td class='num'>{c['events']}</td><td class='num'>{c['share']:.1f}%</td>"
        f"<td class='num'>{c['killed']}</td><td class='num'>{c['injured']}</td></tr>"
        for i, c in enumerate(cs, 1))
    star = ("<p class='muted' style='font-size:12px'>Заг.*, Пор.* — загиблі та поранені, орієнтовна оцінка з текстів повідомлень."
            + (" Позначка * біля назви — місто ближче до фронту." if any(c.get("border") for c in cs) else "") + "</p>")
    return (
        "<div class='note'>Це спрощений вигляд. Щоб побачити карту й графіки, відкрийте файл у браузері "
        "(Chrome, Safari, Edge), а не у вбудованому переглядачі месенджера.</div>"
        f"<p><b>Період:</b> {fd(m.get('from',''))} – {fd(m.get('to',''))} · <b>подій:</b> {total} · "
        f"<b>міст:</b> {len(cs)} · <b>загиблі (≈):</b> {kill} · <b>поранені (≈):</b> {inj}</p>"
        "<div style='overflow-x:auto'><table><thead><tr><th>№</th><th>Місто</th><th class='num'>Подій</th><th class='num'>Частка</th>"
        "<th class='num'>Заг.*</th><th class='num'>Пор.*</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>{star}"
    )


GEO_URL = "https://cdn.amcharts.com/lib/5/geodata/json/ukraineLow.json"   # контури областей (amCharts)
_GEO_CACHE = None


def _rdp(pts, eps):
    """Спрощення ламаної (Douglas–Peucker)."""
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = (dx * dx + dy * dy) ** 0.5
    md, mi = 0.0, 0
    for i in range(1, len(pts) - 1):
        p = pts[i]
        d = (((p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2) ** 0.5 if L < 1e-9
             else abs(dy * p[0] - dx * p[1] + b[0] * a[1] - b[1] * a[0]) / L)
        if d > md:
            md, mi = d, i
    if md > eps:
        return _rdp(pts[:mi + 1], eps)[:-1] + _rdp(pts[mi:], eps)
    return [a, b]


def load_geo():
    """Контури областей України (разом із Кримом): кеш ukraine_geo.json або одноразове завантаження.
    Формат: список полігонів [[lon, lat], ...]."""
    global _GEO_CACHE
    import json
    if _GEO_CACHE is not None:
        return _GEO_CACHE
    f = BASE / "ukraine_geo.json"
    try:
        if f.exists():
            cached = f.read_text(encoding="utf-8")
            if cached.strip().startswith("[["):
                _GEO_CACHE = cached
                return _GEO_CACHE
        import urllib.request
        req = urllib.request.Request(GEO_URL, headers={"User-Agent": "Mozilla/5.0"})
        geo = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8"))
        polys = []
        for feat in geo.get("features", []):
            gm = feat.get("geometry") or {}
            parts = [gm.get("coordinates", [])] if gm.get("type") == "Polygon" else gm.get("coordinates", [])
            for poly in parts:
                if not poly:
                    continue
                r = [[round(x, 2), round(y, 2)] for x, y in _rdp([list(map(float, q[:2])) for q in poly[0]], 0.03)]
                if len(r) > 3:
                    polys.append(r)
        if len(polys) < 20:
            raise ValueError("замало контурів")
        _GEO_CACHE = json.dumps(polys, separators=(",", ":"))
        f.write_text(_GEO_CACHE, encoding="utf-8")
        print(f"Контури областей завантажено ({len(polys)}).")
    except Exception as e:
        print(f"[i] Контури областей не завантажено ({type(e).__name__}: {e}) — карти будуть без них.")
        _GEO_CACHE = "null"
    return _GEO_CACHE


def write_html(data, out_path):
    import json
    tpl = (BASE / "report_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (tpl.replace("/*__DATA__*/null", payload)
               .replace("/*__GEO__*/null", load_geo().replace("</", "<\\/"))
               .replace("<!--__STATIC__-->", static_html(data)))
    out_path.write_text(html, encoding="utf-8")


def find_browser():
    """Edge (є в кожному Windows) або Chrome — для збереження PDF/PNG."""
    import os
    import shutil
    cands = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    for n in ("msedge", "chrome", "google-chrome", "chromium"):
        w = shutil.which(n)
        if w:
            return w
    return None


def export_pdf_png(data, pdf_path, png_path):
    """Зберігає звіт у PDF (повний, A4) і PNG (картка: підсумки, топ-15, карта)."""
    import copy
    import subprocess
    import tempfile
    br = find_browser()
    if not br:
        print("[!] Не знайдено Edge/Chrome — PDF і PNG не створено.")
        return
    tmpdir = Path(tempfile.mkdtemp(prefix="rating_"))
    base = [br, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=20000", f"--user-data-dir={tmpdir / 'profile'}"]
    import os
    if os.name != "nt":
        base.append("--no-sandbox")
    jobs = []
    for mode in ("print", "card"):
        d = copy.deepcopy(data)
        d.setdefault("meta", {})["mode"] = mode
        d["log"] = []                    # журнал у PDF/PNG не потрібен
        h = tmpdir / f"{mode}.html"
        write_html(d, h)
        jobs.append((mode, h))
    for mode, h in jobs:
        url = h.resolve().as_uri()
        if mode == "print":
            cmd = base + [f"--print-to-pdf={pdf_path}", "--no-pdf-header-footer", "--print-to-pdf-no-header", url]
        else:
            cmd = base + [f"--screenshot={png_path}", "--window-size=1320,930",
                          "--force-device-scale-factor=1.5", url]
        try:
            subprocess.run(cmd, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[!] {mode}: {e}")
    for f, name in ((pdf_path, "PDF"), (png_path, "PNG")):
        print(f"{name}: {f}" if Path(f).exists() else f"[!] {name} не створено")


def publish_github(html_path):
    """Завантажує звіт на GitHub (GitHub Pages) через API — git не потрібен."""
    import base64
    import json
    import urllib.error
    import urllib.request
    if not GITHUB_TOKEN:
        return
    path = GITHUB_PATH
    if path.lower() in ("index.html", ""):
        # з 20.11.2026 головна сторінка сайту — новий застосунок; звіт з комп'ютера публікуємо окремо, щоб його не затерти
        path = "old/index.html"
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    hdr = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json",
           "User-Agent": "rating-vluchan"}
    try:
        sha = None
        try:
            with urllib.request.urlopen(urllib.request.Request(api, headers=hdr), timeout=30) as r:
                sha = json.load(r).get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        body = {"message": f"Рейтинг влучань: оновлення {datetime.now(KYIV):%d.%m.%Y %H:%M}",
                "content": base64.b64encode(Path(html_path).read_bytes()).decode()}
        if sha:
            body["sha"] = sha
        req = urllib.request.Request(api, data=json.dumps(body).encode(), headers=hdr, method="PUT")
        urllib.request.urlopen(req, timeout=60).read()
        owner, repo = GITHUB_REPO.split("/")
        folder = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
        print(f"Опубліковано: https://{owner.lower()}.github.io/{repo}/{folder}  (оновиться за 1-2 хв)")
    except urllib.error.HTTPError as e:
        msg = {401: "токен недійсний або прострочений", 403: "токену бракує дозволу Contents: Read and write",
               404: "репозиторій не знайдено або токен не має до нього доступу"}.get(e.code, str(e))
        print(f"[!] Публікація на GitHub не вдалася: {msg}")
    except Exception as e:
        print(f"[!] Публікація на GitHub не вдалася: {type(e).__name__}: {e}")


# ------------------------------------------------------------ ЗБІР З TELEGRAM
async def qr_login(client):
    """Вхід через QR-код (Telegram на телефоні -> Налаштування -> Пристрої -> Підключити пристрій)."""
    import asyncio
    import getpass
    import os
    from telethon.errors import SessionPasswordNeededError
    qr = await client.qr_login()
    png = BASE / "qr_вхід.png"
    print("\n=== ВХІД ЧЕРЕЗ QR-КОД ===")
    print("Telegram на телефоні -> Налаштування -> Пристрої -> «Підключити пристрій»")
    print("і відскануйте QR-код, що відкриється у вікні.\n")
    for attempt in range(6):
        try:
            import qrcode
            qrcode.make(qr.url).save(png)
            try:
                os.startfile(str(png))
            except Exception:
                print(f"Відкрийте файл вручну: {png}")
        except ImportError:
            print("Бібліотека qrcode не встановлена. Посилання для QR:", qr.url)
        try:
            await qr.wait(timeout=55)
            print("Вхід виконано.")
            break
        except asyncio.TimeoutError:
            print("QR-код застарів, створюю новий...")
            await qr.recreate()
        except SessionPasswordNeededError:
            pw = getpass.getpass("Увімкнено двоетапну перевірку. Введіть хмарний пароль (символи не відображаються): ")
            await client.sign_in(password=pw)
            print("Вхід виконано.")
            break
    else:
        raise RuntimeError("Не вдалося увійти через QR-код за 5 хвилин.")
    try:
        png.unlink()
    except Exception:
        pass


async def collect(channels, d_from, d_to, city_res):
    from telethon import TelegramClient
    raw = []
    client = TelegramClient(str(BASE / "rating_session"), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        await qr_login(client)
    async with client:
        for ch in channels:
            print(f"-> {ch} ...", flush=True)
            n = 0
            try:
                async for m in client.iter_messages(ch, offset_date=d_to):
                    if m.date < d_from:
                        break
                    text = m.message or ""
                    cities = classify(text, city_res)
                    if cities:
                        day = m.date.astimezone(KYIV).date()
                        reg = CHANNEL_REGION.get(ch.lower())
                        if reg:
                            cities = [c for c in cities if CITY_REGION.get(c) == reg]
                        for c in cities:
                            k, i = casualties_for_city(text, city_res[c], len(cities))
                            raw.append({"дата": day, "канал": ch, "місто": c,
                                        "текст": text[:1500], "id": m.id,
                                        "загиблі": k, "поранені": i})
                    n += 1
            except Exception as e:
                print(f"   ! канал пропущено ({type(e).__name__}: {e})")
                continue
            print(f"   переглянуто {n} повідомлень")
    return raw


KYIV = timezone(timedelta(hours=2))


def rebuild(a):
    """Перебудова звіту з уже зібраних повідомлень (аркуш «Повідомлення (перевірка)»)."""
    import pandas as pd
    src = Path(a.out)
    if not src.exists():
        print(f"[!] Немає файлу {src}. Спершу запустіть повний збір.")
        sys.exit(1)
    x = pd.read_excel(src, sheet_name="Повідомлення (перевірка)")
    raw = []
    for _, r in x.iterrows():
        raw.append({"дата": pd.Timestamp(r["дата"]).date(), "канал": str(r["канал"]), "місто": str(r["місто"]),
                    "текст": str(r["текст"]), "id": int(r["id"]),
                    "загиблі": int(r.get("загиблі", 0) or 0), "поранені": int(r.get("поранені", 0) or 0)})
    rating, weekly, data = aggregate(raw)
    border = any(c in BORDER_CITIES for c in rating["місто"]) if not rating.empty else False
    days = [r["дата"] for r in raw]
    prev = {}
    try:  # період беремо з попереднього звіту, якщо він є
        import json
        h = (BASE / "Звіт_рейтинг_влучань.html").read_text(encoding="utf-8")
        prev = json.loads(re.search(r"const DATA = (\{.*?\});\n", h, re.S).group(1)).get("meta", {})
    except Exception:
        pass
    data["meta"] = {
        "from": a.d_from or prev.get("from") or (min(days).isoformat() if days else ""),
        "to": a.d_to or prev.get("to") or (max(days).isoformat() if days else ""),
        "channels": len(CHANNELS), "border": bool(border),
        "generated": datetime.now(KYIV).strftime("%d.%m.%Y %H:%M"),
    }
    html_path = BASE / "Звіт_рейтинг_влучань.html"
    write_html(data, html_path)
    print(f"HTML-звіт: {html_path}")
    publish_github(html_path)
    print("Створюю PDF і PNG для месенджерів...")
    export_pdf_png(data, BASE / "Рейтинг_влучань.pdf", BASE / "Рейтинг_влучань.png")


def main():
    import asyncio
    import pandas as pd
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d_from", help="YYYY-MM-DD")
    ap.add_argument("--to", dest="d_to", help="YYYY-MM-DD (включно)")
    ap.add_argument("--rebuild", action="store_true",
                    help="не звертатися до Telegram: перебудувати звіт, PDF і PNG з даних у rating_vluchan.xlsx")
    ap.add_argument("--include-border", action="store_true",
                    help="додати міста ближче до фронту: Дніпро, Одеса, Миколаїв, Харків, Запоріжжя, Кривий Ріг")
    ap.add_argument("--out", default=str(BASE / "rating_vluchan.xlsx"))
    a = ap.parse_args()

    load_config()
    if a.rebuild:
        return rebuild(a)
    if not a.d_from or not a.d_to:
        ap.error("потрібні --from і --to")
    if not API_ID or not API_HASH:
        print("\n[!] Не заповнено api_id / api_hash у файлі налаштування.txt")
        print("    Отримайте їх на https://my.telegram.org -> API development tools")
        sys.exit(1)
    print(f"Каналів: {len(CHANNELS)} | період {a.d_from} .. {a.d_to}")

    d_from = datetime.fromisoformat(a.d_from).replace(tzinfo=KYIV)
    d_to = (datetime.fromisoformat(a.d_to) + timedelta(days=1)).replace(tzinfo=KYIV)
    city_res = compile_cities(a.include_border)

    raw = asyncio.run(collect(CHANNELS, d_from, d_to, city_res))
    rating, weekly, data = aggregate(raw)
    print("\n", rating.to_string(index=False) if not rating.empty else "Подій не знайдено.")

    with pd.ExcelWriter(a.out) as xw:
        rating.to_excel(xw, sheet_name="Рейтинг", index=False)
        weekly.to_excel(xw, sheet_name="По тижнях")
        pd.DataFrame(raw).to_excel(xw, sheet_name="Повідомлення (перевірка)", index=False)
    print(f"\nExcel: {a.out}")

    data["meta"] = {
        "from": a.d_from, "to": a.d_to, "channels": len(CHANNELS),
        "border": bool(a.include_border),
        "generated": datetime.now(KYIV).strftime("%d.%m.%Y %H:%M"),
    }
    html_path = BASE / "Звіт_рейтинг_влучань.html"
    write_html(data, html_path)
    print(f"HTML-звіт: {html_path}")
    publish_github(html_path)
    if not rating.empty:
        print("Створюю PDF і PNG для месенджерів...")
        export_pdf_png(data, BASE / "Рейтинг_влучань.pdf", BASE / "Рейтинг_влучань.png")


if __name__ == "__main__":
    main()
