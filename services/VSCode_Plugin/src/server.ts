/**
 * The coding agent service the chat talks to.
 *
 * - Managed (default, `alisa.server.url` empty): starts `coding-agent serve` for the open folders on a
 *   free local port, with a random token only this extension knows, and stops it with the window.
 * - External (`alisa.server.url` set): connects to a service you run, with the token from
 *   "ALISA: Set Server Token" (kept in VS Code's secret storage).
 */

import type * as cp from 'child_process';
import * as crypto from 'crypto';
import * as vscode from 'vscode';
import { AgentClient } from './client';
import type { ServerStatus } from './protocol';
import { freePort, killTree, serveArgs, spawnProblem, spawnService } from './service';

export const TOKEN_SECRET = 'alisa.server.token';
const START_TIMEOUT_MS = 120_000; // the first start imports the agent and reads 59 unit folders

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export class AgentServer implements vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<ServerStatus>();
  readonly onDidChange = this.changed.event;
  private current: AgentClient | undefined;
  private starting: Promise<AgentClient> | undefined;
  private child: cp.ChildProcess | undefined;
  private value: ServerStatus = { state: 'stopped', managed: true, url: '', message: 'Not started' };
  private busy = 0;
  private stale = false; // settings or folders changed while jobs were running: restart when idle
  private readonly disposables: vscode.Disposable[] = [];

  /** To the output channel. The service's last lines and its exit can come after VS Code closed it. */
  private log(text: string): void {
    try {
      this.output.append(text);
    } catch {
      // the window is closing
    }
  }

  constructor(private readonly secrets: vscode.SecretStorage, readonly output: vscode.OutputChannel) {
    this.disposables.push(
      vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration('alisa.server')) {
          void this.refresh();
        }
      }),
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        if (this.child) {
          void this.refresh(); // the service may only use the folders it was started with
        }
      }),
      secrets.onDidChange((event) => {
        if (event.key === TOKEN_SECRET && !this.value.managed) {
          void this.refresh();
        }
      }),
    );
  }

  get status(): ServerStatus {
    return this.value;
  }

  /** A connected client; starts or connects to the service first when needed. */
  async client(): Promise<AgentClient> {
    if (this.stale && this.busy === 0) {
      await this.stop();
    }
    if (this.current && (!this.value.managed || this.child)) {
      return this.current;
    }
    if (!this.starting) {
      this.starting = this.connect().finally(() => {
        this.starting = undefined;
      });
    }
    return this.starting;
  }

  /** Keep the service as it is (no restart for changed settings) while a job is followed. */
  hold(): vscode.Disposable {
    this.busy++;
    return new vscode.Disposable(() => {
      this.busy = Math.max(0, this.busy - 1);
    });
  }

  /** A request could not reach an external service: show it, and reconnect on the next call. */
  lost(message: string): void {
    if (this.value.managed || !this.current) {
      return; // a managed service that exits reports itself
    }
    this.current = undefined;
    this.set({ state: 'error', managed: false, url: this.value.url, message });
  }

  async restart(): Promise<AgentClient> {
    await this.stop();
    return this.client();
  }

  async stop(): Promise<void> {
    const child = this.child;
    this.child = undefined;
    this.current = undefined;
    this.stale = false;
    if (child) {
      this.log('[stopping the coding agent service]\n');
      await killTree(child);
    }
    this.set({ state: 'stopped', managed: this.value.managed, url: '', message: 'Stopped' });
  }

  dispose(): void {
    if (this.child) {
      void killTree(this.child);
      this.child = undefined;
    }
    this.disposables.forEach((d) => d.dispose());
    this.changed.dispose();
  }

  private async refresh(): Promise<void> {
    if (this.busy > 0) {
      this.stale = true;
      return;
    }
    await this.stop();
    void this.client().catch(() => undefined); // the status shows why, if it fails
  }

  private set(status: ServerStatus): void {
    this.value = status;
    this.changed.fire(status);
  }

  private connect(): Promise<AgentClient> {
    const config = vscode.workspace.getConfiguration('alisa.server');
    const url = (config.get<string>('url') ?? '').trim();
    return url ? this.connectExternal(url) : this.startManaged(config);
  }

  private async connectExternal(url: string): Promise<AgentClient> {
    this.set({ state: 'starting', managed: false, url, message: `Connecting to ${url}` });
    const client = new AgentClient(url, (await this.secrets.get(TOKEN_SECRET)) || undefined);
    try {
      await client.health();
      await client.units(); // also checks the token
    } catch (error) {
      this.set({ state: 'error', managed: false, url, message: error instanceof Error ? error.message : String(error) });
      throw error;
    }
    this.current = client;
    this.set({ state: 'ready', managed: false, url, message: `Connected to ${url}` });
    return client;
  }

  private async startManaged(config: vscode.WorkspaceConfiguration): Promise<AgentClient> {
    const folders = (vscode.workspace.workspaceFolders ?? []).filter((f) => f.uri.scheme === 'file').map((f) => f.uri.fsPath);
    const roots = [...folders, ...(config.get<string[]>('allowedRoots') ?? [])].filter((r) => r.trim());
    if (!roots.length) {
      const message = 'Open a folder first: the coding agent works in a workspace folder.';
      this.set({ state: 'error', managed: true, url: '', message });
      throw new Error(message);
    }
    const port = await freePort();
    const url = `http://127.0.0.1:${port}`;
    const token = crypto.randomBytes(24).toString('hex');
    const command = (config.get<string>('command') ?? '').trim() || 'coding-agent';
    const args = serveArgs(config.get<string[]>('args') ?? [], roots, port);
    this.set({ state: 'starting', managed: true, url, message: 'Starting the coding agent service' });
    this.log(`> ${command} ${args.join(' ')}\n`);

    const child: cp.ChildProcess = spawnService(command, args, roots[0], token);
    this.child = child;
    this.stale = false;
    const recent: string[] = [];
    const log = (chunk: Buffer) => {
      const text = chunk.toString('utf8');
      this.log(text);
      recent.push(...text.split(/\r?\n/).filter((line) => line.trim()));
      recent.splice(0, Math.max(0, recent.length - 12));
    };
    child.stdout?.on('data', log);
    child.stderr?.on('data', log);
    let failure: string | undefined;
    child.on('error', (error) => {
      failure = spawnProblem(command, error);
      this.log(`${failure}\n`);
    });
    child.on('exit', (code, signal) => {
      this.log(`[coding agent service exited: ${signal ?? code}]\n`);
      failure ??= `The service exited (${signal ?? code})${recent.length ? `: ${recent.slice(-3).join(' | ')}` : ''}`;
      if (this.child === child) { // not a stop we asked for
        this.child = undefined;
        this.current = undefined;
        this.set({ state: 'error', managed: true, url, message: `${failure}. See "ALISA: Show Agent Server Output".` });
      }
    });

    const client = new AgentClient(url, token);
    const deadline = Date.now() + START_TIMEOUT_MS;
    while (Date.now() < deadline && failure === undefined && this.child === child) {
      try {
        await client.health(1000);
        this.current = client;
        this.set({ state: 'ready', managed: true, url, message: `Local service for ${roots.length} folder(s)` });
        return client;
      } catch {
        await sleep(300);
      }
    }
    if (this.child === child) {
      this.child = undefined;
      await killTree(child);
    }
    const message = failure ?? `The service did not answer within ${START_TIMEOUT_MS / 1000} s`;
    this.set({ state: 'error', managed: true, url, message });
    throw new Error(message);
  }
}
