/**
 * The managed service, for real: `coding-agent serve` started the way the extension starts it, then
 * stopped. Skipped when the coding agent is not installed. No model is called (/health and /units only).
 */

import assert from 'node:assert/strict';
import * as cp from 'node:child_process';
import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as net from 'node:net';
import * as os from 'node:os';
import * as path from 'node:path';
import { test } from 'node:test';
import { AgentClient, AgentError } from '../client';
import { freePort, killTree, serveArgs, spawnProblem, spawnService } from '../service';

const installed = cp.spawnSync('coding-agent', ['--help'], { windowsHide: true }).status === 0;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function accepts(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = net.connect(port, '127.0.0.1');
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('error', () => resolve(false));
  });
}

test('serve arguments', () => {
  assert.deepEqual(serveArgs(['-m', 'coding_agent'], ['D:/a', 'D:/b c'], 9000),
    ['-m', 'coding_agent', 'serve', '--allow-root', 'D:/a', '--allow-root', 'D:/b c', '--host', '127.0.0.1', '--port', '9000']);
});

test('a missing command gets an install hint', async () => {
  const child = spawnService('alisa-no-such-command', [], os.tmpdir(), 't');
  const error = await new Promise<NodeJS.ErrnoException>((resolve) => child.once('error', resolve));
  assert.match(spawnProblem('alisa-no-such-command', error), /Could not find "alisa-no-such-command"\. Install the coding agent/);
});

test('the local service starts with its own token and stops with its whole process tree', { skip: !installed && 'coding-agent is not installed', timeout: 120_000 }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'alisa-service-'));
  const port = await freePort();
  const token = crypto.randomBytes(24).toString('hex');
  const child = spawnService('coding-agent', serveArgs([], [root], port), root, token);
  let output = '';
  child.stdout?.on('data', (chunk) => { output += chunk; });
  child.stderr?.on('data', (chunk) => { output += chunk; });
  try {
    const client = new AgentClient(`http://127.0.0.1:${port}`, token);
    for (let attempt = 0; ; attempt++) {
      try {
        assert.equal((await client.health(1000)).status, 'ok');
        break;
      } catch (error) {
        if (attempt > 200 || child.exitCode !== null) {
          throw new Error(`service did not start: ${output}`);
        }
        await sleep(250);
      }
    }
    // The token from the environment protects everything but /health; the command line never had it.
    assert.ok(!serveArgs([], [root], port).join(' ').includes(token));
    await assert.rejects(new AgentClient(`http://127.0.0.1:${port}`, 'wrong').units(), (e: unknown) => e instanceof AgentError && e.status === 401);
    const tree = await client.units();
    assert.equal(tree.units.length, 7);
    await assert.rejects(client.startAsk({ question: 'x', workspace: os.homedir() }), (e: unknown) => e instanceof AgentError && e.status === 403);
  } finally {
    await killTree(child);
  }
  assert.notEqual(child.exitCode === null && child.signalCode === null, true, 'the launcher exited');
  // If only the launcher had died, Python would still hold the port.
  for (let attempt = 0; attempt < 20 && await accepts(port); attempt++) {
    await sleep(250);
  }
  assert.equal(await accepts(port), false, 'nothing listens on the port after stopping');
  fs.rmSync(root, { recursive: true, force: true });
});
