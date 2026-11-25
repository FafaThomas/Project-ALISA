/**
 * Shapes exchanged with the coding agent service (agents/forge/coding_agent/src/coding_agent/api.py)
 * and between the extension and its chat webview. No `vscode` import: unit tests load this freely.
 */

// ------------------------------------------------------------------ service
export interface UnitNode {
  id: string;
  path: string;
  name: string;
  level: 'agent' | 'division' | 'specialist';
  lead?: string | null;
  kind?: string;
  responsibility?: string;
  effective_model?: string;
  is_team?: boolean;
  units: UnitNode[];
}

export type AgentEvent = { event: string; [key: string]: unknown };

export interface FileChange {
  path: string;
  action: 'create' | 'modify' | 'delete';
  author_role?: string;
  content?: string;
}

export interface Finding {
  role: string;
  severity: 'info' | 'low' | 'medium' | 'high' | 'critical';
  issue: string;
  file?: string;
  suggestion?: string;
}

export interface GateProblem {
  path: string;
  check: string;
  detail: string;
}

export interface TestResult {
  command: string;
  ran_as?: string;
  exit_code: number | null;
  timed_out: boolean;
  skipped: string;
  unavailable?: string;
  reported?: string;
  stderr: string;
}

export interface RoleOutput {
  role: string;
  phase: string;
  tokens?: Record<string, number>;
}

export interface RunReport {
  run_id: string;
  status: 'completed' | 'proposed' | 'failed';
  task: string;
  workspace: string;
  team: string[];
  team_rationale: string;
  unit_level: string;
  unit_id: string;
  outputs: RoleOutput[];
  changes: FileChange[];
  verification: { passed: boolean; checks: string[]; problems: GateProblem[] } | null;
  advisory_findings: Finding[];
  error: string;
  warnings: string[];
  test_run: TestResult[];
  artifacts_dir: string;
  notes: string[];
}

export interface Answer {
  role: string;
  answer: string;
  files: string[];
  error: string;
  model: string;
  model_source: string;
  tokens: Record<string, number>;
  notes: string[];
}

export interface JobSnapshot {
  job_id: string;
  kind?: 'run' | 'ask';
  status: string;
  events: AgentEvent[];
  report: RunReport | null;
  answer: Answer | null;
  error: string;
}

export interface RunPayload {
  task: string;
  workspace: string;
  mode: 'propose' | 'apply';
  constraints?: string[];
  acceptance_criteria?: string[];
  fix_review_findings?: boolean;
  max_team_size?: number;
  target_environment?: string;
}

export interface ChatTurn {
  role: 'user' | 'agent';
  text: string;
}

export interface AskPayload {
  question: string;
  workspace: string;
  history?: ChatTurn[];
  focus_files?: string[];
}

export interface ApplyResult {
  run_id: string;
  status: string;
  written: string[];
  notes: string[];
  artifacts_dir: string;
}

// ------------------------------------------------------------------- view
export type Mode = 'code' | 'ask';

export interface UnitOption {
  path: string; // '' is the whole agent
  label: string;
  level: UnitNode['level'];
  depth: number;
  group: string;
  description: string;
}

export interface TimelineEntry {
  kind: 'phase' | 'start' | 'ok' | 'info' | 'warn' | 'error';
  text: string;
  detail?: string;
}

export interface RunSummary {
  runId: string;
  status: RunReport['status'];
  artifactsDir: string;
  team: string[];
  teamRationale: string;
  changes: { path: string; action: FileChange['action']; author: string }[];
  gate: { passed: boolean; problems: GateProblem[] } | null;
  warnings: string[];
  findings: Finding[];
  tests: { command: string; status: string }[];
  notes: string[];
  error: string;
  tokens: number;
}

export type ItemStatus =
  | 'queued' | 'running' | 'completed' | 'proposed' | 'failed' | 'interrupted' | 'applied' | 'discarded';

export interface UserItem {
  type: 'user';
  id: string;
  mode: Mode;
  text: string;
  unitLabel: string;
  files: string[];
}

export interface AnswerItem {
  type: 'answer';
  id: string;
  jobId: string;
  workspace: string;
  unitPath: string;
  status: ItemStatus;
  role: string;
  markdown: string;
  html?: string; // rendered by the extension (escape-first), never persisted
  files: string[];
  notes: string[];
  model: string;
  tokens: number;
  error: string;
}

export interface RunItem {
  type: 'run';
  id: string;
  jobId: string;
  workspace: string;
  task: string;
  unitLabel: string;
  status: ItemStatus;
  progress: string;
  timeline: TimelineEntry[];
  seen: number; // events already turned into timeline entries
  result: RunSummary | null;
  error: string;
  appliedNotes: string[];
}

export interface NoticeItem {
  type: 'notice';
  id: string;
  level: 'info' | 'error';
  text: string;
}

export type ChatItem = UserItem | AnswerItem | RunItem | NoticeItem;

export interface ServerStatus {
  state: 'stopped' | 'starting' | 'ready' | 'error';
  managed: boolean;
  url: string;
  message: string;
}

export interface Attachment {
  path: string; // workspace-relative
  label: string;
}

/** Messages from the webview to the extension. */
export type FromView =
  | { type: 'ready' }
  | {
      type: 'send';
      text: string;
      mode: Mode;
      unitPath: string;
      shareActiveFile: boolean;
      constraints: string[];
      acceptance: string[];
    }
  | { type: 'setMode'; mode: Mode }
  | { type: 'setUnit'; unitPath: string }
  | { type: 'removeAttachment'; path: string }
  | { type: 'apply'; itemId: string }
  | { type: 'discard'; itemId: string }
  | { type: 'stop'; itemId: string }
  | { type: 'openDiff'; itemId: string; path: string }
  | { type: 'reviewAll'; itemId: string }
  | { type: 'openFile'; workspace: string; path: string }
  | { type: 'openReport'; itemId: string }
  | { type: 'newChat' }
  | { type: 'restartServer' }
  | { type: 'showOutput' };

/** Messages from the extension to the webview. */
export type ToView =
  | {
      type: 'init';
      items: ChatItem[];
      units: UnitOption[];
      mode: Mode;
      unitPath: string;
      server: ServerStatus;
      activeFile: string | null;
      shareActiveFile: boolean;
      attachments: Attachment[];
      autoApply: boolean;
    }
  | { type: 'upsert'; item: ChatItem }
  | { type: 'remove'; ids: string[] }
  | { type: 'units'; units: UnitOption[] }
  | { type: 'server'; server: ServerStatus }
  | { type: 'activeFile'; path: string | null }
  | { type: 'attachments'; attachments: Attachment[] }
  | { type: 'prefill'; text: string };
