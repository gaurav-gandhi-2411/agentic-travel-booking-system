import type { NextRequest } from 'next/server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function sseHeaders(): HeadersInit {
  return {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    'X-Accel-Buffering': 'no',
    'Connection': 'keep-alive',
  };
}

function errorSSE(message: string): Response {
  const body = `data: ${JSON.stringify({ type: 'error', message })}\n\n`;
  return new Response(body, { headers: sseHeaders() });
}

export async function POST(req: NextRequest): Promise<Response> {
  let query: string;
  try {
    const body = (await req.json()) as { query?: unknown };
    query = typeof body.query === 'string' ? body.query.trim() : '';
  } catch {
    return errorSSE('Invalid request body');
  }

  if (!query) {
    return errorSSE('query is required');
  }

  const apiBase = process.env.API_BASE_URL ?? 'http://localhost:8000';
  const apiKey = process.env.DEMO_API_KEY ?? '';

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30_000);

  try {
    const upstream = await fetch(`${apiBase}/search`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(apiKey ? { 'X-API-Key': apiKey } : {}),
      },
      body: JSON.stringify({ query }),
      signal: controller.signal,
    });

    clearTimeout(timeoutId);

    if (!upstream.ok || !upstream.body) {
      return errorSSE(`Backend returned ${upstream.status}`);
    }

    return new Response(upstream.body, { headers: sseHeaders() });
  } catch (err) {
    clearTimeout(timeoutId);
    const msg = err instanceof Error ? err.message : 'Connection failed';
    return errorSSE(msg);
  }
}
