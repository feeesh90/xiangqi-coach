import json
import os
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_DIR=Path.home()/".xiangqi-coach"
CONFIG_FILE=CONFIG_DIR/"api_settings.json"

DEFAULTS={
    "enabled":False,
    "endpoint":"",
    "model":"",
    "api_key":"",
    "save_key":False,
    "auto_explain":False,
    "timeout":25,
    "temperature":0.2,
}

class ApiError(RuntimeError):
    pass

class ApiClient:
    def __init__(self):
        self.settings=self.load()

    def load(self):
        s=dict(DEFAULTS)
        try:
            if CONFIG_FILE.exists():
                obj=json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(obj,dict):s.update(obj)
        except Exception:
            pass
        # Environment variable always wins when present.
        env=os.getenv("XIANGQI_API_KEY","").strip()
        if env:s["api_key"]=env
        return s

    def save(self,settings):
        s=dict(DEFAULTS);s.update(settings or {})
        CONFIG_DIR.mkdir(parents=True,exist_ok=True)
        out=dict(s)
        if not out.get("save_key"):
            out["api_key"]=""
        CONFIG_FILE.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
        # Keep the current session key even when not persisted.
        self.settings=s

    def configured(self):
        s=self.settings
        return bool(s.get("enabled") and s.get("endpoint","").strip()
                    and s.get("model","").strip() and s.get("api_key","").strip())

    def status_text(self):
        if not self.settings.get("enabled"):return "API未启用"
        if not self.configured():return "API待配置"
        return "API已连接配置"

    def _request(self,messages,max_tokens=700,response_format=None):
        s=self.settings
        if not self.configured():
            raise ApiError("API尚未启用或配置不完整")
        endpoint=s["endpoint"].strip()
        payload={
            "model":s["model"].strip(),
            "messages":messages,
            "temperature":float(s.get("temperature",0.2)),
            "max_tokens":int(max_tokens),
            "stream":False,
        }
        if response_format:
            payload["response_format"]=response_format
        data=json.dumps(payload,ensure_ascii=False).encode("utf-8")
        req=urllib.request.Request(
            endpoint,data=data,method="POST",
            headers={
                "Content-Type":"application/json",
                "Authorization":"Bearer "+s["api_key"].strip(),
                "User-Agent":"XiangqiCoach/8.2",
            }
        )
        try:
            with urllib.request.urlopen(req,timeout=int(s.get("timeout",25))) as r:
                raw=r.read().decode("utf-8","replace")
        except urllib.error.HTTPError as e:
            body=e.read().decode("utf-8","replace")[:1000]
            raise ApiError(f"HTTP {e.code}: {body}") from e
        except Exception as e:
            raise ApiError(str(e)) from e

        try:
            obj=json.loads(raw)
        except Exception:
            raise ApiError("API返回的不是JSON："+raw[:500])

        # OpenAI-compatible chat/completions shape.
        try:
            content=obj["choices"][0]["message"]["content"]
            if content:return str(content).strip()
        except Exception:
            pass

        # Some compatible gateways return top-level output_text/content.
        for k in ("output_text","content","text"):
            if obj.get(k):return str(obj[k]).strip()
        raise ApiError("API返回中没有可识别的文本内容")

    def test(self):
        return self._request([
            {"role":"system","content":"你是一个连接测试助手。只回复“连接成功”。"},
            {"role":"user","content":"测试"}
        ],max_tokens=20)

    @staticmethod
    def _json_object(text):
        text=str(text or "").strip()
        if text.startswith("```"):
            lines=text.splitlines()
            if lines and lines[0].lstrip().startswith("```"):lines=lines[1:]
            if lines and lines[-1].strip()=="```":lines=lines[:-1]
            text="\n".join(lines).strip()
        try:
            obj=json.loads(text)
        except Exception:
            start=text.find("{");end=text.rfind("}")
            if start<0 or end<=start:
                raise ApiError("API没有返回要求的JSON选着结果")
            try:obj=json.loads(text[start:end+1])
            except Exception as e:raise ApiError("API选着JSON无法解析") from e
        if not isinstance(obj,dict):raise ApiError("API选着结果必须是JSON对象")
        return obj

    def choose_move(self,context,allowed_moves):
        allowed=[]
        for move in allowed_moves:
            move=str(move or "").strip().lower()
            if move and move not in allowed:allowed.append(move)
        if not allowed:raise ApiError("当前引擎没有提供可供API选择的候选着")

        system=(
            "你是中国象棋代走决策者。当前引擎已经完成局面搜索，你必须参考它的候选着、"
            "评分、主变化和大师棋谱，从白名单中选择一着。只能选择输入 allowed_moves 中"
            "完全一致的UCCI着法，禁止自创着法。只输出JSON对象，不要Markdown，格式必须为"
            '{"move":"h2e2","reason":"不超过120字的具体理由"}。'
        )
        payload=dict(context or {})
        payload["allowed_moves"]=allowed
        user=(
            "请根据以下当前引擎分析选择代走着法。JSON中的move必须来自allowed_moves：\n"
            +json.dumps(payload,ensure_ascii=False,separators=(",",":"))
        )
        messages=[
            {"role":"system","content":system},
            {"role":"user","content":user},
        ]
        try:
            text=self._request(messages,max_tokens=260,
                               response_format={"type":"json_object"})
        except ApiError as e:
            # Older OpenAI-compatible gateways may reject response_format.
            # Retry only for a format/capability error, not for timeouts or auth.
            message=str(e).lower()
            if not any(x in message for x in ("http 400","http 404","http 422","response_format")):
                raise
            text=self._request(messages,max_tokens=260)
        obj=self._json_object(text)
        move=str(obj.get("move") or obj.get("ucci") or "").strip().lower()
        if move not in allowed:
            raise ApiError(f"API选择 {move or '空着法'}，不在 当前引擎 候选白名单中")
        reason=str(obj.get("reason") or obj.get("explanation") or "").strip()
        return {"move":move,"reason":reason[:500]}

    def explain_xiangqi(self,context):
        system=(
            "你是一名参与决策复核的中国象棋教练。当前引擎 是最终棋力裁决者。"
            "你要主动比较候选方案、核对主变化、指出风险并给出人类可执行的计划，"
            "但不能推翻、替换或重新指定最佳着，也不能编造输入中不存在的着法。"
            "不要输出数值分数。解释必须具体到棋子、线路、威胁、对手最强应手和下一步计划。"
            "若信息不足，明确说依据主变化只能确认到哪里。"
            "用简洁中文回答，控制在500字以内。"
        )
        user=(
            f"任务类型：{context.get('task_type','推荐方案评议')}\n"
            "请协作评议下面这次 当前引擎 分析。\n\n"
            f"当前FEN：{context.get('fen','')}\n"
            f"当前方案：{context.get('move','')}\n"
            f"方案身份：{context.get('plan_label','首选')}\n"
            f"当前引擎主变化：{context.get('pv','')}\n"
            f"其他候选方案：{context.get('alternatives','无')}\n"
            f"本地规则解释：{context.get('local_explanation','')}\n"
            f"大师棋谱参考：{context.get('book','无')}\n\n"
            "请按以下五段输出：\n"
            "1. 结论复核（明确仍以当前引擎首选为最终落子）\n"
            "2. 候选方案差别\n"
            "3. 对方最强回应为什么麻烦\n"
            "4. 我方后续计划\n"
            "5. 实战中最容易犯的错误"
        )
        return self._request([
            {"role":"system","content":system},
            {"role":"user","content":user}
        ],max_tokens=800)
