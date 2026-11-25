import assert from 'node:assert/strict';
import { test } from 'node:test';
import { describeEvent, progressLabel, runAsTurn, summarizeReport, testStatus } from '../events';
import type { AgentEvent, RunReport, TestResult } from '../protocol';

const names = new Map([
  ['python_developer', 'Python Developer'], ['tech_lead', 'Tech Lead'], ['development', 'Development'],
  ['debugging_specialist', 'Debugging Specialist'],
]);

test('progress events read like the CLI', () => {
  assert.deepEqual(describeEvent({ event: 'team', team: ['tech_lead', 'python_developer'], rationale: 'small tool' }, names),
    { kind: 'info', text: 'Team: Tech Lead, Python Developer', detail: 'small tool' });
  assert.deepEqual(describeEvent({ event: 'phase', phase: 'build', divisions: ['development'] }, names),
    { kind: 'phase', text: 'Build phase', detail: 'Development' });
  assert.equal(describeEvent({ event: 'role_started', role: 'python_developer', work_item: 'W1', phase: 'build' }, names)?.text,
    'Python Developer on W1...');
  assert.equal(describeEvent({ event: 'role_started', role: 'debugging_specialist', phase: 'repair', attempt: 2 }, names)?.text,
    'Debugging Specialist repairing (round 2)...');
  const wrote = describeEvent({ event: 'role_finished', role: 'python_developer', files: ['a.py'], summary: 'core', model: 'gemma4:e4b', model_source: '.env X' }, names);
  assert.deepEqual(wrote, { kind: 'ok', text: 'Python Developer wrote a.py', detail: 'core\ngemma4:e4b (.env X)' });
  assert.equal(describeEvent({ event: 'role_finished', role: 'tech_lead', error: 'timeout' }, names)?.kind, 'error');
  assert.equal(describeEvent({ event: 'verification', passed: false, problems: [{ path: 'a.py', check: 'compile', detail: 'bad' }] }, names)?.detail,
    'a.py [compile] bad');
  assert.equal(describeEvent({ event: 'brief', open_issues: 1, requirements: 2, left_open: 0, history: true }, names)?.text,
    'Project brief: 1 open issue found in the code, 2 requirements from earlier runs, recent CHANGELOG.md entries');
  assert.equal(describeEvent({ event: 'something_new' }, names), null);
  assert.equal(describeEvent({ event: 'ask_finished' }, names), null);
});

test('test run statuses match the CLI', () => {
  const base: TestResult = { command: 'python main.py', exit_code: 0, timed_out: false, skipped: '', stderr: '' };
  assert.equal(testStatus(base), 'ok (exit 0)');
  assert.equal(testStatus({ ...base, timed_out: true }), 'HUNG');
  assert.equal(testStatus({ ...base, stderr: 'Traceback (most recent call last):\n  x' }), 'CRASHED');
  assert.equal(testStatus({ ...base, skipped: 'needs a server' }), 'skipped (needs a server)');
  assert.equal(testStatus({ ...base, unavailable: 'no database' }), 'n/a (no database)');
  const entry = describeEvent({ event: 'test_run', results: [base, { ...base, timed_out: true }, { ...base, skipped: 'x' }] }, names);
  assert.equal(entry?.text, 'Test run: 1 ok, 1 failed, 1 not judged');
  assert.equal(entry?.kind, 'warn');
});

test('progress label follows the latest phase and specialist', () => {
  const events: AgentEvent[] = [
    { event: 'run_started' }, { event: 'team', team: [] }, { event: 'phase', phase: 'build' },
    { event: 'role_started', role: 'python_developer', work_item: 'W2' },
  ];
  assert.equal(progressLabel([], names), 'starting');
  assert.equal(progressLabel(events.slice(0, 2), names), 'team picked');
  assert.equal(progressLabel(events, names), 'build: Python Developer on W2');
  assert.equal(progressLabel([...events, { event: 'role_finished', role: 'python_developer' }], names), 'build');
  assert.equal(progressLabel([...events, { event: 'verification', passed: true }], names), 'files gate');
});

test('report summary drops file contents and adds up tokens', () => {
  const report = {
    run_id: '20261125-101010-abc123', status: 'proposed', task: 't', workspace: 'w', team: ['python_developer'],
    team_rationale: 'r', unit_level: 'agent', unit_id: 'coding_agent', artifacts_dir: 'd', notes: ['n'], error: '',
    outputs: [{ role: 'a', phase: 'build', tokens: { total: 100 } }, { role: 'b', phase: 'plan', tokens: { total: 23 } }, { role: 'c', phase: 'x' }],
    changes: [{ path: 'a.py', action: 'create', content: 'SECRET CONTENT', author_role: 'python_developer' }],
    verification: { passed: true, checks: [], problems: [] }, advisory_findings: [], warnings: [],
    test_run: [{ command: 'python a.py', exit_code: 0, timed_out: false, skipped: '', stderr: '' }],
  } as RunReport;
  const summary = summarizeReport(report);
  assert.equal(summary.tokens, 123);
  assert.deepEqual(summary.changes, [{ path: 'a.py', action: 'create', author: 'python_developer' }]);
  assert.ok(!JSON.stringify(summary).includes('SECRET CONTENT'));
  assert.deepEqual(summary.tests, [{ command: 'python a.py', status: 'ok (exit 0)' }]);
  assert.equal(runAsTurn('Build it', summary, 'applied'), 'Run on "Build it": applied; changes: create a.py.');
  assert.equal(runAsTurn('Build it', null, 'interrupted'), 'Run on "Build it": interrupted.');
});
