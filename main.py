#!/usr/bin/env python3
"""
每日新聞早報 Agent - 主程式
流程：抓取原始資料 -> 丟給 Gemini 整理成 1~3 段 -> 用 LINE 一次推播多則訊息
用法：python3 main.py
建議透過 cron 每天早上排程執行（見 README.md）。
"""
import logging
import sys
from datetime import datetime

import config
from notifier import push_line_texts
from sources import (
    TAIWAN_TZ,
    fetch_fred_todays_releases,
    fetch_rss_headlines,
    fetch_us_market_summary,
    is_us_market_likely_closed,
)
from summarizer import summarize_with_gemini

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("daily-news-agent")


def collect_raw_data() -> str:
    today_str = datetime.now(TAIWAN_TZ).strftime("%Y-%m-%d")
    raw_data = f"[資料日期] {today_str}\n\n"

    if is_us_market_likely_closed():
        log.info("美股當天休市（週末或假日），略過美股段落與抓取")
    else:
        log.info("抓取美股指數...")
        us_market = fetch_us_market_summary(config.US_INDICES)
        raw_data += f"[美股主要指數]\n{us_market}\n\n"

    log.info("抓取台灣新聞 RSS...")
    tw_news = fetch_rss_headlines(config.RSS_FEEDS, config.HEADLINES_PER_SOURCE)
    raw_data += f"[台灣新聞標題]\n{tw_news}\n"

    log.info("查詢今日 FRED 經濟數據是否有公布...")
    fred_today = fetch_fred_todays_releases(config.FRED_API_KEY, config.FRED_RELEASES)

    # 只有今天真的有經濟數據公布時，才把這個區塊加進原始資料，
    # 這樣 Gemini 看不到這個區塊，prompt 規則就會要求它不要生出第三段。
    if fred_today:
        raw_data += f"\n[今日經濟數據公布]\n{fred_today}\n"
    else:
        log.info("今天沒有排定的經濟數據公布（或未設定 FRED_API_KEY／查詢失敗），略過此段落")

    return raw_data


def main() -> int:
    try:
        raw_data = collect_raw_data()
        log.info("原始資料蒐集完成，長度 %d 字元", len(raw_data))

        log.info("呼叫 Gemini 進行摘要整理...")
        sections = summarize_with_gemini(config.GEMINI_API_KEY, config.GEMINI_MODEL, raw_data)
        log.info("Gemini 回傳 %d 個段落", len(sections))

        log.info("推播至 LINE...")
        push_line_texts(config.LINE_CHANNEL_ACCESS_TOKEN, config.LINE_USER_ID, sections)
        log.info("推播完成")
    except Exception:
        log.exception("執行失敗")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
