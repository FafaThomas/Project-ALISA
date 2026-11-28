/**
 * Turns the service's progress events and run report into what the chat shows. Mirrors the CLI's
 * `_print_event` and `_summarize` (coding_agent/cli.py) so both tell the same story.
 */

import type { AgentEvent, RunReport, RunSummary, TestResult, TimelineEntry } from './protocol';

type Names = Map<string, string>;

const str = (value: unknown): string => (value === undefined || value === null ? '' : String(value));
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`;

function name(names: Names, id: unknown): string {
  const key = str(id);
  return names.get(key) ?? key.replace(/_/g, ' ');
}

function capitalize(text: string): string {
  return text ? text[0].toUpperCase() + text.slice(1) : text;
}

/** How one test-run command went, in the CLI's words. */
export function testStatus(result: TestResult): string {
  if (result.skipped) {
    return `skipped (${result.skipped})`;
  }
  if (result.unavailable) {
    return `n/a (${result.unavailable})`;
  }
  if (result.timed_out) {
    return 'HUNG';
  }
  if (str(result.stderr).includes('Traceback (most recent call last)')) {
    return 'CRASHED';
  }
  if (result.reported) {
    return 'ERRORS';
  }
  return `ok (exit ${result.exit_code})`;
}

export function describeEvent(event: AgentEvent, names: Names): TimelineEntry | null {
  const e = event as Record<string, unknown>;
  switch (event.event) {
    case 'run_started':
      return { kind: 'info', text: `Started ${str(e.level)} ${str(e.path) || str(e.unit)} (context: ${str(e.context_strategy)})` };
    case 'team':
      return {
        kind: 'info',
        text: `Team: ${list(e.team).map((id) => name(names, id)).join(', ') || '(none)'}`,
        detail: str(e.rationale) || undefined,
      };
    case 'brief': {
      const parts = [`${plural(Number(e.open_issues) || 0, 'open issue')} found in the code`];
      if (e.requirements) {
        parts.push(`${plural(Number(e.requirements), 'requirement')} from earlier runs`);
      }
      if (e.left_open) {
        parts.push(`${plural(Number(e.left_open), 'item')} the last run left open`);
      }
      if (e.history) {
        parts.push('recent CHANGELOG.md entries');
      }
      return { kind: 'info', text: `Project brief: ${parts.join(', ')}` };
    }
    case 'phase':
      return {
        kind: 'phase',
        text: `${capitalize(str(e.phase))} phase`,
        detail: list(e.divisions).map((id) => name(names, id)).join(', ') || undefined,
      };
    case 'role_started': {
      let text = name(names, e.role);
      if (e.phase === 'repair') {
        const of = e.max_attempts ? ` of ${str(e.max_attempts)}` : '';
        text += ` repairing${e.attempt ? ` (round ${str(e.attempt)}${of})` : ''}`;
      } else if (e.phase === 'test-plan') {
        text += ' planning the test run';
      } else if (e.work_item) {
        text += ` on ${str(e.work_item)}`;
      }
      if (e.lead) {
        text += ' as lead';
      }
      if (e.remote) {
        text += ' [remote]';
      }
      return { kind: 'start', text: `${text}...` };
    }
    case 'team_routed':
      return { kind: 'info', text: `${name(names, e.team)} routed to ${list(e.members).map((id) => name(names, id)).join(', ')}` };
    case 'role_finished': {
      const who = name(names, e.role);
      const model = e.model ? `${str(e.model)}${e.model_source ? ` (${str(e.model_source)})` : ''}` : '';
      if (e.error) {
        return { kind: 'error', text: `${who} failed: ${str(e.error)}`, detail: model || undefined };
      }
      const files = list(e.files).map(str);
      const detail = [str(e.summary), model].filter(Boolean).join('\n') || undefined;
      return files.length
        ? { kind: 'ok', text: `${who} wrote ${files.join(', ')}`, detail }
        : { kind: 'ok', text: `${who} done`, detail };
    }
    case 'tool_fix': {
      const fixes = list(e.fixes) as Record<string, unknown>[];
      const label = e.phase === 'migrations' ? 'Migrations' : 'Tools';
      return {
        kind: 'ok',
        text: `${label} fixed ${plural(fixes.length, 'file')} without a model`,
        detail: fixes.map((f) => `${str(f.action)} ${str(f.path)}: ${str(f.reason)}`).join('\n'),
      };
    }
    case 'self_check': {
      const problems = list(e.problems) as Record<string, unknown>[];
      return {
        kind: 'warn',
        text: `${name(names, e.role)} found ${plural(problems.length, 'problem')} in its own files and fixes them`,
        detail: problems.slice(0, 6).map((p) => `${str(p.path)} [${str(p.check)}] ${str(p.detail)}`).join('\n'),
      };
    }
    case 'verification': {
      const problems = list(e.problems) as Record<string, unknown>[];
      return e.passed
        ? { kind: 'ok', text: 'Files gate passed' }
        : {
            kind: 'warn',
            text: `Files gate: ${plural(problems.length, 'problem')}`,
            detail: problems.map((p) => `${str(p.path)} [${str(p.check)}] ${str(p.detail)}`).join('\n'),
          };
    }
    case 'test_env':
      if (e.warning) {
        return { kind: 'warn', text: `Test environment: ${str(e.warning)}`, detail: str(e.note) || undefined };
      }
      return e.note ? { kind: 'info', text: `Test environment: ${str(e.note)}` } : null;
    case 'test_note':
      return { kind: 'warn', text: `Test run: ${str(e.note)}` };
    case 'test_run': {
      const results = list(e.results) as TestResult[];
      const statuses = results.map(testStatus);
      const ok = statuses.filter((s) => s.startsWith('ok')).length;
      const failed = statuses.filter((s) => ['HUNG', 'CRASHED', 'ERRORS'].includes(s)).length;
      return {
        kind: failed ? 'warn' : 'ok',
        text: `Test run: ${ok} ok, ${failed} failed, ${results.length - ok - failed} not judged`,
        detail: results.map((r, i) => `${statuses[i].padEnd(14)} ${r.ran_as || r.command}`).join('\n'),
      };
    }
    case 'import_check': {
      const failures = list(e.failures) as Record<string, unknown>[];
      return {
        kind: failures.length ? 'warn' : 'ok',
        text: `Import check: ${str(e.modules)} module(s) imported, ${failures.length} failed${e.note ? ` (${str(e.note)})` : ''}`,
        detail: failures.map((f) => `${str(f.path)}: ${str(f.error)}${f.changed ? '' : ' (code this run did not change)'}`).join('\n') || undefined,
      };
    }
    case 'run_finished':
      return {
        kind: e.status === 'failed' ? 'error' : 'ok',
        text: `Run ${str(e.status)}`,
        detail: str(e.error) || undefined,
      };
    case 'ask_started': {
      const files = list(e.files).map(str);
      return { kind: 'start', text: `${str(e.name) || name(names, e.role)} is answering...`, detail: files.length ? `Shared: ${files.join(', ')}` : undefined };
    }
    default:
      return null; // ask_finished and anything newer than this extension
  }
}

/** What the run is doing now, e.g. "build: Python Developer on W1". */
export function progressLabel(events: AgentEvent[], names: Names): string {
  let phase = '';
  let active = '';
  for (const event of events) {
    const e = event as Record<string, unknown>;
    if (event.event === 'phase') {
      phase = str(e.phase);
      active = '';
    } else if (event.event === 'role_started') {
      active = name(names, e.role) + (e.work_item ? ` on ${str(e.work_item)}` : '');
      if (e.phase === 'repair' || e.phase === 'test-plan') {
        phase = str(e.phase);
      }
    } else if (event.event === 'role_finished') {
      active = '';
    } else if (event.event === 'verification') {
      phase = 'files gate';
      active = '';
    } else if (event.event === 'team') {
      phase = 'team picked';
    }
  }
  if (!phase && !active) {
    return 'starting';
  }
  return [phase, active].filter(Boolean).join(': ');
}

/** The report without file contents, for the chat card. */
export function summarizeReport(report: RunReport): RunSummary {
  const tokens = (report.outputs ?? []).reduce((sum, o) => sum + (Number(o.tokens?.total) || 0), 0);
  return {
    runId: report.run_id,
    status: report.status,
    artifactsDir: report.artifacts_dir,
    team: report.team ?? [],
    teamRationale: report.team_rationale ?? '',
    changes: (report.changes ?? []).map((c) => ({ path: c.path, action: c.action, author: c.author_role ?? '' })),
    gate: report.verification ? { passed: report.verification.passed, problems: report.verification.problems ?? [] } : null,
    warnings: report.warnings ?? [],
    findings: report.advisory_findings ?? [],
    tests: (report.test_run ?? []).map((r) => ({ command: r.ran_as || r.command, status: testStatus(r) })),
    notes: report.notes ?? [],
    error: report.error ?? '',
    tokens,
  };
}

const clip = (text: string, max: number) => (text.length > max ? `${text.slice(0, max - 3)}...` : text);

const RUN_OUTCOMES: Record<string, string> = {
  proposed: 'proposed; the user has not applied it, so nothing is written yet',
  applied: 'applied by the user; its changes are written',
  completed: 'finished; its changes are written',
  discarded: 'proposal discarded by the user; nothing was written',
  failed: 'failed',
  interrupted: 'stopped before it finished',
};
const MAX_TURN = 2000;

/**
 * A finished run as the agent's turn in the chat history later messages carry (questions and runs):
 * what it was for, how it ended and what went wrong, so "fix it" has something to point at.
 */
export function runAsTurn(task: string, summary: RunSummary | null, status: string): string {
  const lines = [`Code run for "${clip(task, 200)}": ${RUN_OUTCOMES[status] ?? status}.`];
  if (!summary) {
    return lines[0];
  }
  if (summary.changes.length) {
    lines.push(clip(`Files: ${summary.changes.map((c) => `${c.action} ${c.path}`).join(', ')}`, 400));
  }
  if (summary.error) {
    lines.push(`Error: ${clip(summary.error, 300)}`);
  }
  const problems = summary.gate && !summary.gate.passed ? summary.gate.problems : [];
  for (const p of problems.slice(0, 3)) {
    lines.push(`- files gate: ${p.path} [${p.check}] ${clip(p.detail, 250)}`);
  }
  for (const t of summary.tests.filter((t) => /^(HUNG|CRASHED|ERRORS)/.test(t.status)).slice(0, 3)) {
    lines.push(`- test run: ${clip(t.command, 120)}: ${t.status}`);
  }
  for (const f of summary.findings.filter((f) => f.severity === 'high' || f.severity === 'critical').slice(0, 3)) {
    lines.push(`- review (${f.severity}): ${f.file ? `${f.file}: ` : ''}${clip(f.issue, 150)}`);
  }
  return clip(lines.join('\n'), MAX_TURN);
}
