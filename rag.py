"""
RAG (Retrieval-Augmented Generation) 模块
- 文本分块
- DashScope Embedding 调用
- 余弦相似度检索
- Prompt 组装
"""
import os
import json
import math
import logging
import requests
import database as db

logger = logging.getLogger(__name__)

DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
EMBEDDING_MODEL = 'text-embedding-v3'
EMBEDDING_DIM = 1024


# ============================================
# 建表（启动时调用）
# ============================================

def ensure_tables():
    """创建 RAG 相关表（幂等）"""
    sqls = [
        """
        CREATE TABLE IF NOT EXISTS corpus_documents (
            id SERIAL PRIMARY KEY,
            filename VARCHAR(512) NOT NULL,
            doc_type VARCHAR(64) NOT NULL DEFAULT 'general',
            project VARCHAR(32) NOT NULL DEFAULT 'CFL',
            uploaded_by INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS corpus_chunks (
            id SERIAL PRIMARY KEY,
            doc_id INTEGER REFERENCES corpus_documents(id) ON DELETE CASCADE,
            chunk_text TEXT NOT NULL,
            embedding TEXT,
            project VARCHAR(32) NOT NULL DEFAULT 'CFL',
            doc_type VARCHAR(64) NOT NULL DEFAULT 'general'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            mode VARCHAR(64) NOT NULL DEFAULT 'copywriting',
            project VARCHAR(32) NOT NULL DEFAULT 'CFL',
            title VARCHAR(256),
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            session_id INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
            role VARCHAR(16) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
    ]
    try:
        for sql in sqls:
            db.execute(sql)
        logger.info("✅ RAG 相关表已就绪")
    except Exception as e:
        logger.warning(f"⚠️ RAG 建表失败（数据库可能不可用）: {e}")


# ============================================
# 文本分块
# ============================================

def chunk_text(text, chunk_size=500, overlap=50):
    """按字符数分块，相邻块有 overlap 字重叠。"""
    text = text.strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


# ============================================
# Embedding
# ============================================

def get_embedding(text):
    """调用 DashScope text-embedding API，返回向量列表（float list）。"""
    if not DASHSCOPE_API_KEY:
        logger.error("❌ DASHSCOPE_API_KEY 未配置，无法生成 embedding")
        return None

    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": text[:8000],
        "dimensions": EMBEDDING_DIM,
        "encoding_format": "float",
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]
    except Exception as e:
        err_detail = getattr(e, "response", None)
        if err_detail is not None and hasattr(err_detail, "text"):
            logger.error(f"❌ Embedding 调用失败: {e} | 响应: {err_detail.text[:500]}")
        else:
            logger.error(f"❌ Embedding 调用失败: {e}")
        return None


def get_embeddings_batch(texts, batch_size=10):
    """批量获取 embedding，返回与 texts 等长的向量列表。DashScope 兼容接口单次最多 10 条。"""
    results = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        if not DASHSCOPE_API_KEY:
            results.extend([None] * len(batch))
            continue

        url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": EMBEDDING_MODEL,
            "input": [t[:8000] for t in batch],
            "dimensions": EMBEDDING_DIM,
            "encoding_format": "float",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            for item in sorted(data["data"], key=lambda x: x["index"]):
                results.append(item["embedding"])
        except Exception as e:
            err_detail = getattr(e, "response", None)
            if err_detail is not None and hasattr(err_detail, "text"):
                logger.error(f"❌ 批量 Embedding 调用失败: {e} | 响应: {err_detail.text[:500]}")
            else:
                logger.error(f"❌ 批量 Embedding 调用失败: {e}")
            results.extend([None] * len(batch))
    return results


# ============================================
# 相似度检索
# ============================================

def _cosine_similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def search_similar(query_embedding, project, doc_type=None, top_k=5):
    """
    从 corpus_chunks 中检索与 query_embedding 最相似的 Top-K 片段。
    doc_type 为 None 时检索该 project 下所有类型。
    返回 [{'chunk_text': ..., 'score': ...}, ...]
    """
    if not query_embedding:
        return []

    sql = "SELECT chunk_text, embedding FROM corpus_chunks WHERE project = %s"
    params = [project]
    if doc_type:
        sql += " AND doc_type = %s"
        params.append(doc_type)

    try:
        rows = db.query_all(sql, tuple(params))
    except Exception as e:
        logger.error(f"❌ 检索语料失败: {e}")
        return []

    scored = []
    for row in rows:
        emb_json = row.get('embedding')
        if not emb_json:
            continue
        try:
            emb = json.loads(emb_json)
        except (json.JSONDecodeError, TypeError):
            continue
        score = _cosine_similarity(query_embedding, emb)
        scored.append({'chunk_text': row['chunk_text'], 'score': score})

    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:top_k]


# ============================================
# Prompt 组装
# ============================================

def build_rag_prompt(system_template, retrieved_chunks, chat_history, user_message):
    """
    组装完整的 messages 列表，用于发给通义千问。
    system_template 中可含 {retrieved_context} 和 {user_message} 占位符。
    chat_history: [{'role': 'user'|'assistant', 'content': '...'}, ...]
    """
    context_parts = []
    for i, chunk in enumerate(retrieved_chunks, 1):
        context_parts.append(f"[参考{i}] {chunk['chunk_text']}")
    retrieved_context = "\n\n".join(context_parts) if context_parts else "（暂无参考语料，请根据你的专业知识回答）"

    system_content = system_template.replace("{retrieved_context}", retrieved_context)

    messages = [{"role": "system", "content": system_content}]
    for msg in chat_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_message})

    return messages


# ============================================
# 文件解析
# ============================================

def parse_file(filename, content_bytes):
    """
    解析上传的文件，返回纯文本字符串。
    支持 .txt, .pdf, .docx, .xlsx
    """
    fname = filename.lower()

    if fname.endswith('.txt'):
        for enc in ('utf-8', 'gbk', 'gb2312', 'latin-1'):
            try:
                return content_bytes.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return content_bytes.decode('utf-8', errors='replace')

    if fname.endswith('.pdf'):
        try:
            import io
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(content_bytes))
            pages = [page.extract_text() or '' for page in reader.pages]
            return "\n".join(pages)
        except ImportError:
            logger.warning("⚠️ PyPDF2 未安装，跳过 PDF 解析")
            return ""
        except Exception as e:
            logger.error(f"❌ PDF 解析失败: {e}")
            return ""

    if fname.endswith('.docx'):
        try:
            import io
            import zipfile
            import xml.etree.ElementTree as ET
            zf = zipfile.ZipFile(io.BytesIO(content_bytes))
            xml_content = zf.read('word/document.xml')
            tree = ET.fromstring(xml_content)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            paragraphs = tree.findall('.//w:p', ns)
            texts = []
            for p in paragraphs:
                runs = p.findall('.//w:t', ns)
                texts.append(''.join(r.text or '' for r in runs))
            return "\n".join(texts)
        except Exception as e:
            logger.error(f"❌ DOCX 解析失败: {e}")
            return ""

    if fname.endswith('.xlsx') or fname.endswith('.xls'):
        try:
            import io
            import pandas as pd
            df = pd.read_excel(io.BytesIO(content_bytes))
            return df.to_string(index=False)
        except Exception as e:
            logger.error(f"❌ Excel 解析失败: {e}")
            return ""

    return ""


# ============================================
# 语料入库（上传流程）
# ============================================

def ingest_document(filename, content_bytes, project, doc_type, user_id):
    """
    完整的语料入库流程：解析文件 -> 分块 -> embedding -> 写入数据库。
    返回 (doc_id, chunk_count) 或 (None, error_msg)。
    """
    text = parse_file(filename, content_bytes)
    if not text or not text.strip():
        return None, "文件内容为空或无法解析"

    chunks = chunk_text(text)
    if not chunks:
        return None, "分块后无内容"

    logger.info(f"📄 {filename}: 解析得到 {len(chunks)} 个分块，开始 embedding...")

    embeddings = get_embeddings_batch(chunks)

    try:
        doc_id = db.execute_and_fetch_id(
            """
            INSERT INTO corpus_documents (filename, doc_type, project, uploaded_by)
            VALUES (%s, %s, %s, %s) RETURNING id
            """,
            (filename, doc_type, project, user_id)
        )
    except Exception as e:
        return None, f"写入文档记录失败: {e}"

    inserted = 0
    for chunk_text_item, emb in zip(chunks, embeddings):
        emb_json = json.dumps(emb) if emb else None
        try:
            db.execute(
                """
                INSERT INTO corpus_chunks (doc_id, chunk_text, embedding, project, doc_type)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (doc_id, chunk_text_item, emb_json, project, doc_type)
            )
            inserted += 1
        except Exception as e:
            logger.error(f"❌ 写入分块失败: {e}")

    logger.info(f"✅ 语料入库完成: doc_id={doc_id}, 共 {inserted}/{len(chunks)} 块")
    return doc_id, inserted
