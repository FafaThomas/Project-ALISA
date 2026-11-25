// The chat's view. It draws what the extension sends and reports clicks back; the conversation and
// every request to the coding agent live in the extension (src/chatView.ts).
//
// Text from the agent is always set with textContent. The only HTML inserted is item.html, which the
// extension renders escape-first (src/markdown.ts).
(function () {
  'use strict';

  const vscode = acquireVsCodeApi();
  const saved = vscode.getState() || {};
  const ui = {
    draft: saved.draft || '',
    constraints: saved.constraints || '',
    acceptance: saved.acceptance || '',
    open: saved.open || {}, // "<item id>:<section>" -> true for expanded sections
  };
  const state = {
    items: [], units: [], mode: 'code', unitPath: '', server: null, activeFile: null,
    shareActiveFile: true, attachments: [], autoApply: false,
  };
  const STATUS = {
    queued: 'queued', running: 'working', proposed: 'to review', completed: 'done', applied: 'applied',
    discarded: 'discarded', failed: 'failed', interrupted: 'stopped',
  };

  const saveUi = () => vscode.setState(ui);
  const send = (message) => vscode.postMessage(message);
  const basename = (path) => String(path).split('/').pop();
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const isActive = (item) => item.status === 'queued' || item.status === 'running';

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat()) {
      if (child === undefined || child === null || child === false) continue;
      node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }

  // ------------------------------------------------------------------ layout
  const app = document.getElementById('app');
  const unitSelect = h('select', {
    class: 'unit', title: 'Who works on your message', 'aria-label': 'Who works on your message',
    onchange: () => {
      state.unitPath = unitSelect.value;
      send({ type: 'setUnit', unitPath: state.unitPath });
    },
  });
  const serverLine = h('div', { class: 'server', role: 'status' });
  const messages = h('div', { class: 'messages', role: 'log', 'aria-live': 'polite' });
  const modeButtons = {
    code: h('button', { type: 'button', class: 'mode', text: 'Code', title: 'Give the team a task; you review the changes before they are written', onclick: () => setMode('code') }),
    ask: h('button', { type: 'button', class: 'mode', text: 'Ask', title: 'Ask about the project; nothing is changed', onclick: () => setMode('ask') }),
  };
  const attachments = h('div', { class: 'attachments' });
  const input = h('textarea', { class: 'input', rows: '3', 'aria-label': 'Message' });
  const constraints = h('textarea', { rows: '2', id: 'constraints', placeholder: 'One per line, e.g. Keep the API under /api/' });
  const acceptance = h('textarea', { rows: '2', id: 'acceptance', placeholder: 'One per line, e.g. python main.py list prints every item' });
  const requirements = h('details', { class: 'requirements' },
    h('summary', { text: 'Requirements' }),
    h('label', { for: 'constraints', text: 'Constraints' }), constraints,
    h('label', { for: 'acceptance', text: 'Acceptance criteria' }), acceptance,
    h('p', { class: 'hint', text: 'Kept in the project brief, so later runs keep meeting them.' }));
  const hint = h('span', { class: 'hint' });
  const sendButton = h('button', { type: 'submit', class: 'primary', text: 'Send' });
  const composer = h('form', { class: 'composer', onsubmit: (event) => { event.preventDefault(); submit(); } },
    h('div', { class: 'modes', role: 'group', 'aria-label': 'Mode' }, modeButtons.code, modeButtons.ask),
    attachments, input, requirements,
    h('div', { class: 'actions' }, hint, sendButton));
  app.append(h('header', { class: 'header' }, unitSelect), serverLine, messages, composer);

  input.value = ui.draft;
  constraints.value = ui.constraints;
  acceptance.value = ui.acceptance;
  input.addEventListener('input', () => { ui.draft = input.value; saveUi(); autosize(); });
  constraints.addEventListener('input', () => { ui.constraints = constraints.value; saveUi(); });
  acceptance.addEventListener('input', () => { ui.acceptance = acceptance.value; saveUi(); });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      submit();
    }
  });

  function autosize() {
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight || 0, 240) || 60}px`;
  }

  const lines = (text) => text.split('\n').map((line) => line.trim()).filter(Boolean);

  function submit() {
    const text = input.value.trim();
    if (!text) return;
    const code = state.mode === 'code';
    send({
      type: 'send', text, mode: state.mode, unitPath: state.unitPath, shareActiveFile: state.shareActiveFile,
      constraints: code ? lines(constraints.value) : [], acceptance: code ? lines(acceptance.value) : [],
    });
    input.value = '';
    ui.draft = '';
    if (code) {
      constraints.value = acceptance.value = ui.constraints = ui.acceptance = '';
    }
    saveUi();
    autosize();
  }

  function setMode(mode) {
    state.mode = mode;
    send({ type: 'setMode', mode });
    renderComposer();
  }

  // ---------------------------------------------------------------- composer
  function renderComposer() {
    for (const [mode, button] of Object.entries(modeButtons)) {
      button.classList.toggle('active', state.mode === mode);
      button.setAttribute('aria-pressed', String(state.mode === mode));
    }
    const code = state.mode === 'code';
    requirements.hidden = !code;
    input.placeholder = code
      ? 'Describe what to build or change (Enter to send, Shift+Enter for a new line)'
      : 'Ask about this project (Enter to send, Shift+Enter for a new line)';
    hint.textContent = code
      ? (state.autoApply ? 'Changes are written once they pass the files gate' : 'You review the changes before anything is written')
      : 'Answers change nothing';
    renderAttachments();
  }

  function renderAttachments() {
    const chips = state.attachments.map((file) => h('span', { class: 'chip', title: file.path },
      file.label,
      h('button', {
        type: 'button', class: 'chip-x', text: '×', 'aria-label': `Remove ${file.label}`,
        onclick: () => send({ type: 'removeAttachment', path: file.path }),
      })));
    const active = state.activeFile;
    if (state.mode === 'ask' && active && !state.attachments.some((file) => file.path === active)) {
      const box = h('input', { type: 'checkbox', checked: state.shareActiveFile });
      box.addEventListener('change', () => { state.shareActiveFile = box.checked; });
      chips.push(h('label', { class: 'chip toggle', title: `Share ${active} with the specialist who answers` }, box, ` ${basename(active)}`));
    }
    attachments.replaceChildren(...chips);
    attachments.hidden = chips.length === 0;
  }

  function renderUnits() {
    unitSelect.replaceChildren();
    const groups = new Map();
    for (const unit of state.units) {
      const option = h('option', { value: unit.path, title: unit.description || undefined },
        '  '.repeat(unit.depth) + unit.label);
      if (unit.level === 'agent') {
        unitSelect.append(option);
        continue;
      }
      let group = groups.get(unit.group);
      if (!group) {
        group = h('optgroup', { label: unit.group });
        groups.set(unit.group, group);
        unitSelect.append(group);
      }
      group.append(option);
    }
    if (state.unitPath && !state.units.some((unit) => unit.path === state.unitPath)) {
      unitSelect.append(h('option', { value: state.unitPath }, state.unitPath)); // the list is still loading
    }
    unitSelect.value = state.unitPath;
  }

  function renderServer() {
    const status = state.server;
    serverLine.replaceChildren();
    if (!status || status.state === 'ready' || status.state === 'stopped') {
      serverLine.hidden = true;
      return;
    }
    serverLine.hidden = false;
    serverLine.className = `server ${status.state}`;
    if (status.state === 'starting') {
      serverLine.append(h('span', { class: 'spinner', 'aria-hidden': 'true' }), h('span', { text: `${status.message || 'Starting the coding agent service'}...` }));
      return;
    }
    serverLine.append(h('div', { class: 'error', text: status.message || 'The coding agent service is not available.' }),
      h('div', { class: 'buttons' },
        h('button', { type: 'button', class: 'secondary', text: 'Retry', onclick: () => send({ type: 'restartServer' }) }),
        h('button', { type: 'button', class: 'secondary', text: 'Show output', onclick: () => send({ type: 'showOutput' }) })));
  }

  // ---------------------------------------------------------------- messages
  const nearEnd = () => messages.scrollHeight - messages.scrollTop - messages.clientHeight < 48;
  const scrollToEnd = () => { messages.scrollTop = messages.scrollHeight; };
  const nodeFor = (id) => Array.from(messages.children).find((node) => node.dataset.id === id);

  function renderItems() {
    messages.replaceChildren(...(state.items.length ? state.items.map(renderItem) : [welcome()]));
    scrollToEnd();
  }

  function upsert(item) {
    const follow = nearEnd();
    const index = state.items.findIndex((existing) => existing.id === item.id);
    if (index >= 0) state.items[index] = item;
    else state.items.push(item);
    const node = renderItem(item);
    const old = nodeFor(item.id);
    if (old) {
      old.replaceWith(node);
    } else {
      messages.querySelector('.welcome')?.remove();
      messages.append(node);
    }
    if (follow || index < 0) scrollToEnd();
  }

  function remove(ids) {
    state.items = state.items.filter((item) => !ids.includes(item.id));
    for (const id of ids) nodeFor(id)?.remove();
    if (!state.items.length) renderItems();
  }

  function renderItem(item) {
    switch (item.type) {
      case 'user': return renderUser(item);
      case 'answer': return renderAnswer(item);
      case 'run': return renderRun(item);
      default: return h('div', { class: `notice ${item.level}`, 'data-id': item.id, text: item.text });
    }
  }

  function welcome() {
    return h('div', { class: 'welcome' },
      h('h2', { text: 'ALISA Coding Agent' }),
      h('p', {}, h('strong', { text: 'Code' }), ': give the team a task. It plans, builds and checks the change, then shows you the diffs. Nothing is written until you apply it.'),
      h('p', {}, h('strong', { text: 'Ask' }), ': put a question about this project to the whole team, a division or one specialist. Answers change nothing.'),
      h('p', { class: 'muted', text: 'Choose who works on your message in the list above. To bring code from the editor, select it and run "ALISA: Send Selection to Chat".' }));
  }

  function renderUser(item) {
    return h('div', { class: 'msg user', 'data-id': item.id },
      h('div', { class: 'meta', text: `${item.mode === 'ask' ? 'Ask' : 'Code'} · ${item.unitLabel}` }),
      h('div', { class: 'text', text: item.text }),
      item.files.length ? h('div', { class: 'files' }, item.files.map((file) => h('span', { class: 'chip', title: file, text: basename(file) }))) : null);
  }

  function working(item, label) {
    return h('div', { class: 'working' },
      h('span', { class: 'spinner', 'aria-hidden': 'true' }),
      h('span', { class: 'progress', text: label }),
      h('button', { type: 'button', class: 'secondary small', text: 'Stop', onclick: () => send({ type: 'stop', itemId: item.id }) }));
  }

  const openFile = (workspace, path) => send({ type: 'openFile', workspace, path });

  function renderAnswer(item) {
    const node = h('div', { class: 'msg agent answer', 'data-id': item.id },
      h('div', { class: 'meta' },
        h('span', { text: item.role || 'Coding agent' }),
        item.model ? h('span', { class: 'muted', text: ` · ${item.model}${item.tokens ? ` · ${item.tokens} tokens` : ''}` }) : null));
    if (isActive(item)) {
      node.append(working(item, item.role ? `${item.role} is answering...` : 'Thinking...'));
      return node;
    }
    if (item.error) node.append(h('div', { class: 'error', text: item.error }));
    if (item.html) {
      const body = h('div', { class: 'markdown' });
      body.innerHTML = item.html; // escape-first rendering by the extension (src/markdown.ts)
      linkFiles(body, item);
      node.append(body);
    }
    if (item.files.length) {
      node.append(h('div', { class: 'files' }, item.files.map((file) => h('button', {
        type: 'button', class: 'chip link', title: `Open ${file}`, text: file, onclick: () => openFile(item.workspace, file),
      }))));
    }
    for (const note of item.notes) node.append(h('div', { class: 'note', text: note }));
    return node;
  }

  /** `path/to/file.py` in an answer opens the file, when the answer listed it. */
  function linkFiles(body, item) {
    const files = new Set(item.files);
    for (const code of body.querySelectorAll('code')) {
      if (code.closest('pre')) continue;
      const path = code.textContent.replace(/:\d+(-\d+)?$/, '');
      if (!files.has(path)) continue;
      code.classList.add('file-link');
      code.setAttribute('role', 'link');
      code.tabIndex = 0;
      code.title = `Open ${path}`;
      code.addEventListener('click', () => openFile(item.workspace, path));
      code.addEventListener('keydown', (event) => { if (event.key === 'Enter') openFile(item.workspace, path); });
    }
  }

  function section(item, key, title, body) {
    const id = `${item.id}:${key}`;
    const details = h('details', { class: `section ${key}`, open: Boolean(ui.open[id]) }, h('summary', { text: title }), body);
    details.addEventListener('toggle', () => {
      if (details.open) ui.open[id] = true;
      else delete ui.open[id];
      saveUi();
    });
    return details;
  }

  const testClass = (status) => (status.startsWith('ok') ? 'ok' : /^(HUNG|CRASHED|ERRORS)/.test(status) ? 'bad' : 'muted');

  function renderRun(item) {
    const result = item.result;
    const node = h('div', { class: `msg agent run status-${item.status}`, 'data-id': item.id },
      h('div', { class: 'meta' },
        h('span', { text: `Run · ${item.unitLabel}` }),
        h('span', { class: `badge ${item.status}`, text: STATUS[item.status] || item.status })),
      h('div', { class: 'task', text: item.task }));
    if (isActive(item)) node.append(working(item, item.progress || 'starting'));
    if (item.error) node.append(h('div', { class: 'error', text: item.error }));
    if (item.timeline.length) {
      node.append(section(item, 'timeline', `Activity (${item.timeline.length})`,
        h('ol', { class: 'timeline' }, item.timeline.map((entry) => h('li', { class: `entry ${entry.kind}` },
          h('span', { text: entry.text }),
          entry.detail ? h('pre', { class: 'detail', text: entry.detail }) : null)))));
    }
    if (!result) return node;

    if (result.changes.length) {
      const done = item.status === 'applied' || item.status === 'completed';
      node.append(h('div', { class: 'changes' },
        h('div', { class: 'label', text: `${plural(result.changes.length, 'file')} ${done ? 'changed' : 'proposed'}` }),
        h('ul', { class: 'plain' }, result.changes.map((change) => h('li', {},
          h('span', { class: `action ${change.action}`, text: change.action }), ' ',
          h('button', {
            type: 'button', class: 'link', text: change.path, title: 'Show the diff',
            onclick: () => send({ type: 'openDiff', itemId: item.id, path: change.path }),
          }))))));
    } else if (!item.error) {
      node.append(h('div', { class: 'note', text: 'The team finished without changing any files.' }));
    }
    if (result.gate) {
      node.append(result.gate.passed
        ? h('div', { class: 'gate ok', text: 'Files gate passed' })
        : section(item, 'gate', `Files gate: ${plural(result.gate.problems.length, 'problem')}`,
          h('ul', { class: 'plain' }, result.gate.problems.map((p) => h('li', {}, h('code', { text: p.path }), ` [${p.check}] ${p.detail}`)))));
    }
    if (result.tests.length) {
      node.append(section(item, 'tests', `Test run (${plural(result.tests.length, 'command')})`,
        h('ul', { class: 'plain mono' }, result.tests.map((t) => h('li', {}, h('span', { class: testClass(t.status), text: t.status }), ` ${t.command}`)))));
    }
    if (result.findings.length) {
      node.append(section(item, 'findings', `Review findings (${result.findings.length})`,
        h('ul', { class: 'plain' }, result.findings.map((f) => h('li', {},
          h('span', { class: `sev ${f.severity}`, text: f.severity }), ' ',
          f.file ? h('code', { text: f.file }) : null, f.file ? ' ' : null, f.issue,
          f.suggestion ? h('div', { class: 'muted', text: `Suggested: ${f.suggestion}` }) : null)))));
    }
    if (result.warnings.length) {
      node.append(section(item, 'warnings', `Warnings (${result.warnings.length})`,
        h('ul', { class: 'plain' }, result.warnings.map((w) => h('li', { text: w })))));
    }
    const notes = [...result.notes, ...item.appliedNotes];
    if (notes.length) {
      node.append(section(item, 'notes', `Notes (${notes.length})`, h('ul', { class: 'plain' }, notes.map((n) => h('li', { text: n })))));
    }

    const buttons = [];
    if (result.changes.length && item.status !== 'discarded') {
      buttons.push(h('button', { type: 'button', class: 'secondary', text: 'Review changes', onclick: () => send({ type: 'reviewAll', itemId: item.id }) }));
    }
    if (item.status === 'proposed') {
      buttons.push(h('button', { type: 'button', class: 'primary', text: 'Apply', title: 'Write these changes to your files', onclick: () => send({ type: 'apply', itemId: item.id }) }));
      buttons.push(h('button', { type: 'button', class: 'secondary', text: 'Discard', title: 'Leave your files as they are', onclick: () => send({ type: 'discard', itemId: item.id }) }));
    }
    if (result.artifactsDir) {
      buttons.push(h('button', { type: 'button', class: 'secondary', text: 'Report', title: 'Open the run report', onclick: () => send({ type: 'openReport', itemId: item.id }) }));
    }
    node.append(h('div', { class: 'buttons' }, buttons));
    return node;
  }

  // ------------------------------------------------------------------ inbox
  window.addEventListener('message', (event) => {
    const message = event.data;
    switch (message.type) {
      case 'init': {
        Object.assign(state, {
          items: message.items, units: message.units, mode: message.mode, unitPath: message.unitPath,
          server: message.server, activeFile: message.activeFile, shareActiveFile: message.shareActiveFile,
          attachments: message.attachments, autoApply: message.autoApply,
        });
        const ids = new Set(state.items.map((item) => item.id));
        for (const key of Object.keys(ui.open)) if (!ids.has(key.split(':')[0])) delete ui.open[key];
        saveUi();
        renderUnits();
        renderServer();
        renderComposer();
        renderItems();
        break;
      }
      case 'upsert': upsert(message.item); break;
      case 'remove': remove(message.ids); break;
      case 'units': state.units = message.units; renderUnits(); break;
      case 'server': state.server = message.server; renderServer(); break;
      case 'activeFile': state.activeFile = message.path; renderAttachments(); break;
      case 'attachments': state.attachments = message.attachments; renderAttachments(); break;
      case 'prefill':
        if (message.text) {
          input.value = (input.value.trim() ? `${input.value.replace(/\s+$/, '')}\n\n` : '') + message.text;
          ui.draft = input.value;
          saveUi();
          autosize();
        }
        input.focus();
        break;
    }
  });

  renderComposer();
  send({ type: 'ready' });
}());
