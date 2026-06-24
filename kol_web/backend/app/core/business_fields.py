from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FieldGroup = Literal["core", "metrics", "business", "audience", "progress", "detail"]
FieldUsage = Literal["list", "filter", "detail", "export", "create", "update"]


@dataclass(frozen=True)
class BusinessField:
    key: str
    label: str
    group: FieldGroup
    usages: tuple[FieldUsage, ...]
    data_type: Literal["text", "number", "link", "date"] = "text"
    source: Literal["model", "extra"] = "model"
    extra_key: str | None = None

    @property
    def filter_key(self) -> str:
        return f"extra:{self.extra_key}" if self.source == "extra" and self.extra_key else self.key


BUSINESS_FIELDS: tuple[BusinessField, ...] = (
    BusinessField("name", "KOL 名称/Name", "core", ("list", "filter", "detail", "export", "create", "update")),
    BusinessField("major_category", "大类/Category", "core", ("list", "filter", "detail", "export")),
    BusinessField("normalized_category", "标准类目/Sub-category", "core", ("filter", "detail", "export", "update")),
    BusinessField("platform_text", "平台/Platform", "core", ("list", "filter", "detail", "export", "create", "update")),
    BusinessField("country", "国家/地区/Country", "core", ("list", "filter", "detail", "export", "create", "update")),
    BusinessField("language", "语言/Language", "core", ("list", "filter", "detail", "export", "create", "update")),
    BusinessField("content_tags", "内容标签/Content Tags", "detail", ("filter", "detail", "export", "create", "update")),
    BusinessField("case_links", "案例链接/Case Links", "progress", ("detail", "export", "create", "update"), "link"),
    BusinessField("tt_link", "TikTok 链接/Link", "core", ("list", "detail", "export", "create", "update"), "link"),
    BusinessField("tt_follower", "TikTok 粉丝/Followers", "metrics", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("tt_avv", "TikTok AVV/均观看量", "metrics", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("tt_acv", "TikTok ACV/平均直播观看人数", "metrics", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("tt_short_video_price", "TikTok 短视频报价/Short Video Price", "business", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("tt_anchor_link_price", "TikTok Anchor Link 报价/Anchor Link Price", "business", ("detail", "export", "create", "update"), "number"),
    BusinessField("tt_collaboration", "TikTok 合作模式/Collaboration", "business", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="TikTok - 合作模式"),
    BusinessField("tt_main_price", "TikTok 主报价/Main Price", "business", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="TikTok - 主报价"),
    BusinessField("tt_cpm", "TikTok CPM", "business", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="TikTok - CPM"),
    BusinessField("ins_link", "Instagram 链接/Link", "core", ("list", "detail", "export", "create", "update"), "link"),
    BusinessField("ins_follower", "Instagram 粉丝/Followers", "metrics", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("ins_acv", "Instagram ACV/平均直播观看人数", "metrics", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("ins_post_price", "Instagram Post 报价/Post Price", "business", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("ins_reels_price", "Instagram Reels 报价/Reels Price", "business", ("detail", "export", "create", "update"), "number"),
    BusinessField("ins_collaboration", "Instagram 合作模式/Collaboration", "business", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="Instagram - 合作模式"),
    BusinessField("ins_main_price", "Instagram 主报价/Main Price", "business", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="Instagram - 主报价"),
    BusinessField("ins_cpm", "Instagram CPM", "business", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="Instagram - CPM"),
    BusinessField("yt_link", "YouTube 链接/Link", "core", ("list", "detail", "export", "create", "update"), "link"),
    BusinessField("yt_follower", "YouTube 粉丝/Followers", "metrics", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("yt_avv", "YouTube AVV/均观看量", "metrics", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("yt_acv", "YouTube ACV/平均直播观看人数", "metrics", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("yt_full_video_price", "YouTube 长视频报价/Full Video Price", "business", ("list", "filter", "detail", "export", "create", "update"), "number"),
    BusinessField("yt_live_2hr_price", "YouTube 直播报价/Live 2hr Price", "business", ("detail", "export", "create", "update"), "number"),
    BusinessField("yt_pre_roll_price", "YouTube 贴片报价/Pre-roll Price", "business", ("detail", "export", "create", "update"), "number"),
    BusinessField("yt_short_video_price", "YouTube 短视频报价/Short Video Price", "business", ("detail", "export", "create", "update"), "number"),
    BusinessField("yt_collaboration", "YouTube 合作模式/Collaboration", "business", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="YouTube - 合作模式"),
    BusinessField("yt_main_price", "YouTube 主报价/Main Price", "business", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="YouTube - 主报价"),
    BusinessField("yt_cpm", "YouTube CPM", "business", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="YouTube - CPM"),
    BusinessField("audience_region", "受众地区/Audience Region", "audience", ("filter", "detail", "export", "create", "update")),
    BusinessField("audience_age", "受众年龄/Audience Age", "audience", ("filter", "detail", "export", "create", "update")),
    BusinessField("audience_gender", "受众性别/Audience Gender", "audience", ("filter", "detail", "export", "create", "update")),
    BusinessField("progress_status", "推进状态/Progress", "progress", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="TikTok - 推进状态"),
    BusinessField("client_feedback", "客户反馈/Client Feedback", "progress", ("list", "filter", "detail", "export", "update"), source="extra", extra_key="TikTok - 客户反馈"),
    BusinessField("source_file", "来源文件/Source File", "detail", ("filter", "detail", "export")),
)

BUSINESS_FIELD_BY_KEY = {field.key: field for field in BUSINESS_FIELDS}


def fields_for_usage(usage: FieldUsage) -> list[BusinessField]:
    return [field for field in BUSINESS_FIELDS if usage in field.usages]


def field_payload(field: BusinessField) -> dict[str, str]:
    return {
        "key": field.key,
        "filter_key": field.filter_key,
        "label": field.label,
        "group": field.group,
        "data_type": field.data_type,
        "source": field.source,
    }
