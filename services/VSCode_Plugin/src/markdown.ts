/**
 * A small Markdown renderer for answers in the chat. Escape-first: every character of the source is
 * HTML-escaped before any tag is added, and the only tags added are the fixed ones below, so model
 * output cannot inject markup. Links are made only for http(s) URLs.
 *
 * Supports: fenced code, inline code, headings, bold, italics, lists, block quotes, tables, rules.
 */

const ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

const FENCE = /^\s*(```+|~~~+)\s*([\w+#.-]*)\s*$/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^\s*>\s?(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function inline(text: string): string {
  const codes: string[] = [];
  let out = escapeHtml(text.replace(/\u0000/g, ''));
  out = out.replace(/`([^`]+)`/g, (_m, code: string) => {
    codes.push(`<code>${code}</code>`);
    return `\u0000${codes.length - 1}\u0000`;
  });
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2">$1</a>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // *italic*, but not inside words or around spaces (a * b * c stays as it is); _x_ is left alone so
  // snake_case names survive.
  out = out.replace(/(^|[^*\w])\*([^*\s](?:[^*]*[^*\s])?)\*(?![\w*])/g, '$1<em>$2</em>');
  return out.replace(/\u0000(\d+)\u0000/g, (_m, i: string) => codes[Number(i)]);
}

function isTableStart(lines: string[], i: number): boolean {
  return TABLE_ROW.test(lines[i]) && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1]);
}

function isBlockStart(lines: string[], i: number): boolean {
  const line = lines[i];
  return FENCE.test(line) || HEADING.test(line) || RULE.test(line) || QUOTE.test(line)
    || BULLET.test(line) || ORDERED.test(line) || isTableStart(lines, i);
}

function cells(row: string): string[] {
  return row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
}

export function renderMarkdown(source: string): string {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const html: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(FENCE);
    if (fence) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith(fence[1])) {
        body.push(lines[i]);
        i++;
      }
      i++; // the closing fence (or past the end when it is missing)
      const lang = fence[2] ? ` data-lang="${escapeHtml(fence[2])}"` : '';
      html.push(`<pre${lang}><code>${escapeHtml(body.join('\n'))}</code></pre>`);
      continue;
    }
    if (!line.trim()) {
      i++;
      continue;
    }
    const heading = line.match(HEADING);
    if (heading) {
      const level = Math.min(6, heading[1].length + 2); // # -> h3: the chat is narrow
      html.push(`<h${level}>${inline(heading[2].replace(/\s+#+\s*$/, ''))}</h${level}>`);
      i++;
      continue;
    }
    if (RULE.test(line)) {
      html.push('<hr>');
      i++;
      continue;
    }
    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        quoted.push((lines[i].match(QUOTE) as RegExpMatchArray)[1]);
        i++;
      }
      html.push(`<blockquote>${renderMarkdown(quoted.join('\n'))}</blockquote>`);
      continue;
    }
    if (isTableStart(lines, i)) {
      const head = cells(lines[i]);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && TABLE_ROW.test(lines[i])) {
        rows.push(cells(lines[i]));
        i++;
      }
      html.push('<table><thead><tr>' + head.map((c) => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>'
        + rows.map((r) => '<tr>' + r.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>').join('')
        + '</tbody></table>');
      continue;
    }
    if (BULLET.test(line) || ORDERED.test(line)) {
      const pattern = BULLET.test(line) ? BULLET : ORDERED;
      const tag = pattern === BULLET ? 'ul' : 'ol';
      const items: string[] = [];
      while (i < lines.length) {
        const item = lines[i].match(pattern);
        if (item) {
          items.push(item[1]);
        } else if (items.length && /^\s{2,}\S/.test(lines[i]) && !BULLET.test(lines[i]) && !ORDERED.test(lines[i])) {
          items[items.length - 1] += '\n' + lines[i].trim(); // a wrapped item
        } else {
          break;
        }
        i++;
      }
      html.push(`<${tag}>${items.map((t) => `<li>${t.split('\n').map(inline).join('<br>')}</li>`).join('')}</${tag}>`);
      continue;
    }
    const paragraph = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !isBlockStart(lines, i)) {
      paragraph.push(lines[i]);
      i++;
    }
    html.push(`<p>${paragraph.map(inline).join('<br>')}</p>`);
  }
  return html.join('\n');
}
