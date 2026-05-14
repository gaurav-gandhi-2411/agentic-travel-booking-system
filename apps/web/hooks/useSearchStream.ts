'use client';

import { useState, useCallback, useRef } from 'react';
import type { SseEvent, Archetype } from '@/lib/event-map';

export type SearchStatus = 'idle' | 'streaming' | 'done' | 'error';

export interface SearchStream {
  start: (query: string) => void;
  events: SseEvent[];
  archetypes: Archetype[];
  status: SearchStatus;
  error: string | null;
  reset: () => void;
  lastQuery: string;
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

export function useSearchStream(): SearchStream {
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [archetypes, setArchetypes] = useState<Archetype[]>([]);
  const [status, setStatus] = useState<SearchStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [lastQuery, setLastQuery] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setEvents([]);
    setArchetypes([]);
    setStatus('idle');
    setError(null);
  }, []);

  const start = useCallback(async (query: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLastQuery(query);
    setEvents([]);
    setArchetypes([]);
    setError(null);
    setStatus('streaming');

    try {
      const response = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        setStatus('error');
        setError(`Request failed (${response.status})`);
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
          setEvents(prev => [...prev, event]);

          if (event.type === 'archetype_ready' && event.archetype) {
            const arch = event.archetype;
            setArchetypes(prev => [...prev, arch]);
          }
          if (event.type === 'done') {
            sawDone = true;
          }
          if (event.type === 'error') {
            setStatus('error');
            setError(event.message ?? 'Unknown error');
            return;
          }
        }
      }

      if (controller.signal.aborted) return;
      setStatus(sawDone ? 'done' : 'error');
      if (!sawDone) {
        setError('Stream ended unexpectedly');
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Connection failed');
    }
  }, []);

  return { start, events, archetypes, status, error, reset, lastQuery };
}
