/* Minimal CommonMark subset, enough for what the pipeline generates.
 *
 * Deliberately not a library: a CDN <script> is exactly what fails on the
 * networks this tool runs on, and vendoring a full parser to render model
 * output is more surface than the job needs.
 *
 * Safety: every span of source text passes through escapeHtml *before* any tag
 * is inserted, so a model answer containing <script> renders as literal text.
 * Nothing here ever emits attacker-controlled markup.
 */
(function (global) {
  "use strict";

  var ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, function (ch) {
      return ESCAPES[ch];
    });
  }

  var LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  var HEADING = /^(#{1,6})\s+(.*)$/;
  var FENCE = /^\s*```\s*([\w+#-]*)\s*$/;
  var RULE = /^\s*([-*_])\s*(\1\s*){2,}$/;
  var QUOTE = /^\s*>\s?/;
  var TABLE_SEP = /^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$/;
  // Placeholder for extracted code spans. U+0000 is stripped from the input, so
  // it cannot be forged by the text being rendered.
  var SLOT = /\u0000(\d+)\u0000/g;

  function isBlockStart(line) {
    return (
      FENCE.test(line) ||
      HEADING.test(line) ||
      RULE.test(line) ||
      QUOTE.test(line) ||
      LIST_ITEM.test(line)
    );
  }

  /** Emphasis, links and code spans within one block of text. */
  function inline(text) {
    var spans = [];
    var out = escapeHtml(text).replace(/`+([^`]+?)`+/g, function (_, code) {
      // Pulled out first so that ** or _ inside a code span stays literal.
      spans.push(code);
      return "\u0000" + (spans.length - 1) + "\u0000";
    });

    out = out
      .replace(
        /\[([^\]]*)\]\((https?:\/\/[^\s)]+|\/[^\s)]*)\)/g,
        '<a href="$2" target="_blank" rel="noreferrer noopener">$1</a>'
      )
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[^*\w])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/(^|[^_\w])_([^_\n]+)_/g, "$1<em>$2</em>")
      .replace(/~~([^~]+)~~/g, "<del>$1</del>")
      // Two trailing spaces is markdown's hard break; the citation blocks the
      // pipeline emits rely on it.
      .replace(/ {2,}\n/g, "<br>\n");

    return out.replace(SLOT, function (_, index) {
      return "<code>" + spans[Number(index)] + "</code>";
    });
  }

  /** Turn indent-tagged items into nested <ul>/<ol>. Returns [html, nextIndex]. */
  function buildList(items, index) {
    var indent = items[index].indent;
    var tag = items[index].ordered ? "ol" : "ul";
    var parts = [];

    while (index < items.length && items[index].indent >= indent) {
      if (items[index].indent > indent) {
        var nested = buildList(items, index);
        parts[parts.length - 1] += nested[0];
        index = nested[1];
        continue;
      }
      parts.push(inline(items[index].parts.join(" ")));
      index += 1;
    }

    var body = parts
      .map(function (part) {
        return "<li>" + part + "</li>";
      })
      .join("");
    return ["<" + tag + ">" + body + "</" + tag + ">", index];
  }

  function renderTable(header, rows) {
    var head = header
      .map(function (cell) {
        return "<th>" + inline(cell) + "</th>";
      })
      .join("");
    var body = rows
      .map(function (row) {
        return (
          "<tr>" +
          row
            .map(function (cell) {
              return "<td>" + inline(cell) + "</td>";
            })
            .join("") +
          "</tr>"
        );
      })
      .join("");
    return "<table><thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>";
  }

  function splitRow(row) {
    return row
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map(function (cell) {
        return cell.trim();
      });
  }

  function render(source) {
    var lines = String(source == null ? "" : source)
      .replace(/\u0000/g, "")
      .replace(/\r\n?/g, "\n")
      .split("\n");
    var out = [];
    var i = 0;

    while (i < lines.length) {
      var line = lines[i];

      if (!line.trim()) {
        i += 1;
        continue;
      }

      var fence = line.match(FENCE);
      if (fence) {
        var code = [];
        i += 1;
        while (i < lines.length && !FENCE.test(lines[i]) && !/^\s*```/.test(lines[i])) {
          code.push(lines[i]);
          i += 1;
        }
        i += 1; // closing fence, or past the end
        var cls = fence[1] ? ' class="language-' + escapeHtml(fence[1]) + '"' : "";
        out.push("<pre><code" + cls + ">" + escapeHtml(code.join("\n")) + "</code></pre>");
        continue;
      }

      var heading = line.match(HEADING);
      if (heading) {
        var level = heading[1].length;
        out.push("<h" + level + ">" + inline(heading[2]) + "</h" + level + ">");
        i += 1;
        continue;
      }

      if (RULE.test(line)) {
        out.push("<hr>");
        i += 1;
        continue;
      }

      if (QUOTE.test(line)) {
        var quoted = [];
        while (i < lines.length && QUOTE.test(lines[i])) {
          quoted.push(lines[i].replace(QUOTE, ""));
          i += 1;
        }
        out.push("<blockquote>" + render(quoted.join("\n")) + "</blockquote>");
        continue;
      }

      if (line.indexOf("|") !== -1 && TABLE_SEP.test(lines[i + 1] || "")) {
        var header = splitRow(line);
        i += 2;
        var rows = [];
        while (i < lines.length && lines[i].trim() && lines[i].indexOf("|") !== -1) {
          rows.push(splitRow(lines[i]));
          i += 1;
        }
        out.push(renderTable(header, rows));
        continue;
      }

      if (LIST_ITEM.test(line)) {
        var items = [];
        while (i < lines.length && lines[i].trim()) {
          var match = lines[i].match(LIST_ITEM);
          if (match) {
            items.push({
              indent: match[1].length,
              ordered: /^\d/.test(match[2]),
              parts: [match[3]],
            });
          } else if (/^\s{2,}\S/.test(lines[i])) {
            // Indented continuation of the item above.
            items[items.length - 1].parts.push(lines[i].trim());
          } else {
            break;
          }
          i += 1;
        }
        out.push(buildList(items, 0)[0]);
        continue;
      }

      var para = [];
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) {
        para.push(lines[i]);
        i += 1;
      }
      if (!para.length) {
        // Belt and braces: consuming nothing here would spin forever.
        para.push(lines[i]);
        i += 1;
      }
      out.push("<p>" + inline(para.join("\n")) + "</p>");
    }

    return out.join("\n");
  }

  global.md = { render: render, inline: inline, escape: escapeHtml };
})(window);
