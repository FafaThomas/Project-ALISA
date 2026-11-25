/** The unit tree from GET /units, as options for the chat's unit picker. */

import type { UnitNode, UnitOption } from './protocol';

export const AGENT_OPTION: UnitOption = {
  path: '', label: 'Coding Agent (whole team)', level: 'agent', depth: 0, group: 'Agent',
  description: 'The Engineering Director picks a team for each request.',
};

/** The agent first, then each division followed by its specialists (and sub-specialists, indented). */
export function flattenUnits(root: UnitNode | null | undefined): UnitOption[] {
  if (!root) {
    return [AGENT_OPTION];
  }
  const options: UnitOption[] = [{ ...AGENT_OPTION, label: `${root.name} (whole team)` }];
  for (const division of root.units ?? []) {
    options.push({
      path: division.path, label: `${division.name} (division)`, level: 'division', depth: 0,
      group: division.name, description: division.lead ? `Lead: ${division.lead}` : '',
    });
    const walk = (node: UnitNode, depth: number): void => {
      options.push({
        path: node.path, label: node.name + (node.is_team ? ' (team)' : ''), level: node.level, depth,
        group: division.name, description: node.responsibility ?? '',
      });
      for (const child of node.units ?? []) {
        walk(child, depth + 1);
      }
    };
    for (const specialist of division.units ?? []) {
      walk(specialist, 1);
    }
  }
  return options;
}

/** id -> display name for every unit, used to describe progress events. */
export function unitNames(root: UnitNode | null | undefined): Map<string, string> {
  const names = new Map<string, string>();
  const walk = (node: UnitNode): void => {
    names.set(node.id, node.name);
    for (const child of node.units ?? []) {
      walk(child);
    }
  };
  if (root) {
    walk(root);
  }
  return names;
}

export function unitLabel(options: UnitOption[], path: string): string {
  return options.find((o) => o.path === path)?.label.replace(/ \((whole team|division|team)\)$/, '') ?? (path || 'Coding Agent');
}
