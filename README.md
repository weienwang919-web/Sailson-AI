# Sailson AI 工作台

多部门协作的 AI 舆情分析和竞品监控平台

## 功能特性

### 核心功能
- 🔍 **舆情分析** - Facebook 评论智能分类和情感分析
- 📊 **竞品监控** - TikTok 数据抓取和报表生成
- 👥 **用户管理** - 多用户、多部门权限管理
- 💰 **成本追踪** - 实时统计 API 消费和使用情况

### 技术栈
- **后端**: Flask + PostgreSQL
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
