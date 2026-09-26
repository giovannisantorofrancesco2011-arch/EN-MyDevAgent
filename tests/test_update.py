import subprocess

from mydevagent import update


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


def test_update_pulls_new_commits(tmp_path, monkeypatch):
    github = tmp_path / "github"
    github.mkdir()
    git(github, "init", "-q", "-b", "main")
    (github / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    git(github, "add", ".")
    git(github, "commit", "-qm", "first")
    git(tmp_path, "clone", "-q", str(github), "home")
    monkeypatch.setattr(update, "HOME", tmp_path / "home")
    monkeypatch.delenv("MYDEVAGENT_OFFLINE")
    assert update.available() == 0
    result = update.update()
    assert result.ok and not result.restart and "latest version" in result.message

    (github / "vio.py").write_text("x = 1\n")
    git(github, "add", ".")
    git(github, "commit", "-qm", "Added /update")
    assert update.available() == 1
    result = update.update()
    assert result.ok and result.restart and result.changes == ["Added /update"]
    assert (tmp_path / "home" / "vio.py").is_file() and update.available() == 0

    (tmp_path / "home" / "vio.py").write_text("edited by hand\n")  # conflicting local changes
    (github / "vio.py").write_text("x = 2\n")
    git(github, "commit", "-qam", "Vio 2")
    result = update.update()
    assert not result.ok and "git stash" in result.message


def test_update_needs_a_git_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "HOME", tmp_path)
    result = update.update()
    assert not result.ok and "README" in result.message
    assert update.available() == 0
