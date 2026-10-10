"""验证更新密码鉴权；不启动真实采集任务。"""
import hashlib
import importlib.util
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "update_server", Path(__file__).resolve().parents[1] / "scripts" / "serve.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class UpdateAuthTests(unittest.TestCase):
    def test_password_required_and_old_token_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            password_file = Path(directory) / "password.sha256"
            password_file.write_text(hashlib.sha256(b"test-password").hexdigest())
            running = Mock()
            running.poll.return_value = None
            with patch.object(server, "PASSWORD_FILE", password_file), \
                    patch.dict(server._proc, {"running": running}), \
                    patch.object(server.subprocess, "Popen") as spawn:
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                thread.start()
                try:
                    cases = [
                        ("/api/v1/update", {}, 401),
                        ("/api/v1/update", {"X-Update-Password": "wrong"}, 401),
                        ("/api/v1/update?utoken=test-password", {"X-Update-Token": "test-password"}, 401),
                        ("/api/v1/update", {"X-Update-Password": "test-password"}, 409),
                    ]
                    for path, headers, expected in cases:
                        with self.subTest(headers=headers, path=path):
                            conn = HTTPConnection(*httpd.server_address)
                            conn.request("POST", path, headers=headers)
                            response = conn.getresponse()
                            self.assertEqual(response.status, expected)
                            response.read()
                            conn.close()
                    spawn.assert_not_called()
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    thread.join()


if __name__ == "__main__":
    unittest.main()
