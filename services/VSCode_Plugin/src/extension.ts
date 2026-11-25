import * as vscode from 'vscode';
import { ChatViewProvider, EMPTY_SCHEME } from './chatView';
import { AgentServer, TOKEN_SECRET } from './server';

let server: AgentServer | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel('ALISA Coding Agent');
  server = new AgentServer(context.secrets, output);
  const chat = new ChatViewProvider(context, server);
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = `${ChatViewProvider.viewId}.focus`;

  const renderStatus = (): void => {
    const progress = chat.activeProgress();
    const state = server?.status;
    status.backgroundColor = undefined;
    if (progress) {
      status.text = `$(sync~spin) ALISA: ${progress.length > 40 ? `${progress.slice(0, 39)}…` : progress}`;
      status.tooltip = 'The coding agent is working. Click to open the chat.';
    } else if (state?.state === 'starting') {
      status.text = '$(loading~spin) ALISA';
      status.tooltip = state.message;
    } else if (state?.state === 'error') {
      status.text = '$(warning) ALISA';
      status.tooltip = state.message;
      status.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else {
      status.text = '$(hubot) ALISA';
      status.tooltip = state?.state === 'ready' ? `Coding agent service: ${state.url}` : 'ALISA coding agent: open the chat';
    }
    status.show();
  };
  renderStatus();

  context.subscriptions.push(
    output, server, chat, status,
    server.onDidChange(renderStatus),
    chat.onDidChangeProgress(renderStatus),
    vscode.window.registerWebviewViewProvider(ChatViewProvider.viewId, chat),
    // Diffs of created and deleted files compare against an empty document.
    vscode.workspace.registerTextDocumentContentProvider(EMPTY_SCHEME, { provideTextDocumentContent: () => '' }),
    vscode.commands.registerCommand('alisa.newChat', () => chat.newChat()),
    vscode.commands.registerCommand('alisa.sendSelection', () => chat.sendSelection()),
    vscode.commands.registerCommand('alisa.showOutput', () => output.show()),
    vscode.commands.registerCommand('alisa.restartServer', async () => {
      try {
        await server?.restart();
      } catch (error) {
        const choice = await vscode.window.showErrorMessage(
          `ALISA: ${error instanceof Error ? error.message : String(error)}`, 'Show Output');
        if (choice) {
          output.show();
        }
      }
    }),
    vscode.commands.registerCommand('alisa.setToken', async () => {
      const token = await vscode.window.showInputBox({
        title: 'ALISA: coding agent service token',
        prompt: 'The CODING_AGENT_API_TOKEN of the service in "alisa.server.url". Leave empty to remove it.',
        password: true,
        ignoreFocusOut: true,
      });
      if (token === undefined) {
        return;
      }
      if (token.trim()) {
        await context.secrets.store(TOKEN_SECRET, token.trim());
      } else {
        await context.secrets.delete(TOKEN_SECRET);
      }
      void vscode.window.showInformationMessage('ALISA: service token saved.');
    }),
  );
}

export async function deactivate(): Promise<void> {
  await server?.stop();
}
