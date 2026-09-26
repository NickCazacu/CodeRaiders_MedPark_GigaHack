"""Client minimal pentru Ollama local: /api/chat cu `format` = schema JSON.

Doar biblioteca standard. Proxy-urile din mediu sunt ignorate, ca apelul să nu
plece niciodată din mașină. La eșec: o reîncercare, apoi rezultat cu `error`
(apelantul decide cum continuă, ședința nu se oprește).
"""
import json
import time
import urllib.error
import urllib.request

from LLM.config import check_local

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class OllamaClient:
    def __init__(self, cfg):
        o = cfg["ollama"]
        check_local(o["url"])
        self.url = o["url"].rstrip("/") + "/api/chat"
        self.o = o
        self.stages = cfg["stages"]

    def chat(self, stage, messages, schema, num_ctx=None):
        """Returnează dict: data (JSON parsat sau None), raw, error, attempts, meta."""
        st = self.stages[stage]
        body = {
            "model": self.o["model"],
            "messages": messages,
            "format": schema,
            "stream": False,
            "think": bool(st.get("think", False)),
            "keep_alive": self.o.get("keep_alive", "5m"),
            "options": {
                "temperature": self.o["temperature"],
                "num_ctx": num_ctx or self.o["num_ctx"],
                "num_predict": st.get("num_predict", 2048),
                "repeat_penalty": self.o.get("repeat_penalty", 1.0),
                "seed": self.o.get("seed"),
            },
        }
        errors, raw, meta = [], None, {}
        for attempt in range(1 + int(self.o.get("retries", 1))):
            if attempt and body["options"]["seed"] is not None:
                body["options"]["seed"] += attempt  # altfel reîncercarea reproduce exact aceeași buclă
            t0 = time.perf_counter()
            try:
                resp = self._post(body)
                meta = {k: resp.get(k) for k in ("prompt_eval_count", "eval_count", "done_reason",
                                                 "total_duration", "load_duration")}
                meta["seconds"] = round(time.perf_counter() - t0, 2)
                raw = resp.get("message", {}).get("content", "")
                if resp.get("done_reason") == "length":
                    raise ValueError(f"răspuns tăiat la num_predict={body['options']['num_predict']}")
                return {"data": json.loads(raw), "raw": raw, "error": None, "errors": errors,
                        "attempts": attempt + 1, "meta": meta, "thinking": resp.get("message", {}).get("thinking")}
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
                # json.JSONDecodeError e ValueError
                errors.append(f"încercarea {attempt + 1}: {type(e).__name__}: {e}")
                print(f"[llm] {stage}: {errors[-1]}")
        return {"data": None, "raw": raw, "error": errors[-1], "errors": errors,
                "attempts": len(errors), "meta": meta, "thinking": None}

    def _post(self, body):
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with _opener.open(req, timeout=self.o["timeout_s"]) as r:
            return json.loads(r.read().decode("utf-8"))
