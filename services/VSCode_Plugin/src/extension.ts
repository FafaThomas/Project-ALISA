import * as vscode from 'vscode';
import { ChatViewProvider, EMPTY_SCHEME } from './chatView';
import { AgentServer, TOKEN_SECRET } from './server';

let server: AgentServer | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel('ALISA Coding Agent');
  server = new AgentServer(context.secrets, output);
  const chat = new ChatViewProvider(context, server);
  // The way back to the chat when its tab or window was closed: click it, or pick a place in its tooltip.
  const status = vscode.window.createStatusBarItem('alisa.status', vscode.StatusBarAlignment.Left, 50);
  status.name = 'ALISA Chat';
  status.command = 'alisa.showChat';
  const tooltip = (line: string): vscode.MarkdownString => {
    const md = new vscode.MarkdownString(undefined, true);
    md.appendText(line);
    md.appendMarkdown('\n\nClick to open the chat where you last had it, or open it in the '
      + '[$(layout-sidebar-left) sidebar](command:alisa.showInSidebar), '
      + 'an [$(link-external) editor tab](command:alisa.openInEditor), '
      + 'a [$(empty-window) new window](command:alisa.openInNewWindow) or '
      + '[$(screen-full) full screen](command:alisa.toggleFullScreen).');
    md.isTrusted = { enabledCommands: ['alisa.showInSidebar', 'alisa.openInEditor', 'alisa.openInNewWindow', 'alisa.toggleFullScreen'] };
    return md;
  };

  const renderStatus = (): void => {
    const progress = chat.activeProgress();
    const state = server?.status;
    status.backgroundColor = undefined;
    if (progress) {
      status.text = `$(sync~spin) ALISA: ${progress.length > 40 ? `${progress.slice(0, 39)}…` : progress}`;
      status.tooltip = tooltip(`The coding agent is working: ${progress}`);
    } else if (state?.state === 'starting') {
      status.text = '$(loading~spin) ALISA';
      status.tooltip = tooltip(state.message);
    } else if (state?.state === 'error') {
      status.text = '$(warning) ALISA';
      status.tooltip = tooltip(state.message);
      status.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
    } else {
      status.text = '$(hubot) ALISA';
      status.tooltip = tooltip(state?.state === 'ready' ? `Coding agent service: ${state.url}` : 'ALISA coding agent');
    }
    status.show();
  };
  renderStatus();

  context.subscriptions.push(
    output, server, chat, status,
    server.onDidChange(renderStatus),
    chat.onDidChangeProgress(renderStatus),
    vscode.window.registerWebviewViewProvider(ChatViewProvider.viewId, chat),
    // The chat's editor tab comes back after a reload (activation event onWebviewPanel:alisa.chatPanel).
    vscode.window.registerWebviewPanelSerializer(ChatViewProvider.panelType, {
      deserializeWebviewPanel: async (panel) => chat.restorePanel(panel),
    }),
    // Diffs of created and deleted files compare against an empty document.
    vscode.workspace.registerTextDocumentContentProvider(EMPTY_SCHEME, { provideTextDocumentContent: () => '' }),
    vscode.commands.registerCommand('alisa.newChat', () => chat.newChat()),
    vscode.commands.registerCommand('alisa.showChat', () => chat.show()),
    vscode.commands.registerCommand('alisa.showInSidebar', () => chat.showInSidebar()),
    vscode.commands.registerCommand('alisa.openInEditor', () => chat.openInEditor()),
    vscode.commands.registerCommand('alisa.openInNewWindow', () => chat.openInNewWindow()),
    vscode.commands.registerCommand('alisa.toggleFullScreen', () => chat.toggleFullScreen()),
    // In the chat's own window (its title bar, when isAuxiliaryWindow): that window full screen.
    vscode.commands.registerCommand('alisa.toggleWindowFullScreen',
      () => vscode.commands.executeCommand('workbench.action.toggleFullScreen')),
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
