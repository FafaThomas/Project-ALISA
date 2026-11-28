# ALISA Coding Agent for VS Code

A sidebar chat for The Forge's coding agent. You can give its team a task and review the changes as
diffs before anything is written, or ask questions about your project. Each message can go to the whole
agent, one division, or one specialist.

The extension is a client of the agent's HTTP service (`agents/forge/coding_agent` in the ALISA
repository; its README covers models and configuration). It starts that service for you, so the
models, tools, files gate and run records are the same as on the command line.

## What you can do

**Code.** Describe what to build or change. The team plans, builds and checks the change, and the
chat shows the progress live: the team, each phase, who is working, the files gate, and the test run.
When the run ends you get a card with:

- the proposed files. Click one for its diff, or **Review changes** for all of them in one view
- the files gate result, the test run, review findings and warnings
- **Apply** writes the changes. **Discard** leaves your files as they are.

Nothing is written until you click Apply. To skip the review step, turn on `alisa.run.autoApply`.

When the files gate or the test run finds problems, the Debugging Specialist fixes them and everything
is checked again. It gets 2 rounds before the run fails. The progress shows each one, e.g.
"repairing (round 1 of 2)". Set `alisa.run.maxRepairs` (0 to 10) to change that. Each round is one
more model call and another check.

Open **Requirements** under the message box to add constraints and acceptance criteria. The agent
keeps them in the project brief, so later runs keep meeting them.

**Follow-ups work.** The team sees the chat before your task: your earlier messages, the answers, and
how each earlier run ended. That includes files gate problems, test commands that hung or crashed,
and high or critical review findings. So "lets fix it" after a failed run tells the team what to fix.

- **Retried runs:** a retried run keeps its earlier try in that chat, so the team knows what went
  wrong last time.
- **How much it sees:** the last 6 turns, newest kept when they are long. The agent's README has the
  limits.

**Ask.** Ask a question about the project. Nothing is changed and nothing is added to the agent's
memory. Who answers depends on what you pick:

| You pick | Who answers |
|---|---|
| Coding Agent (whole team) | the Codebase Analyst |
| A division | its lead, e.g. the Tech Lead for Development |
| A specialist | that specialist |

The answering specialist sees the same Markdown context as a run (conventions, project brief, memory,
docs, file map, related source). It also sees the file in your active editor (you can untick it per
question), any files you attached, and the last 8 turns of the chat. Files named in the answer open
when clicked. Secrets files such as `.env` are never shared, even when they are open.

**Retry or edit a message.**

- **Retry** sends the same message to the same unit again as a new turn. It keeps the attached files
  and requirements. It is on runs that failed, were stopped or discarded, or are waiting for review, and
  on answers.
  - Retrying a proposal discards it, because the new try replaces it.
  - A run whose changes were written has no Retry, because running the same task again would build on
    top of those changes. Send a follow-up instead.
  - A retried question doesn't get the earlier tries of that question in its chat history, so the
    answer is fresh.
- **Edit** on any of your messages puts it back in the message box, with its mode, unit, files and
  requirements, for you to change and send.

**Bring code from the editor.** Select code and run **ALISA: Send Selection to Chat** (also in the
editor's right-click menu). The selection is quoted in the message box and its file is attached.

**Give the chat more room.** The buttons in the chat's title bar:

- **Open Chat in Editor** opens the chat as an editor tab, which you can split, drag, or maximize.
- **Open Chat in New Window** moves that tab into its own window, e.g. for a second monitor. Dragging
  the tab out of VS Code's window does the same. The sidebar can't be dragged out: VS Code only lets
  editor tabs leave the window.
- **Toggle Chat Full Screen** puts the tab in Zen Mode. Run it again, or press Esc twice, to leave.
  In the chat's own window, the button makes that window full screen instead, because VS Code only
  has Zen Mode in the main window.

The sidebar and the tab show the same conversation, so you can use either one. The tab comes back
after a window reload. In a wide tab the messages stay in a readable column.

**Get the chat back after closing it.** Click **ALISA** in the status bar. It opens the chat where you
last put it with those buttons: in its own window, in an editor tab, or in the sidebar. To open it
somewhere else, hover over the item and pick a place.

**Follow along.** The status bar shows what the agent is doing while you work elsewhere. You get a
notification when a run finishes while the chat is hidden.

## Getting started

1. Install the coding agent and set its models in the ALISA `.env` (see its README):

   ```powershell
   python -m pip install -e agents/forge/coding_agent
   ```

2. Build and install the extension:

   ```powershell
   cd services/VSCode_Plugin
   npm install
   npm run package
   code --install-extension alisa-coding-agent-0.1.0.vsix
   ```

   To run it from source instead, open `services/VSCode_Plugin` in VS Code and press F5.

3. Open a project folder and click the ALISA icon in the activity bar.

## How it connects

```
VS Code extension  --HTTP, bearer token-->  coding-agent serve  -->  units, models, tools, workspace
```

By default (`alisa.server.url` empty), the extension starts the service itself, the first time the
chat is shown in a window:

- `coding-agent serve` runs on `127.0.0.1` on a free port.
- It may only work in the open workspace folders, plus any `alisa.server.allowedRoots`.
- It gets a random token through its environment (`CODING_AGENT_API_TOKEN`), never on the command
  line. Other programs on your machine can't use it.
- It stops with the window, along with the Python process the `coding-agent` launcher starts.
- Its output is in **ALISA: Show Agent Server Output**.

To use a service you run yourself, set `alisa.server.url` (e.g. `http://127.0.0.1:8765`), and set its
token with **ALISA: Set Server Token**. The token is kept in VS Code's secret storage. The service must
see your workspace at the same path, and the path must be under one of its `--allow-root` folders.

The extension uses these endpoints:

| Endpoint | Used for |
|---|---|
| `GET /health`, `GET /units` | connecting, and the list of units |
| `POST /runs`, `POST /units/<path>/runs` | Code messages (`mode: "propose"`, or `"apply"` with auto-apply), with the chat as `history` |
| `POST /ask`, `POST /units/<path>/ask` | Ask messages |
| `GET /runs/<job_id>` | progress, polled once a second |
| `POST /proposals/apply` | the Apply button |

## Reviewing and applying

- **Diffs** compare your file with the team's proposal in `.coding-agent/runs/<run>/proposed/`. That
  is a real file. If you edit it in the diff view, your edit is what Apply writes. The files gate
  checked the team's version, not your edit.
- **Edited since the proposal.** The service records each file's state when the team changed it. If
  you changed one of those files afterwards, Apply lists them and asks before replacing them. Unsaved
  editors for those files must be saved or reverted first.
- **Backups.** Every overwritten file is copied to the run's `backup/` folder. After Apply,
  **Review changes** compares that backup with the file now.
- **Run records.** After Apply, the agent records the run as it does on the command line: an entry in
  `CHANGELOG.md`, an updated `.coding-agent/PROJECT.md`, and a line in the run memory.
- **Proposals survive reloads.** Apply reads the proposal from the run folder, so a proposal made
  before a window reload can still be applied.

## Settings

| Setting | Default | |
|---|---|---|
| `alisa.server.url` | empty | a service you run; empty starts a local one |
| `alisa.server.command` | `coding-agent` | program that starts the local service |
| `alisa.server.args` | `[]` | arguments before `serve`, e.g. `["-m", "coding_agent"]` with command `python` |
| `alisa.server.allowedRoots` | `[]` | extra folders the local service may work in |
| `alisa.run.autoApply` | `false` | write changes as soon as they pass the files gate |
| `alisa.run.fixReviewFindings` | `false` | send high and critical review findings back for one fix round |
| `alisa.run.maxTeamSize` | empty | most specialists on a run's team |
| `alisa.run.maxRepairs` | empty (2) | rounds the Debugging Specialist gets to fix files gate and test run problems before the run fails, 0 to 10 |
| `alisa.run.targetEnvironment` | empty | where the generated code must run, e.g. `Windows 11, Python 3.12` |
| `alisa.chat.shareActiveFile` | `true` | in Ask mode, share the active editor's file |

## Commands

| Command | |
|---|---|
| ALISA: New Chat | clear the conversation (running jobs stay) |
| ALISA: Show Chat | the chat where you last put it, opening its closed tab or window again (also the status bar item) |
| ALISA: Show Chat in Sidebar | the chat in the sidebar, from now on where Show Chat goes |
| ALISA: Open Chat in Editor | the chat as an editor tab |
| ALISA: Open Chat in New Window | the chat's editor tab in a window of its own |
| ALISA: Toggle Chat Full Screen | the chat's editor tab in Zen Mode, or back. With the chat in its own window: that window full screen |
| ALISA: Toggle Chat Window Full Screen | in the chat's own window: that window full screen, or back |
| ALISA: Send Selection to Chat | quote the selection and attach its file |
| ALISA: Restart Agent Server | restart the local service, or reconnect to yours |
| ALISA: Show Agent Server Output | the service's log |
| ALISA: Set Server Token | the token for `alisa.server.url` |

## Limitations

- **Stopping a run.** The service can't cancel a single job. **Stop** restarts the local service,
  which ends everything it is working on. With your own service, Stop only stops following the job.
- **Progress lives in the service.** If the window reloads while a job is running, that job's
  progress is lost, because the local service stops with the window. Finished proposals are on disk
  and can still be applied.
- **One model call per answer.** Questions get one model call, with no file browsing, so answers are
  only as good as the context and the model assigned to the answering unit.
- **Cloud models.** Units set to `gemini:` or `openai:` models send what they see, including your
  code, to Google, OpenAI, or the server `OPENAI_API_BASE` points at. This is the same as on the
  command line.
- **Untested inside VS Code.** These are covered by tests: the chat webview, the client, the markdown
  renderer, the Retry and Edit rules, and service start and stop. The VS Code wiring (`chatView.ts`,
  `server.ts`, `extension.ts`) is compiled with strict TypeScript, but no automated test runs it inside
  VS Code. That wiring includes the editor tab, the new window and full screen.
- **A tab you dragged out comes back as a tab.** VS Code doesn't tell extensions which window a tab is
  in. So Show Chat only knows the chat had its own window when you used **Open Chat in New Window**.
  The same applies to **Toggle Chat Full Screen** from the status bar or the Command Palette. The button
  in the chat window's title bar always works.
- **Chats from before Retry.** Replies saved before Retry existed didn't keep their request. Retry
  rebuilds it from the message before the reply, without its requirements. The agent keeps
  requirements in the project brief anyway.

## Development

```powershell
npm install
npm run compile      # TypeScript -> out/
npm test             # compile, then run the tests with Node's test runner
npm run package      # alisa-coding-agent-<version>.vsix
```

The tests cover:

- the HTTP client, against a local server
- the event and report formatting
- the markdown renderer, including injection attempts
- the Retry and Edit rules
- the chat webview, in jsdom

One test also starts the real `coding-agent serve` and stops it. It is skipped when the agent isn't
installed.

| File | |
|---|---|
| `src/extension.ts` | activation, commands, status bar |
| `src/chatView.ts` | the conversation and the views showing it (sidebar, editor tab): sending, retry, following jobs, apply, diffs, stop |
| `src/requests.ts` | what a message asked for: the run payload, and when and how it is sent again |
| `src/server.ts` | the managed or external service and its status |
| `src/service.ts` | starting and stopping the `coding-agent serve` process |
| `src/client.ts` | the HTTP client |
| `src/events.ts` | progress events and reports as the chat shows them (mirrors the CLI) |
| `src/markdown.ts` | escape-first Markdown for answers |
| `src/units.ts` | the unit tree as picker options |
| `src/protocol.ts` | service and webview message types |
| `media/chat.js`, `media/chat.css` | the webview (plain DOM, VS Code theme colors) |
