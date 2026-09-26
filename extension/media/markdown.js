// Markdown for Vio's answers: headings, lists, tables, quotes, code (with Copy and Insert), links.
// All text goes through escape(): no HTML from the model reaches the page.
(function () {
  const escape = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

  function inline(text) {
    const codes = [];
    let out = escape(text).replace(/`([^`\n]+)`/g, (_, code) => {
      codes.push(code);
      return `\u0000${codes.length - 1}\u0000`;
    });
    out = out
      .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="#" data-href="$2">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, '$1<a href="#" data-href="$2">$2</a>')
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\s][^*\n]*?)\*(?!\w)/g, "$1<em>$2</em>")
      .replace(/(^|[^_\w])_([^_\s][^_\n]*?)_(?!\w)/g, "$1<em>$2</em>")
      .replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    return out.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`);
  }

  function codeBlock(body, info) {
    const [lang = "", ...rest] = info.trim().split(/\s+/);
    const file = (rest.join(" ").match(/file=(\S+)/) || [])[1] || "";
    const label = file || lang || "code";
    let inner;
    if (lang === "diff") {
      inner = body.split("\n").map((line) => {
        const kind = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : "";
        return `<span class="${kind}">${escape(line)}</span>`;
      }).join("\n");
    } else {
      inner = escape(body);
    }
    return `<div class="code"><div class="code-head"><span>${escape(label)}</span>` +
      `<span class="code-actions"><button class="link" data-copy>Copy</button>` +
      (lang === "diff" ? "" : `<button class="link" data-insert>Insert</button>`) +
      `</span></div><pre><code>${inner}</code></pre></div>`;
  }

  const LIST = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  const FENCE = /^\s*(`{3,}|~{3,})(.*)$/;
  const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  function list(lines, start) {
    // a list (nested too, by indentation): → [html, index of the first line after it]
    const first = lines[start].match(LIST);
    const indent = first[1].length;
    const ordered = /\d/.test(first[2]);
    let html = ordered ? "<ol>" : "<ul>";
    let i = start;
    while (i < lines.length) {
      const m = lines[i].match(LIST);
      if (!m || m[1].length < indent) break;
      if (m[1].length > indent) {
        const [sub, next] = list(lines, i);
        html = html.replace(/<\/li>$/, "") + sub + "</li>";
        i = next;
        continue;
      }
      let text = m[3];
      i++;
      while (i < lines.length && lines[i].trim() && !LIST.test(lines[i]) && !FENCE.test(lines[i]) &&
             /^\s+/.test(lines[i])) {
        text += " " + lines[i].trim();
        i++;
      }
      const task = text.match(/^\[([ xX])\]\s+(.*)$/);
      html += task ? `<li class="task${task[1] === " " ? "" : " done"}">${inline(task[2])}</li>`
        : `<li>${inline(text)}</li>`;
      if (i < lines.length && !lines[i].trim() && i + 1 < lines.length && LIST.test(lines[i + 1]) &&
          lines[i + 1].match(LIST)[1].length >= indent) {
        i++; // a blank line between items does not end the list
      }
    }
    return [html + (ordered ? "</ol>" : "</ul>"), i];
  }

  function table(lines, start) {
    const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
    let html = "<div class=\"table\"><table><thead><tr>" + cells(lines[start]).map((c) => `<th>${inline(c)}</th>`).join("") +
      "</tr></thead><tbody>";
    let i = start + 2;
    while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
      html += "<tr>" + cells(lines[i]).map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
      i++;
    }
    return [html + "</tbody></table></div>", i];
  }

  function render(source) {
    const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
    let html = "";
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(FENCE);
      if (fence) {
        const body = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith(fence[1])) body.push(lines[i++]);
        i++;
        html += codeBlock(body.join("\n"), fence[2]);
        continue;
      }
      if (!line.trim()) { i++; continue; }
      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        const level = Math.min(heading[1].length + 2, 6); // in the chat, headings stay small
        html += `<h${level}>${inline(heading[2].replace(/\s*#+\s*$/, ""))}</h${level}>`;
        i++;
        continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { html += "<hr>"; i++; continue; }
      if (line.includes("|") && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
        const [part, next] = table(lines, i);
        html += part;
        i = next;
        continue;
      }
      if (/^\s*>/.test(line)) {
        const quoted = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) quoted.push(lines[i++].replace(/^\s*>\s?/, ""));
        html += `<blockquote>${render(quoted.join("\n"))}</blockquote>`;
        continue;
      }
      if (LIST.test(line)) {
        const [part, next] = list(lines, i);
        html += part;
        i = next;
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !FENCE.test(lines[i]) && !LIST.test(lines[i]) &&
             !/^(#{1,6})\s/.test(lines[i]) && !/^\s*>/.test(lines[i]) &&
             !(para.length && lines[i].includes("|") && TABLE_RULE.test(lines[i + 1] || ""))) {
        para.push(lines[i++]);
      }
      html += `<p>${para.map(inline).join("<br>")}</p>`;
    }
    return html;
  }

  (typeof window !== "undefined" ? window : globalThis).Markdown = { render, escape, inline };
  if (typeof module !== "undefined") module.exports = { render, escape, inline };
})();
