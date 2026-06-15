from __future__ import annotations

# 快速筛选用的国家 key -> 数据库中可能出现的写法
COUNTRY_FILTER_ALIASES: dict[str, tuple[str, ...]] = {
    "美国": ("美国", "US", "USA", "U.S.", "United States", "America"),
    "日本": ("日本", "JP", "Japan"),
    "韩国": ("韩国", "KR", "Korea", "South Korea", "Republic of Korea"),
    "泰国": ("泰国", "TH", "Thailand"),
    "印尼": ("印尼", "ID", "Indonesia"),
    "英国": ("英国", "UK", "GB", "United Kingdom", "Britain", "England"),
}


def expand_country_filter(value: str) -> list[str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    for key, aliases in COUNTRY_FILTER_ALIASES.items():
        if text == key or text in aliases:
            return list(dict.fromkeys([key, *aliases]))
    return None
