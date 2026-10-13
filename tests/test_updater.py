import subprocess

from musik.core import Store
from musik.updater import check_and_update, read_status


def git(*arguments, cwd=None):
    return subprocess.check_output(["git", *arguments], cwd=cwd, text=True).strip()


def repository(tmp_path):
    root = tmp_path / "upstream"
    root.mkdir()
    git("init", "-b", "main", cwd=root)
    git("config", "user.name", "Test", cwd=root)
    git("config", "user.email", "test@example.com", cwd=root)
    (root / "version.txt").write_text("first")
    git("add", ".", cwd=root)
    git("commit", "-m", "first", cwd=root)
    return root


def test_update_detects_new_commits_without_touching_checkout(tmp_path):
    root = repository(tmp_path)
    store = Store(tmp_path / "state")
    installed = []
    def install(source):
        installed.append((source / "version.txt").read_text())
    check_and_update(store, root.as_uri(), install)
    assert installed == ["first"]
    assert read_status(store)["installed_commit"] == git("rev-parse", "HEAD", cwd=root)
    check_and_update(store, root.as_uri(), install)
    assert installed == ["first"]
    (root / "version.txt").write_text("second")
    git("add", ".", cwd=root)
    git("commit", "-m", "second", cwd=root)
    (root / "personal-note.txt").write_text("local changes must remain")
    check_and_update(store, root.as_uri(), install)
    assert installed == ["first", "second"]
    assert (root / "personal-note.txt").read_text() == "local changes must remain"
    assert "next launch" in read_status(store)["message"]


def test_failed_update_retries_and_does_not_mark_commit_installed(tmp_path):
    root = repository(tmp_path)
    store = Store(tmp_path / "state")
    def fail(source):
        raise RuntimeError("failed installation")
    check_and_update(store, root.as_uri(), fail)
    assert read_status(store)["installed_commit"] is None
    assert "retry" in read_status(store)["message"]
    installed = []
    check_and_update(store, root.as_uri(), lambda source: installed.append(True))
    assert installed == [True]
    assert read_status(store)["installed_commit"]


def test_private_update_uses_saved_git_credentials_without_prompts(tmp_path, monkeypatch):
    import base64
    import http.server
    import os
    import shlex
    import sys
    import threading
    from urllib.parse import urlparse

    root = repository(tmp_path)
    bare = tmp_path / 'private.git'
    git('clone', '--bare', str(root), str(bare))
    marker = tmp_path / 'credentials-used.txt'
    helper = tmp_path / 'credential-helper.py'
    helper.write_text(
        'import os, sys\n'
        'from pathlib import Path\n'
        f'Path({str(marker)!r}).write_text(os.environ.get("GIT_TERMINAL_PROMPT", "") + ":" + os.environ.get("GCM_INTERACTIVE", ""))\n'
        'if sys.argv[-1] == "get":\n'
        '    print("username=fixture")\n'
        '    print("password=fixture")\n')
    config = tmp_path / 'gitconfig'
    command = '!' + shlex.quote(sys.executable.replace('\\', '/')) + ' ' + shlex.quote(helper.as_posix())
    git('config', '--file', str(config), 'credential.helper', command)
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')
    authorization = 'Basic ' + base64.b64encode(b'fixture:fixture').decode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def serve(self):
            if self.headers.get('Authorization') != authorization:
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Basic realm="Private fixture"')
                self.end_headers()
                return
            parsed = urlparse(self.path)
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            environment = dict(os.environ, GIT_PROJECT_ROOT=str(tmp_path), GIT_HTTP_EXPORT_ALL='1',
                               REQUEST_METHOD=self.command, PATH_INFO=parsed.path, QUERY_STRING=parsed.query,
                               CONTENT_TYPE=self.headers.get('Content-Type', ''), CONTENT_LENGTH=str(len(body)),
                               REMOTE_USER='fixture', REMOTE_ADDR='127.0.0.1')
            result = subprocess.run(['git', 'http-backend'], input=body, capture_output=True, env=environment, timeout=10)
            headers, content = result.stdout.split(b'\r\n\r\n', 1)
            entries = [line.decode().split(':', 1) for line in headers.split(b'\r\n')]
            status = next((int(value.strip().split()[0]) for key, value in entries if key == 'Status'), 200)
            self.send_response(status)
            for key, value in entries:
                if key != 'Status':
                    self.send_header(key, value.strip())
            self.end_headers()
            self.wfile.write(content)
        do_GET = serve
        do_POST = serve
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = Store(tmp_path / 'state')
    installed = []
    try:
        check_and_update(store, f'http://127.0.0.1:{server.server_port}/private.git',
                         lambda source: installed.append((source/'version.txt').read_text()))
        assert installed == ['first']
        assert read_status(store)['installed_commit'] == git('rev-parse', 'HEAD', cwd=root)
        assert marker.read_text() == '0:never'
        assert 'fixture' not in (store.directory/'update.json').read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
