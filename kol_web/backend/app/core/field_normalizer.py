from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from app.core.pipeline import clean_text, is_empty_value

PLATFORM_LABELS = {
    "tiktok": "TikTok",
    "ins": "Instagram",
    "instagram": "Instagram",
    "youtube": "YouTube",
    "yt": "YouTube",
}


@dataclass(frozen=True)
class StandardField:
    label: str
    aliases: tuple[str, ...]
    validator: Callable[[str], bool]


def any_value(value: str) -> bool:
    return bool(clean_text(value))


def price_value(value: str) -> bool:
    text = clean_text(value).lower()
    return bool(re.search(r"[$￥€]|\d", text)) and "http" not in text


def percent_or_ratio_value(value: str) -> bool:
    text = clean_text(value)
    return bool(re.search(r"%|\d|m/f|f/m|男|女", text, re.I))


def link_value(value: str) -> bool:
    return bool(re.search(r"https?://", clean_text(value), re.I))


STANDARD_PLATFORM_FIELDS = {
    "合作模式": StandardField(
        label="合作模式",
        aliases=("合作模式", "Form of\nCollaboration", "Form of Collaboration"),
        validator=any_value,
    ),
    "主报价": StandardField(
        label="主报价",
        aliases=(
            "报价",
            "报价（$）",
            "商务报价",
            "Expected\nPrice（USD）",
            "Expected \nPrice",
            "Expected Price",
            "迈蒂报价",
            "迈蒂报价$",
            "打包价",
            "待/可确认合作价",
            "更新后报价",
            "视频发布报价",
            "发布视频报价",
            "Vod报价",
            "VOD报价",
            "Vod精华剪辑10分钟发布YTB报价",
            "视频报价（$）",
            "定制报价（$）",
            "定制长视频报价（10-15min）",
            "定制长视频报价",
            "贴片报价(90s)",
            "贴片报价$",
            "Integration (Tie-in) USD",
            "Total Tie-in + License",
            "Total Discounted",
        ),
        validator=price_value,
    ),
    "CPM": StandardField(
        label="CPM",
        aliases=("CPM", "商务CPM", "cpm", "Expected \nCPM", "Expected\nCPM", "CPM-商务", "发布CPM"),
        validator=price_value,
    ),
    "直播报价": StandardField(
        label="直播报价",
        aliases=("直播", "直播报价", "直播1h报价", "直播1h", "直播报价（$）"),
        validator=price_value,
    ),
    "授权报价": StandardField(
        label="授权报价",
        aliases=(
            "授权报价($)",
            "素材授权+二剪",
            "视频授权价格/月",
            "直播回放授权/月",
            "License ( 1 month ) USD",
            "1个月授权报价",
            "3个月授权报价",
            "一个月授权费（$）",
            "一月视频素材",
            "不发布视频报价",
        ),
        validator=price_value,
    ),
    "受众年龄": StandardField(
        label="受众年龄",
        aliases=("⬆️ 受众年龄", "年龄占比", "Audience age（年龄）", "Audience age", "Age Distribution", "Age Distibution"),
        validator=percent_or_ratio_value,
    ),
    "受众性别": StandardField(
        label="受众性别",
        aliases=("⬆️ 受众性别", "性别占比", "Audience gender（性别）", "Audience gender", "Gender Ratio ( Percentage )"),
        validator=percent_or_ratio_value,
    ),
    "受众地区": StandardField(
        label="受众地区",
        aliases=("⬆️ 受众地区", "国家占比", "Audience geography（受众区域）", "Audience geography", "Nationality Distribution"),
        validator=any_value,
    ),
    "互动率": StandardField(label="互动率", aliases=("互动率",), validator=percent_or_ratio_value),
    "活跃率": StandardField(label="活跃率", aliases=("活跃率", "Activeness"), validator=any_value),
    "客户反馈": StandardField(label="客户反馈", aliases=("客户反馈", "Feedback"), validator=any_value),
    "推进状态": StandardField(label="推进状态", aliases=("是否推进", "是否通过", "是否合作", "状态", "新进展", "进展更新"), validator=any_value),
}

CASE_LINK_HEADERS = (
    "过往案例",
    "⬆️ 商单Link",
    "合作案例",
    "Collab case（合作案例）",
    "Collab case",
    "Tie-in Clip Example",
    "过往推广链接",
    "过往作品",
    "推广案例",
    "过往代表作品",
    "推广游戏名称+ 案例",
    "合作案例及表现",
    "竞品推广案例/推广案例/推荐理由",
)
NOTE_HEADERS = (
    "备注",
    "具体原因/建议",
    "Brief Intro",
    "Additional notes/Info can be added here（补充说明）",
)

CONSUMED_HEADERS = {
    alias
    for field in STANDARD_PLATFORM_FIELDS.values()
    for alias in field.aliases
} | set(CASE_LINK_HEADERS) | set(NOTE_HEADERS)


def build_platform_extra_fields(row: dict[str, Any], platform: str) -> dict[str, str]:
    label = PLATFORM_LABELS.get(platform, "YouTube")
    extra: dict[str, str] = {}
    for standard, field in STANDARD_PLATFORM_FIELDS.items():
        value = first_standard_value(row, standard)
        if value:
            extra[f"{label} - {field.label}"] = value
    return extra


def first_standard_value(row: dict[str, Any], standard: str) -> str:
    field = STANDARD_PLATFORM_FIELDS[standard]
    for alias in field.aliases:
        value = cell(row.get(alias))
        if value and field.validator(value):
            return value
    return ""


def case_links(row: dict[str, Any]) -> str:
    values: list[str] = []
    for header in CASE_LINK_HEADERS:
        value = cell(row.get(header))
        if value and link_value(value):
            values.append(value)
    for header in NOTE_HEADERS:
        value = cell(row.get(header))
        if value and is_link_only(value):
            values.append(value)
        elif value:
            values.extend(urls_in_text(value))
    return "\n".join(dict.fromkeys(values))


def first_note(row: dict[str, Any]) -> str:
    for header in NOTE_HEADERS:
        value = text_without_urls(cell(row.get(header)))
        if value:
            return value
    return ""


def cell(value: Any) -> str:
    return "" if is_empty_value(value) else clean_text(value)


def is_link_only(value: str) -> bool:
    text = clean_text(value)
    if not link_value(text):
        return False
    stripped = re.sub(r"https?://\S+", "", text, flags=re.I)
    stripped = re.sub(r"[\s,;，；。./、|()（）\[\]【】_-]+", "", stripped)
    return len(stripped) <= 6


def urls_in_text(value: str) -> list[str]:
    return re.findall(r"https?://[^\s，；。]+", value, flags=re.I)


def text_without_urls(value: str) -> str:
    text = re.sub(r"https?://[^\s，；。]+", "", clean_text(value), flags=re.I)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip(" \n:：,，;；")
