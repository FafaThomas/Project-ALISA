import assert from 'node:assert/strict';
import * as http from 'node:http';
import type { AddressInfo } from 'node:net';
import { after, before, test } from 'node:test';
import { AgentClient, AgentError, unitRoute } from '../client';

type Seen = { method: string; url: string; auth: string; body: unknown };
const seen: Seen[] = [];
let server: http.Server;
let base = '';

before(async () => {
  server = http.createServer((req, res) => {
    let raw = '';
    req.on('data', (chunk) => { raw += chunk; });
    req.on('end', () => {
      seen.push({ method: req.method ?? '', url: req.url ?? '', auth: req.headers.authorization ?? '', body: raw ? JSON.parse(raw) : undefined });
      const reply = (status: number, body: unknown) => {
        res.writeHead(status, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(body));
      };
      if (req.url === '/health') return reply(200, { status: 'ok', unit: 'coding_agent' });
      if (req.headers.authorization !== 'Bearer tok') return reply(401, { error: 'unauthorized' });
      if (req.url === '/proposals/apply') return reply(409, { error: 'files changed', conflicts: ['a.py'] });
      if (req.url === '/runs/missing') return reply(404, { error: 'unknown job' });
      if (req.url === '/slow') return;
      return reply(202, { job_id: 'j1', status: 'queued', events: [], report: null, answer: null, error: '' });
    });
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(() => {
  server.closeAllConnections();
  server.close();
});

test('unit routes', () => {
  assert.equal(unitRoute('', 'runs'), '/runs');
  assert.equal(unitRoute('development/python_developer', 'ask'), '/units/development/python_developer/ask');
  assert.equal(unitRoute('a b/c', 'runs'), '/units/a%20b/c/runs');
});

test('requests carry the token and the payload', async () => {
  const client = new AgentClient(`${base}/`, 'tok');
  const job = await client.startAsk({ question: 'q', workspace: 'w', focus_files: ['a.py'] }, 'security');
  assert.equal(job.job_id, 'j1');
  const last = seen[seen.length - 1];
  assert.deepEqual(last, { method: 'POST', url: '/units/security/ask', auth: 'Bearer tok', body: { question: 'q', workspace: 'w', focus_files: ['a.py'] } });
  await client.startRun({ task: 't', workspace: 'w', mode: 'propose' });
  assert.equal(seen[seen.length - 1].url, '/runs');
});

test('errors keep the status and the service message', async () => {
  await assert.rejects(new AgentClient(base).units(), (error: unknown) =>
    error instanceof AgentError && error.status === 401 && error.message.includes('Set Server Token'));
  await assert.rejects(new AgentClient(base, 'tok').job('missing'), (error: unknown) =>
    error instanceof AgentError && error.status === 404 && error.message === 'unknown job');
  await assert.rejects(new AgentClient(base, 'tok').applyProposal('w', 'r'), (error: unknown) =>
    error instanceof AgentError && error.status === 409 && error.conflicts.join() === 'a.py');
  const sent = seen[seen.length - 1].body as Record<string, unknown>;
  assert.deepEqual(sent, { workspace: 'w', run_id: 'r' }); // force is sent only when asked for
});

test('an unreachable service is status 0 with a readable reason', async () => {
  const closed = http.createServer();
  await new Promise<void>((resolve) => closed.listen(0, '127.0.0.1', resolve));
  const port = (closed.address() as AddressInfo).port;
  await new Promise<void>((resolve) => closed.close(() => resolve()));
  await assert.rejects(new AgentClient(`http://127.0.0.1:${port}`).health(), (error: unknown) =>
    error instanceof AgentError && error.status === 0 && /Cannot reach/.test(error.message));
});

test('a request that never answers times out', async () => {
  const client = new AgentClient(base, 'tok') as unknown as { request: (m: string, p: string, b?: unknown, t?: number) => Promise<unknown> };
  await assert.rejects(client.request('GET', '/slow', undefined, 200), (error: unknown) =>
    error instanceof AgentError && error.status === 0 && /timed out/.test(error.message));
});
