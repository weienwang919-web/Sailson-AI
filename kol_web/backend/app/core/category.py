from __future__ import annotations

import re

STANDARD_CATEGORIES = [
    "Gaming",
    "Entertainment",
    "Lifestyle",
    "Beauty/Fashion",
    "Tech",
    "Music",
    "Sports",
    "Food/Travel",
    "Education/Review",
    "Cosplay/ACG",
    "Other",
]

CATEGORY_RULES = [
    ("Gaming", ["gaming", "gamer", "streamer", "游戏", "moba", "minecraft", "塔防", "策略", "动作游戏", "角色扮演", "解说", "caster"]),
    ("Cosplay/ACG", ["cos", "coser", "cosplay", "vtuber", "anime", "animation", "漫画", "动漫", "动画", "卡通", "acg"]),
    ("Beauty/Fashion", ["beauty", "fashion", "sexy", "时尚", "美妆", "美妆", "穿搭"]),
    ("Tech", ["tech", "3c", "digital", "electronics", "appliances", "科技", "数码"]),
    ("Music", ["music", "musician", "dj", "band", "音乐", "乐队"]),
    ("Sports", ["sports", "运动"]),
    ("Food/Travel", ["food", "cooking", "travel", "family", "美食", "烹饪", "旅行", "旅游"]),
    ("Education/Review", ["education", "review", "reveiw", "aninews", "资讯", "测评", "教育", "杂谈"]),
    ("Entertainment", ["entertainment", "comedy", "funny", "actor", "actress", "celebrity", "talents", "podcast", "vlog", "娱乐", "搞笑", "演绎", "短剧", "营销号"]),
    ("Lifestyle", ["lifestyle", "生活", "生活方式"]),
]


def normalize_category(category: str | None) -> str:
    text = clean_category_text(category)
    if not text:
        return "Other"
    for standard, keywords in CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            return standard
    return "Other"


def clean_category_text(category: str | None) -> str:
    text = str(category or "").lower()
    return re.sub(r"[\s,;/；、，|+()\[\]（）]+", " ", text).strip()
