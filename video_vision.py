import os
import logging
import time
import tempfile
import base64
from typing import List, Dict, Tuple

import requests
import cv2
from openai import OpenAI

logger = logging.getLogger(__name__)

DASHSCOPE_API_KEY = os.environ.get("DASHSCOPE_API_KEY")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

if DASHSCOPE_API_KEY:
    try:
        _vision_client = OpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        logger.info("✅ Qwen-VL 视觉模型客户端初始化成功")
    except Exception as e:
        logger.error(f"❌ 初始化 Qwen-VL 客户端失败: {e}")
        _vision_client = None
else:
    logger.warning("⚠️ DASHSCOPE_API_KEY 未配置，视频视觉分析不可用")
    _vision_client = None


def _run_tiktok_video_downloader(video_urls: List[str]) -> List[Dict]:
    """调用 Apify api-ninja/tiktok-video-downloader，返回 dataset items 列表。

    使用 run-sync-get-dataset-items 端点，直接拿到结果。
    """
    if not APIFY_TOKEN:
        logger.error("❌ APIFY_TOKEN 未配置，无法运行 TikTok Video Downloader")
        return []

    if not video_urls:
        return []

    api_url = "https://api.apify.com/v2/acts/api-ninja~tiktok-video-downloader/run-sync-get-dataset-items"
    params = {"token": APIFY_TOKEN}
    payload = {
        "videoUrls": video_urls,
        "format": "Video",
        "ttl": "none",
    }

    try:
        logger.info(f"🎬 调用 TikTok Video Downloader，视频数: {len(video_urls)}")
        start = time.time()
        resp = requests.post(api_url, params=params, json=payload, timeout=300)
        resp.raise_for_status()
        data = resp.json()
        elapsed = time.time() - start
        logger.info(f"✅ TikTok Video Downloader 完成，耗时 {elapsed:.1f}s")

        # 返回可能是数组，也可能包装在 items 字段里
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "items" in data and isinstance(data["items"], list):
                return data["items"]
            # 有些 actor 直接把 dataset items 放在 data 字段
            if "data" in data and isinstance(data["data"], list):
                return data["data"]
        return []
    except Exception as e:
        logger.error(f"❌ 调用 TikTok Video Downloader 失败: {e}")
        return []


def _build_input_to_storage_map(items: List[Dict]) -> Dict[str, str]:
    """将 actor 输出转换为 inputUrl -> storageUrl 映射。"""
    mapping: Dict[str, str] = {}
    for it in items or []:
        input_url = it.get("inputUrl") or it.get("url")
        # storageUrl 可能在顶层，也可能嵌套在 data 字段中
        storage_url = it.get("storageUrl") or it.get("play")
        if not storage_url:
            data = it.get("data")
            if isinstance(data, dict):
                storage_url = data.get("play") or data.get("wmplay") or data.get("hdplay")
            elif isinstance(data, str):
                # data 可能是字符串形式的字典
                try:
                    import ast
                    parsed = ast.literal_eval(data)
                    if isinstance(parsed, dict):
                        storage_url = parsed.get("play") or parsed.get("wmplay") or parsed.get("hdplay")
                except Exception:
                    pass
        if input_url and storage_url:
            mapping[input_url] = storage_url
        else:
            logger.warning(f"⚠️ 映射失败: inputUrl={input_url}, storageUrl={storage_url}")
    logger.info(f"📦 视频直链映射构建完成，共 {len(mapping)} 条")
    return mapping


def _download_video(storage_url: str) -> str:
    """下载视频到临时文件，返回文件路径。失败返回空字符串。"""
    try:
        logger.info(f"⬇️ 下载视频: {storage_url}")
        resp = requests.get(storage_url, stream=True, timeout=60)
        resp.raise_for_status()
        suffix = ".mp4"
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        logger.info(f"✅ 视频已下载到临时文件: {path}")
        return path
    except Exception as e:
        logger.error(f"❌ 下载视频失败: {e}")
        return ""


def _extract_frames(video_path: str, max_frames: int = 5) -> List[bytes]:
    """从视频中按时间均匀抽帧，返回 JPEG base64 字节列表。"""
    frames_b64: List[bytes] = []
    if not os.path.exists(video_path):
        logger.warning(f"⚠️ 视频文件不存在: {video_path}")
        return frames_b64

    cap = cv2.VideoCapture(video_path)
    try:
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frame_count <= 0:
            logger.warning("⚠️ 无法获取视频帧数")
            return frames_b64

        # 选择若干关键帧（避开首尾）
        step = max(frame_count // (max_frames + 1), 1)
        indices = [(i + 1) * step for i in range(max_frames)]

        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            ok, buf = cv2.imencode(".jpg", frame)
            if not ok:
                continue
            frames_b64.append(base64.b64encode(buf.tobytes()))

        logger.info(f"🎞 共抽取 {len(frames_b64)} 帧")
        return frames_b64
    finally:
        cap.release()


def _classify_video_with_vision(frames_b64: List[bytes], desc: str, project: str) -> Dict:
    """调用 Qwen-VL 对多帧截图判断营销动作类型，返回 {"type": "...", "summary": "..."}。"""
    if not _vision_client:
        logger.warning("⚠️ Qwen-VL 客户端不可用，跳过视觉分析")
        return {}

    if not frames_b64:
        return {}

    frames_b64 = frames_b64[:4]

    import json as _json

    project_name = project or "CFL"
    user_text = (
        f"你是一名竞品情报分析师，正在为项目「{project_name}」做竞品监控。\n"
        f"以下是同一条 TikTok 视频的多帧截图。\n\n"
        f"请完成以下判断：\n"
        f"1. 营销类型（type）：从以下类型中选择最匹配的一个：\n"
        f"   游戏实录、赛事预热、版本更新预告、KOL合作/开箱、福利/抽奖活动、品牌形象宣传、UGC互动征集、其他\n"
        f"2. 情绪分类（emotion）：从以下类型中选择最匹配的一个：\n"
        f"   有趣好玩、荣誉关怀、好奇炫酷、抽象类\n"
        f"3. 目标用户（target_user）：从以下类型中选择最匹配的一个：\n"
        f"   新进用户、大盘活跃用户、回流用户\n"
        f"4. 简述（summary）：用 1-2 句话概括该视频的主要内容（不超过 50 字）。\n\n"
        f"仅输出 JSON，格式：{{\"type\":\"营销类型\",\"emotion\":\"情绪分类\",\"target_user\":\"目标用户\",\"summary\":\"简述\"}}\n"
        f"不要输出任何其他文字。\n\n"
        f"原始视频文案（仅供参考）：{desc[:200]}\n"
    )

    content: List[Dict] = [{"type": "text", "text": user_text}]
    for b64 in frames_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64," + b64.decode("utf-8")},
        })

    try:
        logger.info("👀 调用 Qwen-VL 进行视频营销类型判断")
        resp = _vision_client.chat.completions.create(
            model="qwen3-vl-plus",
            messages=[{"role": "user", "content": content}],
            temperature=0.3,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # 清理 Qwen3 的 <think>...</think> 思考过程
        import re
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        # 清理可能的 markdown 包裹
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        logger.info(f"🔍 Qwen-VL 原始返回（清理后）: {raw[:200]}")
        result = _json.loads(raw)
        if isinstance(result, dict) and "type" in result:
            return result
        logger.warning(f"⚠️ Qwen-VL 返回格式异常: {raw}")
        return {}
    except Exception as e:
        logger.error(f"❌ 调用 Qwen-VL 失败: {e}")
        return {}


def analyze_all_videos_for_export(
    cleaned_videos: List[Dict], project: str = "CFL"
) -> List[Dict]:
    """对所有视频做 VL 分析，返回包含情绪/目标用户/简述/关键帧的完整数据列表。

    返回：[{
        "emotion": "有趣好玩",
        "target_user": "新进用户",
        "summary": "视频内容描述",
        "type": "营销类型",
        "author": "发布账号",
        "url": "视频链接",
        "thumbnail_b64": "关键帧 base64 JPEG",
        "views": 播放量,
        "date": "发布日期"
    }, ...]
    """
    if not cleaned_videos:
        return []

    videos = [v for v in cleaned_videos if v.get("url")]
    if not videos:
        return []

    # 不限制数量，处理所有视频
    input_urls = [v["url"] for v in videos]

    # 批量获取视频直链（Apify 支持批量）
    items = _run_tiktok_video_downloader(input_urls)
    mapping = _build_input_to_storage_map(items)
    if not mapping:
        logger.warning("⚠️ 未获取到任何视频直链，跳过全量视频分析")
        return []

    results: List[Dict] = []

    for v in videos:
        page_url = v["url"]
        storage_url = mapping.get(page_url)
        if not storage_url:
            continue

        tmp_video = _download_video(storage_url)
        if not tmp_video:
            continue

        try:
            frames_b64 = _extract_frames(tmp_video, max_frames=4)
            classification = _classify_video_with_vision(
                frames_b64, v.get("desc") or "", project
            )
            if not classification:
                continue

            # 选取中间帧作为参考图缩略图
            thumbnail_b64 = ""
            if frames_b64:
                mid = len(frames_b64) // 2
                thumbnail_b64 = frames_b64[mid].decode("utf-8") if isinstance(frames_b64[mid], bytes) else frames_b64[mid]

            results.append({
                "emotion": classification.get("emotion", "其他"),
                "target_user": classification.get("target_user", "未知"),
                "summary": classification.get("summary", ""),
                "type": classification.get("type", "其他"),
                "author": v.get("author", "未知"),
                "url": page_url,
                "thumbnail_b64": thumbnail_b64,
                "views": v.get("views", 0),
                "date": v.get("date", ""),
            })
        finally:
            try:
                if os.path.exists(tmp_video):
                    os.remove(tmp_video)
            except Exception:
                pass

    logger.info(f"✅ 全量视频分析完成，共 {len(results)} 条结果")
    return results


def _build_vision_html_from_results(video_results: List[Dict]) -> Tuple[str, str]:
    """从全量分析结果构建 HTML 表格和文本摘要。

    表头：情绪 | 目标用户 | 简述 | 发布账号 | 参考图 | 链接
    按情绪分类排序，同情绪的视频相邻，第一列用 rowspan 合并。
    """
    if not video_results:
        return "", ""

    from collections import OrderedDict

    # 按情绪分组
    groups: Dict[str, List[Dict]] = OrderedDict()
    sorted_results = sorted(video_results, key=lambda x: x.get("emotion", "其他"))
    for vr in sorted_results:
        e = vr.get("emotion", "其他")
        if e not in groups:
            groups[e] = []
        groups[e].append(vr)

    rows_html: List[str] = []
    text_lines: List[str] = []
    td_style = 'padding:12px 10px; border-bottom:1px solid #F1F3F5; font-size:0.9rem; vertical-align:middle;'

    for emotion, members in groups.items():
        count = len(members)
        for i, m in enumerate(members):
            thumb_html = ''
            if m.get('thumbnail_b64'):
                thumb_html = f'<img src="data:image/jpeg;base64,{m["thumbnail_b64"]}" style="width:120px; height:auto; border-radius:4px;">'

            link_html = f'<a href="{m.get("url", "#")}" target="_blank" style="color:#D32F2F; text-decoration:none;">查看视频</a>' if m.get("url") else ''

            if i == 0:
                rows_html.append(f"""
                <tr>
                    <td rowspan="{count}" style="{td_style} font-weight:600; text-align:center;">{emotion}</td>
                    <td style="{td_style} text-align:center;">{m.get('target_user', '未知')}</td>
                    <td style="{td_style}">{m.get('summary', '')}</td>
                    <td style="{td_style} text-align:center;">{m.get('author', '未知')}</td>
                    <td style="{td_style} text-align:center;">{thumb_html}</td>
                    <td style="{td_style} text-align:center;">{link_html}</td>
                </tr>""")
            else:
                rows_html.append(f"""
                <tr>
                    <td style="{td_style} text-align:center;">{m.get('target_user', '未知')}</td>
                    <td style="{td_style}">{m.get('summary', '')}</td>
                    <td style="{td_style} text-align:center;">{m.get('author', '未知')}</td>
                    <td style="{td_style} text-align:center;">{thumb_html}</td>
                    <td style="{td_style} text-align:center;">{link_html}</td>
                </tr>""")

        summaries = "; ".join(m.get("summary", "") for m in members if m.get("summary"))
        text_lines.append(f"【{emotion}】({count}条) {summaries}")

    th_style = 'padding:12px 10px; text-align:center; color:#666; font-weight:600; border-bottom:2px solid #EEE;'
    section_html = f"""
<div style="margin-top:30px;">
    <h3 style="color:#D32F2F; border-bottom:2px solid #eee; padding-bottom:10px; margin-bottom:10px;">
        🎬 竞品视频情绪分类总览（共 {len(video_results)} 条视频）
    </h3>
    <table style="width:100%; border-collapse:collapse; margin:15px 0; border:1px solid #eee; border-radius:10px; overflow:hidden; font-size:0.9rem;">
        <thead>
            <tr style="background:#f8f9fa;">
                <th style="{th_style} width:100px;">情绪</th>
                <th style="{th_style} width:110px;">目标用户</th>
                <th style="{th_style}">简述</th>
                <th style="{th_style} width:110px;">发布账号</th>
                <th style="{th_style} width:140px;">参考图</th>
                <th style="{th_style} width:80px;">链接</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows_html)}
        </tbody>
    </table>
</div>
""".strip()

    text_block = "\n".join(text_lines)
    return section_html, text_block


def get_video_vision_section(
    cleaned_videos: List[Dict], project: str = "CFL", top_k: int = 5
) -> Tuple[str, str]:
    """对播放量 Top K 的视频做「看视频」分析。

    返回 (html_section, text_summaries)：
    - html_section: 按营销类型归类的总结表 HTML
    - text_summaries: 按类型归总的纯文本摘要（可传给 AI 报告模板）
    """
    if not cleaned_videos:
        return "", ""

    videos = [v for v in cleaned_videos if v.get("url")]
    if not videos:
        return "", ""

    videos = sorted(videos, key=lambda x: x.get("views", 0), reverse=True)[:top_k]
    input_urls = [v["url"] for v in videos]

    items = _run_tiktok_video_downloader(input_urls)
    mapping = _build_input_to_storage_map(items)
    if not mapping:
        logger.warning("⚠️ 未获取到任何视频直链，跳过看视频分析")
        return "", ""

    # 收集每条视频的分类结果
    video_results: List[Dict] = []

    for v in videos:
        page_url = v["url"]
        storage_url = mapping.get(page_url)
        if not storage_url:
            continue

        tmp_video = _download_video(storage_url)
        if not tmp_video:
            continue

        try:
            frames_b64 = _extract_frames(tmp_video, max_frames=4)
            classification = _classify_video_with_vision(frames_b64, v.get("desc") or "", project)
            if not classification:
                continue

            video_results.append({
                "type": classification.get("type", "其他"),
                "summary": classification.get("summary", ""),
                "url": page_url,
                "views": v.get("views", 0),
            })
        finally:
            try:
                if os.path.exists(tmp_video):
                    os.remove(tmp_video)
            except Exception:
                pass

    if not video_results:
        return "", ""

    # 按营销类型分组
    from collections import OrderedDict
    groups: Dict[str, List[Dict]] = OrderedDict()
    for vr in video_results:
        t = vr["type"]
        if t not in groups:
            groups[t] = []
        groups[t].append(vr)

    # 构建 HTML 归类表
    rows_html: List[str] = []
    text_lines: List[str] = []

    for type_name, members in groups.items():
        count = len(members)
        links = ", ".join(
            f'<a href="{m["url"]}" target="_blank" style="color:#D32F2F; text-decoration:none;">查看</a>'
            for m in members
        )
        # 用该类型下所有视频的 summary 合并为策略总结
        summaries = "; ".join(m["summary"] for m in members if m["summary"])
        if not summaries:
            summaries = type_name

        rows_html.append(f"""
        <tr>
            <td style="padding:12px 10px; border-bottom:1px solid #F1F3F5; font-weight:600; font-size:0.9rem;">{type_name}</td>
            <td style="padding:12px 10px; border-bottom:1px solid #F1F3F5; text-align:center; font-size:0.9rem;">{count}</td>
            <td style="padding:12px 10px; border-bottom:1px solid #F1F3F5; font-size:0.85rem;">{links}</td>
            <td style="padding:12px 10px; border-bottom:1px solid #F1F3F5; font-size:0.9rem;">{summaries}</td>
        </tr>
        """)
        text_lines.append(f"【{type_name}】({count}条) {summaries}")

    section_html = f"""
<div style="margin-top:30px;">
    <h3 style="color:#D32F2F; border-bottom:2px solid #eee; padding-bottom:10px; margin-bottom:10px;">
        🎬 竞品本周宣发动作归类（Top {len(video_results)} 热门视频）
    </h3>
    <table style="width:100%; border-collapse:collapse; margin:15px 0; border:1px solid #eee; border-radius:10px; overflow:hidden; font-size:0.9rem;">
        <thead>
            <tr style="background:#f8f9fa;">
                <th style="padding:12px 10px; text-align:left; color:#666; font-weight:600; border-bottom:2px solid #EEE; width:140px;">营销类型</th>
                <th style="padding:12px 10px; text-align:center; color:#666; font-weight:600; border-bottom:2px solid #EEE; width:60px;">条数</th>
                <th style="padding:12px 10px; text-align:left; color:#666; font-weight:600; border-bottom:2px solid #EEE; width:160px;">代表视频</th>
                <th style="padding:12px 10px; text-align:left; color:#666; font-weight:600; border-bottom:2px solid #EEE;">策略总结</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows_html)}
        </tbody>
    </table>
</div>
""".strip()

    text_block = "\n".join(text_lines)
    return section_html, text_block

