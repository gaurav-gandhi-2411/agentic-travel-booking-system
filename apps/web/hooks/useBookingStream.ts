'use client';

import { useState, useCallback, useRef } from 'react';
import type { SseEvent } from '@/lib/event-map';

export type BookingStatus =
  | 'idle'
  | 'revalidating'
  | 'price_confirm'
  | 'confirmed'
  | 'cancelling'
  | 'cancelled'
  | 'error';

export interface BookingPricedData {
  offer_id: string;
  current_price_inr: number;
  previous_price_inr?: number;
  price_changed: boolean;
  is_available: boolean;
}

export interface BookingConfirmedData {
  pnr: string;
  offer_lock_id: string;
  hold_expires_at: string;
  idempotency_key: string;
  audit_id: string | null;
}

export interface BookingStream {
  status: BookingStatus;
  pricedEvent: BookingPricedData | null;
  confirmedEvent: BookingConfirmedData | null;
  error: string | null;
  errorCode: string | null;
  book: (offerId: string, requestId?: string) => void;
  confirmPriceChange: () => void;
  cancel: (bookingRef: string) => void;
  reset: () => void;
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

async function consumeBookingStream(
  url: string,
  body: object,
  signal: AbortSignal,
  onEvent: (event: SseEvent) => void,
  onDone: () => void,
  onError: (msg: string, code?: string) => void,
): Promise<void> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buf += decoder.decode(value, { stream: true });
    const { events: parsed, remaining } = parseSSEBuffer(buf);
    buf = remaining;

    for (const event of parsed) {
      if (event.type === 'booking_error') {
        onError(event.message ?? 'Unknown booking error', event.code);
        return;
      }
      onEvent(event);
      if (event.type === 'booking_confirmed' || event.type === 'booking_cancelled') {
        onDone();
        return;
      }
    }
  }

  if (!signal.aborted) {
    onDone();
  }
}

export function useBookingStream(): BookingStream {
  const [status, setStatus] = useState<BookingStatus>('idle');
  const [pricedEvent, setPricedEvent] = useState<BookingPricedData | null>(null);
  const [confirmedEvent, setConfirmedEvent] = useState<BookingConfirmedData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const currentOfferIdRef = useRef<string | null>(null);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setStatus('idle');
    setPricedEvent(null);
    setConfirmedEvent(null);
    setError(null);
    setErrorCode(null);
    currentOfferIdRef.current = null;
  }, []);

  const _runBookStream = useCallback(async (
    offerId: string,
    key: string,
    requestId?: string,
  ) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setStatus('revalidating');
    setError(null);
    setErrorCode(null);

    try {
      await consumeBookingStream(
        '/api/book',
        { offer_id: offerId, idempotency_key: key, ...(requestId ? { request_id: requestId } : {}) },
        controller.signal,
        (event) => {
          if (event.type === 'booking_priced') {
            const priced: BookingPricedData = {
              offer_id: event.offer_id ?? offerId,
              current_price_inr: event.current_price_inr ?? 0,
              previous_price_inr: event.previous_price_inr,
              price_changed: event.price_changed ?? false,
              is_available: event.is_available ?? true,
            };
            setPricedEvent(priced);
            if (event.price_changed) {
              setStatus('price_confirm');
            }
          } else if (event.type === 'booking_confirmed') {
            const confirmed: BookingConfirmedData = {
              pnr: event.pnr ?? '',
              offer_lock_id: event.offer_lock_id ?? '',
              hold_expires_at: event.hold_expires_at ?? '',
              idempotency_key: event.idempotency_key ?? key,
              audit_id: event.audit_id ?? null,
            };
            setConfirmedEvent(confirmed);
            setStatus('confirmed');
          }
        },
        () => {
          // onDone — confirmed/cancelled events already set status;
          // if we reach done without confirmed, treat as unexpected
          setStatus(prev => {
            if (prev === 'revalidating') {
              setError('Stream ended unexpectedly');
              return 'error';
            }
            return prev;
          });
        },
        (msg, code) => {
          if (!controller.signal.aborted) {
            setStatus('error');
            setError(msg);
            setErrorCode(code ?? null);
          }
        },
      );
    } catch (err) {
      if (controller.signal.aborted) return;
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Connection failed');
    }
  }, []);

  const book = useCallback((offerId: string, requestId?: string) => {
    const key = crypto.randomUUID();
    currentOfferIdRef.current = offerId;
    void _runBookStream(offerId, key, requestId);
  }, [_runBookStream]);

  const confirmPriceChange = useCallback(() => {
    const offerId = currentOfferIdRef.current;
    if (!offerId) return;
    const newKey = crypto.randomUUID();
    void _runBookStream(offerId, newKey);
  }, [_runBookStream]);

  const cancel = useCallback(async (bookingRef: string) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setStatus('cancelling');
    setError(null);
    setErrorCode(null);

    try {
      await consumeBookingStream(
        '/api/cancel',
        { booking_ref: bookingRef },
        controller.signal,
        (event) => {
          if (event.type === 'booking_cancelled') {
            setStatus('cancelled');
          }
        },
        () => {
          setStatus(prev => {
            if (prev === 'cancelling') {
              setError('Stream ended unexpectedly');
              return 'error';
            }
            return prev;
          });
        },
        (msg, code) => {
          if (!controller.signal.aborted) {
            setStatus('error');
            setError(msg);
            setErrorCode(code ?? null);
          }
        },
      );
    } catch (err) {
      if (controller.signal.aborted) return;
      setStatus('error');
      setError(err instanceof Error ? err.message : 'Connection failed');
    }
  }, []);

  return {
    status,
    pricedEvent,
    confirmedEvent,
    error,
    errorCode,
    book,
    confirmPriceChange,
    cancel,
    reset,
  };
}
