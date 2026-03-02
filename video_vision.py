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
        resp = requests.post(api_url, params=params, json=payload, timeout=120)
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
        storage_url = it.get("storageUrl") or it.get("play")
        if input_url and storage_url:
            mapping[input_url] = storage_url
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


def _describe_video_with_vision(frames_b64: List[bytes], desc: str, project: str) -> str:
    """调用 Qwen-VL 对多帧截图做画面总结，返回中文描述。"""
    if not _vision_client:
        logger.warning("⚠️ Qwen-VL 客户端不可用，跳过视觉分析")
        return ""

    if not frames_b64:
        return ""

    # 只取前 4 帧，避免 prompt 太大
    frames_b64 = frames_b64[:4]

    project_name = project or "CFL"
    user_text = (
        f"你是一名短视频内容分析师，正在为项目「{project_name}」做竞品监控。\n"
        f"以下是同一条 TikTok 视频的多帧截图，请基于画面内容，用中文输出「视频画面总结」，要求：\n"
        f"1）先用 1 句话概括整体画面风格与核心信息；\n"
        f"2）再用 2-3 句话说明镜头中出现的关键元素（人物/场景/字幕/特效等）以及节奏感；\n"
        f"3）最后补充 1 句话，说明该视频更适合用于什么类型的营销场景（如：新品曝光、活动预热、福利派发、品牌形象等）。\n"
        f"如果你能从画面中推测出这条视频的脚本结构，也可以简单点出（如：开场钩子-亮点展示-福利收尾）。\n"
        f"原始视频文案/描述（可能为空，仅供参考）：{desc[:200]}\n"
    )

    content = [
        {"type": "text", "text": user_text},
    ]
    for b64 in frames_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:image/jpeg;base64," + b64.decode("utf-8")
                },
            }
        )

    try:
        logger.info("👀 调用 Qwen-VL 进行视频画面总结")
        resp = _vision_client.chat.completions.create(
            model="qwen3-vl-plus",
            messages=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
            temperature=0.4,
        )
        result = resp.choices[0].message.content
        return (result or "").strip()
    except Exception as e:
        logger.error(f"❌ 调用 Qwen-VL 失败: {e}")
        return ""


def get_video_vision_section(
    cleaned_videos: List[Dict], project: str = "CFL", top_k: int = 5
) -> Tuple[str, str]:
    """对播放量 Top K 的视频做「看视频」分析，返回 (html_section, text_summaries)。"""
    if not cleaned_videos:
        return "", ""

    # 只保留有 URL 的视频
    videos = [v for v in cleaned_videos if v.get("url")]
    if not videos:
        return "", ""

    # 按播放量排序，取 Top K
    videos = sorted(videos, key=lambda x: x.get("views", 0), reverse=True)[:top_k]
    input_urls = [v["url"] for v in videos]

    items = _run_tiktok_video_downloader(input_urls)
    mapping = _build_input_to_storage_map(items)
    if not mapping:
        logger.warning("⚠️ 未获取到任何视频直链，跳过看视频分析")
        return "", ""

    cards_html: List[str] = []
    summaries_text: List[str] = []

    for idx, v in enumerate(videos, start=1):
        page_url = v["url"]
        storage_url = mapping.get(page_url)
        if not storage_url:
            logger.warning(f"⚠️ 未找到直链，跳过视频: {page_url}")
            continue

        tmp_video = _download_video(storage_url)
        if not tmp_video:
            continue

        try:
            frames_b64 = _extract_frames(tmp_video, max_frames=5)
            summary = _describe_video_with_vision(frames_b64, v.get("desc") or "", project)
            if not summary:
                continue

            # 用第一帧作为封面
            cover_b64 = frames_b64[0].decode("utf-8") if frames_b64 else ""

            views = v.get("views", 0)
            desc = v.get("desc") or "无描述"

            card_html = f"""
            <div style="display:flex; gap:16px; align-items:flex-start; padding:12px; border-radius:10px; background:#FFF9F9; border:1px solid #F5C1C1; margin-bottom:12px;">
                <div style="flex-shrink:0;">
                    {'<img src="data:image/jpeg;base64,' + cover_b64 + '" style="width:180px; border-radius:8px; object-fit:cover;" />' if cover_b64 else ''}
                </div>
                <div style="flex:1;">
                    <p style="margin-bottom:4px;"><strong>视频 {idx} · 播放 {views}</strong></p>
                    <p style="margin-bottom:4px; font-size:0.85rem; color:#777;">原始描述：{desc}</p>
                    <p style="margin-bottom:6px; font-size:0.9rem; color:#333; white-space:pre-wrap;">{summary}</p>
                    <p style="margin-bottom:0; font-size:0.85rem;">
                        <a href="{page_url}" target="_blank" style="color:#D32F2F; text-decoration:none;">🔗 查看 TikTok 原视频</a>
                    </p>
                </div>
            </div>
            """
            cards_html.append(card_html)
            summaries_text.append(f"【视频{idx}，播放 {views}】{summary}")
        finally:
            try:
                if os.path.exists(tmp_video):
                    os.remove(tmp_video)
            except Exception:
                pass

    if not cards_html:
        return "", ""

    section_html = f"""
    <div style="margin-top:30px;">
        <h3 style="color:#D32F2F; border-bottom:2px solid #eee; padding-bottom:10px; margin-bottom:10px;">
            🎬 视频画面总结（Top {len(cards_html)} 热门视频）
        </h3>
        <div>
            {''.join(cards_html)}
        </div>
    </div>
    """
    section_html = section_html.strip()
    text_block = "\n".join(summaries_text)
    return section_html, text_block

