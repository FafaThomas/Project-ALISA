import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { AnswerItem, ChatItem, ItemStatus, RunItem, SentRequest, UnitOption, UserItem } from '../protocol';
import { canRetry, editFor, requestFor, runPayload } from '../requests';
import { AGENT_OPTION } from '../units';

const units: UnitOption[] = [
  AGENT_OPTION,
  { path: 'development', label: 'Development (division)', level: 'division', depth: 0, group: 'Development', description: '' },
  { path: 'development/python_developer', label: 'Python Developer', level: 'specialist', depth: 1, group: 'Development', description: '' },
];

const request: SentRequest = {
  text: 'Add a delete command', unitPath: 'development/python_developer', files: ['main.py'], focusFiles: [],
  constraints: ['Keep argparse'], acceptance: ['python main.py delete 0 removes the first expense'],
};

const user = (id: string, extra: Partial<UserItem> = {}): UserItem => ({
  type: 'user', id, mode: 'code', text: 'Add a delete command', unitLabel: 'Python Developer', files: ['main.py'], ...extra,
});

const run = (id: string, status: ItemStatus, extra: Partial<RunItem> = {}): RunItem => ({
  type: 'run', id, jobId: 'j', workspace: 'D:/work/app', task: 'Add a delete command', unitLabel: 'Python Developer', status,
  progress: '', timeline: [], seen: 0, result: null, error: '', appliedNotes: [], ...extra,
});

const answer = (id: string, status: ItemStatus, extra: Partial<AnswerItem> = {}): AnswerItem => ({
  type: 'answer', id, jobId: 'j', workspace: 'D:/work/app', unitPath: 'development', status, role: '', markdown: '',
  files: [], notes: [], model: '', tokens: 0, error: '', ...extra,
});

test('the run payload carries the request and the run settings', () => {
  assert.deepEqual(runPayload(request, 'D:/work/app', {
    autoApply: false, fixReviewFindings: true, maxTeamSize: 3, maxRepairs: 5, targetEnvironment: ' Windows 11, Python 3.12 ',
  }), {
    task: 'Add a delete command\n\nFiles this refers to: main.py', workspace: 'D:/work/app', mode: 'propose',
    constraints: ['Keep argparse'], acceptance_criteria: ['python main.py delete 0 removes the first expense'],
    fix_review_findings: true, max_team_size: 3, max_repairs: 5, target_environment: 'Windows 11, Python 3.12',
  });
  const defaults = { autoApply: false, fixReviewFindings: false, maxTeamSize: null, maxRepairs: null, targetEnvironment: '' };
  const plain = runPayload({ ...request, files: [] }, 'D:/w', { ...defaults, autoApply: true });
  assert.equal(plain.task, 'Add a delete command');
  assert.equal(plain.mode, 'apply');
  assert.ok(!('max_team_size' in plain) && !('max_repairs' in plain) && !('target_environment' in plain) && !('history' in plain));
  // 0 means no repair rounds, so it is sent; values the agent would refuse are brought into its range.
  const repairs = (maxRepairs: number) => runPayload(request, 'D:/w', { ...defaults, maxRepairs }).max_repairs;
  assert.deepEqual([repairs(0), repairs(25), repairs(-3), repairs(2.7)], [0, 10, 0, 2]);
  const followUp = runPayload({ ...request, text: 'lets fix it', files: [] }, 'D:/w', defaults,
    [{ role: 'user', text: 'Add a delete command' }, { role: 'agent', text: 'Code run for "Add a delete command": failed.' }]);
  assert.equal(followUp.task, 'lets fix it');
  assert.deepEqual(followUp.history?.map((t) => t.role), ['user', 'agent']);
});

test('runs can be retried until their changes are written, answers once they finish', () => {
  const retryable = (item: RunItem | AnswerItem) => canRetry([item], { ...item, request } as ChatItem);
  for (const status of ['failed', 'interrupted', 'discarded', 'proposed'] as const) {
    assert.ok(retryable(run('r', status)), status);
  }
  for (const status of ['queued', 'running', 'applied', 'completed'] as const) {
    assert.ok(!retryable(run('r', status)), status);
  }
  for (const status of ['completed', 'failed', 'interrupted'] as const) {
    assert.ok(retryable(answer('a', status)), status);
  }
  assert.ok(!retryable(answer('a', 'running')));
  assert.ok(!canRetry([user('u')], user('u')));
});

test('a reply keeps its request, and older chats rebuild it from the message before', () => {
  const kept = run('r', 'failed', { request });
  assert.equal(requestFor([kept], kept, units), request);

  // Saved before replies kept their request: the message just before, and the unit by its label.
  const legacyRun = run('r1', 'failed');
  const items: ChatItem[] = [user('u1'), legacyRun];
  assert.ok(canRetry(items, legacyRun));
  assert.deepEqual(requestFor(items, legacyRun, units), {
    text: 'Add a delete command', unitPath: 'development/python_developer', files: ['main.py'], focusFiles: ['main.py'],
    constraints: [], acceptance: [],
  });
  const legacyAnswer = answer('a1', 'failed');
  assert.equal(requestFor([user('u2', { mode: 'ask' }), legacyAnswer], legacyAnswer, units)?.unitPath, 'development');

  // Nothing to rebuild from: no message before it, or a unit that is not in the list.
  const orphan = run('r2', 'failed');
  assert.ok(!canRetry([orphan], orphan));
  assert.equal(requestFor([orphan], orphan, units), undefined);
  const gone = run('r3', 'failed', { unitLabel: 'Marketing Lead' });
  assert.equal(requestFor([user('u3'), gone], gone, units), undefined);
});

test('Edit takes the request from the reply, or the message alone', () => {
  const items: ChatItem[] = [user('u1'), run('r1', 'failed', { request }), user('u2', { mode: 'ask', text: 'Why?', files: [], unitLabel: 'Development' })];
  assert.deepEqual(editFor(items, items[0] as UserItem, units), { mode: 'code', request });
  assert.deepEqual(editFor(items, items[2] as UserItem, units), {
    mode: 'ask', request: { text: 'Why?', unitPath: 'development', files: [], focusFiles: [], constraints: [], acceptance: [] },
  });
});
