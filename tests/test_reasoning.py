from mydevagent.reasoning import (
    ThinkFilter,
    effort_directive,
    extract_code_blocks,
    parse_review,
    strip_thinking,
)


def test_strip_thinking():
    assert strip_thinking("<think>secret</think>\nanswer") == "answer"
    assert strip_thinking("answer<think>unfinished") == "answer"
    assert strip_thinking("reasoning only</think>final") == "final"


def test_think_filter_streaming_split_tags():
    f = ThinkFilter()
    chunks = ["Hel", "lo <thi", "nk>hidden", " stuff</th", "ink> world"]
    out = "".join(f.feed(c) for c in chunks) + f.flush()
    assert out == "Hello world"


def test_think_filter_show():
    f = ThinkFilter(show=True)
    assert f.feed("<think>x</think>") == "<think>x</think>"


def test_code_blocks_with_path_and_run():
    text = "```python file=app/main.py\nx = 1\n```\n```python run\nprint(1)\n```"
    blocks = extract_code_blocks(text)
    assert blocks[0].path == "app/main.py" and not blocks[0].is_run
    assert blocks[1].is_run and blocks[1].path is None


def test_parse_review():
    review = parse_review("VERDICT: APPROVE\n- [BLOCKER] api.py: SQL injection → use params\n- [MINOR] x: y")
    assert review.verdict == "REVISE"  # a BLOCKER overrides APPROVE
    assert review.blocking == [("BLOCKER", "api.py: SQL injection → use params")]
    assert parse_review("VERDICT: APPROVE\n- none").verdict == "APPROVE"


def test_effort_directive():
    assert effort_directive("qwen3:8b", True) == "/think"
    assert effort_directive("qwen3:8b", False) == "/no_think"
    assert effort_directive("qwen3-coder:30b", False) == ""
    assert effort_directive("gpt-oss:20b", True) == "Reasoning: high"


def test_path_inference_without_file_attr():
    text = ("Create `app/text.py`:\n```python\ndef f():\n    pass\n```\n"
            "```python\n# utils/helpers.py\nX = 1\n```\n"
            "```bash\npip install x\n```\n"
            "```python\nprint('no path')\n```")
    paths = [b.path for b in extract_code_blocks(text)]
    assert paths == ["app/text.py", "utils/helpers.py", None, None]


def test_truncated_code_block_is_extracted():
    blocks = extract_code_blocks("intro\n```python file=a.py\nx = 1\ny = 2")
    assert len(blocks) == 1 and blocks[0].path == "a.py" and "y = 2" in blocks[0].body


def test_multi_file_block_is_split():
    text = "```python\n# app/text.py\ndef f():\n    return 1\n\n# tests/test_text.py\nfrom app.text import f\n```"
    blocks = extract_code_blocks(text)
    assert [b.path for b in blocks] == ["app/text.py", "tests/test_text.py"]
    assert "from app.text" in blocks[1].body and "from app.text" not in blocks[0].body
