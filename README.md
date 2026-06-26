# Sailson AI 工作台

多部门协作的 AI 舆情分析和竞品监控平台

## 功能特性

### 核心功能
- 🔍 **舆情分析** - Facebook 评论智能分类和情感分析
- 📊 **竞品监控** - TikTok 数据抓取和报表生成
- 🧑‍💼 **KOL 名单管理** - KOL 导入、筛选、编辑、导出和平台数据更新
- 👥 **用户管理** - 多用户、多部门权限管理
- 💰 **成本追踪** - 实时统计 API 消费和使用情况

### 技术栈
- **后端**: Flask + PostgreSQL
- **KOL 子服务**: FastAPI + SQLite/Render Disk + React/Vite
- **AI**: 阿里云通义千问 (qwen-turbo)
- **爬虫**: Apify (Facebook + TikTok)
- **部署**: Render

## 部署说明

### 环境变量配置

在 Render 控制台添加以下环境变量：

```
DASHSCOPE_API_KEY=sk-xxxxx
APIFY_TOKEN=apify_api_xxxxx
DATABASE_URL=postgresql://xxxxx
PORT=5001
SECRET_KEY=your_secret_key_here
```

主页视频定时同步到飞书多维表格时，额外配置：

```
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxx
FEISHU_BITABLE_APP_TOKEN=bascnxxxxx
FEISHU_BITABLE_TABLE_ID=tblxxxxx
PROFILE_VIDEO_SYNC_HOUR=9
PROFILE_VIDEO_MAX_VIDEOS=50
PROFILE_VIDEO_RECENT_DAYS=7
```

飞书表建议字段：视频唯一键、主页链接、平台、视频链接、作者、发布日期、视频文案、视频时长、播放量、点赞量、评论量、转发量、收藏量、互动量、主页粉丝数、最后同步时间。

每日达人主页视频自动化写入飞书四表时，配置：

```
FEISHU_VIDEO_BASE_TOKEN=WC84bWdlzacNumsp28scYugznei
FEISHU_VIDEO_CONFIG_TABLE_ID=tblSiuLqcbxWseBg
FEISHU_VIDEO_LATEST_TABLE_ID=tblp2I0sdiO6qWq4
FEISHU_VIDEO_SNAPSHOT_TABLE_ID=tblIGP3NEEebnOGS
FEISHU_VIDEO_LOG_TABLE_ID=tblif1prY2jT25k6
```

四表分别是：`达人主页配置`、`视频数据最新表`、`视频每日快照表`、`每日同步日志表`。业务同学维护配置表中的主页链接、启用状态、抓取小时、抓取范围、最大视频数；系统每小时检查一次，到点后抓取启用主页的视频数据并写入最新表、每日快照表和日志表。

### KOL 功能部署

KOL 功能在 Render 的主 Web 服务中随 Flask 一起启动：`start_render.sh` 会先在 `127.0.0.1:8001` 拉起 KOL FastAPI，再启动 Gunicorn。主 Flask 服务通过 `/kol-api` 代理访问它。

KOL 服务默认复用主站的 `DATABASE_URL`，因此会把 KOL 表建在同一个 PostgreSQL 数据库里。若要调用 Apify 更新 KOL 平台数据，请确保主服务里已配置 `APIFY_TOKEN`。

### 数据库初始化

首次部署后，运行：

```bash
python init_db.py
```

这将创建：
- 用户表 (users)
- 使用记录表 (usage_logs)
- 分析结果表 (analysis_results)
- 初始管理员账号 (admin / Admin@123)

### 默认账号

- **用户名**: admin
- **密码**: Admin@123
- **角色**: 管理员

⚠️ 首次登录后请立即修改密码！

## 使用说明

### 普通用户

1. 登录系统
2. 使用舆情分析或竞品监控功能
3. 查看"我的统计"了解个人使用情况

### 管理员

1. 访问"管理后台"
2. 查看全局统计数据
3. 管理用户（添加/删除）
4. 查看部门和用户消费排行

## 成本说明

### 舆情分析（每次）
- AI 成本: ~¥0.32 (500条评论)
- 爬虫成本: ~¥9.00 (500条评论 × $2.50/1000 × 7.2)
- **总计**: ~¥9.32/次

### 竞品监控（每次）
- AI 成本: ~¥0.04 (35条视频)
- 爬虫成本: ~¥0.93 (35条视频 × $3.70/1000 × 7.2)
- **总计**: ~¥0.97/次

## 部门设置

- 项目一组
- 项目二组
- 项目三组

## 开发者

Built with ❤️ by Sailson Team
Powered by Claude Sonnet 4.5
