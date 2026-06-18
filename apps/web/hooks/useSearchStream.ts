'use client';

import { useState, useCallback, useRef } from 'react';
import type { SseEvent, Archetype, RouteAlternative } from '@/lib/event-map';

export type SearchStatus = 'idle' | 'streaming' | 'done' | 'error';

export interface NoDataState {
  origin_iata: string;
  destination_iata: string;
  message: string;
  alternatives: RouteAlternative[];
}

export interface SearchStream {
  start: (query: string, profile?: string) => void;
  refine: (refinement: string, profile?: string) => void;
  events: SseEvent[];
  archetypes: Archetype[];
  status: SearchStatus;
  error: string | null;
  noData: NoDataState | null;
  reset: () => void;
  lastQuery: string;
  requestId: string | null;
}

function parseSSEBuffer(buffer: string): { events: SseEvent[]; remaining: string } {
  const parts = buffer.split('\n\n');
  const remaining = parts.pop() ?? '';
  const events: SseEvent[] = [];

  for (const part of parts) {
    for (const line of part.split('\n')) {
      const trimmed = line.trim();
      if (trimmed.startsWith('data:')) {
        const data = trimmed.slice(5).trim();
        if (data) {
          try {
            events.push(JSON.parse(data) as SseEvent);
          } catch {
            // skip malformed lines
          }
        }
      }
    }
  }

  return { events, remaining };
}

async function consumeStream(
  url: string,
  body: object,
  signal: AbortSignal,
  onEvent: (event: SseEvent) => void,
  onDone: () => void,
  onError: (msg: string) => void,
  profile?: string,
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(profile ? { 'X-LLM-Profile': profile } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok || !response.body) {
    onError(`Request failed (${response.status})`);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let sawDone = false;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buf += decoder.decode(value, { stream: true });
    const { events: parsed, remaining } = parseSSEBuffer(buf);
    buf = remaining;

    for (const event of parsed) {
      if (event.type === 'done') sawDone = true;
      if (event.type === 'no_data_for_route') {
        onEvent(event);
        onDone(); // treat as a clean end so status -> 'done' and the input re-enables
        return;
      }
      if (event.type === 'error') {
        onError(event.message ?? 'Unknown error');
        return;
      }
      onEvent(event);
    }
  }

  if (!signal.aborted) {
    if (sawDone) onDone();
    else onError('Stream ended unexpectedly');
  }
}

export function useSearchStream(): SearchStream {
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [archetypes, setArchetypes] = useState<Archetype[]>([]);
  const [status, setStatus] = useState<SearchStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [noData, setNoData] = useState<NoDataState | null>(null);
  const [lastQuery, setLastQuery] = useState('');
  const [requestId, setRequestId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setEvents([]);
    setArchetypes([]);
    setStatus('idle');
    setError(null);
    setNoData(null);
    setRequestId(null);
  }, []);

  const _runStream = useCallback(async (
    url: string,
    body: object,
    appendEvents: boolean,
    profile?: string,
  ) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    if (!appendEvents) {
      setEvents([]);
      setArchetypes([]);
      setError(null);
      setNoData(null);
    } else {
      // For refine: keep prior events, clear archetypes to show new ones
      setArchetypes([]);
    }
    setStatus('streaming');

    try {
      await consumeStream(
        url,
        body,
        controller.signal,
        (event) => {
          setEvents(prev => [...prev, event]);
          if (event.type === 'archetype_ready' && event.archetype) {
            setArchetypes(prev => [...prev, event.archetype!]);
          }
          if (event.type === 'done' && event.request_id) {
            setRequestId(event.request_id);
          }
          if (event.type === 'no_data_for_route') {
            setNoData({
              origin_iata: event.origin_iata ?? '',
              destination_iata: event.destination_iata ?? '',
              message: event.message ?? 'No flights found.',
              alternatives: event.alternatives ?? [],
            });
          }
        },
        () => setStatus('done'),
        (msg) => {
          setStatus('error');
          setError(msg);
        },
        profile,
      );
    } catch (err) {
      if (controller.signal.aborted) return;
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Connection failed');
    }
  }, []);

  const start = useCallback(async (query: string, profile?: string) => {
    setLastQuery(query);
    setRequestId(null);
    await _runStream('/api/search', { query }, false, profile);
  }, [_runStream]);

  const refine = useCallback(async (refinement: string, profile?: string) => {
    if (!requestId) return;
    await _runStream('/api/refine', { request_id: requestId, refinement }, true, profile);
  }, [_runStream, requestId]);

  return { start, refine, events, archetypes, status, error, noData, reset, lastQuery, requestId };
}
