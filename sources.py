"""
資料蒐集模組。
設計原則：每個 fetch 函式都「只回傳事實資料的文字區塊」，不做任何摘要或判斷，
確保後面餵給 Gemini 的是可查證的原始資訊，避免 LLM 憑空生成數字。
任何一個來源抓取失敗都不應讓整支程式掛掉，因此逐一包 try/except。
"""
import logging
from datetime import datetime, timedelta, timezone

import feedparser
import pandas_market_calendars as mcal
import requests

log = logging.getLogger("daily-news-agent.sources")

TIMEOUT = 10
UA_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DailyNewsBot/1.0)"}
TAIWAN_TZ = timezone(timedelta(hours=8))
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

    註：實際抓到的收盤數字一律是 Stooq/Yahoo 當下回傳的「最新」收盤價，這裡只負責判斷
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


def fetch_rss_headlines(feeds: dict, limit_per_source: int) -> str:
    """抓取多個 RSS 來源的最新標題與連結。"""
    blocks = []
    for name, url in feeds.items():
        try:
            resp = requests.get(url, timeout=TIMEOUT, headers=UA_HEADERS)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            lines = []
            for entry in feed.entries[:limit_per_source]:
                title = getattr(entry, "title", "").strip()
                link = getattr(entry, "link", "").strip()
                lines.append(f"- {title}\n  連結: {link}" if link else f"- {title}")
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


def _parse_stooq(symbol: str):
    """從 Stooq 抓 CSV，回傳 (今日收盤, 前一日收盤, 日期字串)。"""
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    resp = requests.get(url, timeout=TIMEOUT, headers=UA_HEADERS)
    resp.raise_for_status()
    rows = [r.split(",") for r in resp.text.strip().splitlines() if r][1:]  # 去表頭
    if len(rows) < 2:
        # Stooq 對雲端主機（如 Oracle/AWS）的 IP 有時會回傳空資料擋爬蟲，改試 Yahoo。
        raise ValueError("回傳資料筆數不足，可能被判定為機器人流量而擋下")
    date_today, close_today = rows[-1][0], float(rows[-1][4])
    close_prev = float(rows[-2][4])
    return close_today, close_prev, date_today


def _parse_yahoo(symbol: str):
    """從 Yahoo Finance 公開 chart API 抓資料，作為 Stooq 的備援來源。"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    resp = requests.get(
        url, params={"range": "5d", "interval": "1d"}, timeout=TIMEOUT, headers=UA_HEADERS
    )
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    timestamps = result["timestamp"]
    valid = [(t, c) for t, c in zip(timestamps, closes) if c is not None]
    if len(valid) < 2:
        raise ValueError("回傳資料筆數不足")
    (_, close_prev), (t_today, close_today) = valid[-2], valid[-1]
    date_today = datetime.fromtimestamp(t_today, tz=timezone.utc).strftime("%Y-%m-%d")
    return close_today, close_prev, date_today


def _fetch_index(name: str, symbols: dict) -> str:
    """依序嘗試 Stooq -> Yahoo，任一來源成功就回傳（含漲跌符號），兩者都失敗才回報抓取失敗。"""
    attempts = [("Stooq", _parse_stooq, symbols.get("stooq")),
                ("Yahoo", _parse_yahoo, symbols.get("yahoo"))]
    errors = []
    for source_name, parse_fn, symbol in attempts:
        if not symbol:
            continue
        try:
            close_today, close_prev, date_today = parse_fn(symbol)
            change = close_today - close_prev
            pct = change / close_prev * 100 if close_prev else 0
            emoji = _trend_emoji(change)
            return (
                f"{emoji} {name}：{close_today:,.2f}"
                f"（{change:+.2f}，{pct:+.2f}%）[{date_today}，來源:{source_name}]"
            )
        except Exception as e:
            errors.append(f"{source_name}失敗({e})")
    return f"{name}：抓取失敗（{'；'.join(errors) if errors else '未設定任何來源代碼'}）"


def fetch_us_market_summary(indices: dict) -> str:
    """組合所有美股指數的摘要文字。indices 格式見 config.py 的 US_INDICES。"""
    return "\n".join(_fetch_index(name, symbols) for name, symbols in indices.items())


def fetch_fred_todays_releases(api_key: str, releases: dict) -> str:
    """
    檢查「今天」是否有指定的經濟數據公布，若有才抓取最新數值與前一期比較。
    回傳空字串代表今天沒有任何一項數據公布（或未設定金鑰），呼叫端應直接省略這個段落，
    不要在推播中出現空段落。
    releases 格式: {"顯示名稱": {"release_id": int, "series_id": str}}

    注意：這裡用「台灣時區的今天日期」而不是 datetime.now(timezone.utc).date()，
    原因是舊版用 UTC 日期在台灣時間清晨 0~8 點執行時，UTC 日期還停留在前一天，
    會誤判成「還沒公布」，導致同一天不同時間執行結果不一致（7 點查不到、11 點查得到）。
    改用 TAIWAN_TZ 直接取得台灣當地日期後，不管排程幾點執行，「今天」的計算結果都固定，
    不會再受 UTC 換日邊界影響。
    （原本這裡曾經改成往前推一天，是誤套用了美股段落的邏輯，經實測證實應維持查詢「台灣今天」
    才正確，已改回。）
    """
    if not api_key:
        log.warning("未設定 FRED_API_KEY，略過 Fed 經濟數據查詢")
        return ""

    target_date = datetime.now(TAIWAN_TZ).date().isoformat()
    blocks = []
    for name, meta in releases.items():
        release_id = meta["release_id"]
        series_id = meta["series_id"]
        try:
            date_url = (
                "https://api.stlouisfed.org/fred/release/dates"
                f"?release_id={release_id}&realtime_start={target_date}&realtime_end={target_date}"
                f"&api_key={api_key}&file_type=json&include_release_dates_with_no_data=true"
            )
            resp = requests.get(date_url, timeout=TIMEOUT)
            resp.raise_for_status()
            dates = [d["date"] for d in resp.json().get("release_dates", [])]
            if target_date not in dates:
                continue  # 今天沒有這項數據公布，跳過，不算錯誤

            obs_url = (
                "https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}&sort_order=desc&limit=2"
                f"&api_key={api_key}&file_type=json"
            )
            obs_resp = requests.get(obs_url, timeout=TIMEOUT)
            obs_resp.raise_for_status()
            observations = obs_resp.json().get("observations", [])
            valid_obs = [o for o in observations if o.get("value") not in (None, ".")]
            if len(valid_obs) < 2:
                blocks.append(f"- {name}：今日公布，但可比對的歷史資料筆數不足")
                continue

            latest, prev = valid_obs[0], valid_obs[1]
            latest_val, prev_val = float(latest["value"]), float(prev["value"])
            change = latest_val - prev_val
            pct = change / prev_val * 100 if prev_val else 0
            emoji = _trend_emoji(change)
            blocks.append(
                f"- {name}（資料期間 {latest['date']}）{emoji}：{latest_val:,.2f}"
                f"（較上期 {change:+.2f}，{pct:+.2f}%）"
            )
        except Exception as e:
            log.warning("FRED %s 查詢失敗：%s", name, e)

    return "\n".join(blocks)