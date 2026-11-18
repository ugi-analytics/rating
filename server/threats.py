# -*- coding: utf-8 -*-
"""
Розпізнавання попереджень «що куди летить» у дописах Telegram-каналів
(Повітряні сили, ОВА, моніторингові канали).

Допис ділиться на рядки/речення; рядок вважається попередженням, якщо в ньому є
тип загрози (БпЛА, ракета, балістика, КАБ, швидкісна ціль) і місто чи область,
а сам допис не є повідомленням про влучання (ті йдуть у рейтинг).
"""
import re
import time

import rating_core as rc

KINDS = [   # (код, назва, значок, шаблон) — порядок = пріоритет
    ("bal", "балістика", "🚀", r"балістик|балістичн|іскандер|кинджал"),
    ("rkt", "ракети", "🚀", r"ракет|крилат|калібр|\bх-\d+"),
    ("fast", "швидкісна ціль", "⚡", r"швидкісн\w* ціл"),
    ("jet", "реактивний БпЛА", "✈️", r"реактивн\w* (?:бпла|безпілот|шахед|дрон)"),
    ("kab", "КАБи", "💣", r"\bкаб\w*\b|авіабомб|керован\w* (?:авіа)?бомб"),
    ("uav", "БпЛА", "🛵", r"бпла|безпілот|шахед|дрон|мопед|герань|гербер|🛵|🏍"),
]
KIND_RES = [(k, n, ic, re.compile(rx, re.I)) for k, n, ic, rx in KINDS]
MOVE_RE = re.compile(r"курс\w*|напрям|у бік|в бік|рухає|руха|летить|летять|заход\w*|загроз|в районі|"
                     r"над |повз|прямує|на підльоті|підлітає|в сторону|кружля|маневр|➡|→|⬆|⬇|↗|↘|↙|↖", re.I)
CLEAR_RE = re.compile(r"відбій|відбою|загроз\w* (?:минула|немає)|чисто\b", re.I)

REGIONS = {
    "Київщина": r"Київщин|Київськ\w* обл",
    "Полтавщина": r"Полтавщин|Полтавськ\w* обл",
    "Кіровоградщина": r"Кіровоградщин|Кіровоградськ\w* обл",
    "Черкащина": r"Черкащин|Черкаськ\w* обл",
    "Вінниччина": r"Вінниччин|Вінницьк\w* обл",
    "Житомирщина": r"Житомирщин|Житомирськ\w* обл",
    "Хмельниччина": r"Хмельниччин|Хмельницьк\w* обл",
    "Рівненщина": r"Рівненщин|Рівненськ\w* обл",
    "Волинь": r"Волин[ьіюя]|Волинськ\w* обл",
    "Тернопільщина": r"Тернопільщин|Тернопільськ\w* обл",
    "Львівщина": r"Львівщин|Львівськ\w* обл",
    "Прикарпаття": r"Прикарпатт|Івано-Франківщин|Івано-Франківськ\w* обл",
    "Закарпаття": r"Закарпатт|Закарпатськ\w* обл",
    "Буковина": r"Буковин|Чернівецьк\w* обл",
    "Дніпропетровщина": r"Дніпропетровщин|Дніпропетровськ\w* обл|Січеславщин",
    "Одещина": r"Одещин|Одеськ\w* обл",
    "Миколаївщина": r"Миколаївщин|Миколаївськ\w* обл",
    "Харківщина": r"Харківщин|Харківськ\w* обл",
    "Запоріжжя": r"Запорізьк\w* обл|Запоріжжі? області",
}
REGION_RES = {n: re.compile(rx, re.I) for n, rx in REGIONS.items()}
_SPLIT = re.compile(r"\n+|(?<=[.!;])\s+")


def detect(text, ch, city_res, msg_time):
    """Допис -> список загроз [{c, r, k, kn, ic, x}]; порожньо, якщо це не попередження."""
    if not text or len(text) > 3000:
        return []
    if rc.classify(text, city_res):          # це повідомлення про влучання — не попередження
        return []
    ch_reg = rc.CHANNEL_REGION.get(ch.lower())
    out, seen = [], set()
    kind_ctx = None                          # тип, згаданий у попередньому рядку («Шахеди:\nПолтава\nКременчук»)
    for line in _SPLIT.split(text):
        line = line.strip()
        if not line or CLEAR_RE.search(line):
            kind_ctx = None if line else kind_ctx
            continue
        kind = next(((k, n, ic) for k, n, ic, rx in KIND_RES if rx.search(line)), None) or kind_ctx
        if not kind:
            continue
        cities = [c for c, rx in city_res.items() if rx.search(line)]
        regs = [r for r, rx in REGION_RES.items() if rx.search(line)]
        if ch_reg:                           # канал ОВА — лише своя область
            cities = [c for c in cities if rc.CITY_REGION.get(c) == ch_reg]
            regs = [r for r in regs if r == ch_reg]
        short = len(line) < 60               # моніторингові «Полтава — шахед»
        if not (cities or regs):
            kind_ctx = kind
            continue
        if not (MOVE_RE.search(line) or short or kind_ctx):
            continue
        for c in cities:
            key = ("c", c)
            if key not in seen:
                seen.add(key)
                out.append({"c": c, "r": rc.CITY_REGION.get(c, ""), "k": kind[0], "kn": kind[1], "ic": kind[2], "x": line[:220]})
        if not cities:
            for r in regs:
                key = ("r", r)
                if key not in seen:
                    seen.add(key)
                    out.append({"c": "", "r": r, "k": kind[0], "kn": kind[1], "ic": kind[2], "x": line[:220]})
    return out
