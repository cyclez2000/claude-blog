# claude-blog — AI 操作说明

## 目的

监控 Anthropic/Claude 官方全渠道内容更新，自动抓取英文原文，本地 Ollama 全文翻译为中文日报。

## 架构

```
GitHub Actions (云端, UTC 08:00)         本地 (此仓库)
┌────────────────────────────────┐      ┌─────────────────────────────────┐
│ scripts/scrape.py              │ push │ git pull                        │
│  ├─ claude.com/blog            │ ───→ │ scripts/translate.py            │
│  ├─ claude.com/customers       │      │  ├─ Ollama hy1.5t 逐篇全文翻译  │
│  ├─ anthropic.com/engineering  │      │  └─ → output/daily/YYYY-MM-DD.md│
│  ├─ anthropic.com/research     │      │                                 │
│  ├─ anthropic.com/news         │      │ helpai.md (本文)                 │
│  └─ anthropic.com/economic     │      │ status.md (执行状态)             │
│                                 │      └─────────────────────────────────┘
│ → data/YYYY-MM-DD.json         │
└────────────────────────────────┘
```

## 数据文件

```
data/YYYY-MM-DD.json          # 英文原文（GitHub Actions 抓取，自动 git push）
output/daily/YYYY-MM-DD.md    # 中文全文翻译（本地 Ollama 翻译）
```

## 每日任务执行流程

### 前置条件
- Ollama 已启动且模型 `hy1.5t` 可用
- `ollama serve` 运行中

### 执行步骤

1. **拉取最新数据**
   ```bash
   git pull
   ```

2. **确认数据文件存在**
   - 检查 `data/YYYY-MM-DD.json`（当天日期）是否存在
   - 若不存在：说明 GitHub Actions 尚未执行或今日无更新
   - 可手动触发: 访问 GitHub Actions → "Daily Anthropic Content Scrape" → Run workflow

3. **执行翻译**
   ```bash
   python scripts/translate.py
   # 或指定日期:
   python scripts/translate.py --date 2026-05-25
   # 预览模式（不调 Ollama）:
   python scripts/translate.py --dry-run
   ```

4. **读取并汇报**
   - 读取 `output/daily/YYYY-MM-DD.md`
   - 向用户汇报今日更新摘要（渠道、篇数、核心主题）

5. **更新 status.md**
   - 记录执行日期、文章数量、异常情况

### 依赖安装（仅首次）

```bash
pip install -r scripts/requirements.txt
playwright install chromium
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama API 地址 |
| `OLLAMA_MODEL` | `hy1.5t` | 翻译模型名 |

## 抓取渠道

| # | 渠道 | URL | 方式 | 说明 |
|---|------|-----|------|------|
| 1 | Blog | claude.com/blog | Playwright | 产品公告、使用指南 |
| 2 | Customers | claude.com/customers | Playwright | 客户案例 |
| 3 | Engineering | anthropic.com/engineering | Playwright | 工程实践、系统设计 |
| 4 | Research | anthropic.com/research | HTTP | 学术论文、安全研究 |
| 5 | News | anthropic.com/news | HTTP | 新闻公告 |
| 6 | Economic | anthropic.com/economic-futures | HTTP | 经济影响研究 |

Status Page (`status.anthropic.com`) 有官方 RSS，建议直接订阅，不纳入本抓取。

## 翻译策略

- **全文翻译**，保留 Markdown 格式
- 技术术语保留英文（API、SDK、MCP 等）
- 产品名/公司名保留英文
- 长文章自动分段翻译后拼接

## 注意事项

- GitHub Actions 抓取有 1-2 天容错窗口（`lastmod` 取最近 3 天）
- `claude.com` 文章无 sitemap，依赖 Playwright 从列表页动态提取 URL
- 若 Playwright 未安装，Blog/Customers/Engineering 三个渠道会被跳过
- Ollama 翻译长文章需 1-3 分钟/篇，耐心等待
