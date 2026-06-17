from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.display_format import collect_all_prices, format_all_prices_cell, has_displayable_prices


def record(**kwargs):
    defaults = {
        "tt_short_video_price": None,
        "tt_anchor_link_price": None,
        "ins_post_price": None,
        "ins_reels_price": None,
        "yt_full_video_price": None,
        "yt_live_2hr_price": None,
        "yt_pre_roll_price": None,
        "yt_short_video_price": None,
        "extra_fields": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_pre_roll_value_equal_to_youtube_cpm_is_not_displayed_as_price():
    kol = record(
        yt_pre_roll_price=45,
        extra_fields=json.dumps({"YouTube - CPM": 45}, ensure_ascii=False),
    )

    assert collect_all_prices(kol) == []
    assert format_all_prices_cell(kol) == ""
    assert has_displayable_prices(kol) is False


def test_real_pre_roll_price_still_displays_when_different_from_cpm():
    kol = record(
        yt_pre_roll_price=450,
        extra_fields=json.dumps({"YouTube - CPM": 45}, ensure_ascii=False),
    )

    assert collect_all_prices(kol) == [("YT 贴片报价", 450)]
    assert format_all_prices_cell(kol) == "YT 贴片报价 450"
    assert has_displayable_prices(kol) is True


def test_main_price_equal_to_cpm_is_preserved():
    kol = record(extra_fields=json.dumps({"YouTube - CPM": 45, "YouTube - 主报价": 45}, ensure_ascii=False))

    assert collect_all_prices(kol) == [("YT 主报价", 45)]
    assert has_displayable_prices(kol) is True
