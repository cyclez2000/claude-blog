#!/usr/bin/env python3
"""Ollama 全文翻译脚本。

读取 data/ 目录最新抓取结果，调用本地 Ollama (hy1.5t) 逐篇翻译，
输出: output/daily/YYYY-MM-DD.md（中文日报）。

用法:
    python scripts/translate.py                    # 翻译今天的数据
    python scripts/translate.py --date 2026-05-25  # 翻译指定日期
    python scripts/translate.py --date latest      # 翻译最新数据
    python scripts/translate.py --dry-run          # 预览，不调 Ollama
"""

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests

# --- 配置 ---
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
OUTPUT_DIR = ROOT_DIR / "output" / "daily"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OLLAMA_BASE = "http://localhost:11434"
OLLAMA_MODEL = "hy1.5t"

# 翻译 System Prompt
SYSTEM_PROMPT = """You are a professional English-to-Chinese translator specializing in AI/tech content.

Translate the following article into Simplified Chinese (zh-CN).

Rules:
1. Keep ALL Markdown formatting intact (headings, lists, code blocks, links, tables).
2. Keep technical terms in English: API, SDK, MCP, CLI, GPU, REST, JSON, etc.
3. Keep product names in English: Claude, Claude Code, Claude Cowork, Opus, Sonnet, Haiku, etc.
4. Keep company names in English: Anthropic, AWS, Google, Microsoft, etc.
5. Preserve ALL URLs and image references exactly.
6. Translate naturally and professionally — not word-for-word.
7. Maintain the original paragraph structure and line breaks.
8. Do NOT add any commentary, notes, or meta-text — output ONLY the translated text."""

# 渠道中文名
CHANNEL_NAMES_ZH = {
    "blog": "官方博客",
    "customers": "客户案例",
    "engineering": "工程博客",
    "research": "研究报告",
    "news": "新闻动态",
    "economic": "经济研究",
}

# 翻译分块阈值（字符数，超过则分段翻译）
CHUNK_SIZE = 4000


def find_latest_data() -> Path | None:
    """找到最新的数据文件。"""
    files = sorted(DATA_DIR.glob("*.json"), reverse=True)
    return files[0] if files else None


def find_data_by_date(target_date: str) -> Path | None:
    """按日期找到数据文件。"""
    f = DATA_DIR / f"{target_date}.json"
    return f if f.exists() else None


def translate_chunk(text: str, retries: int = 2) -> str:
    """调用 Ollama 翻译一段文本。"""
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{OLLAMA_BASE}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    "stream": False,
                },
                timeout=120,
            )
            resp.raise_for_status()
            result = resp.json()
            return result["message"]["content"].strip()
        except requests.exceptions.ConnectionError:
            if attempt < retries:
                print(f"    ⚠ Ollama 连接失败，重试 ({attempt + 1}/{retries})...")
                time.sleep(3)
            else:
                raise
        except Exception as e:
            if attempt < retries:
                print(f"    ⚠ 翻译出错: {e}，重试...")
                time.sleep(1)
            else:
                raise

    return ""  # unreachable


def translate_article(title: str, content: str, dry_run: bool = False) -> str:
    """翻译单篇文章。长文章分段翻译后拼接。"""
    if dry_run:
        return f"[DRY RUN] 将翻译: {title} ({len(content)} 字符)"

    # 短文章直接翻译
    if len(content) <= CHUNK_SIZE:
        return translate_chunk(f"Title: {title}\n\n{content}")

    # 长文章分段翻译
    print(f"    长文章 ({len(content)} 字符)，分段翻译...")
    paragraphs = content.split("\n\n")
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) < CHUNK_SIZE:
            current_chunk += para + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = para + "\n\n"
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    # 翻译标题 + 第一段，其余段落独立翻译
    translated_parts = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            prompt = f"Title: {title}\n\n{chunk}"
        else:
            prompt = f"(Continued from previous part)\n\n{chunk}"
        print(f"    翻译第 {i + 1}/{len(chunks)} 段 ({len(chunk)} 字符)...")
        translated = translate_chunk(prompt)
        translated_parts.append(translated)
        time.sleep(0.5)  # 避免 Ollama 过载

    return "\n\n".join(translated_parts)


def build_markdown(data: dict, translations: dict) -> str:
    """组装中文日报 Markdown。"""
    scrape_date = data.get("scrape_date", "unknown")
    lines = [
        f"# Anthropic/Claude 内容日报 — {scrape_date}",
        "",
        f"> 自动抓取 & Ollama ({OLLAMA_MODEL}) 全文翻译",
        f"> 抓取时间: {data.get('scrape_time', 'N/A')}",
        "",
        "---",
        "",
    ]

    channels = data.get("channels", {})
    for ch_id, articles in channels.items():
        if not articles:
            continue

        ch_name = CHANNEL_NAMES_ZH.get(ch_id, ch_id)
        translated_articles = translations.get(ch_id, [])
        lines.append(f"## {ch_name}（{len(articles)} 篇）")
        lines.append("")

        for i, art in enumerate(articles):
            title = art.get("title", "Untitled")
            url = art.get("url", "")
            art_date = art.get("date", "")

            lines.append(f"### {title}")
            lines.append("")
            lines.append(f"- 🔗 [{url}]({url})")
            if art_date:
                lines.append(f"- 📅 {art_date}")
            lines.append("")

            # 翻译正文
            if i < len(translated_articles):
                lines.append(translated_articles[i])
            else:
                lines.append("> ⚠ 翻译失败")

            lines.append("")
            lines.append("---")
            lines.append("")

    total = sum(len(v) for v in channels.values())
    lines.append(f"*共 {total} 篇文章，由 Ollama `{OLLAMA_MODEL}` 翻译*")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Ollama 全文翻译 Anthropic 内容日报")
    parser.add_argument(
        "--date", default="latest",
        help="日期 (YYYY-MM-DD) 或 'latest' (默认: latest)"
    )
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不调 Ollama")
    args = parser.parse_args()

    # --- 定位数据文件 ---
    if args.date == "latest":
        data_file = find_latest_data()
    else:
        data_file = find_data_by_date(args.date)

    if not data_file:
        print(f"❌ 未找到数据文件: data/{args.date}.json")
        print("   请先运行: python scripts/scrape.py")
        sys.exit(1)

    print(f"📂 数据文件: {data_file}")
    with open(data_file, encoding="utf-8") as f:
        data = json.load(f)

    # --- 检查 Ollama ---
    if not args.dry_run:
        try:
            resp = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            if OLLAMA_MODEL not in models and not any(m.startswith(OLLAMA_MODEL) for m in models):
                print(f"⚠ Ollama 模型 '{OLLAMA_MODEL}' 未找到!")
                print(f"  可用模型: {models}")
                print("  继续尝试...")
        except requests.exceptions.ConnectionError:
            print("❌ 无法连接 Ollama，请确认 ollama serve 已启动")
            sys.exit(1)

    # --- 逐渠道翻译 ---
    channels = data.get("channels", {})
    translations: dict[str, list[str]] = {}
    total_articles = sum(len(v) for v in channels.values())

    if total_articles == 0:
        print("📭 今日无新文章")
        # 仍然生成一个空日报
        md = f"# Anthropic/Claude 内容日报 — {data['scrape_date']}\n\n> 今日无新文章。\n"
        out_file = OUTPUT_DIR / f"{data['scrape_date']}.md"
        out_file.write_text(md, encoding="utf-8")
        print(f"💾 {out_file}")
        return

    article_idx = 0
    for ch_id, articles in channels.items():
        if not articles:
            translations[ch_id] = []
            continue

        ch_name = CHANNEL_NAMES_ZH.get(ch_id, ch_id)
        print(f"\n📝 [{ch_name}] {len(articles)} 篇待翻译")

        ch_translations = []
        for art in articles:
            article_idx += 1
            title = art.get("title", "Untitled")
            print(f"  [{article_idx}/{total_articles}] {title[:60]}...")

            if args.dry_run:
                ch_translations.append(translate_article(title, "", dry_run=True))
            else:
                try:
                    translated = translate_article(title, art.get("content", ""))
                    ch_translations.append(translated)
                except Exception as e:
                    print(f"    ❌ 翻译失败: {e}")
                    ch_translations.append(f"> ⚠ 翻译失败: {e}")

            if not args.dry_run:
                time.sleep(0.5)

        translations[ch_id] = ch_translations

    # --- 生成 Markdown ---
    md = build_markdown(data, translations)
    out_file = OUTPUT_DIR / f"{data['scrape_date']}.md"
    out_file.write_text(md, encoding="utf-8")
    print(f"\n💾 {out_file}")

    if not args.dry_run:
        # 简单统计
        total_chars = sum(
            len(t) for tl in translations.values() for t in tl
            if not t.startswith("[DRY RUN]")
        )
        print(f"📊 共翻译 {total_chars:,} 中文字符")


if __name__ == "__main__":
    main()
