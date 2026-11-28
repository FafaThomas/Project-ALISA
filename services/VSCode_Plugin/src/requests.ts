/**
 * What a message asked for, and how it is sent again. Each run or answer keeps the request behind it,
 * so Retry sends the same message to the same unit, and Edit puts it back in the composer.
 */

import type {
  AnswerItem, ChatItem, ChatTurn, Mode, RunItem, RunPayload, SentRequest, UnitOption, UserItem,
} from './protocol';
import { unitLabel } from './units';

export interface RunSettings {
  autoApply: boolean;
  fixReviewFindings: boolean;
  maxTeamSize: number | null | undefined;
  maxRepairs: number | null | undefined;
  targetEnvironment: string;
}

/** The most repair rounds the agent accepts (MAX_REPAIR_ROUNDS in its models.py). */
export const MAX_REPAIRS = 10;

/** The POST /runs payload for a Code message, with the chat before it (so "fix it" can be understood). */
export function runPayload(
  request: SentRequest, workspace: string, settings: RunSettings, history: ChatTurn[] = [],
): RunPayload {
  const payload: RunPayload = {
    task: request.files.length ? `${request.text}\n\nFiles this refers to: ${request.files.join(', ')}` : request.text,
    workspace,
    mode: settings.autoApply ? 'apply' : 'propose',
    constraints: request.constraints,
    acceptance_criteria: request.acceptance,
    fix_review_findings: settings.fixReviewFindings,
  };
  if (settings.maxTeamSize) {
    payload.max_team_size = settings.maxTeamSize;
  }
  if (typeof settings.maxRepairs === 'number' && Number.isFinite(settings.maxRepairs)) {
    // Settings edited as JSON skip the editor's range check; the agent would refuse the run.
    payload.max_repairs = Math.min(MAX_REPAIRS, Math.max(0, Math.floor(settings.maxRepairs)));
  }
  const target = settings.targetEnvironment.trim();
  if (target) {
    payload.target_environment = target;
  }
  if (history.length) {
    payload.history = history;
  }
  return payload;
}

const RETRY_RUN_STATUSES = new Set(['failed', 'interrupted', 'discarded', 'proposed']);

/**
 * Whether a reply's message can be sent again. Not while it is still working, and not for a run whose
 * changes were written: the same task again would build on top of them, so that is a new message.
 * The reply needs its request, or (in older chats) the message just before it to rebuild it from.
 */
export function canRetry(items: ChatItem[], item: ChatItem): item is RunItem | AnswerItem {
  if (item.type !== 'run' && item.type !== 'answer') {
    return false;
  }
  const finished = item.type === 'answer'
    ? item.status !== 'queued' && item.status !== 'running'
    : RETRY_RUN_STATUSES.has(item.status);
  return finished && (Boolean(item.request) || items[items.indexOf(item) - 1]?.type === 'user');
}

const unitPathFor = (units: UnitOption[], label: string): string | undefined =>
  units.find((option) => unitLabel(units, option.path) === label)?.path;

/**
 * The request behind a reply. Chats saved before replies kept it are rebuilt from the message just
 * before the reply, without requirements (the agent keeps those in the project brief anyway).
 */
export function requestFor(items: ChatItem[], reply: RunItem | AnswerItem, units: UnitOption[]): SentRequest | undefined {
  if (reply.request) {
    return reply.request;
  }
  const user = items[items.indexOf(reply) - 1];
  if (user?.type !== 'user') {
    return undefined;
  }
  const unitPath = reply.type === 'answer' ? reply.unitPath : unitPathFor(units, reply.unitLabel);
  if (unitPath === undefined) {
    return undefined; // the unit list is not loaded, or the unit no longer exists
  }
  return { text: user.text, unitPath, files: user.files, focusFiles: user.files, constraints: [], acceptance: [] };
}

/** What Edit puts back in the composer for a message: from its reply's request when there is one. */
export function editFor(items: ChatItem[], user: UserItem, units: UnitOption[]): { mode: Mode; request: SentRequest } {
  const reply = items[items.indexOf(user) + 1];
  const request = reply && (reply.type === 'run' || reply.type === 'answer') ? requestFor(items, reply, units) : undefined;
  return {
    mode: user.mode,
    request: request ?? {
      text: user.text, unitPath: unitPathFor(units, user.unitLabel) ?? '', files: user.files, focusFiles: [],
      constraints: [], acceptance: [],
    },
  };
}
