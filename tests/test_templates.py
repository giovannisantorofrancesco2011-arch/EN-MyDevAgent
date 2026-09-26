import importlib.util
import subprocess
import sys

import pytest

from mydevagent import templates, update


def test_target_folder_rules(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert templates.target_for(empty, "website") == empty  # empty folder: the project is created there
    work = tmp_path / "work"
    (work / "api").mkdir(parents=True)
    (work / "api" / "main.py").write_text("x = 1\n")
    assert templates.target_for(work, "api") == work / "api-2"
    assert templates.target_for(work, "api", "my-api") == work / "my-api"
    with pytest.raises(ValueError):
        templates.target_for(work, "api", "api")  # already exists and is not empty
    with pytest.raises(ValueError):
        templates.target_for(work, "api", "../outside")
    monkeypatch.setattr(update, "HOME", work)
    assert templates.target_for(work, "website") == tmp_path / "website"  # never inside the MyDevAgent folder


@pytest.mark.parametrize("kind", list(templates.TEMPLATES))
def test_templates_are_ready_to_use(kind, tmp_path):
    dest = tmp_path / kind
    created = templates.create(kind, dest)
    assert "MYDEVAGENT.md" in created and ".gitignore" in created
    assert not any(name in created for name in templates.DOTFILES)  # renamed with the dot
    for path in dest.rglob("*.py"):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
    if kind == "website":
        html = (dest / "index.html").read_text(encoding="utf-8")
        assert "style.css" in html and "script.js" in html
    needs = {"python": "pytest", "api": "fastapi"}
    if kind in needs and importlib.util.find_spec(needs[kind]):
        tests = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=dest,
                               capture_output=True, text=True, timeout=120)
        assert tests.returncode == 0, tests.stdout + tests.stderr
