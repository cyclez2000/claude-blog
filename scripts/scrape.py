#!/usr/bin/env python3
"""Anthropic/Claude 全渠道内容抓取器。

抓取 6 个官方渠道的文章全文：
- claude.com/blog (Webflow CMS, 需 Playwright)
- claude.com/customers (Webflow CMS, 需 Playwright)
- anthropic.com/engineering (Sanity CMS, 需 Playwright)
- anthropic.com/research (纯 SSR, HTTP 即可)
- anthropic.com/news (纯 SSR)
- anthropic.com/economic-futures (纯 SSR)

输出: data/YYYY-MM-DD.json
"""

import asyncio
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET

# Playwright - GH Actions 原生支持，本地需安装
try:
    from playwright.async_api import async_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    print("⚠ Playwright not installed. Blog/Customers/Engineering will be skipped.")
    print("  Install: pip install playwright && playwright install chromium --with-deps")

# --- 配置 ---
TODAY = date.today()
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = DATA_DIR / f"{TODAY.isoformat()}.json"

UA = (
    "Mozilla/5.0 (compatible; ClaudeBlogBot/1.0; "
    "+https://github.com/cyclez2000/claude-blog)"
)

# 渠道配置
CHANNELS = {
    "blog": {
        "name": "Claude Blog",
        "list_url": "https://claude.com/blog",
        "source": "claude.com",
        "method": "playwright_list",
        "article_url_pattern": re.compile(
            r"^https://claude\.com/blog/(?!category/|blog-product/|blog-usecases/)[a-z0-9-]+$"
        ),
    },
    "customers": {
        "name": "Customer Stories",
        "list_url": "https://claude.com/customers",
        "source": "claude.com",
        "method": "playwright_list",
        "article_url_pattern": re.compile(
            r"^https://claude\.com/customers/[a-z0-9-]+$"
        ),
    },
    "engineering": {
        "name": "Engineering at Anthropic",
        "sitemap": "https://www.anthropic.com/sitemap.xml",
        "path_prefix": "/engineering/",
        "source": "anthropic.com",
        "method": "sitemap_playwright",
    },
    "research": {
        "name": "Research",
        "sitemap": "https://www.anthropic.com/sitemap.xml",
        "path_prefix": "/research/",
        "source": "anthropic.com",
        "method": "sitemap_http",
    },
    "news": {
        "name": "Newsroom",
        "sitemap": "https://www.anthropic.com/sitemap.xml",
        "path_prefix": "/news/",
        "source": "anthropic.com",
        "method": "sitemap_http",
    },
    "economic": {
        "name": "Economic Futures",
        "sitemap": "https://www.anthropic.com/sitemap.xml",
        "path_prefix": "/economic-futures/",
        "source": "anthropic.com",
        "method": "sitemap_http",
    },
}

# 日期解析正则
DATE_PATTERNS = [
    # "May 22, 2026" / "Published Apr 08, 2026"
    r"(?:Published\s+)?([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})",
    # ISO format fallback
    r"(\d{4}-\d{2}-\d{2})",
]
DATE_RE = re.compile("|".join(f"(?:{p})" for p in DATE_PATTERNS))


# --- 工具函数 ---

def parse_date(text: str) -> Optional[date]:
    """从文本中提取日期。"""
    match = DATE_RE.search(text)
    if not match:
        return None
    date_str = match.group(0).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def clean_text(text: str) -> str:
    """清理文本：去重空行、移除导航残留。"""
    lines = [l.strip() for l in text.split("\n")]
    skip_prefixes = (
        "Skip to main content", "Skip to footer", "Skip to",
        "Contact sales", "Try Claude", "Log in", "Home page", "Homepage",
        "Thank you!", "Oops!", "Get the developer newsletter",
        "© 2026", "Cookie settings",
        "Explore here", "Subscribe",
        "Anthropic", "Claude\n", "Products\n", "Features\n",
        "Models\n", "Solutions\n", "Resources\n", "Company\n",
        "Help and security", "Terms and policies",
        "x.com", "LinkedIn", "YouTube", "Instagram",
        "English (US)",
    )
    cleaned = []
    skip_mode = False
    for line in lines:
        if not line:
            continue
        if any(line.startswith(p) for p in skip_prefixes):
            continue
        # 跳过纯导航链接行（单行且很短）
        if len(line) < 5 and not line[0].isalpha():
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def extract_metadata(text: str) -> dict:
    """从文章正文提取标题和日期。"""
    lines = text.split("\n")
    title = ""
    for line in lines:
        line = line.strip()
        if len(line) > 15 and not line.startswith(("Skip", "http", "Explore")):
            title = line
            break
    article_date = parse_date(text)
    return {
        "title": title,
        "date": article_date.isoformat() if article_date else None,
    }


# --- Sitemap 解析 ---

def fetch_sitemap_articles(sitemap_url: str, path_prefix: str) -> list[dict]:
    """从 sitemap XML 提取指定路径前缀的文章 URL 及 lastmod。"""
    try:
        resp = requests.get(sitemap_url, headers={"User-Agent": UA}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠ Sitemap fetch failed: {e}")
        return []

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.fromstring(resp.content)
    articles = []

    for url_elem in root.findall("sm:url", ns):
        loc = url_elem.find("sm:loc", ns)
        lastmod = url_elem.find("sm:lastmod", ns)
        if loc is None or loc.text is None:
            continue

        url = loc.text.strip()
        if path_prefix not in url:
            continue

        slug = url.split(path_prefix, 1)[-1].strip("/")
        # 跳过索引页
        if not slug:
            continue

        article_date = None
        if lastmod is not None and lastmod.text:
            try:
                article_date = datetime.fromisoformat(
                    lastmod.text.replace("Z", "+00:00")
                ).date()
            except ValueError:
                pass

        articles.append({"url": url, "lastmod": article_date, "slug": slug})

    return articles


# --- HTTP 抓取（SSR 页面） ---

async def fetch_article_http(url: str) -> Optional[str]:
    """HTTP 获取文章全文（适用于 SSR 渲染的页面）。"""
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"    ⚠ HTTP {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()

    content = soup.find("main") or soup.find("article") or soup.find("body")
    text = content.get_text(separator="\n", strip=True) if content else soup.get_text(separator="\n", strip=True)
    return clean_text(text)


# --- Playwright 抓取 ---

async def fetch_article_playwright(page, url: str) -> Optional[str]:
    """Playwright 获取文章全文（Webflow/Sanity CMS JS 渲染）。"""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2000)
    except Exception as e:
        print(f"    ⚠ Playwright goto {url}: {e}")
        return None

    try:
        text = await page.evaluate("""() => {
            const main = document.querySelector('main') ||
                         document.querySelector('article') ||
                         document.body;
            return main ? main.innerText.trim() : '';
        }""")
    except Exception as e:
        print(f"    ⚠ Playwright eval {url}: {e}")
        return None

    return clean_text(text) if text else None


async def scrape_playwright_list(page, list_url: str, url_pattern: re.Pattern) -> list[dict]:
    """Playwright 打开列表页，提取所有符合模式的文章链接。"""
    print(f"  Navigating to {list_url}...")
    try:
        await page.goto(list_url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)
    except Exception as e:
        print(f"  ⚠ Failed to load: {e}")
        return []

    links = await page.evaluate("""() => {
        return Array.from(document.querySelectorAll('a[href]')).map(a => ({
            href: a.href,
            text: a.textContent.trim().substring(0, 200)
        }));
    }""")

    articles, seen = [], set()
    for link in links:
        href = link.get("href", "")
        if not url_pattern.match(href) or href in seen:
            continue
        seen.add(href)
        articles.append({
            "url": href,
            "title": link.get("text", ""),
            "source": list_url,
        })

    print(f"  Found {len(articles)} unique article URLs")
    return articles


# --- 主流程 ---

async def main():
    print(f"\n{'='*60}")
    print(f"  Claude Blog Scraper - {TODAY.isoformat()}")
    print(f"{'='*60}\n")

    result = {
        "scrape_date": TODAY.isoformat(),
        "scrape_time": datetime.now(timezone.utc).isoformat(),
        "channels": {},
    }

    # ===== Phase 1: Sitemap 渠道 (anthropic.com) =====
    # 先统一拉 sitemap，各渠道复用
    sitemap_url = "https://www.anthropic.com/sitemap.xml"
    sitemap_cache: dict[str, list[dict]] = {}

    for ch_id, ch_cfg in CHANNELS.items():
        if ch_cfg["method"] not in ("sitemap_http", "sitemap_playwright"):
            continue

        print(f"\n📡 [{ch_cfg['name']}]")

        if ch_cfg["path_prefix"] not in sitemap_cache:
            sitemap_cache[ch_cfg["path_prefix"]] = fetch_sitemap_articles(
                sitemap_url, ch_cfg["path_prefix"]
            )
        all_arts = sitemap_cache[ch_cfg["path_prefix"]]
        print(f"  Total in sitemap: {len(all_arts)}")

        # 取最近 7 天（周末/假期容错）
        valid_dates = {TODAY - timedelta(days=i) for i in range(7)}
        today_arts = [a for a in all_arts if a["lastmod"] and a["lastmod"] in valid_dates]
        print(f"  Recent (≤7 days): {len(today_arts)}")

        if not today_arts:
            result["channels"][ch_id] = []
            print(f"  ✅ No new articles")
            continue

        # 按方法抓取文章全文
        articles = []
        need_pw = ch_cfg["method"] == "sitemap_playwright"

        if need_pw and HAS_PLAYWRIGHT:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                ctx = await browser.new_context(user_agent=UA)
                pg = await ctx.new_page()
                for art in today_arts:
                    print(f"  Fetching: {art['slug'][:60]}")
                    content = await fetch_article_playwright(pg, art["url"])
                    if content:
                        meta = extract_metadata(content)
                        articles.append({
                            "title": meta["title"] or art["slug"].replace("-", " ").title(),
                            "url": art["url"],
                            "date": meta["date"] or (art["lastmod"].isoformat() if art["lastmod"] else None),
                            "content": content,
                        })
                await browser.close()
        elif need_pw and not HAS_PLAYWRIGHT:
            print(f"  ⚠ Skipped - Playwright not available")
        else:
            for art in today_arts:
                print(f"  Fetching: {art['slug'][:60]}")
                content = await fetch_article_http(art["url"])
                if content:
                    meta = extract_metadata(content)
                    articles.append({
                        "title": meta["title"] or art["slug"].replace("-", " ").title(),
                        "url": art["url"],
                        "date": meta["date"] or (art["lastmod"].isoformat() if art["lastmod"] else None),
                        "content": content,
                    })

        result["channels"][ch_id] = articles
        print(f"  ✅ Scraped {len(articles)} articles")

    # ===== Phase 2: Playwright 列表渠道 (claude.com) =====
    pw_channels = {k: v for k, v in CHANNELS.items() if v["method"] == "playwright_list"}

    if pw_channels and HAS_PLAYWRIGHT:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=UA)
            list_page = await ctx.new_page()
            article_page = await ctx.new_page()

            for ch_id, ch_cfg in pw_channels.items():
                print(f"\n🎭 [{ch_cfg['name']}]")

                # 1) 从列表页提取文章 URL
                article_list = await scrape_playwright_list(
                    list_page, ch_cfg["list_url"], ch_cfg["article_url_pattern"]
                )
                if not article_list:
                    result["channels"][ch_id] = []
                    continue

                # 列表页已按时间倒序，取前 30 篇直接收
                articles = []
                for art in article_list[:30]:
                    slug = art["url"].rstrip("/").split("/")[-1]
                    print(f"  Fetching: {slug[:60]}")
                    content = await fetch_article_playwright(article_page, art["url"])
                    if not content:
                        continue

                    meta = extract_metadata(content)
                    articles.append({
                        "title": meta["title"] or art["title"] or slug.replace("-", " ").title(),
                        "url": art["url"],
                        "date": meta.get("date"),
                        "content": content,
                    })

                result["channels"][ch_id] = articles
                print(f"  ✅ Scraped {len(articles)} articles (filtered to recent)")

            await browser.close()
    elif pw_channels and not HAS_PLAYWRIGHT:
        for ch_id in pw_channels:
            print(f"\n⚠ [{CHANNELS[ch_id]['name']}] Skipped - Playwright not available")
            result["channels"][ch_id] = []

    # ===== 写入 JSON =====
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # ===== 汇总 =====
    total = sum(len(v) for v in result["channels"].values())
    print(f"\n{'='*60}")
    print(f"📊 Done: {total} articles across {len(result['channels'])} channels")
    for ch, arts in result["channels"].items():
        print(f"  {ch:15s} → {len(arts):3d} articles")
    print(f"💾 {OUTPUT_FILE}")
    print(f"{'='*60}\n")

    return 0 if total > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
