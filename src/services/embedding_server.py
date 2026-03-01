"""Embeddingサーバー: モデル保持 + encode を1プロセスに集約するHTTPサーバー"""
import json
import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "localhost"
PORT = 52836
IDLE_TIMEOUT_SEC = 300  # 5分
MAX_REQUEST_BYTES = 10 * 1024 * 1024  # 10MB

MODEL_NAME = "cl-nagoya/ruri-v3-70m"
DOC_PREFIX = "検索文書: "
QUERY_PREFIX = "検索クエリ: "

logger = logging.getLogger("embedding_server")

# グローバル状態
_model = None
_last_access_time = time.time()


def _setup_logging():
    """ログを ~/.cache/cc-memory/embedding-server.log に出力する。起動のたびにトランケート。"""
    log_dir = os.path.expanduser("~/.cache/cc-memory")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "embedding-server.log")

    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _load_model():
    """sentence-transformersモデルをロードする。"""
    global _model
    logger.info(f"Model loading started: {MODEL_NAME}")
    logger.info(f"Python executable: {sys.executable}")
    try:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(MODEL_NAME)
        logger.info(f"Model loaded successfully: {MODEL_NAME}")
    except Exception as e:
        logger.error(f"Model loading failed: {e}")
        sys.exit(1)


class EmbeddingHandler(BaseHTTPRequestHandler):
    """HTTPリクエストハンドラ"""

    def log_message(self, format, *args):
        """デフォルトのstderrログを抑制し、loggerに転送する。"""
        logger.info(format % args)

    def _send_json(self, status_code: int, data: dict):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_GET(self):
        global _last_access_time
        _last_access_time = time.time()

        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        global _last_access_time
        _last_access_time = time.time()

        if self.path != "/encode":
            self._send_json(404, {"error": "Not found"})
            return

        # リクエストボディをパース
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > MAX_REQUEST_BYTES:
                self._send_json(413, {"error": "Request body too large"})
                return
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json(400, {"error": "Invalid request body"})
            return

        # バリデーション
        texts = data.get("texts")
        prefix_type = data.get("prefix")

        if not isinstance(texts, list) or not texts:
            self._send_json(400, {"error": "texts must be a non-empty list"})
            return
        if prefix_type not in ("document", "query"):
            self._send_json(400, {"error": 'prefix must be "document" or "query"'})
            return

        # prefix付与 + encode
        prefix = DOC_PREFIX if prefix_type == "document" else QUERY_PREFIX
        prefixed_texts = [prefix + t for t in texts]

        try:
            embeddings = _model.encode(prefixed_texts)
            result = [e.tolist() for e in embeddings]
            self._send_json(200, {"embeddings": result})
        except Exception as e:
            logger.error(f"encode failed: {e}")
            self._send_json(500, {"error": "Internal server error"})


def _idle_watchdog(server: ThreadingHTTPServer):
    """IDLE_TIMEOUT_SEC 無アクセスでサーバーを終了させるウォッチドッグ。"""
    while True:
        time.sleep(30)  # 30秒ごとにチェック
        elapsed = time.time() - _last_access_time
        if elapsed >= IDLE_TIMEOUT_SEC:
            logger.info(f"Shutting down: no access for {int(elapsed)}s")
            server.shutdown()
            return


def main():
    _setup_logging()
    _load_model()

    try:
        server = ThreadingHTTPServer((HOST, PORT), EmbeddingHandler)
    except OSError as e:
        logger.error(f"server_bind failed: {e}")
        sys.exit(1)

    logger.info(f"Embedding server listening on {HOST}:{PORT}")

    watchdog = threading.Thread(target=_idle_watchdog, args=(server,), daemon=True)
    watchdog.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down: KeyboardInterrupt")
    finally:
        server.server_close()
        logger.info("Server closed")


if __name__ == "__main__":
    main()
