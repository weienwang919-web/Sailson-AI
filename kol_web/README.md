# KOL List Manager

KOL 名单维护系统。当前作为 Sailson AI 工作台的子功能部署，使用 **FastAPI + SQLite + React/Vite**：

- 中英混排 KOL 看板
- 列筛选 + 高级筛选
- 单元格在线编辑保存
- Excel 上传导入，按平台 Link 去重
- 勾选导出，可选导出前调用 Apify 更新 Follower / AVV
- 导出优先还原 HOK 原表版式（分 Sheet、双行表头）

## 接入主站

- 主站入口：`/kol-tool`
- 主站代理：`/kol-api/*`
- 后端真实路由：`/api/*`
- Render 服务：`flaskproject-kol` 私有服务

前端构建产物输出到主项目的 `static/kol/`，由 Flask 主站托管。

## 本地启动

### 后端

```bash
cd kol_web/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 如果需要 Apify 更新，编辑 .env 填 APIFY_TOKEN
uvicorn app.main:app --reload --port 8001
```

首次导入三份现有 Excel：

```bash
cd kol_web/backend
source .venv/bin/activate
python seed_import.py
```

### 前端

```bash
cd kol_web/frontend
npm install
npm run dev
```

浏览器打开主站 `/kol-tool`，或单独调试前端 `http://127.0.0.1:5173`。

## 数据规则

- 数据库存储为规范化结构：1 行 = 1 个 KOL 记录。
- 去重只按平台 Link：同名不同类目可以共存。
- Apify 只更新：
  - TikTok / YouTube：`Follower`、`AVV`
  - Instagram：`Follower`
- 报价列不被 Apify 覆盖，报价只来自 Excel 导入或页面在线编辑。

## 支持的 Excel

当前导入器支持：

- `/Users/brucewayne/Downloads/【Sailson】HOK_印尼站KOL名单_0520.xlsx`
- `/Users/brucewayne/Downloads/list/list.xlsx`
- `/Users/brucewayne/Downloads/Sailson SMS_KOL List.xlsx`

HOK 双行表头会保存为导出版式模板；导出时按 Category 分 Sheet 还原。
