"""連到 OpenAI 相容的 LLM 伺服器（例如 Ollama、vLLM、OpenAI）。

只用 Python 內建的 urllib，不需要另外安裝套件。
LLM 伺服器在容器外，位址、模型、金鑰用環境變數設定。
"""
import json
import urllib.error
import urllib.request


class LLMError(Exception):
    """連不上 LLM 伺服器，或回傳的內容看不懂。"""


class LLMClient:
    def __init__(self, base_url, model, api_key="not-needed", timeout=60):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def chat(self, messages, tools):
        """送出對話，回傳 {"content": 文字, "tool_calls": [{"id", "name", "arguments"}]}。"""
        body = json.dumps({"model": self.model, "messages": messages, "tools": tools,
                           "tool_choice": "auto", "temperature": 0}).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise LLMError(f"LLM 伺服器回傳 HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError(f"連不上 LLM 伺服器：{e}") from e
        except json.JSONDecodeError as e:
            raise LLMError("LLM 伺服器回傳的不是 JSON") from e

        try:
            msg = data["choices"][0]["message"]
            calls = [{"id": c.get("id", f"call_{i}"),
                      "name": c["function"]["name"],
                      "arguments": c["function"].get("arguments") or "{}"}
                     for i, c in enumerate(msg.get("tool_calls") or [])]
            return {"content": msg.get("content") or "", "tool_calls": calls}
        except (KeyError, IndexError, TypeError, AttributeError) as e:
            raise LLMError("LLM 伺服器回傳的格式看不懂") from e
