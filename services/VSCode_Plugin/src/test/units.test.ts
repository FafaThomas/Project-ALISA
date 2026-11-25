import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { UnitNode } from '../protocol';
import { AGENT_OPTION, flattenUnits, unitLabel, unitNames } from '../units';

const tree: UnitNode = {
  id: 'coding_agent', path: '', name: 'Coding Agent', level: 'agent', lead: 'engineering_director', units: [
    {
      id: 'development', path: 'development', name: 'Development', level: 'division', lead: 'tech_lead', units: [
        { id: 'tech_lead', path: 'development/tech_lead', name: 'Tech Lead', level: 'specialist', responsibility: 'Plans', units: [] },
        {
          id: 'database_developer', path: 'development/database_developer', name: 'Database Developer', level: 'specialist',
          is_team: true, responsibility: 'Databases', units: [
            { id: 'postgres_developer', path: 'development/database_developer/postgres_developer', name: 'Postgres Developer', level: 'specialist', units: [] },
          ],
        },
      ],
    },
  ],
};

test('the picker lists the agent, then each division and its specialists in tree order', () => {
  const options = flattenUnits(tree);
  assert.deepEqual(options.map((o) => [o.path, o.depth, o.group]), [
    ['', 0, 'Agent'],
    ['development', 0, 'Development'],
    ['development/tech_lead', 1, 'Development'],
    ['development/database_developer', 1, 'Development'],
    ['development/database_developer/postgres_developer', 2, 'Development'],
  ]);
  assert.equal(options[3].label, 'Database Developer (team)');
  assert.equal(unitLabel(options, 'development/database_developer'), 'Database Developer');
  assert.equal(unitLabel(options, ''), 'Coding Agent');
  assert.equal(unitLabel(options, 'not/loaded'), 'not/loaded');
});

test('without a service the picker still offers the whole agent', () => {
  assert.deepEqual(flattenUnits(null), [AGENT_OPTION]);
});

test('names cover every level', () => {
  const names = unitNames(tree);
  assert.equal(names.get('coding_agent'), 'Coding Agent');
  assert.equal(names.get('postgres_developer'), 'Postgres Developer');
});
