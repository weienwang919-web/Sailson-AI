from __future__ import annotations

import json
import tempfile
from copy import copy
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from app.core.pipeline import clean_text, normalize_link, parse_platform_blocks
from app.models import KOLRecord, SheetTemplate


def export_records_restored(db: Session, records: list[KOLRecord]) -> Path:
    templates = {t.category: t for t in db.query(SheetTemplate).all()}
    template_path = _first_template_path(templates)
    style_cache: dict[str, list[Any]] = {}
    if template_path and template_path.exists():
        wb = load_workbook(template_path)
        style_cache = _capture_data_row_styles(wb)
        _clear_template_data_rows(wb)
    else:
        wb = Workbook()
        wb.active.title = "KOL List"

    by_category: dict[str, list[KOLRecord]] = {}
    for record in records:
        by_category.setdefault(record.category or "Uncategorized", []).append(record)

    used_categories: set[str] = set()
    for category, rows in by_category.items():
        if category in templates and category in wb.sheetnames:
            metadata = json.loads(templates[category].metadata_json)
            _append_hok_rows(wb[category], metadata, rows, style_cache.get(category))
            used_categories.add(category)

    flat_rows = [r for r in records if (r.category or "Uncategorized") not in used_categories]
    if flat_rows:
        _append_flat_sheet(wb, flat_rows)

    out_dir = Path(tempfile.gettempdir()) / "kol-web-exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "kol_export_restored.xlsx"
    wb.save(out_path)
    return out_path


def export_source_workbook_updated(source_file: str, records: list[KOLRecord]) -> Path:
    source_path = Path(__file__).resolve().parents[2] / "data" / "uploads" / source_file
    if not source_path.exists():
        raise FileNotFoundError(f"Source workbook not found: {source_file}")
    wb = load_workbook(source_path)
    record_index = _build_record_index(records)
    for ws in wb.worksheets:
        if ws.max_row < 1:
            continue
        row0 = [_cell_value(ws, 1, col) for col in range(1, ws.max_column + 1)]
        row1 = [_cell_value(ws, 2, col) for col in range(1, ws.max_column + 1)] if ws.max_row >= 2 else []
        blocks = parse_platform_blocks(row0, row1) if row1 else {}
        if blocks:
            _update_hok_sheet_metrics(ws, blocks, record_index)
        else:
            _update_flat_sheet_metrics(ws, record_index)
        _trim_far_empty_columns(ws)
    out_dir = Path(tempfile.gettempdir()) / "kol-web-exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{Path(source_file).stem}_metrics_updated.xlsx"
    wb.save(out_path)
    return out_path


def _build_record_index(records: list[KOLRecord]) -> dict[tuple[str, str], KOLRecord]:
    index: dict[tuple[str, str], KOLRecord] = {}
    for record in records:
        for platform, link in (
            ("tiktok", record.tt_link),
            ("ins", record.ins_link),
            ("youtube", record.yt_link),
        ):
            normalized = normalize_link(platform, link)
            if normalized:
                index[(platform, normalized)] = record
    return index


def _update_hok_sheet_metrics(
    ws: Worksheet, blocks: dict[str, dict[str, Any]], record_index: dict[tuple[str, str], KOLRecord]
) -> None:
    for row in range(3, ws.max_row + 1):
        for platform, info in blocks.items():
            cols = info.get("cols", {})
            link_col = cols.get("link")
            if link_col is None:
                continue
            link = _cell_value(ws, row, link_col + 1)
            record = record_index.get((platform, normalize_link(platform, link)))
            if not record:
                continue
            _write_metric_cells(ws, row, cols, platform, record)


def _update_flat_sheet_metrics(ws: Worksheet, record_index: dict[tuple[str, str], KOLRecord]) -> None:
    max_scan_col = min(ws.max_column, 80)
    header_map: dict[str, int] = {}
    for row in range(1, ws.max_row + 1):
        values = [_cell_value(ws, row, col) for col in range(1, max_scan_col + 1)]
        if _looks_like_flat_header(values):
            headers = [clean_text(value) for value in values]
            header_map = {header.lower(): idx + 1 for idx, header in enumerate(headers) if header}
            continue
        link_col = _first_col(header_map, "链接", "➡️ link", "link", "url")
        if not link_col and "creator" in header_map and "channel" in header_map:
            link_col = header_map["channel"]
        if not link_col:
            continue
        link = _cell_value(ws, row, link_col)
        platform_col = _first_col(header_map, "平台", "➡️ 平台", "platform")
        platform = _detect_platform(clean_text(_cell_value(ws, row, platform_col or 0)), link)
        record = record_index.get((platform, normalize_link(platform, link)))
        if not record:
            continue
        follower_col = _first_col(header_map, "粉丝数\n(自然数)", "➡️ followers", "followers")
        avv_col = _first_col(
            header_map,
            "均观看量\n(自然数)",
            "近一个月均观看量\n(自然数)",
            "均观看量\n(自然数/ccv)",
            "均观看量\n(视频)",
            "均观看量\n(自然数)/ccv",
            "➡️ avg view",
            "avg view",
            "avg view count (3m)",
            "expected\n30dviews/ccv",
            "expected\n30d views/ccv",
            "expected \n30d views/ccv",
        )
        _set_flat_metrics(ws, row, platform, record, follower_col, avv_col)


def _write_metric_cells(ws: Worksheet, row: int, cols: dict, platform: str, record: KOLRecord) -> None:
    follower_col = cols.get("follower")
    avv_col = cols.get("avv")
    if platform == "tiktok":
        _set_cell(ws, row, follower_col, record.tt_follower, zero_based=True)
        _set_cell(ws, row, avv_col, record.tt_avv, zero_based=True)
    elif platform == "ins":
        _set_cell(ws, row, follower_col, record.ins_follower, zero_based=True)
    elif platform == "youtube":
        _set_cell(ws, row, follower_col, record.yt_follower, zero_based=True)
        _set_cell(ws, row, avv_col, record.yt_avv, zero_based=True)


def _set_flat_metrics(
    ws: Worksheet, row: int, platform: str, record: KOLRecord, follower_col: int | None, avv_col: int | None
) -> None:
    if platform == "tiktok":
        _set_cell(ws, row, follower_col, record.tt_follower)
        _set_cell(ws, row, avv_col, record.tt_avv)
    elif platform == "ins":
        _set_cell(ws, row, follower_col, record.ins_follower)
    elif platform == "youtube":
        _set_cell(ws, row, follower_col, record.yt_follower)
        _set_cell(ws, row, avv_col, record.yt_avv)


def _set_cell(ws: Worksheet, row: int, col: int | None, value: Any, zero_based: bool = False) -> None:
    if col is None or value is None:
        return
    ws.cell(row=row, column=col + 1 if zero_based else col, value=value)


def _first_col(header_map: dict[str, int], *names: str) -> int | None:
    for name in names:
        col = header_map.get(name.lower())
        if col:
            return col
    return None


def _detect_platform(platform_text: str, link: Any) -> str:
    platform = platform_text.lower()
    text = f"{platform} {clean_text(link)}".lower()
    tokens = {token for token in platform.replace("/", " ").replace(",", " ").replace("&", " ").split() if token}
    if "tiktok" in text or "tik tok" in text or "tt" in tokens:
        return "tiktok"
    if "instagram" in text or "ins" in tokens or "ig" in tokens:
        return "ins"
    if "youtube" in text or "youtu.be" in text or "ytb" in tokens or "yt" in tokens:
        return "youtube"
    return ""


def _looks_like_flat_header(values: list[Any]) -> bool:
    labels = {clean_text(value).lower() for value in values if clean_text(value)}
    has_link = bool(labels & {"链接", "➡️ link", "link", "url", "channel link", "channel"})
    has_name = bool(labels & {"渠道名", "资源名称", "creator", "kol name", "name"})
    has_metric = bool(labels & {"粉丝数\n(自然数)", "followers", "➡️ followers"})
    return has_link and (has_name or has_metric)


def _cell_value(ws: Worksheet, row: int, col: int) -> Any:
    if col < 1 or col > ws.max_column:
        return None
    return ws.cell(row=row, column=col).value


def _value_at(values: list[Any], idx: int) -> Any:
    return values[idx] if idx < len(values) else None


def _trim_far_empty_columns(ws: Worksheet, keep_at_least: int = 80) -> None:
    if ws.max_column <= keep_at_least:
        return
    for key, cell in list(ws._cells.items()):
        if key[1] > keep_at_least and cell.value is None:
            del ws._cells[key]


def _first_template_path(templates: dict[str, SheetTemplate]) -> Path | None:
    for template in templates.values():
        if template.source_file:
            return Path(template.source_file)
    return None


def _clear_template_data_rows(wb: Workbook) -> None:
    for ws in wb.worksheets:
        if ws.max_row > 2:
            ws.delete_rows(3, ws.max_row - 2)


def _capture_data_row_styles(wb: Workbook) -> dict[str, list[Any]]:
    cache: dict[str, list[Any]] = {}
    for ws in wb.worksheets:
        if ws.max_row >= 3:
            cache[ws.title] = [copy(ws.cell(row=3, column=col)._style) for col in range(1, ws.max_column + 1)]
    return cache


def _append_hok_rows(
    ws: Worksheet, metadata: dict, records: list[KOLRecord], row_style: list[Any] | None
) -> None:
    max_cols = int(metadata.get("max_cols") or ws.max_column)
    platform_blocks = metadata.get("platform_blocks", {})
    start_row = 3
    for idx, record in enumerate(records):
        row_num = start_row + idx
        ws.cell(row=row_num, column=1, value=record.name)
        ws.cell(row=row_num, column=2, value=record.category)
        for platform, info in platform_blocks.items():
            cols = info.get("cols", {})
            _write_platform(ws, row_num, cols, platform, record)
        for col in range(1, max_cols + 1):
            if row_style and col <= len(row_style):
                ws.cell(row=row_num, column=col)._style = copy(row_style[col - 1])


def _write_platform(ws: Worksheet, row: int, cols: dict, platform: str, record: KOLRecord) -> None:
    def set_col(key: str, value):
        col = cols.get(key)
        if col is not None:
            ws.cell(row=row, column=col + 1, value=value if value is not None else "/")

    if platform == "tiktok":
        set_col("link", record.tt_link)
        set_col("follower", record.tt_follower)
        set_col("avv", record.tt_avv)
        set_col("short video", record.tt_short_video_price)
        set_col("anchor link", record.tt_anchor_link_price)
    elif platform == "ins":
        set_col("link", record.ins_link)
        set_col("follower", record.ins_follower)
        set_col("post", record.ins_post_price)
        set_col("ig reels", record.ins_reels_price)
    elif platform == "youtube":
        set_col("link", record.yt_link)
        set_col("follower", record.yt_follower)
        set_col("avv", record.yt_avv)
        set_col("full-video", record.yt_full_video_price)
        set_col("live 2hr", record.yt_live_2hr_price)
        set_col("pre-roll", record.yt_pre_roll_price)
        set_col("short video", record.yt_short_video_price)


def _append_flat_sheet(wb: Workbook, records: list[KOLRecord]) -> None:
    title = "Other"
    if title in wb.sheetnames:
        ws = wb[title]
    elif len(wb.sheetnames) == 1 and wb.active.max_row == 1 and wb.active["A1"].value is None:
        ws = wb.active
        ws.title = title
    else:
        ws = wb.create_sheet(title)
    headers = [
        "KOL",
        "Category",
        "Country",
        "Platform",
        "TikTok Link",
        "TT Follower",
        "TT AVV",
        "Instagram Link",
        "INS Follower",
        "YouTube Link",
        "YT Follower",
        "YT AVV",
        "Notes",
    ]
    ws.append(headers)
    for record in records:
        ws.append(
            [
                record.name,
                record.category,
                record.country,
                record.platform_text,
                record.tt_link,
                record.tt_follower,
                record.tt_avv,
                record.ins_link,
                record.ins_follower,
                record.yt_link,
                record.yt_follower,
                record.yt_avv,
                record.notes,
            ]
        )
