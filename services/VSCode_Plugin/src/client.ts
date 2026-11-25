/** HTTP client for the coding agent service. No `vscode` import, so it runs under plain Node tests. */

import type {
  ApplyResult, AskPayload, JobSnapshot, RunPayload, UnitNode,
} from './protocol';

export class AgentError extends Error {
  constructor(message: string, readonly status: number, readonly body: Record<string, unknown> = {}) {
    super(message);
    this.name = 'AgentError';
  }

  /** Files that changed on disk since the proposal (HTTP 409 from /proposals/apply). */
  get conflicts(): string[] {
    const value = this.body.conflicts;
    return Array.isArray(value) ? value.map(String) : [];
  }
}

type Fetch = typeof fetch;

/** `/units/<path>/<action>`, or `/<action>` for the served unit itself. */
export function unitRoute(unitPath: string, action: 'runs' | 'ask'): string {
  const segments = unitPath.split('/').filter(Boolean).map(encodeURIComponent);
  return segments.length ? `/units/${segments.join('/')}/${action}` : `/${action}`;
}

export class AgentClient {
  private readonly base: string;

  constructor(baseUrl: string, private readonly token?: string, private readonly fetchImpl: Fetch = fetch) {
    this.base = baseUrl.replace(/\/+$/, '');
  }

  get url(): string {
    return this.base;
  }

  health(timeoutMs = 3000): Promise<{ status: string; unit: string }> {
    return this.request('GET', '/health', undefined, timeoutMs);
  }

  units(): Promise<UnitNode> {
    return this.request('GET', '/units');
  }

  startRun(payload: RunPayload, unitPath = ''): Promise<JobSnapshot> {
    return this.request('POST', unitRoute(unitPath, 'runs'), payload);
  }

  startAsk(payload: AskPayload, unitPath = ''): Promise<JobSnapshot> {
    return this.request('POST', unitRoute(unitPath, 'ask'), payload);
  }

  job(jobId: string): Promise<JobSnapshot> {
    return this.request('GET', `/runs/${encodeURIComponent(jobId)}`);
  }

  applyProposal(workspace: string, runId: string, force = false): Promise<ApplyResult> {
    const body: Record<string, unknown> = { workspace, run_id: runId };
    if (force) {
      body.force = true;
    }
    // Applying writes files, the changelog and the project brief: allow it time.
    return this.request('POST', '/proposals/apply', body, 120_000);
  }

  private async request<T>(method: string, path: string, body?: unknown, timeoutMs = 30_000): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    if (this.token) {
      headers.Authorization = `Bearer ${this.token}`;
    }
    let response: Response;
    try {
      response = await this.fetchImpl(this.base + path, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      const reason = error instanceof Error && error.name === 'TimeoutError'
        ? `timed out after ${Math.round(timeoutMs / 1000)} s` : describeNetworkError(error);
      throw new AgentError(`Cannot reach the coding agent at ${this.base}: ${reason}`, 0);
    }
    const text = await response.text();
    let data: Record<string, unknown> = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      throw new AgentError(`Unexpected reply from ${this.base}${path} (HTTP ${response.status})`, response.status);
    }
    if (!response.ok) {
      const message = typeof data.error === 'string' ? data.error : `HTTP ${response.status}`;
      const hint = response.status === 401 ? ' (set the service token with "ALISA: Set Server Token")' : '';
      throw new AgentError(message + hint, response.status, data);
    }
    return data as T;
  }
}

function describeNetworkError(error: unknown): string {
  if (error instanceof Error) {
    const cause = (error as Error & { cause?: { code?: string; message?: string } }).cause;
    return cause?.code ?? cause?.message ?? error.message;
  }
  return String(error);
}
