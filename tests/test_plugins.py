import json
import subprocess

import pytest

from mydevagent import plugins
from mydevagent.skills import load_skills
from mydevagent.subagents import load_subagents, subagents_prompt
from mydevagent.tui.extras import custom_commands, expand_command


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("MYDEVAGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("MYDEVAGENT_PLUGINS_DIRS", raising=False)
    return tmp_path / "home"


def make_plugin(folder, name="reviewer"):
    """A plugin like Claude Code's: manifest, commands (also in subfolders), skills, agents, hooks."""
    (folder / ".claude-plugin").mkdir(parents=True)
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(
        {"name": name, "version": "1.2.0", "description": "Code review"}))
    (folder / "commands" / "git").mkdir(parents=True)
    (folder / "commands" / "git" / "review.md").write_text(
        "---\ndescription: Reviews a file\nargument-hint: <file>\n---\n"
        "Review $1 with ${CLAUDE_PLUGIN_ROOT}/checklist.md. Notes: $ARGUMENTS\nStatus: !`git status`\n")
    (folder / "skills" / "security").mkdir(parents=True)
    (folder / "skills" / "security" / "SKILL.md").write_text(
        "---\nname: security\ndescription: Looks for security problems.\n---\nCheck inputs and secrets.\n")
    (folder / "agents").mkdir()
    (folder / "agents" / "critic.md").write_text(
        "---\nname: critic\ndescription: Strict reviewer. Examples: <example>long</example>\ntools: Read\n---\n"
        "You are a strict reviewer.\n")
    (folder / "hooks").mkdir()
    (folder / "hooks" / "hooks.json").write_text("{}")
    return folder


def test_load_plugins_from_every_source(tmp_path, home, monkeypatch):
    project = tmp_path / "project"
    make_plugin(project / ".mydevagent" / "plugins" / "reviewer")
    # installed in Claude Code: v2 format (list), v1 (dict), one disabled
    cc = home / ".claude" / "plugins"
    for name in ("formatter", "legacy", "disabled"):
        (cc / "cache" / name / "commands").mkdir(parents=True)
    (cc / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "formatter@mkt": [{"scope": "user", "installPath": str(cc / "cache" / "formatter")}],
        "legacy@mkt": {"version": "1.0", "installPath": str(cc / "cache" / "legacy")},
        "disabled@mkt": [{"installPath": str(cc / "cache" / "disabled")}]}}))
    (home / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {"disabled@mkt": False}}))
    # a marketplace with a local plugin and a remote one (skipped)
    market = tmp_path / "market"
    (market / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": [
        {"name": "translate", "source": "./plugins/translate"},
        {"name": "remote", "source": {"source": "github", "repo": "x/y"}}]}))
    (market / "plugins" / "translate" / "skills").mkdir(parents=True)
    monkeypatch.setenv("MYDEVAGENT_PLUGINS_DIRS", str(market))

    found = plugins.load_plugins(project)
    assert {n: p.source for n, p in found.items()} == {
        "reviewer": "project", "formatter": "claude code", "legacy": "claude code", "translate": "extra"}
    rev = found["reviewer"]
    assert (rev.count("commands"), rev.count("skills"), rev.count("agents")) == (1, 1, 1)
    assert rev.features() == ["hook"] and rev.version == "1.2.0"

    skills = load_skills(project)
    assert skills["security"].source == "plugin reviewer" and "critic" not in skills
    agents = load_subagents(project)  # plugins' agents are subagents
    assert agents["critic"].source == "plugin reviewer" and agents["critic"].prompt == "You are a strict reviewer."
    assert agents["critic"].allowed() == {"read_file"}
    assert subagents_prompt(agents).endswith("- critic: Strict reviewer.")  # no long examples in the prompt

    desc, template = custom_commands(project)["/review"]
    assert desc == "Reviews a file (plugin reviewer)"
    root = project / ".mydevagent" / "plugins" / "reviewer"
    assert expand_command(template, "app.py be careful") == (
        f"Review app.py with {root}/checklist.md. Notes: app.py be careful\n"
        "Status: (run `git status` with your tools and use its output)")


def test_claude_code_commands_and_positional_args(tmp_path, home):
    project = tmp_path / "project"
    (project / ".claude" / "commands").mkdir(parents=True)
    (project / ".claude" / "commands" / "test.md").write_text("Run the tests of $1")
    (home / ".claude" / "commands").mkdir(parents=True)
    (home / ".claude" / "commands" / "test.md").write_text("loses to the project's one")
    desc, template = custom_commands(project)["/test"]
    assert desc == "custom command" and expand_command(template, "api") == "Run the tests of api"
    assert expand_command("Explain the code", "src/app.py") == "Explain the code\n\nsrc/app.py"


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)


def test_install_update_remove(tmp_path):
    repo = make_plugin(tmp_path / "repo" / "reviewer-plugin")
    git("init", "-q", cwd=repo)
    git("add", ".", cwd=repo)
    git("commit", "-qm", "first", cwd=repo)

    root = tmp_path / "project"
    [plugin] = plugins.install(repo.as_uri())  # git URL (file://): like a repo on GitHub
    assert plugin.name == "reviewer" and plugin.source == "user"
    assert plugin.path == tmp_path / "state" / "plugins" / "reviewer-plugin"
    with pytest.raises(ValueError, match="already installed"):
        plugins.install(repo.as_uri())

    (repo / "commands" / "new.md").write_text("new command")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "second", cwd=repo)
    plugins.update("reviewer", root)
    assert (plugin.path / "commands" / "new.md").is_file()

    assert plugins.remove("reviewer", root) == plugin.path and not plugin.path.exists()

    copied = plugins.install(str(repo))  # from a folder: it gets copied, without .git
    assert not (copied[0].path / ".git").exists()
    with pytest.raises(ValueError, match="reinstall it"):
        plugins.update("reviewer", root)
    plugins.remove("reviewer", root)

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no plugin found"):
        plugins.install(str(empty))
    assert not (tmp_path / "state" / "plugins" / "empty").exists()

    make_plugin(root / ".mydevagent" / "plugins" / "local", name="local")
    for name, error in (("local", "was not installed"), ("nope", "unknown"), ("../x", "unknown")):
        with pytest.raises(ValueError, match=error):
            plugins.remove(name, root)


def test_marketplace_folder_is_removed_as_a_whole(tmp_path):
    market = tmp_path / "shop"
    (market / ".claude-plugin").mkdir(parents=True)
    (market / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"plugins": [
        {"name": "a", "source": "./plugins/a"}, {"name": "b", "source": "./plugins/b"}]}))
    make_plugin(market / "plugins" / "a", name="a")
    make_plugin(market / "plugins" / "b", name="b")
    assert [p.name for p in plugins.install(str(market))] == ["a", "b"]
    root = tmp_path / "project"
    with pytest.raises(ValueError, match="/plugin remove shop"):
        plugins.remove("a", root)
    assert plugins.remove("shop", root).name == "shop" and not plugins.load_plugins(root)
