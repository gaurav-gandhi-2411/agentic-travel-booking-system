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
  let offer_id: string;
  let idempotency_key: string;
  let request_id: string | undefined;

  let accept_price_change: boolean;

  try {
    const body = (await req.json()) as {
      offer_id?: unknown;
      idempotency_key?: unknown;
      request_id?: unknown;
      accept_price_change?: unknown;
    };
    offer_id = typeof body.offer_id === 'string' ? body.offer_id.trim() : '';
    idempotency_key = typeof body.idempotency_key === 'string' ? body.idempotency_key.trim() : '';
    request_id =
      typeof body.request_id === 'string' ? body.request_id.trim() : undefined;
    accept_price_change = body.accept_price_change === true;
  } catch {
    return errorSSE('Invalid request body');
  }

  if (!offer_id || !idempotency_key) {
    return errorSSE('offer_id and idempotency_key are required');
  }

  const apiBase = process.env.API_BASE_URL ?? 'http://localhost:8000';
  const apiKey = process.env.DEMO_API_KEY ?? '';

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30_000);

  try {
    const upstream = await fetch(`${apiBase}/book`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(apiKey ? { 'X-API-Key': apiKey } : {}),
      },
      body: JSON.stringify({
        offer_id,
        idempotency_key,
        ...(request_id ? { request_id } : {}),
        ...(accept_price_change ? { accept_price_change: true } : {}),
      }),
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
