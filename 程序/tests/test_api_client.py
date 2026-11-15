import json,threading
import pytest
from http.server import BaseHTTPRequestHandler,HTTPServer
from api_client import ApiClient,ApiError

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n=int(self.headers.get("Content-Length","0"))
        body=json.loads(self.rfile.read(n).decode())
        assert body["model"]=="test-model"
        self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
        self.wfile.write(json.dumps({"choices":[{"message":{"content":"连接成功"}}]}).encode())
    def log_message(self,*a):pass

srv=HTTPServer(("127.0.0.1",0),H)
threading.Thread(target=srv.serve_forever,daemon=True).start()
c=ApiClient()
c.settings={
    "enabled":True,
    "endpoint":f"http://127.0.0.1:{srv.server_port}/v1/chat/completions",
    "model":"test-model","api_key":"x","timeout":3,"temperature":0.2
}
assert c.test()=="连接成功"
srv.shutdown()
print("API_CLIENT_OK")


def test_api_collaboration_receives_engine_candidates_and_guardrail():
    client=ApiClient()
    captured={}
    def fake_request(messages,max_tokens=700):
        captured["messages"]=messages
        return "ok"
    client._request=fake_request
    result=client.explain_xiangqi({
        "task_type":"四步推演评议",
        "fen":"test-fen",
        "move":"炮二平五",
        "plan_label":"Pikafish首选",
        "pv":"h2e2 h9g7",
        "alternatives":"方案2:马二进三",
        "local_explanation":"本地事实",
        "book":"大师参考",
    })
    text="\n".join(m["content"] for m in captured["messages"])
    assert result=="ok"
    assert "四步推演评议" in text
    assert "方案2:马二进三" in text
    assert "不能推翻、替换或重新指定最佳着" in text


def test_choose_move_uses_json_and_accepts_only_whitelisted_candidate():
    client=ApiClient()
    captured={}
    def fake_request(messages,max_tokens=700,response_format=None):
        captured["messages"]=messages
        captured["response_format"]=response_format
        return '{"move":"c3c4","reason":"保留中路弹性"}'
    client._request=fake_request

    choice=client.choose_move({
        "fen":"test-fen",
        "candidates":[{"move":"h2e2"},{"move":"c3c4"}],
        "book":"大师参考",
    },["h2e2","c3c4"])

    assert choice["move"]=="c3c4"
    assert choice["reason"]=="保留中路弹性"
    assert captured["response_format"]=={"type":"json_object"}
    assert "只能选择" in captured["messages"][0]["content"]


def test_choose_move_rejects_move_outside_engine_candidates():
    client=ApiClient()
    client._request=lambda *args,**kwargs:'```json\n{"move":"a0a1","reason":"自创"}\n```'

    with pytest.raises(ApiError,match="不在 当前引擎 候选"):
        client.choose_move({"fen":"test","candidates":[]},["h2e2","c3c4"])


def test_choose_move_retries_without_json_mode_for_compatible_gateway():
    client=ApiClient()
    calls=[]
    def fake_request(messages,max_tokens=700,response_format=None):
        calls.append(response_format)
        if response_format:raise ApiError("HTTP 400: response_format unsupported")
        return '{"move":"h2e2","reason":"兼容模式"}'
    client._request=fake_request

    choice=client.choose_move({"fen":"test"},["h2e2"])

    assert choice["move"]=="h2e2"
    assert calls==[{"type":"json_object"},None]
