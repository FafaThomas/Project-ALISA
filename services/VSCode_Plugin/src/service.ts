/** Starting and stopping the local `coding-agent serve` process. No `vscode` import: tested under Node. */

import * as cp from 'child_process';
import * as net from 'net';

const STOP_TIMEOUT_MS = 5_000;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** A port nothing listens on right now. */
export function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on('error', reject);
    probe.listen(0, '127.0.0.1', () => {
      const address = probe.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      probe.close(() => resolve(port));
    });
  });
}

/** Arguments for `coding-agent serve` on loopback, allowed to work in ``roots``. */
export function serveArgs(prefix: string[], roots: string[], port: number): string[] {
  return [...prefix, 'serve', ...roots.flatMap((root) => ['--allow-root', root]), '--host', '127.0.0.1', '--port', String(port)];
}

/** Start the service. The token reaches it through the environment, never the command line. */
export function spawnService(command: string, args: string[], cwd: string, token: string): cp.ChildProcess {
  return cp.spawn(command, args, {
    cwd,
    windowsHide: true,
    env: { ...process.env, CODING_AGENT_API_TOKEN: token, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
  });
}

/** Stop a process and everything it started (the `coding-agent` launcher runs Python as a child). */
export function killTree(child: cp.ChildProcess): Promise<void> {
  if (child.exitCode !== null || child.signalCode !== null || !child.pid) {
    return Promise.resolve();
  }
  const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
  if (process.platform === 'win32') {
    cp.spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
      .on('error', () => child.kill());
  } else {
    child.kill('SIGTERM');
  }
  return Promise.race([exited, sleep(STOP_TIMEOUT_MS)]).then(() => undefined);
}

export function spawnProblem(command: string, error: NodeJS.ErrnoException): string {
  if (error.code === 'ENOENT') {
    return `Could not find "${command}". Install the coding agent (python -m pip install -e agents/forge/coding_agent) `
      + 'or point the "alisa.server.command" setting at it.';
  }
  return `Could not start "${command}": ${error.message}`;
}
