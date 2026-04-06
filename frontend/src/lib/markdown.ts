/**
 * Lightweight markdown-to-HTML converter for AI responses.
 * Handles: headers, bold, italic, code, bullets, blockquotes, tables, line breaks.
 * No external dependency needed.
 */
export function renderMarkdown(text: string): string {
  if (!text) return "";

  let html = text
    // Escape HTML entities
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    // Restore blockquote markers
    .replace(/^&gt;\s?/gm, "> ");

  // Process blocks
  const lines = html.split("\n");
  const result: string[] = [];
  let inList = false;
  let inBlockquote = false;
  let inTable = false;
  let tableHeaderDone = false;

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];

    // ── Table detection ──
    // A table row starts with | and ends with |
    const isTableRow = /^\|(.+)\|$/.test(line.trim());
    const isSeparator = /^\|[\s\-:|]+\|$/.test(line.trim());

    if (isTableRow || isSeparator) {
      if (inList) { result.push("</ul>"); inList = false; }
      if (inBlockquote) { result.push("</blockquote>"); inBlockquote = false; }

      if (!inTable) {
        result.push('<div class="table-wrap"><table>');
        inTable = true;
        tableHeaderDone = false;
      }

      if (isSeparator) {
        // This is the header/body separator — close thead, open tbody
        if (!tableHeaderDone) {
          result.push("</thead><tbody>");
          tableHeaderDone = true;
        }
        continue;
      }

      // Parse cells
      const cells = line.trim().slice(1, -1).split("|").map((c) => c.trim());
      const tag = !tableHeaderDone ? "th" : "td";

      if (!tableHeaderDone && i === 0 || (!tableHeaderDone && !result[result.length - 1]?.includes("<tr>"))) {
        result.push("<thead>");
      }

      result.push(
        "<tr>" + cells.map((c) => `<${tag}>${inlineFormat(c)}</${tag}>`).join("") + "</tr>"
      );
      continue;
    } else if (inTable) {
      // End of table
      if (tableHeaderDone) {
        result.push("</tbody>");
      } else {
        result.push("</thead>");
      }
      result.push("</table></div>");
      inTable = false;
      tableHeaderDone = false;
    }

    // Headers
    if (line.startsWith("### ")) {
      if (inList) { result.push("</ul>"); inList = false; }
      if (inBlockquote) { result.push("</blockquote>"); inBlockquote = false; }
      result.push(`<h3>${inlineFormat(line.slice(4))}</h3>`);
      continue;
    }
    if (line.startsWith("## ")) {
      if (inList) { result.push("</ul>"); inList = false; }
      if (inBlockquote) { result.push("</blockquote>"); inBlockquote = false; }
      result.push(`<h2>${inlineFormat(line.slice(3))}</h2>`);
      continue;
    }
    if (line.startsWith("# ")) {
      if (inList) { result.push("</ul>"); inList = false; }
      if (inBlockquote) { result.push("</blockquote>"); inBlockquote = false; }
      result.push(`<h2>${inlineFormat(line.slice(2))}</h2>`);
      continue;
    }

    // Blockquotes
    if (line.startsWith("> ")) {
      if (inList) { result.push("</ul>"); inList = false; }
      if (!inBlockquote) { result.push("<blockquote>"); inBlockquote = true; }
      result.push(`<p>${inlineFormat(line.slice(2))}</p>`);
      continue;
    } else if (inBlockquote) {
      result.push("</blockquote>");
      inBlockquote = false;
    }

    // Unordered list items
    if (line.match(/^[\s]*[-*•]\s/)) {
      if (!inList) { result.push("<ul>"); inList = true; }
      const content = line.replace(/^[\s]*[-*•]\s/, "");
      result.push(`<li>${inlineFormat(content)}</li>`);
      continue;
    } else if (inList) {
      result.push("</ul>");
      inList = false;
    }

    // Numbered list items
    if (line.match(/^[\s]*\d+\.\s/)) {
      if (!inList) { result.push("<ul>"); inList = true; }
      const content = line.replace(/^[\s]*\d+\.\s/, "");
      result.push(`<li>${inlineFormat(content)}</li>`);
      continue;
    }

    // Empty lines
    if (line.trim() === "") {
      if (inList) { result.push("</ul>"); inList = false; }
      continue;
    }

    // Regular paragraph
    result.push(`<p>${inlineFormat(line)}</p>`);
  }

  if (inList) result.push("</ul>");
  if (inBlockquote) result.push("</blockquote>");
  if (inTable) {
    if (tableHeaderDone) result.push("</tbody>");
    result.push("</table></div>");
  }

  return result.join("\n");
}

function inlineFormat(text: string): string {
  return text
    // Inline code (before bold/italic to prevent conflicts)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    // Bold + italic
    .replace(/\*\*\*(.+?)\*\*\*/g, "<strong><em>$1</em></strong>")
    // Bold
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // Italic
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    // Colored indicators
    .replace(/✓/g, '<span class="text-profit">✓</span>')
    .replace(/✗/g, '<span class="text-loss">✗</span>')
    .replace(/⚠/g, '<span class="text-amber-400">⚠</span>')
    .replace(/~/g, '<span class="text-gray-400">~</span>');
}
