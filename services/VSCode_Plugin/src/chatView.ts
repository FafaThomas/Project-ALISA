/**
 * The chat: a webview in the ALISA sidebar. The conversation lives here, in the extension (and in
 * workspace state between sessions); the webview only draws it.
 *
 * Code mode starts a run (POST /runs) and follows it; a proposal is reviewed as diffs and written with
 * POST /proposals/apply. Ask mode puts a question to a unit (POST /ask); nothing is written.
 */

import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { AgentClient, AgentError } from './client';
import { describeEvent, progressLabel, runAsTurn, summarizeReport } from './events';
import { renderMarkdown } from './markdown';
import type {
  AnswerItem, Attachment, ChatItem, ChatTurn, FromView, JobSnapshot, Mode, RunItem, RunPayload, ToView,
  UnitOption, UserItem,
} from './protocol';
import { AgentServer } from './server';
import { AGENT_OPTION, flattenUnits, unitLabel, unitNames } from './units';

const ITEMS_KEY = 'alisa.chat.items.v1';
const PREFS_KEY = 'alisa.chat.prefs.v1';
const MAX_ITEMS = 200;
const MAX_TIMELINE = 400;
const POLL_MS = 1000;
const MAX_POLL_FAILURES = 30;
const HISTORY_TURNS = 8;
export const EMPTY_SCHEME = 'alisa-empty';

type Prefs = { mode: Mode; unitPath: string };
type Followed = RunItem | AnswerItem;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const newId = () => crypto.randomBytes(6).toString('hex');
const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
const isActive = (item: ChatItem) =>
  (item.type === 'run' || item.type === 'answer') && (item.status === 'queued' || item.status === 'running');

function samePath(a: string, b: string): boolean {
  const norm = (p: string) => (process.platform === 'win32' ? path.normalize(p).toLowerCase() : path.normalize(p));
  return norm(a) === norm(b);
}

export class ChatViewProvider implements vscode.WebviewViewProvider, vscode.Disposable {
  static readonly viewId = 'alisa.chat';

  private view: vscode.WebviewView | undefined;
  private ready = false;
  private items: ChatItem[];
  private prefs: Prefs;
  private units: UnitOption[] = [AGENT_OPTION];
  private names = new Map<string, string>();
  private attachments: Attachment[] = [];
  private pendingPrefill: string | undefined;
  private readonly following = new Set<string>();
  private saveTimer: NodeJS.Timeout | undefined;
  private readonly progressChanged = new vscode.EventEmitter<void>();
  readonly onDidChangeProgress = this.progressChanged.event;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(private readonly context: vscode.ExtensionContext, private readonly server: AgentServer) {
    this.items = context.workspaceState.get<ChatItem[]>(ITEMS_KEY, []);
    this.prefs = context.workspaceState.get<Prefs>(PREFS_KEY, { mode: 'code', unitPath: '' });
    this.disposables.push(
      server.onDidChange((status) => {
        this.post({ type: 'server', server: status });
        if (status.state === 'ready') {
          void this.loadUnits();
        }
      }),
      vscode.window.onDidChangeActiveTextEditor(() => this.post({ type: 'activeFile', path: this.activeFile()?.path ?? null })),
    );
    this.resumeAfterReload();
  }

  dispose(): void {
    this.flushSave();
    this.disposables.forEach((d) => d.dispose());
    this.progressChanged.dispose();
  }

  // ------------------------------------------------------------------ webview
  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    this.ready = false;
    const media = vscode.Uri.joinPath(this.context.extensionUri, 'media');
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    view.webview.html = this.html(view.webview, media);
    view.webview.onDidReceiveMessage((message: FromView) => {
      this.onMessage(message).catch((error) => void vscode.window.showErrorMessage(`ALISA: ${errorText(error)}`));
    }, undefined, this.disposables);
    view.onDidDispose(() => {
      this.view = undefined;
      this.ready = false;
    }, undefined, this.disposables);
    void this.server.client().catch(() => undefined); // connect early; failures show in the view
  }

  private html(webview: vscode.Webview, media: vscode.Uri): string {
    const nonce = crypto.randomBytes(16).toString('base64');
    const css = webview.asWebviewUri(vscode.Uri.joinPath(media, 'chat.css'));
    const js = webview.asWebviewUri(vscode.Uri.joinPath(media, 'chat.js'));
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; img-src ${webview.cspSource} data:; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="${css}">
<title>ALISA Coding Agent</title>
</head>
<body>
<div id="app"></div>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
  }

  private post(message: ToView): void {
    if (this.view && this.ready) {
      void this.view.webview.postMessage(message);
    }
  }

  private toView(item: ChatItem): ChatItem {
    return item.type === 'answer' && item.markdown ? { ...item, html: renderMarkdown(item.markdown) } : item;
  }

  private sendInit(): void {
    this.post({
      type: 'init', items: this.items.map((i) => this.toView(i)), units: this.units, mode: this.prefs.mode,
      unitPath: this.prefs.unitPath, server: this.server.status, activeFile: this.activeFile()?.path ?? null,
      shareActiveFile: vscode.workspace.getConfiguration('alisa.chat').get('shareActiveFile', true),
      attachments: this.attachments, autoApply: vscode.workspace.getConfiguration('alisa.run').get('autoApply', false),
    });
    if (this.pendingPrefill !== undefined) {
      this.post({ type: 'prefill', text: this.pendingPrefill });
      this.pendingPrefill = undefined;
    }
  }

  private async onMessage(message: FromView): Promise<void> {
    switch (message.type) {
      case 'ready':
        this.ready = true;
        this.sendInit();
        if (this.server.status.state === 'ready') {
          void this.loadUnits();
        }
        return;
      case 'send':
        return this.send(message);
      case 'setMode':
      case 'setUnit':
        this.prefs = message.type === 'setMode' ? { ...this.prefs, mode: message.mode } : { ...this.prefs, unitPath: message.unitPath };
        void this.context.workspaceState.update(PREFS_KEY, this.prefs);
        return;
      case 'removeAttachment':
        this.attachments = this.attachments.filter((a) => a.path !== message.path);
        this.post({ type: 'attachments', attachments: this.attachments });
        return;
      case 'apply':
        return this.withRun(message.itemId, (item) => this.apply(item));
      case 'discard':
        return this.withRun(message.itemId, async (item) => {
          if (item.status === 'proposed') {
            item.status = 'discarded';
            this.update(item);
          }
        });
      case 'stop':
        return this.stop(message.itemId);
      case 'openDiff':
        return this.withRun(message.itemId, (item) => this.openDiff(item, message.path));
      case 'reviewAll':
        return this.withRun(message.itemId, (item) => this.reviewAll(item));
      case 'openReport':
        return this.withRun(message.itemId, async (item) => {
          if (item.result?.artifactsDir) {
            await vscode.window.showTextDocument(vscode.Uri.file(path.join(item.result.artifactsDir, 'report.json')));
          }
        });
      case 'openFile':
        return this.openFile(message.workspace, message.path);
      case 'newChat':
        return this.newChat();
      case 'restartServer':
        await vscode.commands.executeCommand('alisa.restartServer');
        return;
      case 'showOutput':
        this.server.output.show();
        return;
    }
  }

  private async withRun(itemId: string, action: (item: RunItem) => Promise<void>): Promise<void> {
    const item = this.items.find((i) => i.id === itemId);
    if (item?.type === 'run') {
      await action(item);
    }
  }

  // -------------------------------------------------------------------- state
  private add(item: ChatItem): void {
    this.items.push(item);
    const dropped = this.items.splice(0, Math.max(0, this.items.length - MAX_ITEMS));
    if (dropped.length) {
      this.post({ type: 'remove', ids: dropped.map((i) => i.id) });
    }
    this.update(item);
  }

  private update(item: ChatItem): void {
    this.post({ type: 'upsert', item: this.toView(item) });
    this.scheduleSave();
    if (item.type === 'run' || item.type === 'answer') {
      this.progressChanged.fire();
    }
  }

  private notice(level: 'info' | 'error', text: string): void {
    this.add({ type: 'notice', id: newId(), level, text });
  }

  private scheduleSave(): void {
    if (!this.saveTimer) {
      this.saveTimer = setTimeout(() => this.flushSave(), 500);
    }
  }

  private flushSave(): void {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = undefined;
    }
    void this.context.workspaceState.update(ITEMS_KEY, this.items);
  }

  /** What the agent is doing now, for the status bar; null when idle. */
  activeProgress(): string | null {
    const running = [...this.items].reverse().find(isActive);
    if (!running) {
      return null;
    }
    return running.type === 'run' ? running.progress || 'starting' : 'answering';
  }

  /** Clear the conversation. Jobs still running stay, so their results are not lost. */
  async newChat(): Promise<void> {
    this.items = this.items.filter(isActive);
    this.attachments = [];
    this.flushSave();
    this.sendInit();
  }

  private resumeAfterReload(): void {
    const managed = !(vscode.workspace.getConfiguration('alisa.server').get<string>('url') ?? '').trim();
    for (const item of this.items) {
      if (!isActive(item)) {
        continue;
      }
      if (managed || !(item as Followed).jobId) {
        // The local service stopped with the previous window, and its jobs with it.
        this.interrupt(item as Followed, 'The agent service stopped while this was running (the window was closed or reloaded).');
      } else {
        void this.follow(item as Followed);
      }
    }
  }

  private async loadUnits(): Promise<void> {
    try {
      const root = await (await this.server.client()).units();
      this.units = flattenUnits(root);
      this.names = unitNames(root);
      this.post({ type: 'units', units: this.units });
    } catch {
      // keep the agent-only list; the server status explains what is wrong
    }
  }

  // ---------------------------------------------------------------- context
  private workspaceFolder(): vscode.WorkspaceFolder | undefined {
    const editor = vscode.window.activeTextEditor;
    const fromEditor = editor && vscode.workspace.getWorkspaceFolder(editor.document.uri);
    return (fromEditor && fromEditor.uri.scheme === 'file' ? fromEditor : undefined)
      ?? vscode.workspace.workspaceFolders?.find((f) => f.uri.scheme === 'file');
  }

  /** The active editor's file, relative to its workspace folder (or to ``folder`` when given). */
  private activeFile(folder?: vscode.WorkspaceFolder): Attachment | undefined {
    const document = vscode.window.activeTextEditor?.document;
    if (!document || document.uri.scheme !== 'file') {
      return undefined;
    }
    const owner = vscode.workspace.getWorkspaceFolder(document.uri);
    if (!owner || (folder && owner.uri.toString() !== folder.uri.toString())) {
      return undefined;
    }
    const relative = path.relative(owner.uri.fsPath, document.uri.fsPath).split(path.sep).join('/');
    return { path: relative, label: path.basename(document.uri.fsPath) };
  }

  /** Earlier turns of this chat, newest last, for follow-up questions. */
  private history(before: string): ChatTurn[] {
    const turns: ChatTurn[] = [];
    for (const item of this.items) {
      if (item.id === before) {
        break;
      }
      if (item.type === 'user') {
        turns.push({ role: 'user', text: item.text });
      } else if (item.type === 'answer' && item.status === 'completed' && item.markdown) {
        turns.push({ role: 'agent', text: item.markdown });
      } else if (item.type === 'run' && !isActive(item)) {
        turns.push({ role: 'agent', text: runAsTurn(item.task, item.result, item.status) });
      }
    }
    return turns.slice(-HISTORY_TURNS);
  }

  /** "Send Selection to Chat": quote the selection in the composer and attach its file. */
  async sendSelection(): Promise<void> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      return;
    }
    const file = this.activeFile();
    const selection = editor.selection;
    const text = editor.document.getText(selection);
    const lastLine = selection.end.character === 0 && selection.end.line > selection.start.line
      ? selection.end.line : selection.end.line + 1;
    if (file && !this.attachments.some((a) => a.path === file.path)) {
      this.attachments.push(file);
    }
    const where = file?.path ?? path.basename(editor.document.uri.fsPath);
    const quote = text.trim()
      ? `${where}, lines ${selection.start.line + 1}-${lastLine}:\n\`\`\`${editor.document.languageId}\n${text.replace(/\s+$/, '')}\n\`\`\`\n`
      : '';
    if (this.view && this.ready) {
      this.post({ type: 'attachments', attachments: this.attachments });
      this.post({ type: 'prefill', text: quote });
    } else {
      this.pendingPrefill = quote; // sent with the view's init
    }
    await vscode.commands.executeCommand(`${ChatViewProvider.viewId}.focus`);
  }

  // ------------------------------------------------------------------- send
  private async send(message: Extract<FromView, { type: 'send' }>): Promise<void> {
    const text = message.text.trim();
    if (!text) {
      return;
    }
    const folder = this.workspaceFolder();
    if (!folder) {
      this.notice('error', 'Open a folder first: the coding agent works in a workspace folder.');
      return;
    }
    this.prefs = { mode: message.mode, unitPath: message.unitPath };
    void this.context.workspaceState.update(PREFS_KEY, this.prefs);
    const label = unitLabel(this.units, message.unitPath);
    const files = this.attachments.map((a) => a.path);
    const user: UserItem = { type: 'user', id: newId(), mode: message.mode, text, unitLabel: label, files };
    this.add(user);
    this.attachments = [];
    this.post({ type: 'attachments', attachments: [] });
    const workspace = folder.uri.fsPath;

    if (message.mode === 'ask') {
      const shared = new Set(files);
      const active = message.shareActiveFile ? this.activeFile(folder) : undefined;
      if (active) {
        shared.add(active.path);
      }
      const item: AnswerItem = {
        type: 'answer', id: newId(), jobId: '', workspace, unitPath: message.unitPath, status: 'queued', role: '',
        markdown: '', files: [], notes: [], model: '', tokens: 0, error: '',
      };
      this.add(item);
      await this.startJob(item, (client) => client.startAsk(
        { question: text, workspace, history: this.history(user.id), focus_files: [...shared] }, message.unitPath));
      return;
    }

    const config = vscode.workspace.getConfiguration('alisa.run');
    const payload: RunPayload = {
      task: files.length ? `${text}\n\nFiles this refers to: ${files.join(', ')}` : text,
      workspace,
      mode: config.get('autoApply', false) ? 'apply' : 'propose',
      constraints: message.constraints,
      acceptance_criteria: message.acceptance,
      fix_review_findings: config.get('fixReviewFindings', false),
    };
    const maxTeam = config.get<number | null>('maxTeamSize');
    if (maxTeam) {
      payload.max_team_size = maxTeam;
    }
    const target = (config.get<string>('targetEnvironment') ?? '').trim();
    if (target) {
      payload.target_environment = target;
    }
    const item: RunItem = {
      type: 'run', id: newId(), jobId: '', workspace, task: text, unitLabel: label, status: 'queued',
      progress: 'starting the service', timeline: [], seen: 0, result: null, error: '', appliedNotes: [],
    };
    this.add(item);
    await this.startJob(item, (client) => client.startRun(payload, message.unitPath));
  }

  private async startJob(item: Followed, start: (client: AgentClient) => Promise<JobSnapshot>): Promise<void> {
    try {
      const job = await start(await this.server.client());
      item.jobId = job.job_id;
      item.status = 'running';
      this.update(item);
    } catch (error) {
      item.status = 'failed';
      item.error = errorText(error);
      this.update(item);
      return;
    }
    void this.follow(item);
  }

  // ----------------------------------------------------------------- follow
  private async follow(item: Followed): Promise<void> {
    if (this.following.has(item.id)) {
      return;
    }
    this.following.add(item.id);
    const lease = this.server.hold();
    let failures = 0;
    try {
      for (;;) {
        let job: JobSnapshot;
        try {
          job = await (await this.server.client()).job(item.jobId);
          failures = 0;
        } catch (error) {
          if (error instanceof AgentError && error.status === 404) {
            this.interrupt(item, 'The agent service no longer has this job: it was restarted while the job was running.');
            return;
          }
          if (!(error instanceof AgentError)) { // the service could not be started at all
            this.interrupt(item, `The agent service is not available: ${errorText(error)}`);
            return;
          }
          if (error instanceof AgentError && error.status === 0) {
            this.server.lost(error.message);
          }
          if (++failures >= MAX_POLL_FAILURES) {
            this.interrupt(item, `Lost contact with the agent service: ${errorText(error)}`);
            return;
          }
          await sleep(POLL_MS * 2);
          continue;
        }
        if (!this.items.includes(item) || !isActive(item)) {
          return; // removed, or stopped by the user
        }
        const done = item.type === 'run' ? this.absorbRun(item, job) : this.absorbAnswer(item, job);
        this.update(item);
        if (done) {
          this.announce(item);
          return;
        }
        await sleep(POLL_MS);
      }
    } finally {
      this.following.delete(item.id);
      lease.dispose();
      this.progressChanged.fire();
    }
  }

  private absorbRun(item: RunItem, job: JobSnapshot): boolean {
    for (const event of job.events.slice(item.seen)) {
      const entry = describeEvent(event, this.names);
      if (entry) {
        item.timeline.push(entry);
      }
    }
    item.timeline.splice(0, Math.max(0, item.timeline.length - MAX_TIMELINE));
    item.seen = job.events.length;
    item.progress = progressLabel(job.events, this.names);
    if (job.status === 'queued' || job.status === 'running') {
      return false;
    }
    if (job.report) {
      item.result = summarizeReport(job.report);
      item.status = job.report.status;
      item.error = job.report.error || job.error;
    } else {
      item.status = 'failed';
      item.error = job.error || `The job ended as ${job.status} without a report.`;
    }
    return true;
  }

  private absorbAnswer(item: AnswerItem, job: JobSnapshot): boolean {
    const started = job.events.find((e) => e.event === 'ask_started');
    if (started && !item.role) {
      item.role = String(started.name ?? started.role ?? '');
    }
    if (job.status === 'queued' || job.status === 'running') {
      return false;
    }
    const answer = job.answer;
    if (answer) {
      item.role = this.names.get(answer.role) || item.role || answer.role;
      item.markdown = answer.answer;
      item.files = answer.files;
      item.notes = answer.notes;
      item.model = answer.model;
      item.tokens = Number(answer.tokens?.total) || 0;
    }
    item.error = job.error || answer?.error || '';
    item.status = job.status === 'completed' && !item.error ? 'completed' : 'failed';
    return true;
  }

  private interrupt(item: Followed, reason: string): void {
    item.status = 'interrupted';
    item.error = reason;
    this.update(item);
  }

  /** Tell the user about a finished job when the chat is not in view. */
  private announce(item: Followed): void {
    if (this.view?.visible) {
      return;
    }
    let message: string;
    if (item.type === 'answer') {
      message = item.status === 'completed' ? 'ALISA answered your question.' : `ALISA could not answer: ${item.error}`;
    } else if (item.status === 'proposed') {
      message = `ALISA proposed changes to ${plural(item.result?.changes.length ?? 0, 'file')}. Review them before they are written.`;
    } else if (item.status === 'completed') {
      message = item.result?.changes.length ? `ALISA wrote ${plural(item.result.changes.length, 'file')}.` : 'ALISA finished the run.';
    } else {
      message = `ALISA's run failed: ${item.error || 'see the chat'}`;
    }
    const show = item.status === 'failed' ? vscode.window.showWarningMessage : vscode.window.showInformationMessage;
    void show(message, 'Open Chat').then((choice) => {
      if (choice) {
        void vscode.commands.executeCommand(`${ChatViewProvider.viewId}.focus`);
      }
    });
  }

  // ------------------------------------------------------------------ stop
  private async stop(itemId: string): Promise<void> {
    const item = this.items.find((i) => i.id === itemId);
    if (!item || !isActive(item)) {
      return;
    }
    const followed = item as Followed;
    if (!this.server.status.managed) {
      this.interrupt(followed, 'Stopped following this job. It keeps running on the agent service.');
      return;
    }
    const autoApply = vscode.workspace.getConfiguration('alisa.run').get('autoApply', false);
    const choice = await vscode.window.showWarningMessage(
      'Stop the coding agent?',
      {
        modal: true,
        detail: 'The service has no way to cancel a single job, so this restarts the local service and ends '
          + 'everything it is working on. '
          + (autoApply ? 'Files are written at the very end of a run, so a run that is stopped earlier writes nothing.'
            : 'Runs only propose changes until you apply them, so your files are unchanged.'),
      },
      'Stop',
    );
    if (choice !== 'Stop') {
      return;
    }
    for (const other of this.items.filter(isActive) as Followed[]) {
      this.interrupt(other, other === followed ? 'Stopped.' : 'Stopped: the agent service was restarted.');
    }
    await this.server.restart().catch(() => undefined);
  }

  // ----------------------------------------------------------- apply / diff
  private async apply(item: RunItem, force = false): Promise<void> {
    if (item.status !== 'proposed' || !item.result) {
      return;
    }
    const targets = item.result.changes.map((c) => path.join(item.workspace, c.path));
    const dirty = vscode.workspace.textDocuments.filter(
      (d) => d.isDirty && d.uri.scheme === 'file' && targets.some((t) => samePath(t, d.uri.fsPath)));
    if (dirty.length) {
      await vscode.window.showWarningMessage(
        `Save or revert your unsaved changes first: ${dirty.map((d) => vscode.workspace.asRelativePath(d.uri)).join(', ')}.`,
        { modal: true, detail: 'Applying the proposal writes these files.' });
      return;
    }
    try {
      const result = await (await this.server.client()).applyProposal(item.workspace, item.result.runId, force);
      item.status = 'applied';
      item.appliedNotes = result.notes;
      this.update(item);
      vscode.window.setStatusBarMessage(`ALISA wrote ${plural(result.written.length, 'file')}`, 5000);
    } catch (error) {
      if (error instanceof AgentError && error.status === 409 && error.conflicts.length && !force) {
        const choice = await vscode.window.showWarningMessage(
          'Some files changed after the team proposed its changes.',
          {
            modal: true,
            detail: `${error.conflicts.join('\n')}\n\nApplying replaces them with the proposal. The current versions are kept in the run's backup folder.`,
          },
          'Apply Anyway');
        if (choice === 'Apply Anyway') {
          await this.apply(item, true);
        }
        return;
      }
      throw error;
    }
  }

  private diffSides(item: RunItem, change: { path: string; action: string }): { left: vscode.Uri; right: vscode.Uri; label: string } {
    const runDir = item.result?.artifactsDir ?? '';
    const current = vscode.Uri.file(path.join(item.workspace, change.path));
    const empty = vscode.Uri.from({ scheme: EMPTY_SCHEME, path: `/${change.path}` });
    if (item.status === 'applied' || item.status === 'completed') {
      // What the run changed: the file before the run (its backup) against the file now.
      const backup = path.join(runDir, 'backup', change.path);
      return {
        left: change.action !== 'create' && fs.existsSync(backup) ? vscode.Uri.file(backup) : empty,
        right: change.action === 'delete' ? empty : current,
        label: 'changed by ALISA',
      };
    }
    return {
      left: change.action === 'create' ? empty : current,
      right: change.action === 'delete' ? empty : vscode.Uri.file(path.join(runDir, 'proposed', change.path)),
      label: 'ALISA proposal',
    };
  }

  private async openDiff(item: RunItem, relative: string): Promise<void> {
    const change = item.result?.changes.find((c) => c.path === relative);
    if (!change) {
      return;
    }
    const { left, right, label } = this.diffSides(item, change);
    if (right.scheme === 'file' && !fs.existsSync(right.fsPath)) {
      void vscode.window.showWarningMessage(`The proposed copy of ${relative} is no longer in the run folder.`);
      return;
    }
    await vscode.commands.executeCommand('vscode.diff', left, right, `${relative} (${label})`, { preview: true });
  }

  private async reviewAll(item: RunItem): Promise<void> {
    const changes = item.result?.changes ?? [];
    if (!changes.length) {
      return;
    }
    const resources = changes.map((change) => {
      const { left, right } = this.diffSides(item, change);
      return [vscode.Uri.file(path.join(item.workspace, change.path)), left, right];
    });
    try {
      await vscode.commands.executeCommand('vscode.changes', `ALISA run ${item.result?.runId}`, resources);
    } catch {
      await this.openDiff(item, changes[0].path); // editors without the multi-file diff view
    }
  }

  private async openFile(workspace: string, relative: string): Promise<void> {
    const target = path.resolve(workspace, relative);
    if (!target.startsWith(path.resolve(workspace) + path.sep) || !fs.existsSync(target)) {
      void vscode.window.showWarningMessage(`ALISA: ${relative} was not found in ${workspace}.`);
      return;
    }
    await vscode.window.showTextDocument(vscode.Uri.file(target), { preview: true });
  }
}
