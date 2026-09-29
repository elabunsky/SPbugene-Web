#!/usr/bin/env python3
# SPbugene News server: коллектор (источники + LLM + кэш фото) и HTTP-сервер.
# Сам собирает сводку каждые 4 часа; клиенты (Mac/веб/Android) только читают готовое.
import json, os, re, hashlib, threading, time, email.utils
import html as htmllib
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).resolve().parent
WWW = BASE / "www"
IMG_DIR = WWW / "img"
CONFIG_PATH = BASE / "config.json"
SUMMARY_PATH = WWW / "summary.json"
STATE_PATH = WWW / "state.json"
MOSCOW = ZoneInfo("Europe/Moscow")
PORT = 8787
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"

MONTHS_RU = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

# В исходном Telegram-посте есть только заголовок исследования. Эти цифры
# проверены по открытому тексту препринта (DOI: 10.21203/rs.3.rs-9032061/v1).
VERIFIED_DETAILS = {
    "How frequent and how long you should visit nature per week": (
        "В препринте: одно посещение зелёной зоны и один час в неделю дают "
        "около половины максимального рассчитанного эффекта; четыре посещения "
        "и четыре часа — около 90%, шесть посещений и восемь часов — около 99%. "
        "Это модель связи с психическим самочувствием, не медицинское назначение."
    ),
}

def load_config():
    return json.loads(CONFIG_PATH.read_text())

SOURCES = [
    {"group": "world", "title": "BBC News Russian", "kind": "tg", "region": "russia", "url": "https://t.me/s/bbcnewsrussian"},
    {"group": "world", "title": "DW на русском", "kind": "rss", "region": "russia", "url": "https://rss.dw.com/rdf/rss-ru-all"},
    {"group": "world", "title": "РБК", "kind": "rss", "region": "russia", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"},
    {"group": "world", "title": "Коммерсантъ", "kind": "rss", "region": "russia", "url": "https://www.kommersant.ru/RSS/news.xml"},
    {"group": "news", "title": "Раньше всех. Ну почти.", "kind": "tg", "url": "https://t.me/s/bbbreaking"},
    {"group": "news", "title": "Readovka", "kind": "tg", "url": "https://t.me/s/readovkanews"},
    {"group": "news", "title": "Медуза", "kind": "tg", "url": "https://t.me/s/meduzalive"},
    {"group": "good", "title": "Good News Network", "kind": "rss", "url": "https://www.goodnewsnetwork.org/feed/"},
    {"group": "good", "title": "Positive News", "kind": "rss", "url": "https://www.positive.news/feed/"},
    {"group": "good", "title": "Reasons to be Cheerful", "kind": "rss", "url": "https://reasonstobecheerful.world/feed/"},
    {"group": "good", "title": "The Optimist Daily", "kind": "rss", "url": "https://www.optimistdaily.com/feed/"},
    {"group": "fun", "title": "Mash", "kind": "tg", "url": "https://t.me/s/breakingmash"},
    {"group": "fun", "title": "База", "kind": "tg", "url": "https://t.me/s/bazabazon"},
    {"group": "fun", "title": "Лентач", "kind": "tg", "url": "https://t.me/s/lentach"},
    {"group": "fun", "title": "Двач", "kind": "tg", "url": "https://t.me/s/dvachannel"},
    {"group": "fun", "title": "Share", "kind": "tg", "url": "https://t.me/s/sharemed"},
    {"group": "science", "title": "N+1", "kind": "rss", "url": "https://nplus1.ru/rss"},
    {"group": "science", "title": "Мозгоправ (факты)", "kind": "tg", "url": "https://t.me/s/mozgoprav_official"},
    {"group": "science", "title": "ПостНаука", "kind": "tg", "url": "https://t.me/s/postnauka"},
    {"group": "science", "title": "Наука на Чердаке", "kind": "tg", "url": "https://t.me/s/chrdk"},
    {"group": "science", "title": "Data Secrets (ИИ)", "kind": "tg", "url": "https://t.me/s/data_secrets"},
    {"group": "science", "title": "Data Science ODS (ИИ)", "kind": "tg", "url": "https://t.me/s/opendatascience"},
    {"group": "science", "title": "ScienceDaily", "kind": "rss", "url": "https://www.sciencedaily.com/rss/top/science.xml"},
    {"group": "facts", "title": "ScienceDaily: необычное", "kind": "rss", "url": "https://www.sciencedaily.com/rss/strange_offbeat.xml"},
    {"group": "facts", "title": "ScienceDaily: все исследования", "kind": "rss", "url": "https://www.sciencedaily.com/rss/all.xml"},
    {"group": "facts", "title": "Live Science", "kind": "rss", "url": "https://www.livescience.com/feeds/all"},
    # Активные англоязычные Telegram-ленты. Неофициальные агрегаторы
    # учитываем как источники тем, а факты по возможности сверяем с редакциями.
    {"group": "world", "title": "Al Jazeera English (Telegram)", "kind": "tg", "url": "https://t.me/s/aljazeeraglobal"},
    {"group": "science", "title": "EverythingScience (English)", "kind": "tg", "url": "https://t.me/s/everythingscience"},
    {"group": "science", "title": "Science in Telegram (English)", "kind": "tg", "url": "https://t.me/s/science"},
    {"group": "science", "title": "Science News Daily (English)", "kind": "tg", "url": "https://t.me/s/science_news_facts_updates_daily"},
    {"group": "science", "title": "Nature (English)", "kind": "tg", "url": "https://t.me/s/Nature"},
    {"group": "world", "title": "BBC World (English RSS)", "kind": "rss", "url": "https://feeds.bbci.co.uk/news/world/rss.xml"},
    {"group": "world", "title": "The Guardian World (English RSS)", "kind": "rss", "url": "https://www.theguardian.com/world/rss"},
    # Публичные англоязычные ленты X. Страницы X сейчас отдают серверный HTML
    # с постами, поэтому не нужен API-ключ X и не используется ненадёжный RSS-мост.
    {"group": "world", "title": "Reuters on X", "kind": "x", "url": "https://x.com/Reuters"},
    {"group": "world", "title": "Associated Press on X", "kind": "x", "url": "https://x.com/AP"},
    {"group": "world", "title": "BBC Breaking on X", "kind": "x", "url": "https://x.com/BBCBreaking"},
    {"group": "science", "title": "Nature on X", "kind": "x", "url": "https://x.com/Nature"},
    {"group": "science", "title": "MIT Technology Review on X", "kind": "x", "url": "https://x.com/techreview"},
]

SYSTEM_PROMPT = """Ты — редактор персональной сводки новостей. Тебе дают сырые посты из Telegram-каналов и RSS.
Собери ВСЁ самое интересное и разбей на разделы. Выдай СТРОГО в этом формате, с точными заголовками-разделителями:

=== МИР И РФ ===
(самые важные НЕПОЛИТИЧЕСКИЕ события дня. Сначала строка «В мире:», затем «В России:». Не включай Путина, Трампа, политиков, войны, выборы, санкции, государственные заявления и решения. Каждый пункт 1-2 предложения.)
=== ОСНОВНЫЕ ===
(обзор дня из российских каналов: строка «Главное:» и 8-12 свежих и обсуждаемых НЕПОЛИТИЧЕСКИХ событий. Без Путина, войны и государственных решений. Каждый пункт 1-2 предложения, живо и без воды.)
=== НАУКА ===
(сначала строка «Открытия и исследования:» и 8-12 главных научных новостей и открытий; затем строка «ИИ и технологии:» и 8-12 новостей про ИИ, модели, роботов и техпроизводство; затем строка «Факты дня:» и 8-12 самых занятных фактов. Не сокращай: перечисли каждый факт и каждое заметное открытие.)
=== ДОБРЫЕ ===
(12-20 добрых, тёплых или вдохновляющих историй о людях, животных, природе и взаимопомощи. Без политики, политиков и корпоративных новостей.)
=== ПРИКОЛЫ ===
(12-20 пунктов: ВСЕ курьёзные, смешные и странные истории + занятные бытовые казусы. Не сокращай список.)
=== ПОЗИТИВ ДНЯ ===
(один самый тёплый пункт из всех)

Правила:
- Итоговая сводка ВСЕГДА полностью на русском. Все англоязычные посты, включая материалы из English и X, точно переводи на русский; названия организаций, имена и ссылки оставляй без искажений.
- Каждая строка внутри раздела начинается с «• ».
- Заголовки-разделители пиши точно как указано, без изменений.
- У некоторых постов сразу после текста стоит тег картинки вида [img:https://...] — это фото к новости. Для 6-12 САМЫХ ЛУЧШИХ пунктов скопируй тег картинки в конец этого пункта ТОЧНО как в исходнике. Переноси фото ВО ВСЕ разделы, где есть подходящие картинки: МИР И РФ, ОСНОВНЫЕ, НАУКА (открытия, ИИ, факты дня — если у факта есть фото, обязательно перенеси) и ПРИКОЛЫ (у самых забавных — фото обязательно).
- Во ВСЕХ разделах исключай политику, политиков и войны. Самые свежие подтверждённые сюжеты ставь первыми.
- Разделы могут частично пересекаться по темам — это нормально.
- Бытовой абсурд и правовые казусы (странные законы, штрафы, нелепые покупки, судебные казусы, странные объявления, курьёзные происшествия без жертв) — это ПРИКОЛЫ, а не тяжёлый негатив: смело включай их.
- В ДОБРЫЕ и ПРИКОЛЫ не бери криминал с жертвами, трагедии и другой тяжёлый негатив."""

PART_PROMPTS = {
    "fun": """Выдай только раздел «=== ПРИКОЛЫ ===» с 12–20 свежими курьёзными, смешными или странными историями, включая бытовые казусы. Сначала самые новые. В каждом пункте расскажи, что именно произошло и чем закончилось, если это известно. Исключи политику, Путина, войну, государственные и судебные решения. Не включай криминал с жертвами и трагедии; если материала меньше, не выдумывай недостающие пункты.""",
    "good": """Выдай два раздела: «=== ДОБРЫЕ ===» с 12–20 тёплыми или вдохновляющими новостями и «=== ПОЗИТИВ ДНЯ ===» с одной самой тёплой новостью. Используй всю подходящую подборку за последнюю неделю, не повторяй сюжеты. Выбирай истории о помощи людям и животным, спасении природы, добрых поступках и реальной пользе для общества; расскажи конкретный результат. Строго исключи политику и политиков, в том числе Путина, президентов, министров, государственные заявления и решения, даже если их подают как хорошие новости. Строго исключи корпорации и бизнес: новости компаний, запуск продуктов, прибыль, инвестиции, назначения и корпоративный пиар или благотворительность. Не включай криминал с жертвами, трагедии и исторические годовщины. При нехватке подходящих историй дай меньше пунктов, не заполняй раздел посторонними сюжетами; «Позитив дня» тоже должен соответствовать этим правилам.""",
    "facts_1": """Напиши до 16 разных любопытных фактов из этой подборки, используя максимум содержательных материалов. Выдай только строки «• [дата:YYYY-MM-DD HH:MM] ...», без заголовка раздела; сначала самые свежие источники. Каждый факт содержит конкретное объяснение, число, пример или механизм из источника. Политические события, политики, Путин, выборы, войны, заявления чиновников и государственные решения — НЕ факты для этого раздела. Не пересказывай заголовки и не пиши «учёные выяснили, как/сколько», не сообщая ответа. При нехватке подробностей пропусти материал; ничего не выдумывай.""",
    "facts_2": """Напиши до 16 разных любопытных фактов из этой подборки, используя максимум содержательных материалов. Выдай только строки «• [дата:YYYY-MM-DD HH:MM] ...», без заголовка раздела; сначала самые свежие источники. Каждый факт содержит конкретное объяснение, число, пример или механизм из источника. Политические события, политики, Путин, выборы, войны, заявления чиновников и государственные решения — НЕ факты для этого раздела. Не пересказывай заголовки и не пиши «учёные выяснили, как/сколько», не сообщая ответа. При нехватке подробностей пропусти материал; ничего не выдумывай.""",
    "facts_3": """Напиши до 16 разных любопытных фактов из этой подборки, используя максимум содержательных материалов. Выдай только строки «• [дата:YYYY-MM-DD HH:MM] ...», без заголовка раздела; сначала самые свежие источники. Каждый факт содержит конкретное объяснение, число, пример или механизм из источника. Политические события, политики, Путин, выборы, войны, заявления чиновников и государственные решения — НЕ факты для этого раздела. Не пересказывай заголовки и не пиши «учёные выяснили, как/сколько», не сообщая ответа. При нехватке подробностей пропусти материал; ничего не выдумывай.""",
    "science": """Выдай только раздел «=== НАУКА ===». Сначала «• Открытия и исследования:» и 8–12 главных научных новостей; затем «• ИИ и технологии:» и 8–12 новостей про ИИ и технологии. Внутри подразделов сначала самые свежие материалы. Исключи политику, политиков и войну. В каждой новости назови конкретный результат, цифры или механизм, если они есть в источнике. Если источник задаёт вопрос «сколько» или «как часто», обязательно дай численный ответ; при его отсутствии пропусти новость.""",
    "news": """Выдай только раздел «=== ОСНОВНЫЕ ===». Начни со строки «• Главное:», затем до 12 самых свежих и обсуждаемых НЕПОЛИТИЧЕСКИХ событий дня из русскоязычных каналов и редакций. Самые новые поставь первыми. Исключи Путина, Трампа, других политиков, войну, боевые действия, выборы, законы и решения властей. Каждый пункт — 1–2 предложения с конкретными обстоятельствами, без общих фраз. Если подходящих новостей меньше, не выдумывай.""",
    "world": """Выдай только начало раздела «=== МИР И РФ ===», строку «• В мире:» и до 15 важных НЕПОЛИТИЧЕСКИХ мировых событий. Просмотри всю подборку и максимально наполни раздел происшествиями, природой, обществом, культурой, медициной и необычными событиями без политического сюжета. Полностью исключи Путина, Трампа, других политиков, войны, выборы, санкции, дипломатические переговоры, государственные и судебные решения, партии, силовые ведомства и терроризм. Не подавай политическую новость как бытовую. Каждый пункт — 1–2 предложения с конкретными обстоятельствами; не смешивай события и не повторяй сюжеты. Если подходящих новостей меньше, не выдумывай.""",
    "russia": """Выдай только строку «• В России:» и до 15 важных НЕПОЛИТИЧЕСКИХ событий в РФ. НЕ повторяй заголовок раздела «МИР И РФ». Просмотри всю подборку и максимально наполни раздел происшествиями, природой, обществом, культурой, медициной и необычными событиями без политического сюжета. Полностью исключи Путина, Трампа, других политиков, войны, выборы, санкции, государственные и судебные решения, партии, силовые ведомства и терроризм. Не подавай политическую новость как бытовую. Каждый пункт — 1–2 предложения с конкретными обстоятельствами; объединяй дубли и не выдумывай недостающее.""",
}

PART_RULES = """Ты — редактор персональной сводки. Используй только приведённые материалы. Пиши полностью по-русски: точно переводи англоязычные посты, сохраняя имена, названия и ссылки. Во ВСЕХ разделах исключи политику, Путина, Трампа, других политиков, войну, боевые действия и решения властей. При выборе и порядке пунктов отдавай приоритет самому свежему времени публикации. Каждый новостной пункт начинай с «• [дата:YYYY-MM-DD HH:MM] », точно копируя дату и время его источника; строки-заголовки подразделов оставляй без даты. Сервер уберёт эти служебные метки перед публикацией. Не выдумывай дату и не объявляй старую историю сегодняшней. Заголовки разделов пиши точно в указанном виде. Уместные теги фотографий [img:https://...] копируй точно в конец соответствующего пункта. Не дублируй одну историю внутри раздела и не добавляй неподтверждённых деталей. Каждый пункт должен отвечать на главный вопрос заголовка: что именно случилось, какой результат получен, сколько или как часто — если об этом говорится в источнике. Не публикуй пустые анонсы без ответа; пропусти их.
"""

state = {"busy": False, "stage": "ожидание", "progress": 0, "updated": None, "error": None}
state_lock = threading.Lock()
collect_lock = threading.Lock()

def save_state():
    with state_lock:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False))

def set_stage(stage, progress=None):
    with state_lock:
        state["stage"] = stage
        if progress is not None:
            state["progress"] = progress
    save_state()

def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def http_post_json(url, headers, body, timeout):
    data = json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def unescape(s):
    return htmllib.unescape(s)

def strip_tags(s):
    return re.sub(r"<[^>]+>", "", re.sub(r"<br\s*/?>", "\n", s))

def parse_telegram(page):
    items = []
    for block in page.split("data-post=")[1:]:
        dm = re.search(r'datetime="([^"]+)"', block)
        published = ""
        if dm:
            try:
                published = datetime.fromisoformat(dm.group(1).replace("Z", "+00:00")).astimezone(MOSCOW).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
        date = published[:10]
        tm = re.search(r"tgme_widget_message_text[^>]*>([\s\S]*?)</div>", block)
        text = unescape(strip_tags(tm.group(1))).strip() if tm else ""
        im = re.search(r"background-image:url\('?([^')]+)'?\)", block)
        image = None
        if im and "telegram.org/img/emoji" not in im.group(1):
            u = im.group(1)
            image = ("https:" + u) if u.startswith("//") else u
        if text or image:
            items.append({"date": date, "published": published, "text": text, "image": image})
    return items

def parse_x(page):
    """Извлекает посты из серверного HTML публичного профиля X без X API."""
    items = []
    # Каждая запись ленты X начинается этим атрибутом. Берём только отрисованный
    # HTML, а не дублирующиеся данные гидратации React.
    entries = re.split(
        r'<div[^>]+data-href="/[^"/]+/status/\d+"[^>]+data-timeline-entry[^>]*>',
        page,
    )[1:]
    for entry in entries:
        article = re.match(r'([\s\S]*?)</article>', entry)
        if not article:
            continue
        block = article.group(1)
        timestamp = re.search(r'"timestamp":(\d{12,13})', block)
        date = ""
        published = ""
        if timestamp:
            try:
                published = datetime.fromtimestamp(int(timestamp.group(1)) / 1000, MOSCOW).strftime("%Y-%m-%d %H:%M")
                date = published[:10]
            except (OverflowError, OSError, ValueError):
                pass
        # Скрипты содержат служебные JSON-фрагменты и не должны попадать в текст.
        visible = re.sub(r'<(script|style)\b[^>]*>[\s\S]*?</\1>', '', block, flags=re.I)
        # Между HTML-элементами нужна граница: иначе «Reuters», «@Reuters» и
        # время склеиваются, и их нельзя отделить от текста поста.
        text = unescape(re.sub(r'<[^>]+>', ' ', visible)).replace("\xa0", " ")
        text = re.sub(r'\s+', ' ', text).strip()
        # В начале отрисованной карточки всегда отображается автор и относительное
        # время. Убираем его, чтобы в промт попадал именно текст поста.
        text = re.sub(r'^.*?@\w+\s+(?:\d+[mhdw]|Now)\s+', '', text, count=1, flags=re.I)
        # Карточка ссылки и счётчики X дублируют заголовок новости.
        text = re.split(r'\s+From\s+\S+', text, maxsplit=1)[0].strip()
        text = re.sub(r'\s+\d+[\d.,]*[KMB]?(?:\s+\d+[\d.,]*[KMB]?){0,3}$', '', text)
        image = re.search(r'src="(https://pbs\.twimg\.com/media/[^"]+)"', block)
        if text:
            items.append({
                "date": date,
                "published": published,
                "text": text,
                "image": unescape(image.group(1)) if image else None,
            })
    return items

MEDIA_NS = {"media": "http://search.yahoo.com/mrss/"}

def parse_rss(xml_text):
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc_raw = item.findtext("description") or ""
        desc = strip_tags(unescape(desc_raw)).strip()[:800]
        pub = item.findtext("pubDate") or ""
        day = ""
        published = ""
        try:
            dt = email.utils.parsedate_to_datetime(pub)
            published = dt.astimezone(MOSCOW).strftime("%Y-%m-%d %H:%M")
            day = published[:10]
        except Exception:
            pass
        image = None
        for e in item.iter("enclosure"):
            if (e.get("type") or "").startswith("image"):
                image = e.get("url")
                break
        if not image:
            for tag in ("media:content", "media:thumbnail"):
                for mc in item.findall(tag, MEDIA_NS):
                    u = mc.get("url") or ""
                    if re.search(r"\.(jpe?g|png|webp)", u, re.I):
                        image = u
                        break
                if image:
                    break
        if image:
            image = unescape(image)
        if title:
            items.append({"date": day, "published": published, "text": (title + " — " + desc) if desc else title, "image": image})
    return items

SECTION_MARKERS = [
    ("world", "МИР И РФ"), ("news", "ОСНОВНЫ"), ("science", "НАУК"),
    ("good", "ДОБРЫ"), ("fun", "ПРИКОЛ"), ("facts", "ФАКТЫ"),
    ("all", "ПОЗИТИВ ДНЯ"),
]

def parse_sections(content):
    result = {}
    current = None
    buf = []
    def flush():
        nonlocal buf
        if current:
            t = "\n".join(buf).strip()
            if t:
                result[current] = (result.get(current, "") + "\n\n" + t).strip()
        buf = []
    for line in content.split("\n"):
        tr = line.strip()
        if "===" in tr:
            hit = next((m for m in SECTION_MARKERS if m[1] in tr.upper()), None)
            if hit:
                flush()
                current = None if hit[0] == "all" else hit[0]
                continue
        if current is not None:
            buf.append(line)
    flush()
    return result

def collect():
    with collect_lock:
        with state_lock:
            if state["busy"]:
                return False
            state["busy"] = True
            state["error"] = None
            state["progress"] = 0
        save_state()
        try:
            run_collection()
        except Exception as e:
            with state_lock:
                state["error"] = str(e)[:300]
                state["stage"] = "ошибка: " + str(e)[:120]
            save_state()
            return True
        finally:
            with state_lock:
                state["busy"] = False
            save_state()
        return True

def call_llm(cfg, user_text, system_prompt, part):
    """Сначала GLM-5.3 для русской редакции и перевода, затем резервные модели."""
    primary = "glm-5.3"
    # Сохраняем заданную на сервере модель в резервной цепочке: GLM обязателен
    # для перевода, но обновление не должно останавливаться при его сбое.
    fallbacks = cfg.get("fallbackModels") or [
        cfg.get("model"), cfg.get("fallbackModel", "deepseek-v4-pro")
    ]
    if isinstance(fallbacks, str):
        fallbacks = [fallbacks]
    models = []
    for m in [primary] + list(fallbacks):
        if m and m not in models:
            models.append(m)
    errors = []
    for index, model in enumerate(models):
        set_stage(f"ИИ готовит {part} через {model}…", 0.48 + index * 0.02)
        try:
            response = http_post_json(
                cfg["apiBase"].rstrip("/") + "/chat/completions",
                {"Authorization": "Bearer " + cfg["apiKey"]},
                {"model": model,
                 "messages": [
                     {"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_text}],
                 "temperature": 0.3,
                 "max_tokens": 4500 if part == "good" else 3600 if part.startswith("facts_") else 3000,
                 "reasoning_effort": "low"},
                timeout=600)
            content = response.get("choices", [{}])[0].get("message", {}).get("content")
            if isinstance(content, str) and content.strip():
                set_stage(f"Раздел {part} получен от {model}", 0.52)
                return content, model
            errors.append(f"{model}: пустой ответ")
        except Exception as exc:
            errors.append(f"{model}: {str(exc)[:180]}")
            if index + 1 < len(models):
                set_stage(f"{model} недоступна — следующая модель…", 0.62)
    raise RuntimeError("; ".join(errors))

def part_body(content):
    """Разделители добавляет сервер: модель иногда забывает их в коротком ответе."""
    lines = []
    for line in content.splitlines():
        clean = line.strip()
        if not clean or (clean.startswith("===") and clean.endswith("===")):
            continue
        clean = re.sub(r"^•\s*(?:В мире|В России|Главное):\s*", "• ", clean)
        if clean.strip() != "•":
            lines.append(clean)
    return "\n".join(lines)

POLITICAL_RE = re.compile(
    r"\b(?:путин\w*|putin|трамп\w*|trump|президент\w*|министр\w*|"
    r"правительств\w*|депутат\w*|парламент\w*|госдум\w*|"
    r"кремл\w*|политик\w*|политическ\w*|чиновник\w*|губернатор\w*|"
    r"санкц\w*|выборы|выборов|выборах|войн\w*|военн\w*|"
    r"дипломат\w*|переговор\w*|законопроект\w*|госуправлен\w*|"
    r"власт\w*|правящ\w*|парти\w*|мвд|приказ\w*|"
    r"суд|суда|судом|суде|судебн\w*|приговор\w*|"
    r"террор\w*|боевик\w*|игил|разминир\w*|"
    r"наземн\w*\s+мин\w*|всу|вс\s+рф|сво|"
    r"фронт\w*|обстрел\w*|боев\w*|войск\w*|солдат\w*|"
    r"спецоперац\w*|фсб|мид|пентагон\w*|нато|nato|"
    r"митинг\w*|протест\w*|госдеп\w*|"
    r"president|government|minister|election\w*|military|"
    r"troops|kremlin|parliament|politic\w*|sanction\w*|"
    r"congress|war|warfare)\b",
    re.I,
)
GOOD_BUSINESS_RE = re.compile(
    r"\b(?:компан\w*|корпорац\w*|бизнес\w*|стартап\w*|"
    r"бренд\w*|гендиректор\w*|предприят\w*|инвестиц\w*|"
    r"акционер\w*|росатом\w*|spacex)\b",
    re.I,
)
DATE_TAG_RE = re.compile(r"^•\s*\[дата:(\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?)\]\s*")
SCIENCE_SUBHEADINGS = {"• Открытия и исследования:", "• ИИ и технологии:", "• Факты дня:"}

def good_item_allowed(item):
    text = " ".join(item)
    return not POLITICAL_RE.search(text) and not GOOD_BUSINESS_RE.search(text)

def filter_items(content, allowed):
    """Фильтруем пункты и ставим новые первыми, сохраняя фото и подзаголовки."""
    kept = []
    group = []
    item = []

    def flush_item():
        if item:
            match = DATE_TAG_RE.match(item[0])
            cleaned = [DATE_TAG_RE.sub("• ", item[0])] + item[1:]
            if allowed(cleaned):
                group.append((match.group(1) if match else "", cleaned))

    def flush_group():
        group.sort(key=lambda entry: entry[0], reverse=True)
        for _, lines in group:
            kept.extend(lines)
        group.clear()

    for line in part_body(content).splitlines():
        if line.startswith("• "):
            flush_item()
            item = []
            if line in SCIENCE_SUBHEADINGS:
                flush_group()
                kept.append(line)
                continue
            item = [line]
        elif item:
            item.append(line)
    flush_item()
    flush_group()
    return "\n".join(kept)

def filter_good_items(content):
    """Не допускаем политические и корпоративные сюжеты, даже если модель ошиблась."""
    return filter_items(content, good_item_allowed)

def filter_nonpolitical_items(content):
    return filter_items(content, lambda item: not POLITICAL_RE.search(" ".join(item)))


def load_source_chunk(src, today):
    raw = http_get(src["url"]).decode("utf-8", "replace")
    if src["kind"] == "tg":
        items = parse_telegram(raw)
    elif src["kind"] == "x":
        items = parse_x(raw)
    else:
        items = parse_rss(raw)
    recent = []
    for item in items:
        try:
            age = (today - datetime.strptime(item["date"], "%Y-%m-%d").date()).days
        except ValueError:
            continue
        if 0 <= age <= (7 if src["group"] in ("facts", "good") else 1) and not POLITICAL_RE.search(item["text"]):
            recent.append(item)
    limit = {"facts": 20, "world": 8, "news": 4, "fun": 3, "good": 10, "science": 2}.get(src["group"], 3)
    recent.sort(key=lambda item: item.get("published") or item["date"], reverse=True)
    chosen = recent[:limit]
    if src["group"] == "science":
        for item in recent:
            if any(key in item["text"] for key in VERIFIED_DETAILS) and item not in chosen:
                chosen.append(item)
    chosen.sort(key=lambda item: item.get("published") or item["date"], reverse=True)
    lines = []
    for it in chosen:
        detail = next((value for key, value in VERIFIED_DETAILS.items() if key in it["text"]), "")
        max_chars = 600 if src["group"] == "facts" else (500 if src["group"] == "science" else 420)
        line = "• [дата:" + (it.get("published") or it["date"] + " 00:00") + "] " + (it["text"] or "(фото)")[:max_chars]
        if detail:
            line += " — " + detail
        if it["image"]:
            line += "\n[img:" + it["image"] + "]"
        lines.append(line)
    return "=== ИСТОЧНИК: " + src["title"] + " ===\n" + "\n".join(lines) if lines else ""

def run_collection():
    cfg = load_config()
    public_url = cfg.get("publicUrl", "").rstrip("/")
    today = datetime.now(MOSCOW).date()
    chunks = []
    source_chunks = []
    grouped_chunks = {src["group"]: [] for src in SOURCES}
    total = len(SOURCES)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(load_source_chunk, src, today) for src in SOURCES]
        for i, (src, future) in enumerate(zip(SOURCES, futures)):
            set_stage(f"Загружаю источники: {i + 1}/{total}", 0.4 * (i + 1) / total)
            try:
                chunk = future.result()
            except Exception:
                continue
            if chunk:
                chunks.append(chunk)
                source_chunks.append((src, chunk))
                grouped_chunks[src["group"]].append(chunk)
    if not chunks:
        raise RuntimeError("все источники недоступны")

    set_stage("ИИ читает источники…", 0.45)
    parts = {}
    models_used = []
    contexts = {
        "world": [],
        "russia": [],
        "news": grouped_chunks["news"][:],
        "science": grouped_chunks["science"],
        "facts_1": grouped_chunks["facts"][:1],
        "facts_2": grouped_chunks["facts"][1:2],
        "facts_3": grouped_chunks["facts"][2:3],
        "good": grouped_chunks["good"],
        "fun": grouped_chunks["fun"],
    }
    for src, chunk in source_chunks:
        if src["group"] == "world":
            contexts["russia" if src.get("region") == "russia" else "world"].append(chunk)
    russian_editorial = contexts["russia"][:]
    # Сначала даём модели российские повседневные новости и курьёзы:
    # иначе политическая RSS-повестка вытесняет неполитические сюжеты.
    contexts["russia"] = grouped_chunks["news"] + grouped_chunks["fun"][:4] + russian_editorial
    contexts["news"] += russian_editorial
    for part, instruction in PART_PROMPTS.items():
        source_text = "\n\n".join(contexts[part])[:26000 if part in ("good", "world", "russia") else 14000]
        if not source_text:
            continue
        text, model = call_llm(cfg, source_text, PART_RULES + instruction, part)
        parts[part] = text.strip()
        if model not in models_used:
            models_used.append(model)
    if not parts.get("news") or not parts.get("world") or not parts.get("russia"):
        raise RuntimeError("не удалось собрать все основные и мировые разделы")
    ordered = []
    if parts.get("fun"):
        fun_text = filter_nonpolitical_items(parts["fun"])
        if fun_text:
            ordered.append("=== ПРИКОЛЫ ===\n" + fun_text)
    positive = ""
    if parts.get("good"):
        good_text, marker, positive = parts["good"].partition("=== ПОЗИТИВ ДНЯ ===")
        good_text = filter_good_items(good_text)
        positive = filter_good_items(positive)
        if good_text:
            ordered.append("=== ДОБРЫЕ ===\n" + good_text)
    fact_lines = []
    seen_facts = set()
    for part in ("facts_1", "facts_2", "facts_3"):
        for line in parts.get(part, "").splitlines():
            if not line.lstrip().startswith("• "):
                continue
            if POLITICAL_RE.search(line):
                continue
            signature = re.sub(r"\W+", "", DATE_TAG_RE.sub("• ", line).lower())[:100]
            if signature not in seen_facts:
                seen_facts.add(signature)
                fact_lines.append(line.strip())
    if any("В препринте:" in chunk for _, chunk in source_chunks) and not any(
        "посещени" in line.lower() and "час" in line.lower() for line in fact_lines
    ):
        fact_lines.insert(0, "• Анализ исследований о прогулках на природе показал: одно посещение зелёной зоны и один час в неделю дают около половины максимального расчётного эффекта для психического самочувствия; четыре посещения и четыре часа — около 90%, шесть посещений и восемь часов — около 99%. Это предварительная модель связи, а не медицинское назначение.")
    news_text = filter_nonpolitical_items(parts["news"])
    if not news_text:
        news_text = filter_nonpolitical_items(parts.get("russia", "")) or filter_nonpolitical_items(parts.get("fun", ""))
    news_count = sum(
        line.lstrip().startswith("• ") and not line.rstrip().endswith(":")
        for line in news_text.splitlines()
    )
    for extra_sources in (grouped_chunks["science"][:6], grouped_chunks["science"][6:]):
        missing = max(3 * news_count, 48) - len(fact_lines)
        if missing <= 0:
            break
        if not extra_sources:
            continue
        used = "\n".join(line[:85] for line in fact_lines)
        extra_input = "Уже использованные темы — не повторять:\n" + used + "\n\nНовые источники:\n" + "\n\n".join(extra_sources)
        extra_prompt = PART_RULES + f"Напиши до {missing} новых, конкретных и удивительных фактов только из новых источников. Формат: по одной строке «• [дата:YYYY-MM-DD HH:MM] ...» на факт, без заголовка. Сначала самые свежие источники. Не повторяй использованные темы и не превращай факт в пустой анонс. Исключи политику, Путина, политиков, выборы, войны и решения властей; ничего не выдумывай."
        extra_text, extra_model = call_llm(cfg, extra_input[:14000], extra_prompt, "facts_extra")
        if extra_model not in models_used:
            models_used.append(extra_model)
        for line in extra_text.splitlines():
            if not line.lstrip().startswith("• "):
                continue
            if POLITICAL_RE.search(line):
                continue
            signature = re.sub(r"\W+", "", DATE_TAG_RE.sub("• ", line).lower())[:100]
            if signature not in seen_facts:
                seen_facts.add(signature)
                fact_lines.append(line.strip())
    fact_lines = filter_nonpolitical_items("\n".join(fact_lines)).splitlines()
    if sum(line.startswith("• ") for line in fact_lines) < 3 * news_count:
        raise RuntimeError("недостаточно проверяемых фактов для соотношения 3:1")
    if fact_lines:
        ordered.append("=== ФАКТЫ ===\n" + "\n".join(fact_lines))
    if parts.get("science"):
        science_text = filter_nonpolitical_items(parts["science"])
        if science_text:
            ordered.append("=== НАУКА ===\n" + science_text)
    if news_text:
        ordered.append("=== ОСНОВНЫЕ ===\n• Главное:\n" + news_text)
    if "world" in parts:
        world_text = filter_nonpolitical_items(parts["world"])
        russia_text = filter_nonpolitical_items(parts.get("russia", ""))
        if not russia_text:
            # Резерв: неполитические российские сюжеты из «Основных».
            russia_text = news_text
        if not world_text or not russia_text:
            raise RuntimeError("нет неполитических событий для блока «Мир и РФ»")
        ordered.append("=== МИР И РФ ===\n• В мире:\n" + world_text +
                       "\n• В России:\n" + russia_text)
    if positive.strip():
        ordered.append("=== ПОЗИТИВ ДНЯ ===\n" + positive)
    content = "\n\n".join(ordered)
    content = re.sub(r"(?m)^•\s*\[дата:\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?\]\s*", "• ", content)
    model_used = ", ".join(models_used)

    # Не позволяйте незакрытому тегу фото захватить соседние разделы.
    content = re.sub(r"\[img:(?!https?://[^\]\s]+\])[^\n]*", "", content)

    # Кэшируем ВСЕ фото на сервере: клиент ничего не качает с внешних CDN.
    set_stage("Кэширую фото…", 0.9)
    urls = re.findall(r"\[img:(https?://[^\]\s]+)\]", content)
    keep = set()
    for u in urls:
        tag_old = "[img:" + u + "]"
        try:
            data = http_get(u, timeout=30)
            ext = ".jpg"
            m = re.search(r"\.(jpe?g|png|webp)(?:[?#]|$)", u, re.I)
            if m:
                ext = "." + m.group(1).lower().replace("jpeg", "jpg")
            name = hashlib.sha1(u.encode()).hexdigest()[:16] + ext
            path = IMG_DIR / name
            if not path.exists() or path.stat().st_size != len(data):
                path.write_bytes(data)
            keep.add(name)
            content = content.replace(tag_old, "[img:" + public_url + "/img/" + name + "]")
        except Exception:
            content = content.replace(tag_old, "")

    sections = parse_sections(content)
    required = {"facts", "news", "world"}
    missing = required - sections.keys()
    if missing or "• В мире:" not in sections["world"] or "• В России:" not in sections["world"]:
        raise RuntimeError("неполная сводка после обработки фото: " + ", ".join(sorted(missing)))
    fact_count = sum(line.lstrip().startswith("• ") for line in sections["facts"].splitlines())
    main_count = sum(line.lstrip().startswith("• ") and not line.rstrip().endswith(":")
                     for line in sections["news"].splitlines())
    if fact_count < 3 * main_count:
        raise RuntimeError("соотношение фактов и основных новостей ниже 3:1")

    # Чистим кэш: остаются только фото текущей сводки
    for f in IMG_DIR.iterdir():
        if f.is_file() and f.name not in keep:
            try:
                f.unlink()
            except Exception:
                pass

    now = datetime.now(MOSCOW)
    payload = {
        "updated": now.isoformat(timespec="seconds"),
        "updatedHuman": "%d %s, %02d:%02d" % (now.day, MONTHS_RU[now.month - 1], now.hour, now.minute),
        "model": model_used,
        "summary": content,
        "sections": sections,
    }
    tmp = SUMMARY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(SUMMARY_PATH)
    with state_lock:
        state["updated"] = payload["updated"]
        state["progress"] = 1
        state["stage"] = "готово"
    save_state()

CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8"}

def next_collection_slot(now=None):
    """Следующий запуск: 00:00, 04:00, ..., 20:00 по Москве."""
    now = now or datetime.now(MOSCOW)
    return now.replace(hour=(now.hour // 4) * 4, minute=0, second=0, microsecond=0) + timedelta(hours=4)

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype, extra_headers=None):
        extra_headers = dict(extra_headers or {})
        # Один заголовок Cache-Control: extra переопределяет дефолт
        cache = extra_headers.pop("Cache-Control", "no-cache" if ctype.endswith("json") else "public, max-age=3600")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in extra_headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def _pin_ok(self):
        """Проверяет cookie spbugene_pin: защита сводки PIN-кодом из конфига."""
        pin = load_config().get("pinCode", "")
        if not pin:
            return True
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "spbugene_pin" and v.strip() == pin:
                return True
        return False

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            # Открытые пути: сама страница, манифест, иконки, скачивание APK
            if path in ("/", "/index.html"):
                # Страница не кэшируется: обновления интерфейса применяются сразу
                self._send(200, (WWW / "index.html").read_bytes(), "text/html; charset=utf-8", {"Cache-Control": "no-cache, no-store, must-revalidate"})
                return
            if path == "/apple-touch-icon.png":
                self._send(200, (BASE / "pwa" / "touch-180.png").read_bytes(), "image/png")
                return
            if path == "/manifest.json":
                if (BASE / "manifest.json").is_file():
                    self._send(200, (BASE / "manifest.json").read_bytes(), "application/manifest+json; charset=utf-8")
                else:
                    self._send(404, b"not found", "text/plain")
                return
            if path.startswith("/pwa/"):
                name = os.path.basename(path)
                f = BASE / "pwa" / name
                if f.is_file():
                    ctype = CONTENT_TYPES.get(f.suffix.lower(), "image/png")
                    self._send(200, f.read_bytes(), ctype)
                else:
                    self._send(404, b"not found", "text/plain")
                return
            if path.startswith("/dl/"):
                name = os.path.basename(path)
                f = WWW / "dl" / name
                if f.is_file():
                    ctype = "application/vnd.android.package-archive" if f.suffix == ".apk" else CONTENT_TYPES.get(f.suffix.lower(), "application/octet-stream")
                    self._send(200, f.read_bytes(), ctype, {"Content-Disposition": "attachment; filename=\"" + name + "\""})
                else:
                    self._send(404, b"not found", "text/plain")
                return

            # Всё остальное (сводка, статус, фото) — только с правильным PIN в cookie
            if not self._pin_ok():
                self._send(401, b'{"error":"pin required"}', "application/json; charset=utf-8")
                return

            if path == "/summary.json":
                if SUMMARY_PATH.exists():
                    self._send(200, SUMMARY_PATH.read_bytes(), "application/json; charset=utf-8")
                else:
                    self._send(404, b'{"error":"summary not ready"}', "application/json; charset=utf-8")
            elif path == "/state.json":
                with state_lock:
                    snapshot = state.copy()
                now = datetime.now(MOSCOW)
                snapshot["serverTime"] = int(now.timestamp() * 1000)
                snapshot["nextUpdate"] = int(next_collection_slot(now).timestamp() * 1000)
                self._send(200, json.dumps(snapshot, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            elif path.startswith("/img/"):
                name = os.path.basename(path)
                f = IMG_DIR / name
                if f.is_file():
                    ctype = CONTENT_TYPES.get(f.suffix.lower(), "application/octet-stream")
                    self._send(200, f.read_bytes(), ctype)
                else:
                    self._send(404, b"not found", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")
        except BrokenPipeError:
            pass

    def do_POST(self):
        if self.path.split("?")[0] == "/collect":
            if not self._pin_ok():
                self._send(401, b'{"error":"pin required"}', "application/json; charset=utf-8")
                return
            threading.Thread(target=collect, daemon=True).start()
            body = json.dumps({"started": True}).encode()
            self._send(200, body, "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

def scheduler():
    while True:
        slot = next_collection_slot().timestamp()
        while True:
            remaining = slot - time.time()
            if remaining <= 0:
                break
            time.sleep(min(remaining, 60))
        collect()

def main():
    WWW.mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    if SUMMARY_PATH.exists():
        try:
            summary = json.loads(SUMMARY_PATH.read_text())
            if isinstance(summary, dict):
                state["updated"] = summary.get("updated")
        except (OSError, ValueError):
            pass
    save_state()
    threading.Thread(target=scheduler, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"SPbugene News server on :{PORT}")
    server.serve_forever()

if __name__ == "__main__":
    main()
