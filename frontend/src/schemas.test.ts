import { describe, expect, it } from 'vitest';
import { workspaceSchema } from './schemas';
import { applyCommand, emptyWorkspace } from './mocks/engine';

describe('workspace API boundary', () => {
  it('accepts engine output and rejects malformed nested backend records', () => {
    const state = applyCommand(emptyWorkspace(), { type: 'load_demo' });
    expect(workspaceSchema.safeParse(state).success).toBe(true);
    const invalid = structuredClone(state);
    invalid.portfolios[0].merchants[0].sumInsured = 1.5;
    expect(workspaceSchema.safeParse(invalid).success).toBe(false);
    const invalidWind = structuredClone(state);
    invalidWind.storms[0].bulletins[0].wind = -1;
    expect(workspaceSchema.safeParse(invalidWind).success).toBe(false);
  });
});
