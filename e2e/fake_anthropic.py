"""Stand-in for the Anthropic Messages API (streaming only) so the E2E run
exercises the real SDK, prompts, parsing, policy engine and storage for free.
Every request is appended to fake_requests.jsonl for verification."""
import json, sqlite3, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _evidence(db_path: str) -> str:
    """The newest stored Opus evidence in the sandbox DB copy: a real, schema-valid answer."""
    c = sqlite3.connect(db_path)
    for (aj,) in c.execute("SELECT assessment_json FROM assessments WHERE model LIKE 'claude-opus%' ORDER BY id DESC"):
        ev = json.loads(aj).get("evidence")
        if ev and ev.get("ratings"):
            return json.dumps(ev)
    raise SystemExit("no stored Opus assessment to replay in " + db_path)

EVIDENCE = _evidence(sys.argv[2] if len(sys.argv) > 2 else "scout.db")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        with open("fake_requests.jsonl", "a") as f:
            f.write(json.dumps({"t": time.time(), "path": self.path, "model": body.get("model"),
                                "system_chars": len(json.dumps(body.get("system"))), "stream": body.get("stream")}) + "\n")
        text = EVIDENCE
        model = body.get("model")
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        def ev(name, data):
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()); self.wfile.flush()
        usage = {"input_tokens": 12000, "output_tokens": 2500, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        ev("message_start", {"type": "message_start", "message": {"id": "msg_fake", "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None, "usage": {**usage, "output_tokens": 1}}})
        ev("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        for i in range(0, len(text), 4000):
            ev("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[i:i + 4000]}})
        ev("content_block_stop", {"type": "content_block_stop", "index": 0})
        ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 2500}})
        ev("message_stop", {"type": "message_stop"})

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8799), H).serve_forever()
