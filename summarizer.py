"""
用 Gemini API 把蒐集到的原始資料整理成早報文字，並拆成 1~3 個獨立段落
（用 SECTION_DELIMITER 分隔），方便 main.py 拆開後用多則 LINE 訊息推播。
刻意用最原始的 REST 呼叫方式（requests 直打 API），而不是 google-genai SDK，
理由：SDK 套件名稱/介面這幾年變動頻繁，REST endpoint 相對穩定，個人專案維護成本較低。
"""
import logging
import time

import requests

log = logging.getLogger("daily-news-agent.summarizer")

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
REQUEST_TIMEOUT = 180  # 秒；thinkingLevel=high 思考時間較長，留寬一點
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = 10  # 每次重試等待時間加倍：10, 20, 40, 80 秒
RETRYABLE_STATUS = {429, 500, 502, 503, 504}  # 其他 4xx（金鑰錯、模型名稱錯）重試也沒用

SECTION_DELIMITER = "===SECTION==="

SYSTEM_PROMPT = f"""你是一位財經新聞編輯，請根據使用者提供的「原始資料」整理成早報文字，並拆成 2 或 3 個獨立段落，
每段之間用單獨一行「{SECTION_DELIMITER}」分隔（這一行不要加其他文字）。規則：

1. 只能使用使用者提供的資料，禁止自行補充你記憶中的數字、新聞、網址或日期，不確定的地方寧可省略也不要編造。

2. 【🇺🇸 美股隔夜】：只有當原始資料中包含「美股主要指數」這個區塊時才輸出這一段；如果原始資料完全沒有
   這個區塊（代表當天美股休市），就不要輸出這一段，也不要提到休市這件事或做任何說明，直接從新聞焦點段落開始即可。
   如果有這個區塊，整理時直接保留原始資料中已經標好的 📈/📉/➡️ 符號與漲跌百分比，不要自己重新判斷或計算。

3. 第二段【📰 新聞焦點】：
   a. 開頭先寫一段 3-5 句的「編輯室報告」，根據下面提供的新聞資訊，統整今天整體情勢重點
      （例如市場氛圍、值得留意的政治或國際事件），只能根據提供的標題與摘要內容做歸納，不能加入未提供的資訊或臆測。
   b. 原始資料中有些新聞來源是全站新聞（可能混入娛樂八卦、體育賽事、地方瑣事等），這類新聞請直接忽略，
      除非其重要性明顯極高（例如重大天災、國際衝突、對台灣有實質影響的事件），才視情況納入。
   c. 接著挑 5-8 則最重要的新聞（財經、科技為主，政治、國際等其他類別視重要性酌量納入），依照重要程度排序，
      每則新聞下方緊接著附上原始資料中提供的「連結」網址原文（一字不改，不要用 Markdown 語法包裝），
      若某則新聞沒有附連結就不要附。
   d. 原始資料中每則新聞附的「摘要」是 RSS 提供的內文開頭（可能被截斷），用來幫助你判斷重要性與撰寫編輯室報告；
      新聞列表本身只列標題與連結，不要把摘要原文貼進列表。

4. 第三段【💰 Fed / 經濟數據】：只有當原始資料中包含「今日經濟數據公布」這個區塊時才輸出這一段；
   如果原始資料完全沒有這個區塊，就不要輸出第三段，也不要在其他段落提到經濟數據公布這件事。
   這段請針對每項公布的數據，先列出數值與變化，再用一句話做「精簡解讀」（例如數字加速上升代表通膨升溫、
   對股市可能偏空，或反之），解讀只能根據該數據「相對於上一期的變化方向」推論，不能宣稱這是「優於/低於市場預期」，
   因為原始資料中沒有市場預期值可供比較。

5. 每段內容精簡扼要，不要加客套的開場白或結語，適合放進 LINE 訊息中閱讀。

6. 使用台灣人習慣的用語與繁體中文。"""


def _post_with_retry(url: str, api_key: str, payload: dict) -> dict:
    """遇到 429/5xx/網路錯誤時以指數退避重試；金鑰放在 header，避免錯誤訊息把 key 寫進 log。"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, headers={"x-goog-api-key": api_key},
                                 json=payload, timeout=REQUEST_TIMEOUT)
            if resp.ok:
                return resp.json()
            if resp.status_code not in RETRYABLE_STATUS:
                raise RuntimeError(f"Gemini API 錯誤 HTTP {resp.status_code}：{resp.text[:500]}")
            error = f"HTTP {resp.status_code}"
        except (requests.ConnectionError, requests.Timeout) as e:
            error = repr(e)
        if attempt == MAX_RETRIES:
            raise RuntimeError(f"Gemini API 呼叫連續失敗 {MAX_RETRIES} 次，最後錯誤：{error}")
        wait = RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1)
        log.warning("Gemini API 第 %d 次呼叫失敗（%s），%d 秒後重試", attempt, error, wait)
        time.sleep(wait)


def summarize_with_gemini(api_key: str, model: str, raw_data: str) -> list[str]:
    """呼叫 Gemini 並依 SECTION_DELIMITER 拆成多段文字，回傳 list。"""
    if not api_key:
        raise ValueError("尚未設定 GEMINI_API_KEY")

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": raw_data}]}],
        # 僅適用 Gemini 3 系列（2.5 系列要改用 thinkingBudget）。
        # temperature 刻意不設定：官方強烈建議 Gemini 3 維持預設 1.0，調低可能導致重複迴圈或品質下降。
        # thinkingLevel 預設為 medium，這裡拉到 high 以換取更好的新聞篩選與歸納品質。
        # 思考 token 也計入 maxOutputTokens，直接給到模型上限避免被截斷。
        "generationConfig": {
            "thinkingConfig": {"thinkingLevel": "high"},
            "maxOutputTokens": 65536,
        },
    }
    data = _post_with_retry(GEMINI_ENDPOINT.format(model=model), api_key, payload)

    try:
        candidate = data["candidates"][0]
        full_text = "".join(p.get("text", "") for p in candidate["content"]["parts"]).strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Gemini 回應格式異常，無法解析: {data}") from e
    if candidate.get("finishReason") != "STOP":
        log.warning("Gemini 回應非正常結束（finishReason=%s），內容可能不完整", candidate.get("finishReason"))

    sections = [s.strip() for s in full_text.split(SECTION_DELIMITER)]
    sections = [s for s in sections if s]  # 過濾空段落
    if not sections:
        raise RuntimeError("Gemini 回應內容為空，無法拆分段落")
    return sections
