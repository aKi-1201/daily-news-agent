"""
資料蒐集模組。
設計原則：每個 fetch 函式都「只回傳事實資料的文字區塊」，不做任何摘要或判斷，
確保後面餵給 Gemini 的是可查證的原始資訊，避免 LLM 憑空生成數字。
任何一個來源抓取失敗都不應讓整支程式掛掉，因此逐一包 try/except。
"""
import html
import logging
import re
from datetime import datetime, timedelta, timezone

import feedparser
import pandas_market_calendars as mcal
import requests

log = logging.getLogger("daily-news-agent.sources")

TIMEOUT = 10
FRED_TIMEOUT = 30  # FRED 從雲端主機連線常超過 10 秒
UA_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DailyNewsBot/1.0)"}
TAIWAN_TZ = timezone(timedelta(hours=8))
FRED_API = "https://api.stlouisfed.org/fred"
_NYSE_CALENDAR = mcal.get_calendar("NYSE")  # 只需初始化一次，內建假日規則不需要網路查詢


def is_us_market_likely_closed() -> bool:
    """
    判斷「這次推播要不要顯示美股隔夜段落」，回傳 True 代表要跳過該段落。
    規則（皆以台灣時間為準）：
    - 週日：一律跳過，不管週五狀況。
    - 週六：顯示週五收盤；若週五當天休市，則跳過。
    - 週一：顯示週五收盤（週末沒有新交易日，直接定位到週五，不會再往前找週四）；
      若週五當天休市，則跳過。
    - 其他日子（週二~週五）：顯示前一天收盤；若前一天休市（例如週間國定假日），則跳過。

    註：實際抓到的收盤數字一律是 Yahoo 當下回傳的「最新」收盤價，這裡只負責判斷
    要不要顯示這個段落，不需要額外指定要抓哪一天的資料。
    """
    taiwan_today = datetime.now(TAIWAN_TZ).date()

    if taiwan_today.weekday() == 6:  # 週日，一律跳過
        return True

    candidate = taiwan_today - timedelta(days=1)
    if candidate.weekday() == 6:  # 前一天是週日 -> 代表今天是週一，改定位到週五
        candidate = candidate - timedelta(days=2)

    valid_days = _NYSE_CALENDAR.valid_days(start_date=candidate, end_date=candidate)
    return len(valid_days) == 0


def _clean_summary(raw: str, max_chars: int) -> str:
    """去除 HTML 標籤/實體與中央社開頭的「（中央社記者…電）」，再截取前 max_chars 字。"""
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    text = re.sub(r"^（中央社[^）]*）", "", text)
    return text[:max_chars] + "…" if len(text) > max_chars else text


def fetch_rss_headlines(feeds: dict, limit_per_source: int, summary_chars: int) -> str:
    """抓取多個 RSS 來源的最新標題、摘要（前 summary_chars 字）與連結。"""
    blocks = []
    for name, url in feeds.items():
        try:
            resp = requests.get(url, timeout=TIMEOUT, headers=UA_HEADERS)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            lines = []
            for entry in feed.entries[:limit_per_source]:
                lines.append(f"- {getattr(entry, 'title', '').strip()}")
                summary = _clean_summary(getattr(entry, "summary", ""), summary_chars)
                if summary:
                    lines.append(f"  摘要: {summary}")
                link = getattr(entry, "link", "").strip()
                if link:
                    lines.append(f"  連結: {link}")
            if lines:
                blocks.append(f"【{name}】\n" + "\n".join(lines))
            else:
                blocks.append(f"【{name}】（本次未取得任何項目）")
        except Exception as e:
            blocks.append(f"【{name}】抓取失敗：{e}")
    return "\n\n".join(blocks)


def _trend_emoji(change: float) -> str:
    """依漲跌方向回傳對應符號，交給程式碼決定，避免 LLM 判斷錯誤。"""
    if change > 0:
        return "📈"
    if change < 0:
        return "📉"
    return "➡️"


def _fetch_index(name: str, symbol: str) -> str:
    """從 Yahoo Finance 公開 chart API 抓最近兩個交易日收盤價，組成含漲跌符號的一行文字。"""
    try:
        resp = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d"}, timeout=TIMEOUT, headers=UA_HEADERS,
        )
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        valid = [(t, c) for t, c in zip(result["timestamp"], closes) if c is not None]
        if len(valid) < 2:
            raise ValueError("回傳資料筆數不足")
        (_, close_prev), (t_today, close_today) = valid[-2], valid[-1]
    except Exception as e:
        return f"{name}：抓取失敗（{e}）"

    date_today = datetime.fromtimestamp(t_today, tz=timezone.utc).strftime("%Y-%m-%d")
    change = close_today - close_prev
    pct = change / close_prev * 100 if close_prev else 0
    return (
        f"{_trend_emoji(change)} {name}：{close_today:,.2f}"
        f"（{change:+.2f}，{pct:+.2f}%）[{date_today}]"
    )


def fetch_us_market_summary(indices: dict) -> str:
    """組合所有美股指數的摘要文字。indices 格式見 config.py 的 US_INDICES。"""
    return "\n".join(_fetch_index(name, symbol) for name, symbol in indices.items())


def _fred_get(path: str, api_key: str, **params) -> dict:
    resp = requests.get(f"{FRED_API}/{path}", timeout=FRED_TIMEOUT,
                        params={**params, "api_key": api_key, "file_type": "json"})
    resp.raise_for_status()
    return resp.json()


def fetch_fred_todays_releases(api_key: str, releases: dict) -> str:
    """
    檢查「今天」是否有指定的經濟數據公布，若有才抓取最新數值與前一期比較。
    回傳空字串代表今天沒有任何一項數據公布（或未設定金鑰／查詢失敗），呼叫端應直接省略這個段落。
    releases 格式: {"顯示名稱": {"release_id": int, "series_id": str}}

    「今天」刻意用台灣日期：用 UTC 日期的話，台灣清晨 0~8 點執行時 UTC 還停在前一天，會誤判成還沒公布。
    """
    if not api_key:
        log.warning("未設定 FRED_API_KEY，略過 Fed 經濟數據查詢")
        return ""

    def warn(what: str, e: Exception) -> None:
        # requests 的錯誤訊息會帶完整 URL（含 api_key），寫進 log 前先遮蔽
        log.warning("FRED %s 查詢失敗：%s", what, str(e).replace(api_key, "***"))

    target_date = datetime.now(TAIWAN_TZ).date().isoformat()
    try:
        # 一次查出今天所有 release，取代逐項呼叫 release/dates
        data = _fred_get("releases/dates", api_key, realtime_start=target_date,
                         realtime_end=target_date, include_release_dates_with_no_data="true",
                         limit=1000)
    except Exception as e:
        warn("今日公布行事曆", e)
        return ""
    released_today = {d["release_id"] for d in data.get("release_dates", []) if d["date"] == target_date}

    blocks = []
    for name, meta in releases.items():
        if meta["release_id"] not in released_today:
            continue
        try:
            observations = _fred_get("series/observations", api_key, series_id=meta["series_id"],
                                     sort_order="desc", limit=2).get("observations", [])
        except Exception as e:
            warn(name, e)
            continue
        valid_obs = [o for o in observations if o.get("value") not in (None, ".")]
        if len(valid_obs) < 2:
            blocks.append(f"- {name}：今日公布，但可比對的歷史資料筆數不足")
            continue

        latest, prev = valid_obs[0], valid_obs[1]
        latest_val, prev_val = float(latest["value"]), float(prev["value"])
        change = latest_val - prev_val
        pct = change / prev_val * 100 if prev_val else 0
        blocks.append(
            f"- {name}（資料期間 {latest['date']}）{_trend_emoji(change)}：{latest_val:,.2f}"
            f"（較上期 {change:+.2f}，{pct:+.2f}%）"
        )

    return "\n".join(blocks)
