import { http, HttpResponse, delay } from 'msw';
import { setupWorker } from 'msw/browser';
import { openDB } from 'idb';
import { applyCommand, emptyWorkspace } from './engine';
import type { CommandRequest, Workspace } from '../contracts';

const DATABASE_NAME = 'climada-workbench-v1';
const LEGACY_DATABASE_NAME = 'harbor-workbench-v1';
const openWorkspaceStore = (name: string) => openDB(name, 1, { upgrade(db) {
  if (!db.objectStoreNames.contains('state')) db.createObjectStore('state');
  if (!db.objectStoreNames.contains('requests')) db.createObjectStore('requests');
} });
async function openWorkspaceDatabase() {
  const db = await openWorkspaceStore(DATABASE_NAME);
  if (await db.get('state', 'workspace')) return db;

  const legacy = await openWorkspaceStore(LEGACY_DATABASE_NAME);
  const legacyWorkspace = await legacy.get('state', 'workspace');
  if (legacyWorkspace) {
    const legacyRequestKeys = await legacy.getAllKeys('requests');
    const legacyRequests = await legacy.getAll('requests');
    const tx = db.transaction(['state', 'requests'], 'readwrite');
    await tx.objectStore('state').put(legacyWorkspace, 'workspace');
    for (let index = 0; index < legacyRequestKeys.length; index += 1) {
      await tx.objectStore('requests').put(legacyRequests[index], legacyRequestKeys[index]);
    }
    await tx.done;
  }
  legacy.close();
  return db;
}
const dbPromise = openWorkspaceDatabase();
let queue: Promise<unknown> = Promise.resolve();
async function readState(): Promise<Workspace> { return (await (await dbPromise).get('state', 'workspace')) || emptyWorkspace(); }
async function mutate(body: CommandRequest): Promise<Workspace> {
  const db = await dbPromise;
  const tx = db.transaction(['state', 'requests'], 'readwrite');
  const previous = await tx.objectStore('requests').get(body.requestId);
  const state: Workspace = (await tx.objectStore('state').get('workspace')) || emptyWorkspace();
  if (previous) { await tx.done; return state; }
  if (body.expectedVersion !== state.version) { await tx.done; throw new Error('This workspace changed. Refresh and try again.'); }
  const next = applyCommand(state, body.command);
  await tx.objectStore('state').put(next, 'workspace');
  await tx.objectStore('requests').put({ at: new Date().toISOString() }, body.requestId);
  await tx.done;
  return next;
}
export const worker = setupWorker(
  http.get('/api/workspace', async () => { await delay(100); return HttpResponse.json(await readState()); }),
  http.post('/api/commands', async ({ request }) => {
    const body = await request.json() as CommandRequest;
    if (!body.requestId || typeof body.expectedVersion !== 'number' || !body.command?.type) return HttpResponse.json({ message: 'Invalid command envelope.' }, { status: 400 });
    const operation = queue.then(() => mutate(body));
    queue = operation.catch(() => undefined);
    try { await delay(180); return HttpResponse.json(await operation); }
    catch (error) { return HttpResponse.json({ message: error instanceof Error ? error.message : 'Unable to process request.' }, { status: 409 }); }
  }),
);

// Durable runs continue after navigation/reload. In HTTP mode the backend owns this scheduler.
export function startMockScheduler() {
  const timer = window.setInterval(() => {
    const operation = queue.then(async () => {
      const state = await readState();
      const ready = state.runs.find(run => (run.status === 'running' || run.status === 'queued') && Date.now() - Date.parse(run.createdAt) > 1400);
      if (ready) await mutate({ requestId: `finish:${ready.id}`, expectedVersion: state.version, command: { type: 'complete_run', runId: ready.id } });
    });
    queue = operation.catch(() => undefined);
  }, 700);
  return () => window.clearInterval(timer);
}
