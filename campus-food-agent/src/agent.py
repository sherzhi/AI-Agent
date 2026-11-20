"""今天吃什麼：校園餐廳推薦 Agent。

流程：使用者輸入 → LLM 決定要不要呼叫工具 → 執行工具 → 把結果交回 LLM → 最後回答。
每個問題最多跑 MAX_STEPS 輪，避免 LLM 一直呼叫工具停不下來。
"""
import argparse
import json
import os
import sys
import time

from src.llm import LLMClient, LLMError
from src.tools import find_restaurant

MAX_STEPS = 3  

SYSTEM_PROMPT = """你是「今天吃什麼」校園餐廳推薦助理，一律使用臺灣繁體中文回答，絕對不可以出現簡體字（例如要寫「價格」「備註」「素食」，不能寫「价格」「备注」），簡短好讀。

你只能根據 find_restaurant 工具回傳的資料推薦餐廳，不可以自己編餐廳、價格或地址。

怎麼判斷條件：
- category：使用者想吃的食物類型，例如「麵」「飯」「火鍋」。「葷食」「素食」不是食物類型，不能填在 category。
- veg：使用者說「我吃素」→ vegetarian；說「我吃素，朋友吃葷」「葷素都有」→ mixed。只有使用者說自己吃素才填 veg；說「我吃葷」「什麼都吃」或完全沒提到素食，veg 一律不要填。
- max_price：使用者說的預算上限（元），沒說就不要填。
- keyword：使用者提到的餐點名稱（例如「餛飩」「咖哩」「排骨」），沒提到就不要填。想吃的是一道菜時用 keyword，想吃的是一類食物（麵、飯）時用 category。
- 如果使用者完全沒有給條件（例如「今天不知道吃什麼」），就不帶任何參數呼叫工具，把各類型有哪些店列出來讓使用者自己選，不要替他決定。

回答規則：
- 有結果時推薦 1～3 間，說明店名、類型、價格範圍、位置，以及為什麼符合。
- 每間店有 verification：dish 代表有餐點確實符合條件，要列出 matched_dishes 裡的餐點和價格；shop_only 代表只確認到店家層級，要告訴使用者餐點是否符合還沒驗證、請到現場確認；不要把 shop_only 說成已確認。
- 餐點素食標示是「待確認」或「葷」的，不能說成素食。
- 用 keyword 搜尋時，如果 unsearched_shops 大於 0，要提醒使用者有這麼多間店還沒有餐點資料，所以沒搜尋到。
- 工具回傳 no_match 時，直接說資料裡找不到，建議放寬條件，不要編店。
- 工具回傳 error 時，說明查詢出了問題，請稍後再試，不要編店。
- 只照工具回傳的店名、類型、位置、價格說明，不要把備註欄的內部筆記整句照抄給使用者，也不要把店名或餐點名稱寫錯字。
- 價格是大約值，素食資訊是人工整理，提醒使用者到現場再確認。
- 不是餐廳推薦的問題（例如天氣），說明你只能回答校園餐廳推薦。"""

TOOLS = [{
    "type": "function",
    "function": {
        "name": "find_restaurant",
        "description": "從校園餐廳資料表依條件篩選餐廳。四個參數都可以不填；全部不填時回傳各類型有哪些店。有餐點資料的店會驗證餐點是否符合素食和預算。",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "想吃的類型，例如 麵、飯、火鍋、咖哩飯、炸物"},
                "veg": {"type": "string", "enum": ["vegetarian", "mixed"],
                        "description": "vegetarian＝吃素；mixed＝葷素同行（有素食且賣葷食）。沒提到素食就不要填"},
                "max_price": {"type": "integer", "description": "預算上限（元）"},
                "keyword": {"type": "string", "description": "餐點名稱關鍵字，例如 餛飩、咖哩、排骨"},
            },
            "required": [],
        },
    },
}]

REGISTRY = {"find_restaurant": find_restaurant}


def run_tool(name, raw_arguments):
    """執行 LLM 要求的工具。任何錯誤都轉成 error 結果，不讓程式當機。"""
    func = REGISTRY.get(name)
    if func is None:
        return {"status": "error", "message": f"沒有這個工具：{name}"}
    try:
        args = json.loads(raw_arguments) if raw_arguments else {}
        if not isinstance(args, dict):
            raise ValueError("參數必須是物件")
    except (json.JSONDecodeError, ValueError) as e:
        return {"status": "error", "message": f"工具參數不是合法的 JSON：{e}"}
    try:
        return func(**args)
    except TypeError as e:  
        return {"status": "error", "message": f"工具參數錯誤：{e}"}
    except Exception as e:  
        return {"status": "error", "message": f"工具執行失敗：{e}"}


def clean_text(text):
    """去掉輸入法或終端機產生的壞字元（無法編碼的字），避免寫入紀錄時當機。"""
    return text.encode("utf-8", "ignore").decode("utf-8", "ignore").strip()


def run_agent(user_input, llm, trace=None):
    """跑一個問題，回傳最後的回答。trace 是一個 list，會記下每一步。"""
    trace = trace if trace is not None else []
    trace.append({"type": "user_input", "content": user_input})
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input}]

    for step in range(1, MAX_STEPS + 1):
        try:
            msg = llm.chat(messages, TOOLS)
        except LLMError as e:  
            answer = "抱歉，連不上 LLM 伺服器或它回傳了看不懂的內容，請確認設定後再試一次。"
            trace.append({"type": "llm_error", "step": step, "content": str(e)})
            trace.append({"type": "final", "step": step, "content": answer})
            return answer

        calls = msg["tool_calls"]
        if not calls: 
            answer = msg["content"].strip() or "抱歉，我沒有產生回答，請換個方式再問一次。"
            trace.append({"type": "final", "step": step, "content": answer})
            return answer

        messages.append({
            "role": "assistant", "content": msg["content"],
            "tool_calls": [{"id": c["id"], "type": "function",
                            "function": {"name": c["name"], "arguments": c["arguments"]}}
                           for c in calls]})
        for c in calls:
            trace.append({"type": "decision", "step": step, "tool": c["name"],
                          "arguments": c["arguments"], "thought": msg["content"].strip()})
            result = run_tool(c["name"], c["arguments"])
            trace.append({"type": "observation", "step": step, "tool": c["name"],
                          "result": result})
            messages.append({"role": "tool", "tool_call_id": c["id"],
                             "content": json.dumps(result, ensure_ascii=False)})

    answer = "抱歉，這個問題我處理太久了，請把條件說得簡單一點再試一次。"
    trace.append({"type": "final", "step": MAX_STEPS, "content": answer, "note": "超過最大輪數"})
    return answer


def format_trace(trace):
    """把 trace 排成好讀的文字（給終端機和報告用）。"""
    lines = []
    for t in trace:
        kind = t["type"]
        if kind == "user_input":
            lines.append(f"[使用者輸入] {t['content']}")
        elif kind == "decision":
            try:  
                shown = json.dumps(json.loads(t["arguments"]), ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                shown = t["arguments"]
            lines.append(f"[Agent 決策 第{t['step']}輪] 呼叫工具 {t['tool']}，參數 {shown}")
        elif kind == "observation":
            lines.append(f"[觀察結果] {json.dumps(t['result'], ensure_ascii=False)}")
        elif kind == "llm_error":
            lines.append(f"[LLM 錯誤 第{t['step']}輪] {t['content']}")
        elif kind == "final":
            lines.append(f"[最後回答] {t['content']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="今天吃什麼：校園餐廳推薦 Agent")
    parser.add_argument("--once", help="只問一個問題就結束")
    parser.add_argument("--quiet", action="store_true", help="不印出執行過程，只印最後回答")
    args = parser.parse_args()

    base_url = os.environ.get("LLM_BASE_URL")
    model = os.environ.get("LLM_MODEL")
    if not base_url or not model:
        sys.exit("請先設定環境變數 LLM_BASE_URL（LLM 伺服器位址）和 LLM_MODEL（模型名稱），見 README。")
    llm = LLMClient(base_url, model, os.environ.get("LLM_API_KEY", "not-needed"))
    os.makedirs("logs", exist_ok=True)

    def ask(question):
        question = clean_text(question)
        trace = []
        answer = run_agent(question, llm, trace)
        if not args.quiet:
            print(format_trace(trace[:-1]))
            print()
        print(answer)
        try:  # 寫紀錄失敗（例如檔案無法寫入）不應該讓 Agent 當機
            with open("logs/trace.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "trace": trace},
                                   ensure_ascii=False) + "\n")
        except (OSError, UnicodeError) as e:
            print(f"（執行紀錄沒有存成功：{e}）", file=sys.stderr)

    if args.once:
        ask(args.once)
        return
    print("今天吃什麼？輸入問題（直接按 Enter 或輸入 q 結束）")
    while True:
        try:
            q = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q in ("", "q", "quit", "exit"):
            break
        ask(q)


if __name__ == "__main__":
    main()
