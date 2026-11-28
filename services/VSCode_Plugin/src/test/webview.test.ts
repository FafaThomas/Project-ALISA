/** The chat webview (media/chat.js), run in jsdom with a stand-in for VS Code's webview API. */

import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { beforeEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import { renderMarkdown } from '../markdown';
import type { ChatItem, RunItem, ToView } from '../protocol';

const SCRIPT = fs.readFileSync(path.join(__dirname, '..', '..', 'media', 'chat.js'), 'utf8');

let dom: JSDOM;
let posted: Record<string, unknown>[];
let stored: unknown;

function load(state?: unknown): void {
  dom = new JSDOM('<!DOCTYPE html><body><div id="app"></div></body>', { runScripts: 'outside-only' });
  posted = [];
  stored = state;
  Object.assign(dom.window, {
    acquireVsCodeApi: () => ({
      // Serialized like the real postMessage (this also moves objects out of jsdom's realm).
      postMessage: (message: Record<string, unknown>) => posted.push(JSON.parse(JSON.stringify(message))),
      getState: () => stored,
      setState: (value: unknown) => { stored = value; },
    }),
  });
  dom.window.eval(SCRIPT);
}

function deliver(message: ToView): void {
  dom.window.dispatchEvent(new dom.window.MessageEvent('message', { data: message }));
}

const $ = (selector: string) => dom.window.document.querySelector(selector) as HTMLElement | null;
const $$ = (selector: string) => Array.from(dom.window.document.querySelectorAll(selector)) as HTMLElement[];
const last = () => posted[posted.length - 1];
const byText = (selector: string, text: string) => $$(selector).find((n) => n.textContent === text);

const run: RunItem = {
  type: 'run', id: 'r1', jobId: 'j1', workspace: 'D:/work/app', task: 'Build a calculator', unitLabel: 'Coding Agent',
  status: 'proposed', progress: '', seen: 3, error: '', appliedNotes: [],
  timeline: [{ kind: 'phase', text: 'Build phase' }, { kind: 'ok', text: 'Python Developer wrote calculator.py', detail: 'core' }],
  result: {
    runId: '20261125-101010-abc123', status: 'proposed', artifactsDir: 'D:/work/app/.coding-agent/runs/x', team: [],
    teamRationale: '', changes: [{ path: 'calculator.py', action: 'create', author: 'python_developer' }],
    gate: { passed: true, problems: [] }, warnings: ['tech_lead (plan): slow'], findings: [], tests: [], notes: [], error: '', tokens: 0,
  },
};

function init(items: ChatItem[], extra: Partial<Extract<ToView, { type: 'init' }>> = {}): void {
  deliver({
    type: 'init', items, mode: 'code', unitPath: '', activeFile: 'src/app.py', shareActiveFile: true, attachments: [],
    autoApply: false, server: { state: 'ready', managed: true, url: 'http://127.0.0.1:1', message: '' },
    units: [
      { path: '', label: 'Coding Agent (whole team)', level: 'agent', depth: 0, group: 'Agent', description: '' },
      { path: 'development', label: 'Development (division)', level: 'division', depth: 0, group: 'Development', description: '' },
      { path: 'development/python_developer', label: 'Python Developer', level: 'specialist', depth: 1, group: 'Development', description: '' },
    ],
    ...extra,
  });
}

beforeEach(() => load());

test('the view asks for its state when it loads', () => {
  assert.deepEqual(posted, [{ type: 'ready' }]);
  init([]);
  assert.ok($('.welcome'));
  assert.equal($$('select.unit option').length, 3);
  assert.equal($$('select.unit optgroup')[0].getAttribute('label'), 'Development');
});

test('a proposal can be reviewed, applied or discarded', () => {
  init([run]);
  assert.equal($('.badge')?.textContent, 'to review');
  byText('button.link', 'calculator.py')?.click();
  assert.deepEqual(last(), { type: 'openDiff', itemId: 'r1', path: 'calculator.py' });
  byText('button', 'Review changes')?.click();
  assert.deepEqual(last(), { type: 'reviewAll', itemId: 'r1' });
  byText('button', 'Apply')?.click();
  assert.deepEqual(last(), { type: 'apply', itemId: 'r1' });
  byText('button', 'Discard')?.click();
  assert.deepEqual(last(), { type: 'discard', itemId: 'r1' });

  deliver({ type: 'upsert', item: { ...run, status: 'applied', appliedNotes: ['CHANGELOG.md updated'] } });
  assert.equal($$('.msg.run').length, 1, 'the card is replaced, not duplicated');
  assert.equal(byText('button', 'Apply'), undefined);
  assert.equal($('.badge')?.textContent, 'applied');
  assert.ok($('.changes .label')?.textContent?.includes('changed'));
});

test('a running job shows its progress and can be stopped', () => {
  init([{ ...run, status: 'running', progress: 'build: Python Developer on W1', result: null }]);
  assert.equal($('.working .progress')?.textContent, 'build: Python Developer on W1');
  byText('button', 'Stop')?.click();
  assert.deepEqual(last(), { type: 'stop', itemId: 'r1' });
});

test('expanded sections stay expanded when the card is redrawn', () => {
  init([run]);
  const activity = $('details.timeline') as HTMLDetailsElement;
  activity.open = true;
  activity.dispatchEvent(new dom.window.Event('toggle'));
  deliver({ type: 'upsert', item: { ...run, timeline: [...run.timeline, { kind: 'info', text: 'more' }] } });
  assert.equal(($('details.timeline') as HTMLDetailsElement).open, true);
  assert.equal($$('.timeline .entry').length, 3);
});

test('text from the agent is shown as text, and answers link the files they name', () => {
  const markdown = 'See `src/app.py:12` and `other.py`.\n\n<img src=x onerror=alert(1)>';
  init([
    { type: 'user', id: 'u1', mode: 'ask', text: '<img src=x onerror=alert(1)>', unitLabel: 'Coding Agent', files: [] },
    {
      type: 'answer', id: 'a1', jobId: 'j', workspace: 'D:/work/app', unitPath: '', status: 'completed', role: 'Codebase Analyst',
      markdown, html: renderMarkdown(markdown), files: ['src/app.py'], notes: ['not shared: .env'], model: 'gemma4:e4b', tokens: 42, error: '',
    },
    { ...run, id: 'r2', task: '<script>alert(1)</script>', error: '<b>boom</b>' },
  ]);
  assert.equal($$('img').length, 0);
  assert.equal($$('script').length, 0);
  assert.equal($$('b').length, 0);
  assert.equal($('.msg.user .text')?.textContent, '<img src=x onerror=alert(1)>');
  const link = $('.markdown code.file-link');
  assert.equal(link?.textContent, 'src/app.py:12');
  assert.equal($$('.markdown code.file-link').length, 1, 'files the answer did not list stay plain');
  link?.click();
  assert.deepEqual(last(), { type: 'openFile', workspace: 'D:/work/app', path: 'src/app.py' });
  byText('button.chip.link', 'src/app.py')?.click();
  assert.deepEqual(last(), { type: 'openFile', workspace: 'D:/work/app', path: 'src/app.py' });
  assert.ok($('.msg.answer .meta')?.textContent?.includes('42 tokens'));
});

test('Enter sends the message with its mode, unit and requirements', () => {
  init([]);
  const select = $('select.unit') as HTMLSelectElement;
  select.value = 'development/python_developer';
  select.dispatchEvent(new dom.window.Event('change'));
  assert.deepEqual(last(), { type: 'setUnit', unitPath: 'development/python_developer' });
  ($('#constraints') as HTMLTextAreaElement).value = 'Keep the API under /api/\n\n  Python 3.12 only ';
  const input = $('textarea.input') as HTMLTextAreaElement;
  input.value = 'Add a divide command';
  input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  assert.deepEqual(last(), {
    type: 'send', text: 'Add a divide command', mode: 'code', unitPath: 'development/python_developer', shareActiveFile: true,
    constraints: ['Keep the API under /api/', 'Python 3.12 only'], acceptance: [],
  });
  assert.equal(input.value, '');
  assert.equal(($('#constraints') as HTMLTextAreaElement).value, '');
  const count = posted.length;
  input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', shiftKey: true, bubbles: true }));
  assert.equal(posted.length, count, 'Shift+Enter is a new line, not a send');
});

test('Ask mode hides the requirements and offers to share the open file', () => {
  init([]);
  assert.equal(($('.requirements') as HTMLElement).hidden, false);
  byText('button.mode', 'Ask')?.click();
  assert.deepEqual(last(), { type: 'setMode', mode: 'ask' });
  assert.equal(($('.requirements') as HTMLElement).hidden, true);
  const share = $('.chip.toggle input') as HTMLInputElement;
  assert.ok(share.checked);
  share.checked = false;
  share.dispatchEvent(new dom.window.Event('change'));
  const input = $('textarea.input') as HTMLTextAreaElement;
  input.value = 'What does app.py do?';
  ($('form.composer') as HTMLFormElement).dispatchEvent(new dom.window.Event('submit', { cancelable: true }));
  assert.deepEqual(last(), {
    type: 'send', text: 'What does app.py do?', mode: 'ask', unitPath: '', shareActiveFile: false, constraints: [], acceptance: [],
  });
});

test('attachments, prefill and the server state', () => {
  init([]);
  deliver({ type: 'attachments', attachments: [{ path: 'src/app.py', label: 'app.py' }] });
  deliver({ type: 'prefill', text: 'src/app.py, lines 1-2:\n```python\nx = 1\n```\n' });
  assert.ok(($('textarea.input') as HTMLTextAreaElement).value.includes('x = 1'));
  ($('.chip-x') as HTMLElement).click();
  assert.deepEqual(last(), { type: 'removeAttachment', path: 'src/app.py' });

  assert.equal(($('.server') as HTMLElement).hidden, true);
  deliver({ type: 'server', server: { state: 'error', managed: true, url: '', message: 'Could not find "coding-agent".' } });
  assert.equal(($('.server') as HTMLElement).hidden, false);
  assert.ok($('.server')?.textContent?.includes('Could not find'));
  byText('button', 'Retry')?.click();
  assert.deepEqual(last(), { type: 'restartServer' });
  byText('button', 'Show output')?.click();
  assert.deepEqual(last(), { type: 'showOutput' });
});

test('the draft survives the view being hidden and shown again', () => {
  init([]);
  const input = $('textarea.input') as HTMLTextAreaElement;
  input.value = 'half-written task';
  input.dispatchEvent(new dom.window.Event('input'));
  load(stored); // VS Code recreates the webview with the saved state
  assert.equal(($('textarea.input') as HTMLTextAreaElement).value, 'half-written task');
});

test('Retry is offered where the extension allows it, and sends the reply back', () => {
  init([
    { ...run, id: 'failed', status: 'failed', error: 'Generated files did not pass the files gate', retryable: true },
    { ...run, id: 'unstarted', status: 'failed', result: null, error: 'The service is not available', retryable: true },
    { ...run, id: 'applied', status: 'applied', retryable: false },
    { ...run, id: 'proposed', status: 'proposed', retryable: true },
    {
      type: 'answer', id: 'a1', jobId: 'j', workspace: 'D:/work/app', unitPath: '', status: 'failed', role: 'Codebase Analyst',
      markdown: '', files: [], notes: [], model: '', tokens: 0, error: 'model timed out', retryable: true,
    },
  ]);
  const retryOn = (id: string) => $(`[data-id="${id}"]`)?.querySelector('.buttons button[title*="again"]') as HTMLElement | null;
  for (const id of ['failed', 'unstarted', 'proposed', 'a1']) {
    assert.equal(retryOn(id)?.textContent, 'Retry', id);
  }
  assert.equal(retryOn('applied'), null);
  assert.ok(retryOn('proposed')?.title.startsWith('Discard this proposal'));
  retryOn('unstarted')?.click();
  assert.deepEqual(last(), { type: 'retry', itemId: 'unstarted' });
  retryOn('a1')?.click();
  assert.deepEqual(last(), { type: 'retry', itemId: 'a1' });
});

test('Edit puts a message back in the composer, with its mode, unit and requirements', () => {
  init([{ type: 'user', id: 'u1', mode: 'code', text: 'Add a delete command', unitLabel: 'Python Developer', files: [], retry: true }]);
  assert.ok($('.msg.user .meta')?.textContent?.includes('· retry'));
  byText('.msg.user button', 'Edit')?.click();
  assert.deepEqual(last(), { type: 'edit', itemId: 'u1' });

  byText('button.mode', 'Ask')?.click();
  deliver({
    type: 'compose', text: 'Add a delete command', mode: 'code', unitPath: 'development/python_developer',
    constraints: ['Keep argparse'], acceptance: [],
  });
  assert.equal(($('textarea.input') as HTMLTextAreaElement).value, 'Add a delete command');
  assert.equal(($('select.unit') as HTMLSelectElement).value, 'development/python_developer');
  assert.ok($('button.mode.active')?.textContent === 'Code');
  assert.equal(($('#constraints') as HTMLTextAreaElement).value, 'Keep argparse');
  assert.equal(($('.requirements') as HTMLDetailsElement).open, true);
  assert.equal((stored as { draft: string }).draft, 'Add a delete command', 'the draft is kept like a typed one');
});

test("the mode and unit follow the chat's other view", () => {
  init([]);
  deliver({ type: 'prefs', mode: 'ask', unitPath: 'development' });
  assert.ok($('button.mode.active')?.textContent === 'Ask');
  assert.equal(($('select.unit') as HTMLSelectElement).value, 'development');
  assert.equal(($('.requirements') as HTMLElement).hidden, true);
});

test('removing items and an empty chat', () => {
  init([run]);
  deliver({ type: 'remove', ids: ['r1'] });
  assert.equal($$('.msg').length, 0);
  assert.ok($('.welcome'));
});
