# 今天吃什麼：校園餐廳推薦 Agent（作業 2）

用中文說「我吃素，朋友吃葷，想吃麵，100 元以內」，Agent 會把這句話轉成查詢條件，呼叫餐廳查詢工具，
從 `data/restaurants.csv`（29 間三峽北大附近的餐廳）找出符合的店推薦給你。
沒有給任何條件時，會把各類型有哪些店列出來讓你自己選。查不到就直說，不編餐廳。

## 資料夾

```
src/agent.py        Agent 主程式（輸入 → LLM → 工具 → 輸出）
src/tools.py        餐廳查詢工具 find_restaurant
src/llm.py          連到 LLM 伺服器（OpenAI 相容 API）
data/restaurants.csv 餐廳資料，一間店一行
data/dishes.csv      餐點資料，一道餐點一行（目前 24 間店、269 道餐點）
documents/          作業 2 說明文件（Tool 定義、執行追蹤、失敗情境）
Containerfile       Podman 建置檔
```

## 架構

```
使用者輸入 → Agent → LLM（決定要不要呼叫工具）→ find_restaurant 讀 CSV 篩選
          → 結果回到 LLM → 最後回答
```

- 單一工具（Tool Use）加簡單的迴圈，一個問題最多 3 輪。
- 沒有用 ReAct：一次工具呼叫就能完成查詢，不需要反覆思考和行動（原因寫在 `documents/`）。
- LLM 伺服器在容器外，其他東西（程式、工具、資料）都在同一個容器。

## 需要準備

1. 一個 OpenAI 相容的 LLM 伺服器（課程提供的、本機 Ollama、或雲端 API 都可以），
   而且模型要支援 tool calling（function calling）。
2. Podman。

用三個環境變數告訴程式怎麼連 LLM：

| 變數 | 意思 | 範例 |
|---|---|---|
| `LLM_BASE_URL` | LLM 伺服器位址（要含 /v1） | `http://host.containers.internal:11434/v1` |
| `LLM_MODEL` | 模型名稱 | `qwen2.5:7b` |
| `LLM_API_KEY` | 金鑰，不需要的話可以不設 | `sk-...` |

`host.containers.internal` 是容器裡指到你電腦本機的位址（LLM 跑在你自己電腦上時用）。
金鑰不要寫進程式碼，也不要上傳到 GitHub。

## 執行（Podman）

```bash
# 建置
podman build -t campus-food-agent .

# 互動模式（輸入 q 結束）；logs 會存在目前資料夾的 logs/
podman run -it --rm \
  -e LLM_BASE_URL=http://host.containers.internal:11434/v1 \
  -e LLM_MODEL=qwen2.5:7b \
  -v ./logs:/app/logs \
  campus-food-agent

# 只問一個問題
podman run --rm \
  -e LLM_BASE_URL=... -e LLM_MODEL=... \
  campus-food-agent python -m src.agent --once "我吃素，朋友吃葷，想吃麵，100元以內"
```

畫面上會印出每一步：使用者輸入、Agent 決策（呼叫哪個工具、參數）、觀察結果、最後回答。
完整紀錄存在 `logs/trace.jsonl`。

不用容器的話（要有 Python 3.10 以上，程式本身不需要安裝任何套件）：

```bash
export LLM_BASE_URL=... LLM_MODEL=...
python -m src.agent
```

## Tool：find_restaurant

| 項目 | 說明 |
|---|---|
| 名稱 | `find_restaurant` |
| 目的 | 依條件從餐廳資料表篩選餐廳 |
| 輸入 | `category`（字串，選填）想吃的類型；`veg`（選填）`vegetarian`＝吃素、`mixed`＝葷素同行；`max_price`（整數，選填）預算上限（元）；`keyword`（字串，選填）餐點名稱關鍵字 |
| 輸出 | `status` = `ok`（有結果，或沒給條件時回傳各類型清單）／`no_match`（沒有符合）／`error`（出錯）；有結果時附上最多 5 間店的欄位，以及 `verification`（`dish`＝餐點層級已驗證、`shop_only`＝只確認到店家層級）和 `matched_dishes`（符合條件的餐點，最多 3 道） |
| Error Handling | 資料檔不存在、欄位不完整、參數不合法都回傳 `status: error` 和說明，不丟例外；LLM 給錯的工具名稱、不是 JSON 的參數、不存在的參數，Agent 也轉成 error 回給 LLM |

篩選規則：
- `category`：店的「類型」欄有這個字就符合。
- `veg=vegetarian`：素食選項是「有」。
- `veg=mixed`：素食選項是「有」而且賣葷食是「是」。
- 素食選項或賣葷食是「待確認」的店不算符合。
- `max_price`：店的「最低價」小於等於預算。最低價空白的店，有指定預算時不算符合。

餐點層級驗證（有 `dishes.csv` 資料的店）：
- 價格：要有至少一道餐點 ≤ 預算，否則這間店排除（就算店家最低價看起來符合）。
- 素食：要有一道「價格符合」且素食標示是全素／奶素／蛋奶素／鍋邊素／五辛素／素食的餐點，才算 `verification="dish"`，並列出那些餐點。標示「葷」或「待確認」的餐點不會被當成素食。
- 如果店家有素食選項、但沒有餐點能被驗證，仍會回傳，但標成 `shop_only` 並附註「請到現場確認」。
- `keyword`：只搜尋有餐點資料的店，比對餐點名稱；回傳 `unsearched_shops` 告訴你有幾間符合其他條件的店沒有餐點資料所以沒搜到。
- 沒有餐點資料的店：照舊用店家層級資料，標 `shop_only`。
- 排序：餐點已驗證的店排在前面。

## 資料欄位（data/restaurants.csv）

店名、類型（可以有多個，用「、」隔開）、賣葷食（是／否／待確認）、素食選項（有／無／待確認）、
素食類型（全素／蛋奶素／鍋邊素／五辛素／奶素）、最低價、最高價（主餐，不含飲料）、位置、備註。
價格是大約值，素食資訊是人工看菜單整理的，請到現場再確認。要新增餐廳只要在 CSV 加一行，不用改程式。

## 餐點資料（data/dishes.csv）

欄位：店名（要和 restaurants.csv 一模一樣）、餐點、價格、素食（全素／奶素／蛋奶素／鍋邊素／五辛素／素食／葷）、備註。
沒有素食選項的店，餐點一律填「葷」；非全素店的素食餐點填「鍋邊素」。可以用環境變數 `DISHES_CSV` 換檔案；檔案不存在時，所有店都退回店家層級。

## 已知限制

- 餐點資料有 24 間店；珍城自助餐、糖匠秝果、大雄料理店、官小二酸菜魚、白甘蔗創意牛肉麵還沒有餐點資料，用菜名找（keyword）找不到它們。
- 非全素店的素食餐點標「鍋邊素」，沒有素食選項的店，餐點一律標「葷」。
- 「類型」是看店家、「預算和素食」是看餐點，兩個分開比，所以可能出現店家類型符合、但符合預算的餐點是別的東西（例如問麵，卻列出薯條）。
- 餐點資料是從菜單照片人工整理的，部分價格是小份或最低價，點餐前請到現場確認。
- 小菜、配菜也算餐點，所以「吃素、60 元以內」可能推薦到單點小菜（餐點名稱有標「小菜」或「配菜」）。
- 沒有餐點資料的店，價格篩選只看最低價，所以「100 元以內」也可能出現大部分餐點比 100 貴的店。
- 小模型有時會把「葷素同行」誤判成「吃素」，詳見 `documents/`。
