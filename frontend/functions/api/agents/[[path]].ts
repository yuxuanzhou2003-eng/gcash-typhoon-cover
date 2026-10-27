// Cloudflare Pages Function: forwards /api/agents/* to the agent backend named by AGENT_BACKEND_URL.
// AGENT_BACKEND_API_KEY (a Pages secret) is attached server-side so the browser bundle never
// carries the backend's shared key.
interface Env { AGENT_BACKEND_URL?: string; AGENT_BACKEND_API_KEY?: string }

export const onRequest = async ({ request, env }: { request: Request; env: Env }) => {
  const base = env.AGENT_BACKEND_URL?.trim().replace(/\/+$/, '');
  if (!base) return Response.json({ message: 'Agent backend is not configured for this deployment.' }, { status: 503 });
  const url = new URL(request.url);
  const headers = new Headers();
  for (const name of ['content-type', 'accept']) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (env.AGENT_BACKEND_API_KEY) headers.set('x-api-key', env.AGENT_BACKEND_API_KEY);
  const hasBody = !['GET', 'HEAD'].includes(request.method);
  try {
    const upstream = await fetch(base + url.pathname + url.search, { method: request.method, headers, body: hasBody ? await request.arrayBuffer() : undefined });
    return new Response(upstream.body, { status: upstream.status, headers: { 'content-type': upstream.headers.get('content-type') ?? 'application/json' } });
  } catch (error) {
    return Response.json({ message: `Agent backend is offline. ${error instanceof Error ? error.message : ''}`.trim() }, { status: 502 });
  }
};
