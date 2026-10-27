'use strict';
// Aliyun Function Compute Node.js runtime HTTP handler.
// Forwards everything to AGENT_BACKEND_URL and attaches AGENT_BACKEND_API_KEY server-side,
// so the key never reaches the browser. Mirrors the Cloudflare Pages Function this replaces.

const BASE = (process.env.AGENT_BACKEND_URL || '').trim().replace(/\/+$/, '');
const API_KEY = process.env.AGENT_BACKEND_API_KEY || '';

const CORS = {
  'access-control-allow-origin': 'https://climada.ezethan.com',
  'access-control-allow-methods': 'GET, POST, OPTIONS',
  'access-control-allow-headers': 'Content-Type',
};

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

exports.handler = async (req, resp, context) => {
  for (const [k, v] of Object.entries(CORS)) resp.setHeader(k, v);

  if (req.method === 'OPTIONS') {
    resp.setStatusCode(204);
    return resp.send('');
  }
  if (!BASE) {
    resp.setStatusCode(503);
    resp.setHeader('content-type', 'application/json');
    return resp.send(JSON.stringify({ message: 'Agent backend is not configured for this deployment.' }));
  }

  const body = ['GET', 'HEAD'].includes(req.method) ? undefined : await readBody(req);
  const headers = { accept: req.headers.accept || 'application/json' };
  if (req.headers['content-type']) headers['content-type'] = req.headers['content-type'];
  if (API_KEY) headers['x-api-key'] = API_KEY;

  try {
    const upstream = await fetch(BASE + req.url, { method: req.method, headers, body });
    const upstreamBody = Buffer.from(await upstream.arrayBuffer());
    resp.setStatusCode(upstream.status);
    resp.setHeader('content-type', upstream.headers.get('content-type') || 'application/json');
    resp.send(upstreamBody);
  } catch (error) {
    resp.setStatusCode(502);
    resp.setHeader('content-type', 'application/json');
    resp.send(JSON.stringify({ message: `Agent backend is offline. ${error instanceof Error ? error.message : ''}`.trim() }));
  }
};
