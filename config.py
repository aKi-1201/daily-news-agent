"""
設定檔：所有金鑰一律從環境變數讀取（實際值放在 .env，不要 commit 進版控）。
新聞來源、觀察指數等「內容設定」則直接寫在這裡，方便日後自行增減。
"""
import os

from dotenv import load_dotenv

load_dotenv()  # 讀取專案資料夾下的 .env，找不到檔案也不會報錯

# ============ API 金鑰 / Token（從環境變數讀取） ============
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")  # 需為 Gemini 3 系列，見 summarizer.py

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "")  # 你自己的 LINE userId（推播目標）

FRED_API_KEY = os.environ.get("FRED_API_KEY", "")  # 留空則自動略過 Fed 數據段落

# ============ 台灣新聞 RSS 來源 ============
# 來源皆為官方公開 RSS，個人非商業用途免費使用，請勿移除新聞出處。
# 自由時報 RSS 清單: https://service.ltn.com.tw/RSS
# 中央社 RSS 清單:   https://www.cna.com.tw/about/rss.aspx
RSS_FEEDS = {
    "自由時報-即時": "https://news.ltn.com.tw/rss/all.xml",  # 焦點 RSS (focus.xml) 已關閉，改回全站即時
    "中央社-政治": "https://feeds.feedburner.com/rsscna/politics",
    "中央社-國際": "https://feeds.feedburner.com/rsscna/intworld",
    "中央社-科技": "https://feeds.feedburner.com/rsscna/technology",
    "中央社-產經證券": "https://feeds.feedburner.com/rsscna/finance",
    "經濟日報": "https://money.udn.com/rssfeed/news",
    "關鍵評論網": "https://www.thenewslens.com/feed/feedly",
    "TechNews 科技新報": "https://technews.tw/feed/",
}
HEADLINES_PER_SOURCE = 10  # 每個新聞來源取幾則新聞餵給 LLM（8 來源 x 10 則，讓編輯室摘要有足夠素材）
SUMMARY_CHARS = 300  # RSS 摘要（多為新聞第一段）字數上限；現有來源最長約 180 字，等於完整送出，上限只防某個 feed 塞全文

# ============ 美股觀察指數（顯示名稱 -> Yahoo Finance 代碼） ============
US_INDICES = {
    "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC",
    "道瓊工業指數": "^DJI",
    "費城半導體指數": "^SOX",
}

# ============ FRED 經濟數據 ============
# 完整 release 列表: https://fred.stlouisfed.org/releases
# 序列代碼可在該序列頁面網址找到，例如 https://fred.stlouisfed.org/series/CPIAUCSL
FRED_RELEASES = {
    "CPI 消費者物價指數": {"release_id": 10, "series_id": "CPIAUCSL"},
    "非農就業報告 (Employment Situation)": {"release_id": 50, "series_id": "PAYEMS"},
}
