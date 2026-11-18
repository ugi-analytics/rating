# -*- coding: utf-8 -*-
"""
Усі міста України (Вікідані: «місто України», ідентифікатор КАТОТТГ) — для рейтингу й попереджень.
Дані: data_src/cities_ua.txt  «назва|код області|широта|довгота|населення|назва російською».
Для кожної назви автоматично будується шаблон з усіма відмінками («Ніжин» → «у Ніжині», «Ніжина»…).
"""
import re
from pathlib import Path

REGION_BY_CODE = {
    "01": "Крим", "85": "Крим", "05": "Вінниччина", "07": "Волинь", "12": "Дніпропетровщина", "14": "Донеччина",
    "18": "Житомирщина", "21": "Закарпаття", "23": "Запоріжжя", "26": "Прикарпаття", "32": "Київщина", "80": "Київщина",
    "35": "Кіровоградщина", "44": "Луганщина", "46": "Львівщина", "48": "Миколаївщина", "51": "Одещина",
    "53": "Полтавщина", "56": "Рівненщина", "59": "Сумщина", "61": "Тернопільщина", "63": "Харківщина",
    "65": "Херсонщина", "68": "Хмельниччина", "71": "Черкащина", "73": "Буковина", "74": "Чернігівщина",
}

# назви, що збігаються зі звичайними словами: для попереджень беремо їх лише з каналів своєї області
AMBIGUOUS = {"Бар", "Валки", "Броди", "Українка", "Долина", "Городок", "Городище", "Покров", "Сарни", "Щастя",
             "Золоте", "Південне", "Привілля", "Підгороднє", "Лиман", "Залізне", "Гірник", "Кам'янка", "Вишневе",
             "Самар", "Ворожба", "Рудки", "Пологи", "Глиняни", "Миколаївка", "Калинівка", "Березівка", "Соснівка",
             "Гірське", "Мена", "Остер", "Кипуче", "Алмазна", "Турка", "Сколе"}

APO = "['’ʼ]?"
ADJ_M = [("ський", "ськ(?:ий|ого|ому|им|ім)"), ("цький", "цьк(?:ий|ого|ому|им|ім)"), ("зький", "зьк(?:ий|ого|ому|им|ім)"),
         ("ий", "(?:ий|ого|ому|им|ім)"), ("ій", "(?:ій|ього|ьому|ім)")]
ADJ_F = [("ська", "ськ(?:а|ої|ій|у|ою)"), ("цька", "цьк(?:а|ої|ій|у|ою)")]
ADJ_PL = [("ні", "н(?:і|іх|ім|іми)"), ("ті", "т(?:і|их|им|ими)"), ("кі", "к(?:і|их|им|ими)")]


def _word(w, pos, n):
    """Шаблон одного слова з відмінковими закінченнями."""
    w = w.replace("'", "'")
    def fin(stem, ends):
        return re.escape(stem).replace("'", APO).replace("\\'", APO) + ends
    # прикметники: «Кривий», «Біла», «Жовті», «Дністровський»
    for e, rx in ADJ_M:
        if w.endswith(e) and (n > 1):
            return fin(w[: -len(e)], rx)
    for e, rx in ADJ_F:
        if w.endswith(e):
            return fin(w[: -len(e)], rx)
    if n > 1 and pos == 0 and w in ("Біла", "Мала", "Нова", "Гола", "Стара", "Велика", "Судова"):
        return fin(w[:-1], "(?:а|ої|ій|у|ою)")
    if n > 1 and pos == 0:
        for e, rx in ADJ_PL:
            if w.endswith(e):
                return fin(w[: -len(e)], rx)
    # іменники
    if w.endswith(("ськ", "цьк", "зьк")):
        return fin(w, "(?:а|у|ом|ові)?")
    if w.endswith("їв"):
        return fin(w[:-2], "(?:їв|єва|єву|єві|євом)")
    if w.endswith("ів"):
        return fin(w[:-2], "(?:ів|ова|ову|ові|овом)")
    if w.endswith("іль"):
        return fin(w[:-3], "(?:іль|оля|олю|олі|олем)")
    if w.endswith("ець"):
        return fin(w[:-3], "(?:ець|ця|цю|ці|цем|цеві)")
    if w.endswith("ень"):
        return fin(w[:-3], "(?:ень|ня|ню|ні|нем)")
    if w.endswith("ь"):
        return fin(w[:-1], "(?:ь|я|ю|і|ем|ню)")
    if w.endswith("ія"):
        return fin(w[:-2], "(?:ія|ії|ію|ією)")
    if w.endswith("ка"):
        return fin(w[:-2], "(?:ка|ки|ці|ку|кою)")
    if w.endswith("га"):
        return fin(w[:-2], "(?:га|ги|зі|гу|гою)")
    if w.endswith("ця"):
        return fin(w[:-2], "(?:ця|ці|цю|цею)")
    if w.endswith("я"):
        return fin(w[:-1], "(?:я|і|ю|ею|ї)")
    if w.endswith("а"):
        return fin(w[:-1], "(?:а|и|і|у|ою|ої|ій)")
    if w.endswith(("ове", "еве", "єве", "не", "ке", "ле", "ре", "те", "де", "зе", "се", "же", "че")):
        return fin(w[:-1], "(?:е|ого|ому|им|ім)")
    if w.endswith("є"):
        return fin(w[:-1], "(?:є|ього|ьому|ім)")
    if w.endswith("о"):
        return fin(w[:-1], "(?:о|а|у|ом|і|ові)")
    if w.endswith(("ки", "ги", "хи")):
        return fin(w[:-1], "(?:и|ах|ам|ами)?")
    if w.endswith("и"):
        return fin(w[:-1], "(?:и|ах|ам|ами)?")
    if w.endswith("ці"):
        return fin(w[:-1], "(?:і|ів|ях|ям|ями)")
    if w.endswith("і"):
        return fin(w[:-1], "(?:і|ів|ях|ям|ах|ам)")
    if w.endswith("ір"):
        return fin(w[:-2], "(?:ір|ора|ору|орі|ором)")
    if w.endswith("ід"):
        return fin(w[:-2], "(?:ід|ода|оду|оді|одом)")
    if w.endswith(("й",)):
        return fin(w[:-1], "(?:й|ю|я|ї|єм)")
    return fin(w, "(?:а|у|і|ом|ові|е)?")


def city_regex(name):
    words = re.split(r"(\s+|-)", name)
    parts = [p for p in words if p.strip() and p != "-"]
    out, k = [], 0
    for p in words:
        if p == "-":
            out.append("-")
        elif not p.strip():
            out.append(r"\s+")
        else:
            out.append(_word(p, k, len(parts)))
            k += 1
    return r"\b" + "".join(out) + r"\b"


def ru_regex(ru):
    """Наближений шаблон російської назви (для моніторингових каналів)."""
    words = ru.split()
    rx = []
    for w in words:
        stem = re.sub(r"(ый|ий|ой|ая|ое|ые|ье|ь|а|я|о|е|ы|и|й)$", "", w)
        rx.append(re.escape(stem) + r"\w{0,3}")
    return r"\b" + r"\s+".join(rx) + r"\b"


def load(path=None):
    p = Path(path) if path else Path(__file__).with_name("data_src") / "cities_ua.txt"
    rows = []
    if not p.exists():
        return rows
    seen = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        f = line.split("|")
        if len(f) < 6 or not f[1]:
            continue
        name, code, lat, lon, pop, ru = f[0], f[1], float(f[2]), float(f[3]), int(f[4] or 0), f[5]
        if name in seen and seen[name]["pop"] >= pop:     # однойменні міста — лишаємо більше
            continue
        seen[name] = {"name": name, "region": REGION_BY_CODE.get(code, ""), "lat": lat, "lon": lon, "pop": pop, "ru": ru}
    return list(seen.values())
