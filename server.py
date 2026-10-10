"""Local HTTP app, using only Python's standard library."""
import argparse
import base64
import binascii
import hashlib
import json
import mimetypes
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from cleaner import MAX_BYTES, inspect, parse_csv, run_agent
from gemini import GeminiPlanner, PlannerError

ROOT = Path(__file__).resolve().parent


def settings():
    config = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                config[key.strip()] = value.strip().strip("\"'")
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "PORT"):
        if name in os.environ:
            config[name] = os.environ[name]
    return config


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # URL paths are enough for local diagnostics; never log payloads or secrets.
        pass

    def reply(self, code, body, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            config = settings()
            return self.reply(200, {"configured": bool(config.get("GEMINI_API_KEY", "").strip()),
                                    "model": config.get("GEMINI_MODEL") or "gemini-3.5-flash-lite"})
        if path == "/api/sample":
            return self.reply(200, (ROOT / "samples" / "messy_lab_data.csv").read_bytes(), "text/csv; charset=utf-8")
        # Only the built frontend is public; .env and project files cannot be served.
        public = (ROOT / "dist").resolve()
        target = (public / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
        if not target.is_relative_to(public) or not target.is_file():
            if path == "/" and not (public / "index.html").exists():
                return self.reply(503, "Build the interface first: npm install && npm run build", "text/plain")
            return self.reply(404, {"error": "Not found."})
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return self.reply(200, target.read_bytes(), mime)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/inspect", "/api/clean"):
            return self.reply(404, {"error": "Not found."})
        origin = self.headers.get("Origin")
        port = self.server.server_port
        allowed = {f"http://{host}:{p}" for host in ("localhost", "127.0.0.1") for p in (port, 5173)}
        if origin and origin not in allowed:
            return self.reply(403, {"error": "Use the local app to submit files."})
        if self.headers.get_content_type() != "application/json":
            return self.reply(415, {"error": "Expected a JSON request."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8 * 1024 * 1024:
                raise ValueError("The request is empty or too large. Upload a CSV up to 5 MB.")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict) or not isinstance(payload.get("file"), str):
                raise ValueError("Choose a CSV file first.")
            raw = base64.b64decode(payload["file"], validate=True)
            if len(raw) > MAX_BYTES:
                raise ValueError("Upload a CSV up to 5 MB.")
            data = parse_csv(raw, payload.get("delimiter", "auto"))
            if path == "/api/inspect":
                return self.reply(200, {"profile": inspect(data), "headers": data.headers,
                                        "rows": data.rows[:20], "delimiter": data.delimiter,
                                        "encoding": data.encoding})
            config = settings()
            planner = GeminiPlanner(key=config.get("GEMINI_API_KEY", ""),
                                    model=config.get("GEMINI_MODEL") or "gemini-3.5-flash-lite")
            options = payload.get("options", {})
            # This acknowledgement makes the cloud transfer visible in the UI.
            if payload.get("allow_profile_transfer") is not True:
                raise ValueError("Confirm that Gemini may receive column names and quality statistics.")
        except (ValueError, TypeError, binascii.Error, PlannerError) as exc:
            return self.reply(400, {"error": str(exc)})
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        def emit(event):
            self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()

        try:
            cleaned, csv_text, report = run_agent(data, options, planner, emit)
            report["input_sha256"] = hashlib.sha256(raw).hexdigest()
            emit({"phase": "result", "csv": csv_text, "report": report,
                  "headers": cleaned.headers, "rows": cleaned.rows[:20],
                  "row_ids": cleaned.row_ids[:20], "column_ids": cleaned.column_ids})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (ValueError, PlannerError) as exc:
            emit({"phase": "error", "message": str(exc)})
        except Exception:
            emit({"phase": "error", "message": "The agent encountered an unexpected error. No result was exported."})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Dataset Cleaning Agent locally.")
    parser.add_argument("--port", type=int, default=int(settings().get("PORT") or 8000))
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Dataset Cleaning Agent: http://127.0.0.1:{args.port}", flush=True)
    print("Add your Gemini key to .env to enable cleaning. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
