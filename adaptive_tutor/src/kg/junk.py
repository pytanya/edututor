"""Junk-фильтры заголовков графа знаний (перенос из референса knowledge_graph.py).

Наборы фраз и регэкспы — дословно из референса: поисковый/UI-мусор
(`_GRAPH_JUNK_*`, `_WEB_NOISE`), URL-подобные заголовки (`_URL_PATTERN`),
CP1251-mojibake-эвристика и проверки «реальная тема, а не кнопка/навигация».
"""

import html
import re

# Regex для обнаружения URL-адресов в строке (фильтрация шумовых узлов)
_URL_PATTERN = re.compile(
    r"https?://|"                           # http:// или https://
    r"www\.\S+|"                            # www.domain...
    r"\b[A-Z]{2,}\.(?:ru|com|org|net|edu|"  # домены верхнего уровня
    r"io|gov|info|co\.ru)\b",               # распространённые домены
    re.IGNORECASE,
)


def _try_fix_mojibake(text: str) -> str:
    """Попытка восстановить текст из CP1251 mojibake (diamond symbols, garbage chars).

    CP1251-шрифты без ToUnicode генерируют суррогаты (U+D800-U+DFFF) при чтении
    PDF с кириллицей. Также могут быть «кракозябры» типа «ÃâÃç» (UTF-8 прочитан
    как CP1251). Возвращаем очищенный текст.

    ВАЖНО: если текст уже в UTF-8 (нет кракозябр) — не трогаем его.
    """
    if not text:
        return text

    # Проверяем наличие «кракозябр» (UTF-8 прочитан как CP1251)
    # Кракозябры: символы вне ASCII и вне кириллицы, но с ord > 127
    has_mojibake = any(ord(c) > 127 and c not in "А-Яа-яЁё" for c in text)

    if not has_mojibake:
        return text  # текст уже в UTF-8, не трогаем

    # Удаляем суррогаты (CP1251 → UTF-8 mojibake)
    text = re.sub(r"[\ud800-\udfff]", "", text)
    # Удаляем diamond symbols (U+25C6) и другие garbage chars от CP1251
    text = re.sub(r"[\u25c6\u25c7\u25a0\u25a1\u25cb\u25cf\u25b2\u25bc\u25e6\u25e7]", " ", text)
    # Удаляем одиночные control chars и garbage
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)

    # Пробуем декодировать как CP1251 → UTF-8 (обратная операция mojibake)
    try:
        # Проверяем наличие «кракозябр» (UTF-8 прочитан как CP1251)
        if any(ord(c) > 127 and c not in "А-Яа-яЁё" for c in text):
            # Пробуем decode as CP1251, encode as UTF-8
            fixed = text.encode("cp1251", errors="ignore").decode("utf-8", errors="ignore")
            # Если результат читаемый (больше 50% кириллицы) — используем
            cyrillic_ratio = sum(1 for c in fixed if "\u0400" <= c <= "\u04ff") / max(len(fixed), 1)
            if cyrillic_ratio > 0.5 and len(fixed) > len(text) * 0.5:
                text = fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass

    return text


def clean_title(text: str) -> str:
    """Очистка заголовка узла: HTML-entities → символы, суррогаты → удалить, mojibake → исправить.

    Веб-страницы приходят с entity-кодами (&#8470;) и mojibake-суррогатами
    (CP1251-шрифты без ToUnicode) — они ломают отображение и матчинг с wiki.
    """
    if not text:
        return ""
    text = html.unescape(text)
    # Попытка исправить CP1251 mojibake
    text = _try_fix_mojibake(text)
    # Удаляем оставшиеся суррогаты
    text = re.sub(r"[\ud800-\udfff]", "", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text).strip(" *#-–—")
    return text


def _is_valid_topic_title(title: str, min_words: int = 2) -> bool:
    """Проверяет, что title — реальная тема, а не instruction/навигация.

    Отсекает:
    - слишком короткие titles (<min_words слов; 2 по умолчанию — для веб-заголовков)
    - titles которые являются instruction (вернуться, продолжить, см. также)
    - titles которые похожи на навигацию (оглавление, примечания, источники)
    - интерактивные элементы (задания, оценки, комментарии)
    - задания-инструкции (глаголы: выполните, прочитайте, напишите)
    - класс-специфичные ссылки (5-Б класс, 7-А класс)

    Для секций учебника (build_textbook_graph) min_words=1: подзаголовок
    «Параграф 12: Атмосфера» — одно слово, но это валидная тема.
    """
    if not title:
        return False
    title_lower = title.lower().strip()
    # Слишком короткое — не тема (минимум 2 слова для веб-тем, 1 для секций учебника)
    if len(title_lower.split()) < min_words:
        return False
    # Instruction/навигация
    instructions = {
        "вернуться к теме", "вернитесь к теме", "продолжить", "продолжайте",
        "см. также", "смотри также", "читать также", "подробнее",
        "оглавление", "примечания", "источники", "литература",
        "библиотека", "содержание",
        "похожие запросы", "улучшить свой запрос",
    }
    if title_lower in instructions:
        return False
    # Если title начинается с «вернуться» или «продолжить» — не тема
    if title_lower.startswith(("вернуться", "вернитесь", "продолжить", "продолжайте")):
        return False
    # Интерактивные элементы: задания, оценки, комментарии
    interactive = ("задание", "задания", "оценить", "отменить", "хотите", "ответить")
    if any(title_lower.startswith(w) for w in interactive):
        return False
    # Паттерн «Задание №N»
    if re.match(r"^задание\s*№?\s*\d+", title_lower):
        return False
    # Задания-инструкции: глаголы повелительного наклонения
    task_verbs = (
        "выполните", "прочитайте", "напишите", "сделайте", "подготовьте",
        "изучите", "разберите", "определите", "найдите", "сравните",
        "объясните", "докажите", "докажите", "расскажите", "перескажите",
        "запишите", "составьте", "сделайте", "проведите", "проанализируйте",
    )
    if any(title_lower.startswith(v) for v in task_verbs):
        return False
    # Класс-специфичные темы: «5-Б класс», «7-А класс», «урок 5-Б» — не добавляем
    if re.search(r"\b\d+[\-]?[а-я]\s*класс|класс\s*\d+[\-]?[а-я]\b", title_lower, re.IGNORECASE):
        return False
    return not re.search(r"(?:г\.?\s*)?урок\s*\d+\s*[а-я]", title_lower, re.IGNORECASE)


# Шумовые секции веб-страниц, которые НЕ являются темами урока (навигация/служебное)
_WEB_NOISE = {
    "содержание", "оглавление", "примечания", "источники", "литература",
    "см. также", "смотри также", "внешние ссылки", "ссылки", "навигация",
    "reference", "references", "see also", "external links", "notes",
    "navigation", "menu", "about", "о проекте", "категории", "categories",
    "статья в википедии", "связанные статьи", "другие статьи", "читайте также",
    # Навигация и UI элементы веб-страниц
    "библиотека", "library", "download", "скачать", "preview",
    "предварительный просмотр", "поделиться", "share", "печатать", "print",
    "редактировать", "edit", "комментарии", "comments", "обсуждение",
    "обсудить", "отзывы", "reviews", "рейтинг", "rating", "голосовать",
    "vote", "подписаться", "subscribe", "рассылка", "newsletter",
    "реклама", "ads", "рекламный блок", "advert", "баннер", "banner",
    # Интерактивные элементы и задания
    "задания", "задание", "задать вопрос", "ответить", "проверить",
    "оценить", "оценить урок", "отменить", "отменить ответ", "хотите",
    "хотите оставить комментарий", "написать комментарий", "оставить комментарий",
    # Мусор поисковой выдачи
    "похожие запросы", "улучшить свой запрос",
}


def _is_url_like(title: str) -> bool:
    """Проверяет, выглядит ли строка как URL-адрес или доменное имя."""
    if not title:
        return False
    # Если вся строка или основная часть — URL
    if _URL_PATTERN.search(title):
        return True
    # Отсекаем строки, которые полностью совпадают с доменом
    stripped = re.sub(r"^https?://", "", title).strip()
    domain_re = re.compile(
        r"^[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?(?:\.[a-z]{2,})+(?:/[^\s]*)?$",
        re.IGNORECASE,
    )
    return bool(domain_re.match(stripped))


# Мусорные темы графа от поисковых страниц/агрегаторов: заголовки UI поиска
# («Улучшить свой запрос», «Фильтры», «Картинки»), «похожие запросы» и навигация.
_GRAPH_JUNK_EXACT = {
    "улучшить свой запрос", "фильтры", "фильтр", "картинки", "картинка",
    "видео", "видеоролики", "ещё", "все результаты", "похожие запросы",
    "сортировка", "настройки поиска", "искать в интернете", "закрыть", "меню",
    "фонетика и орфоэпия", "лексика и фразеология",
}
_GRAPH_JUNK_SUBSTR = re.compile(
    r"похожие запросы|улучшить свой запрос|результаты поиска|страница не найдена|"
    r"ошибка\s*404|реклама|войти|зарегистрироваться",
    re.IGNORECASE,
)


def is_junk_topic(title: str | None) -> bool:
    """Тема-«мусор» (поисковая выдача/навигация), не являющаяся понятием материала."""
    if not title:
        return True
    low = title.strip().lower()
    if low in _GRAPH_JUNK_EXACT:
        return True
    return bool(_GRAPH_JUNK_SUBSTR.search(title))


def _is_ui_element(title: str) -> bool:
    """Проверяет, является ли строка UI-элементом (кнопка, ссылка действия)."""
    if not title:
        return False
    # Строки, заканчивающиеся на «:» (кнопки действий)
    if title.endswith(":") or title.endswith(": "):
        return True
    # Короткие строки (1-2 слова), которые часто являются UI-элементами
    words = title.split()
    if len(words) <= 2 and len(title) < 25:
        # Проверяем, что это не содержательная тема
        short_ui_keywords = {
            "скачать", "download", "поделиться", "share", "печатать", "print",
            "редактировать", "edit", "комментарии", "comments", "обсудить",
            "отзывы", "reviews", "рейтинг", "rating", "голосовать", "vote",
            "подписаться", "subscribe", "рассылка", "newsletter", "реклама",
            "ads", "баннер", "banner", "библиотека", "library", "предпросмотр",
            "preview", "поиск", "search", "фильтр", "filter", "сортировка",
            "sort", "параметры", "settings", "настройки", "контакты", "contacts",
        }
        if any(w.lower() in short_ui_keywords for w in words):
            return True
    return False


def _is_paragraph_number(title: str) -> bool:
    """Проверяет, является ли строка номером параграфа/раздела (§ N)."""
    # Паттерны: § 58, §58, 58., 58)
    if re.match(r"^§\s*\d+$", title.strip()):
        return True
    return bool(re.match(r"^\d+[.)]\s*$", title.strip()))


def _is_mojibake_heavy(text: str) -> bool:
    """Проверяет, является ли текст сильно повреждённым mojibake.

    Если больше 30% символов — non-Cyrillic non-ASCII (кроме стандартных знаков),
    то это скорее всего mojibake или garbage.
    """
    if not text:
        return True
    total = len(text)
    if total == 0:
        return True
    # Считаем «нормальные» символы: кириллица, латиница, цифры, пробелы, стандартные знаки
    normal_pattern = re.compile(r"[\w\s.,;:!?\-()«»«»—–\u00a0\u2014\u2013\d]")
    normal_count = len(normal_pattern.findall(text))
    # Если нормальных символов меньше 70% — скорее всего mojibake
    return normal_count / total < 0.7
