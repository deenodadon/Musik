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
