"""
Excel ETL helpers: Thai row filter, engagement aggregation, DataFrame export.
"""
import io
import logging
from typing import List, Optional, Tuple

import pandas as pd

from thai_utils import is_thai_content

logger = logging.getLogger(__name__)

# 功能4：兼容列名
DATE_ALIASES = ("date", "post_date", "日期", "day")
ENGAGEMENT_ALIASES = ("engagement", "互动量", "互动", "total_engagement")

MAX_SYNC_EXCEL_ROWS = 50000


def _normalize_date_col(df: pd.DataFrame) -> pd.DataFrame:
    for a in DATE_ALIASES:
        if a in df.columns:
            out = df.rename(columns={a: "_date_norm"})
            return out
    raise ValueError(f"未找到日期列，需要其一: {DATE_ALIASES}")


def _normalize_engagement_col(df: pd.DataFrame) -> pd.DataFrame:
    for a in ENGAGEMENT_ALIASES:
        if a in df.columns:
            out = df.rename(columns={a: "_eng_norm"})
            return out
    raise ValueError(f"未找到互动量列，需要其一: {ENGAGEMENT_ALIASES}")


def sum_daily_engagement_from_excels(file_bytes_list: List[bytes]) -> bytes:
    """Concatenate multiple workbooks and sum engagement by date."""
    frames = []
    for i, raw in enumerate(file_bytes_list):
        df = pd.read_excel(io.BytesIO(raw))
        df = _normalize_date_col(df)
        df = _normalize_engagement_col(df)
        df["_date_norm"] = pd.to_datetime(df["_date_norm"], errors="coerce").dt.strftime("%Y-%m-%d")
        df["_eng_norm"] = pd.to_numeric(df["_eng_norm"], errors="coerce").fillna(0)
        frames.append(df[["_date_norm", "_eng_norm"]])
    if not frames:
        raise ValueError("没有有效文件")
    all_df = pd.concat(frames, ignore_index=True)
    summed = all_df.groupby("_date_norm", as_index=False)["_eng_norm"].sum()
    summed = summed.rename(columns={"_date_norm": "date", "_eng_norm": "engagement"})
    summed = summed.sort_values("date")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summed.to_excel(writer, sheet_name="daily_engagement_sum", index=False)
    buf.seek(0)
    return buf.read()


def filter_thai_rows_excel(file_bytes: bytes, text_column: Optional[str]) -> Tuple[bytes, int, int]:
    """
    Keep rows where text_column passes is_thai_content.
    If text_column is None, auto-pick first object column with most non-null strings.
    Returns (xlsx_bytes, kept_count, dropped_count).
    """
    df = pd.read_excel(io.BytesIO(file_bytes))
    if len(df) > MAX_SYNC_EXCEL_ROWS:
        raise ValueError(f"行数超过上限 {MAX_SYNC_EXCEL_ROWS}")
    col = text_column
    if not col or col not in df.columns:
        col = _guess_text_column(df)
    kept_mask = []
    for val in df[col].astype(str):
        s = val if val != "nan" else ""
        if not s.strip():
            kept_mask.append(False)
        else:
            kept_mask.append(bool(is_thai_content(s)))
    out_df = df.loc[kept_mask].reset_index(drop=True)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        out_df.to_excel(writer, sheet_name="thai_only", index=False)
    buf.seek(0)
    return buf.read(), len(out_df), len(df) - len(out_df)


def _guess_text_column(df: pd.DataFrame) -> str:
    best_col = None
    best_score = -1
    for c in df.columns:
        ser = df[c]
        if ser.dtype == object or str(ser.dtype).startswith("string"):
            non_null = ser.notna() & (ser.astype(str).str.strip() != "")
            score = int(non_null.sum())
            if score > best_score:
                best_score = score
                best_col = c
    if not best_col:
        raise ValueError("无法自动推断文本列，请指定列名")
    return best_col


def posts_metrics_to_excel_bytes(rows: List[dict], start_date: str, end_date: str) -> bytes:
    """Filter posts by post_date and write core columns."""
    if not rows:
        empty = pd.DataFrame(
            columns=[
                "post_url", "platform", "author", "post_date", "post_content",
                "likes", "comments_count", "shares", "views", "engagement", "thumbnail_url",
            ]
        )
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            empty.to_excel(writer, sheet_name="posts", index=False)
        buf.seek(0)
        return buf.read()
    df = pd.DataFrame(rows)
    if "post_date" in df.columns:
        df["post_date"] = df["post_date"].astype(str).str[:10]
        df = df[(df["post_date"] >= start_date) & (df["post_date"] <= end_date)]
    cols = [
        "post_url", "platform", "author", "post_date", "post_content",
        "likes", "comments_count", "shares", "views", "engagement", "thumbnail_url",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="posts", index=False)
    buf.seek(0)
    return buf.read()


def read_urls_from_excel(file_bytes: bytes, url_column: Optional[str]) -> List[str]:
    df = pd.read_excel(io.BytesIO(file_bytes))
    col = url_column or "post_url"
    if col not in df.columns:
        # try common names
        for c in ("url", "link", "帖子链接", "URL", "permalink"):
            if c in df.columns:
                col = c
                break
        else:
            raise ValueError("未找到链接列，请使用 post_url 或指定列名")
    urls = []
    seen = set()
    for v in df[col].astype(str):
        s = (v or "").strip()
        if not s or s == "nan":
            continue
        if s.lower().startswith("http") and s not in seen:
            seen.add(s)
            urls.append(s)
    return urls


def comments_to_excel_bytes(
    comment_rows: List[dict],
    beijing_tz_name: str = "Asia/Shanghai",
) -> bytes:
    """
    comment_rows: dicts with keys post_url, comment_id, author, content, created_at (datetime)
    Sheet1: detail. Sheet2: daily new comment count per post_url.
    """
    if not comment_rows:
        detail = pd.DataFrame(columns=["post_url", "comment_id", "author", "content", "created_at", "date"])
        pivot = pd.DataFrame(columns=["post_url", "date", "comment_count"])
    else:
        detail = pd.DataFrame(comment_rows)
        if "created_at" in detail.columns:
            ts = pd.to_datetime(detail["created_at"], errors="coerce", utc=True)
            ts = ts.dt.tz_convert(beijing_tz_name)
            detail["date"] = ts.dt.strftime("%Y-%m-%d")
            detail["created_at"] = ts.dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            detail["date"] = ""
        pivot = (
            detail.groupby(["post_url", "date"], as_index=False)
            .size()
            .rename(columns={"size": "comment_count"})
        )
        sort_cols = [c for c in ("post_url", "created_at") if c in detail.columns]
        if sort_cols:
            detail = detail.sort_values(sort_cols, na_position="last")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        detail.to_excel(writer, sheet_name="comments_detail", index=False)
        pivot.to_excel(writer, sheet_name="daily_new_comments", index=False)
    buf.seek(0)
    return buf.read()


def list_text_columns_preview(file_bytes: bytes) -> List[str]:
    df = pd.read_excel(io.BytesIO(file_bytes), nrows=0)
    return [str(c) for c in df.columns]
