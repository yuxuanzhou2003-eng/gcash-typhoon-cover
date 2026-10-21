import type { Command, Workspace } from './contracts';
import { workspaceSchema } from './schemas';
export { merchantSchema, portfolioImportSchema } from './schemas';

const BASE = import.meta.env.VITE_API_BASE_URL || '/api';
export const demoMode = import.meta.env.VITE_DATA_MODE !== 'http';

async function request(path: string, init?: RequestInit): Promise<Workspace> {
  const res = await fetch(`${BASE}${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers } });
  const body = await res.json();
  if (!res.ok) throw new Error(body.message || `Request failed (${res.status}).`);
  return workspaceSchema.parse(body);
}
export const api = {
  workspace: () => request('/workspace'),
  command: (command: Command, expectedVersion: number, requestId = crypto.randomUUID()) => request('/commands', { method: 'POST', body: JSON.stringify({ requestId, expectedVersion, command }) }),
};
