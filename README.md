# 每日新聞早報 Agent

自動蒐集「美股隔夜行情、台灣新聞焦點、Fed/經濟數據」，用 Gemini 整理成早報，透過 LINE Messaging API 推播給自己。

## 架構

```
main.py
├─ sources.py      抓 RSS 新聞、Yahoo Finance 指數、FRED 經濟數據（純資料，不摘要）
├─ summarizer.py   把上面抓到的原始資料丟給 Gemini API，整理成早報文字
├─ notifier.py     用 LINE Messaging API 把早報推播給你
└─ config.py       所有可調整的設定（金鑰從 .env / 環境變數讀取）
```

## 1. 安裝

```bash
# 在 Oracle VM 上（A1 或 E1 皆可）
sudo apt update && sudo apt install -y python3-pip python3-venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. 取得金鑰

### Gemini API Key（必要）
到 [Google AI Studio](https://aistudio.google.com/apikey) 免費申請即可，這個任務一天只呼叫 1 次 API，免費額度綽綽有餘。

### LINE Channel Access Token（必要，你已經有 Messaging API 了）
1. 到 [LINE Developers Console](https://developers.line.biz/console/) 選擇你的 Provider / Channel
2. 「Messaging API」分頁 → 「Channel access token」→ 發行一組 **長期權杖 (long-lived)**

### LINE User ID（必要，推播對象）
最簡單的方式：進到你 Channel 的「Basic settings」分頁，往下找到 **「Your user ID」** 欄位，那就是你自己的 userId，直接複製即可，不需要另外架 webhook。
（前提是你已經用該官方帳號的 QR code 加自己為好友，否則收不到推播）

### FRED API Key（選填，不設定就自動跳過「Fed / 經濟數據」段落）
到 [FRED API Key 申請頁](https://fred.stlouisfed.org/docs/api/api_key.html) 免費申請。

## 3. 設定與執行

```bash
cp env.example .env
nano .env   # 填入上面拿到的金鑰
venv/bin/python3 main.py
```

程式啟動時會透過 `python-dotenv` 自動讀取專案資料夾下的 `.env`，不需要手動 export。
第一次執行建議直接在終端機跑一次，確認你的 LINE 真的有收到訊息，再排程。

## 4. 排程（cron）

美股資料建議在**台灣時間清晨**抓（此時美股已收盤、數據穩定），目前排程為每天早上 8:00。`crontab -e` 加入：

```cron
0 8 * * * cd /home/ubuntu/daily-news-agent && venv/bin/python3 main.py >> cron.log 2>&1
```

## 5. 自訂內容

- 想加減新聞來源：改 `config.py` 的 `RSS_FEEDS`（任何提供 RSS 的媒體都可以加，注意各家版權聲明多半僅限個人非商業使用）
- 想加減觀察的股市指數：改 `US_INDICES`，代碼到 [Yahoo Finance](https://finance.yahoo.com/) 查（例如 S&P 500 是 `^GSPC`）
- 想調整早報的語氣/格式：改 `summarizer.py` 裡的 `SYSTEM_PROMPT`
- 想加減追蹤的經濟數據：改 `config.py` 的 `FRED_RELEASES`，需要同時填對 `release_id`（查詢頁面: https://fred.stlouisfed.org/releases）和該 release 對應的主要資料序列 `series_id`（在該序列頁面網址可以找到，例如 CPI 是 `CPIAUCSL`）
- 換 Gemini 模型：改 `.env` 的 `GEMINI_MODEL`。`summarizer.py` 的生成參數是針對 **Gemini 3 系列**設定的（`thinkingLevel=high`、temperature 維持官方預設 1.0），若改回 2.5 系列，需把 `thinkingLevel` 換成 `thinkingBudget`。
- **美股休市判斷已全自動化**：`sources.py` 的 `is_us_market_likely_closed()` 用 [pandas_market_calendars](https://github.com/rsheftel/pandas_market_calendars) 內建的 NYSE 官方行事曆規則（含耶穌受難日這種浮動假日），完全離線計算，不用每年手動維護假日清單。

## 6. 推播行為說明

- **推播拆成 1~3 則訊息**：🇺🇸 美股隔夜、📰 新聞焦點（含編輯室報告）、💰 Fed 經濟數據。美股段落在美股休市隔天（例如週日）不會出現；經濟數據段落**只有當天真的有排定的數據公布時才會出現**。
- **美股漲跌符號**是程式碼直接依漲跌方向算出來的（📈/📉/➡️），不是 Gemini 自己判斷，確保一定正確。
- **Fed 數據段落的「利多利空」解讀有個限制**：FRED 只提供實際公布值，沒有市場預期／共識值，所以解讀只能基於「相對上一期的變化方向」，不能做到「優於/低於市場預期」的判斷。如果之後想做到這個，需要額外串接有共識預期資料的來源（通常是付費服務）。
- FRED 查詢失敗時只會記錄 warning（金鑰會遮蔽），不會把錯誤訊息當成經濟數據送給 Gemini；該次早報會省略經濟數據段落。

## 7. 常見問題

**LINE 推播回傳 400/401 錯誤**
通常是 Channel Access Token 過期或打錯、或是你還沒把該官方帳號加好友。長期權杖理論上不會過期，但如果你在 Console 重新發行過，舊的就會失效。

**RSS 抓不到資料**
新聞網站偶爾會調整 RSS 路徑，`sources.py` 已經做了 try/except，單一來源失敗不會讓整支程式掛掉，該來源會在原始資料中標示「抓取失敗」，去對應網站的 RSS 服務頁面確認新網址即可。

**Gemini 回傳 503**
免費額度在尖峰時段常遇到模型過載，程式會以 10、20、40、80 秒的間隔自動重試最多 5 次，通常第 2~3 次就會成功。

**想在多個 LINE 帳號 / 群組推播**
`notifier.push_line_texts` 目前只推給單一 `user_id`，若要推到群組，把 `to` 換成群組的 `groupId` 即可（Webhook event 裡會帶 `source.groupId`）。
