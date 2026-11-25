import assert from 'node:assert/strict';
import { test } from 'node:test';
import { escapeHtml, renderMarkdown } from '../markdown';

test('model output cannot inject markup', () => {
  const html = renderMarkdown('Hi <img src=x onerror=alert(1)> **<script>alert(1)</script>**\n\n`<b>` [x](javascript:alert(1))');
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script'));
  assert.ok(!html.includes('<b>'));
  assert.ok(!html.includes('href="javascript'));
  assert.ok(html.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(html.includes('<strong>&lt;script&gt;alert(1)&lt;/script&gt;</strong>'));
  assert.ok(html.includes('<code>&lt;b&gt;</code>'));
});

test('quotes in link targets stay inside the attribute', () => {
  const html = renderMarkdown('[docs](https://example.com/a"onmouseover="x)');
  assert.ok(!html.includes('"onmouseover'));
});

test('code fences keep their content verbatim, escaped', () => {
  const html = renderMarkdown('Before\n\n```python\nif a < b and c > d:\n    print("**not bold**")\n```\nAfter');
  assert.ok(html.includes('<pre data-lang="python"><code>if a &lt; b and c &gt; d:\n    print(&quot;**not bold**&quot;)</code></pre>'));
  assert.ok(html.includes('<p>Before</p>'));
  assert.ok(html.includes('<p>After</p>'));
});

test('an unclosed fence runs to the end instead of swallowing nothing', () => {
  assert.equal(renderMarkdown('```\nx = 1'), '<pre><code>x = 1</code></pre>');
});

test('headings, lists, quotes, rules and tables', () => {
  const html = renderMarkdown([
    '# Title', '## Sub', '- one', '- two', '  continued', '', '1. first', '2. second', '',
    '> quoted', '', '---', '', '| a | b |', '|---|---|', '| `x` | 2 |',
  ].join('\n'));
  assert.ok(html.includes('<h3>Title</h3>'));
  assert.ok(html.includes('<h4>Sub</h4>'));
  assert.ok(html.includes('<ul><li>one</li><li>two<br>continued</li></ul>'));
  assert.ok(html.includes('<ol><li>first</li><li>second</li></ol>'));
  assert.ok(html.includes('<blockquote><p>quoted</p></blockquote>'));
  assert.ok(html.includes('<hr>'));
  assert.ok(html.includes('<table><thead><tr><th>a</th><th>b</th></tr></thead><tbody><tr><td><code>x</code></td><td>2</td></tr></tbody></table>'));
});

test('inline formatting leaves identifiers and arithmetic alone', () => {
  const html = renderMarkdown('Use **bold**, *italic*, `snake_case_name` and my_var_name; a * b * c stays.');
  assert.ok(html.includes('<strong>bold</strong>'));
  assert.ok(html.includes('<em>italic</em>'));
  assert.ok(html.includes('<code>snake_case_name</code>'));
  assert.ok(html.includes('my_var_name'));
  assert.ok(html.includes('a * b * c stays'));
});

test('http links become anchors', () => {
  assert.ok(renderMarkdown('[docs](https://example.com/x)').includes('<a href="https://example.com/x">docs</a>'));
});

test('escapeHtml', () => {
  assert.equal(escapeHtml(`<a href="x">'&'</a>`), '&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;');
});
