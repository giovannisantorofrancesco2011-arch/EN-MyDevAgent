// node --test test/  (Vio's answers go through here before reaching the panel)
const test = require("node:test");
const assert = require("node:assert");
const { render, inline } = require("../media/markdown.js");

test("no HTML from the model reaches the page", () => {
  const html = render('<img src=x onerror="alert(1)"> and `<script>` and [x](javascript:alert(1))');
  assert.ok(!html.includes("<img") && !html.includes("<script>"));
  assert.ok(html.includes("&lt;img") && html.includes("<code>&lt;script&gt;</code>"));
  assert.ok(!html.includes('data-href="javascript'));
});

test("code blocks with Copy/Insert, and colored diffs", () => {
  const html = render("```python file=app/main.py\ndef f():\n    return 1 < 2\n```");
  assert.match(html, /<span>app\/main.py<\/span>/);
  assert.ok(html.includes("data-copy") && html.includes("data-insert"));
  assert.ok(html.includes("return 1 &lt; 2"));
  const diff = render("```diff\n-a\n+b\n```");
  assert.ok(diff.includes('<span class="del">-a</span>') && diff.includes('<span class="add">+b</span>'));
  assert.ok(!diff.includes("data-insert"));
});

test("nested lists, tasks, tables, headings and links", () => {
  const html = render("# Title\n- one\n  - two\n- [x] done\n\n| a | b |\n|---|---|\n| 1 | **2** |\n\nsee https://example.com.");
  assert.ok(html.startsWith("<h3>Title</h3><ul><li>one<ul><li>two</li></ul></li>"));
  assert.ok(html.includes('<li class="task done">done</li>'));
  assert.ok(html.includes("<td><strong>2</strong></td>"));
  assert.ok(html.includes('<a href="#" data-href="https://example.com">https://example.com</a>.'));
});

test("inline: code is protected from formatting", () => {
  assert.strictEqual(inline("`a*b*c` and *me*"), "<code>a*b*c</code> and <em>me</em>");
});
