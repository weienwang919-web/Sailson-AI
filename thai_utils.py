"""
Shared utilities for Thai report feature.
Used by both app.py and tasks.py.
"""
import re
import datetime

try:
    from langdetect import detect, DetectorFactory
    from langdetect.lang_detect_exception import LangDetectException
    DetectorFactory.seed = 0
    _LANGDETECT_AVAILABLE = True
except ImportError:
    _LANGDETECT_AVAILABLE = False

_THAI_UNICODE_RE = re.compile(r'[\u0E00-\u0E7F]')

# ---------------------------------------------------------------------------
# Game keyword constants
# ---------------------------------------------------------------------------

MLBB_DISCOVER_KEYWORDS = [
    "#mlbb", "#mlbb*", "mlbb", "#mobilelegends", "mobilelegends", "mobilelegendsbangbang",
    "#mobilelegendsbangbang", "#mobalegends", "#moba55", "mobile legends",
    "mlbbxnaruto", "mlbb naruto", "#mlbbnewskin", "#mlbbfreegaaraskin",
    "#valirfreeskin", "#goldsongkran", "#goldhunt",
]

SPD_KEYWORDS = [
    "mlbbxnaruto", "mlbb naruto", "#mlbbnewskin", "#mlbbfreegaaraskin",
    "#valirfreeskin", "#goldsongkran", "#goldhunt",
]

ROV_DISCOVER_KEYWORDS = [
    "#rov", "#realmofvalor", "#garenarov", "#rovthailand",
    "#rovvietnam", "#อาโอวี", "#garenarovthailand", "#ambassadorofvalor",
]

THAI_GAME_TAGS = {
    'MLBB': MLBB_DISCOVER_KEYWORDS,
    'SPD': SPD_KEYWORDS,
    'ROV': ROV_DISCOVER_KEYWORDS,
}

# ---------------------------------------------------------------------------
# Thai language detection
# ---------------------------------------------------------------------------

def is_thai_content(text):
    """Strict: requires text to be primarily Thai (langdetect or 15%+ Thai chars)."""
    if not text:
        return False
    if len(text.strip()) < 15:
        thai_chars = len(_THAI_UNICODE_RE.findall(text))
        return thai_chars >= 2
    if _LANGDETECT_AVAILABLE:
        try:
            lang = detect(text)
            return lang == 'th'
        except LangDetectException:
            pass
    thai_chars = len(_THAI_UNICODE_RE.findall(text))
    return thai_chars / max(len(text), 1) > 0.15


def has_thai_chars(text: str) -> bool:
    """Lenient: returns True if text contains any Thai Unicode character."""
    if not text:
        return False
    return bool(_THAI_UNICODE_RE.search(text))

# ---------------------------------------------------------------------------
# Tag / term matching
# ---------------------------------------------------------------------------

def normalize_tag_token(term: str) -> str:
    t = (term or '').strip().strip('"').strip("'").lower()
    return t


def contains_any_tag_or_term(text: str, terms, hashtags=None) -> bool:
    """OR match: hashtag exact match first, then caption word-boundary fallback."""
    base = (text or '').lower()
    hashtag_set = {
        str(h or '').strip().lstrip('#').lower()
        for h in (hashtags or [])
        if str(h or '').strip()
    }
    for raw in (terms or []):
        t = normalize_tag_token(raw)
        if not t:
            continue
        wildcard = t.endswith('*')
        t_core = (t[:-1] if wildcard else t).strip()
        if not t_core:
            continue
        t_no_hash = t_core.lstrip('#')

        if wildcard:
            if t_no_hash and any(h.startswith(t_no_hash) for h in hashtag_set):
                return True
        else:
            if t_no_hash and t_no_hash in hashtag_set:
                return True

        if wildcard:
            if t_no_hash and re.search(rf'(?<!\w){re.escape(t_no_hash)}\w*', base):
                return True
            continue
        if t_no_hash and re.search(rf'(?<!\w){re.escape(t_no_hash)}(?!\w)', base):
            return True
    return False

# ---------------------------------------------------------------------------
# Dataset config & matching
# ---------------------------------------------------------------------------

def thai_datasets_config():
    """Dynamic dataset config; active datasets use today as end date."""
    _today = datetime.date.today().isoformat()
    return {
        "泰国区域":     {"game": "MLBB", "start": "2026-04-03", "end": _today},
        "26Y SPD泰国1": {"game": "SPD",  "start": "2026-04-03", "end": _today},
        "ROV泰国":      {"game": "ROV",  "start": "2026-04-03", "end": _today},
        "26Y SPD泰国2": {"game": "SPD",  "start": "2026-03-27", "end": "2026-03-29"},
        "115泰国热点":  {"game": "MLBB", "start": "2026-01-15", "end": "2026-01-17"},
        "25Y SPD泰国":  {"game": "SPD",  "start": "2025-05-02", "end": "2025-05-04"},
    }


def thai_matching_datasets(post_date_str, caption, hashtags):
    """Match a post to one or more datasets based on date range + game keywords."""
    cfg = thai_datasets_config()
    hashtag_text = ' '.join(f'#{h}' for h in (hashtags or []) if h is not None and str(h).strip())
    full_text = re.sub(r'\s+', ' ', f'{caption or ""} {hashtag_text}'.lower()).strip()
    matched = []
    for ds_name, meta in cfg.items():
        start, end = meta['start'], meta['end']
        if post_date_str < start or post_date_str > end:
            continue
        game = meta.get('game') or 'MLBB'
        terms = THAI_GAME_TAGS.get(game, [])
        if contains_any_tag_or_term(full_text, terms, hashtags=hashtags):
            matched.append(ds_name)
    return matched
