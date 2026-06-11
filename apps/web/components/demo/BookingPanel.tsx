'use client';

import { useEffect, useState } from 'react';
import { Loader2, CheckCircle2, X, AlertTriangle, XCircle } from 'lucide-react';
import type { BookingStream } from '@/hooks/useBookingStream';
import type { Archetype } from '@/lib/event-map';
import { cn } from '@/lib/utils';

export interface BookingPanelProps {
  booking: BookingStream;
  archetype: Archetype | null;
  onClose: () => void;
}

function formatINR(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(amount);
}

function formatCountdown(expiresAt: string): string {
  const remaining = new Date(expiresAt).getTime() - Date.now();
  if (remaining <= 0) return 'Expired';
  const totalSeconds = Math.floor(remaining / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return [h, m, s].map(v => String(v).padStart(2, '0')).join(':');
}

/** Live countdown timer that ticks every second. */
function HoldCountdown({ expiresAt }: { expiresAt: string }) {
  const [display, setDisplay] = useState(() => formatCountdown(expiresAt));

  useEffect(() => {
    const id = setInterval(() => setDisplay(formatCountdown(expiresAt)), 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  return (
    <span className={cn('font-mono font-semibold tabular-nums', display === 'Expired' ? 'text-red-600' : 'text-foreground')}>
      {display}
    </span>
  );
}

/** Sandbox badge rendered in booking flow states. */
function SandboxBadge() {
  return (
    <div className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-[11px] font-medium text-amber-700">
      <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />
      Sandbox · demo booking — no payment taken
    </div>
  );
}

interface ErrorConfig {
  heading: string;
  message: string;
  showRetry: boolean;
  showDeeplink: boolean;
}

function getErrorConfig(errorCode: string | null, fallbackMessage: string | null): ErrorConfig {
  switch (errorCode) {
    case 'not_bookable':
      return {
        heading: 'Direct booking not available',
        message: "This inventory source doesn't support direct booking. Use the 'Book on Aviasales' link instead.",
        showRetry: false,
        showDeeplink: true,
      };
    case 'unavailable':
      return {
        heading: 'Offer no longer available',
        message: 'This flight is no longer available. Try searching again.',
        showRetry: true,
        showDeeplink: false,
      };
    case 'conflict':
      return {
        heading: 'Booking conflict',
        message: 'A booking is already in progress for this offer. Please wait and try again.',
        showRetry: true,
        showDeeplink: false,
      };
    case 'provider_error':
      return {
        heading: 'Provider error',
        message: 'Something went wrong with the booking provider. Please try again.',
        showRetry: true,
        showDeeplink: false,
      };
    case 'not_found':
      return {
        heading: 'Booking not found',
        message: 'This booking reference was not found or was already cancelled.',
        showRetry: true,
        showDeeplink: false,
      };
    default:
      return {
        heading: 'Booking error',
        message: fallbackMessage ?? 'An unexpected error occurred.',
        showRetry: true,
        showDeeplink: false,
      };
  }
}

export default function BookingPanel({ booking, archetype, onClose }: BookingPanelProps) {
  const { status, pricedEvent, confirmedEvent, error, errorCode } = booking;

  const showCloseX = status === 'price_confirm' || status === 'error';

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-border/60 bg-background p-5">
      {/* Top row: close button for applicable states */}
      {showCloseX && (
        <div className="flex justify-end">
          <button
            onClick={onClose}
            className="ml-auto text-muted-foreground hover:text-foreground"
            aria-label="Close booking panel"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* REVALIDATING */}
      {status === 'revalidating' && (
        <div className="flex flex-col gap-3">
          <SandboxBadge />
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin text-foreground/60" />
            Checking availability and current price…
          </div>
        </div>
      )}

      {/* PRICE_CONFIRM */}
      {status === 'price_confirm' && pricedEvent && (
        <div className="flex flex-col gap-3">
          <SandboxBadge />
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-500" />
            <span className="text-sm font-semibold text-foreground">Price has changed</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            {pricedEvent.previous_price_inr != null && (
              <span className="text-muted-foreground line-through">
                {formatINR(pricedEvent.previous_price_inr)}
              </span>
            )}
            <span className="text-muted-foreground/60">→</span>
            <span className="font-bold text-foreground">{formatINR(pricedEvent.current_price_inr)}</span>
          </div>
          <p className="text-xs text-muted-foreground">
            The price for this offer has changed since your search. Confirm at the new price to continue.
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={booking.confirmPriceChange}
              className={cn(
                'inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium text-white',
                'transition-colors duration-150',
                archetype?.label === 'best-value'
                  ? 'bg-teal-600 hover:bg-teal-700'
                  : 'bg-blue-600 hover:bg-blue-700',
              )}
            >
              Confirm at {formatINR(pricedEvent.current_price_inr)}
            </button>
            <button
              onClick={onClose}
              className={cn(
                'inline-flex items-center justify-center rounded-lg border border-border px-4 py-2',
                'text-sm font-medium text-foreground/70 hover:bg-muted/60 transition-colors duration-150',
              )}
            >
              Go back
            </button>
          </div>
        </div>
      )}

      {/* CONFIRMED */}
      {status === 'confirmed' && confirmedEvent && (
        <div className="flex flex-col gap-3">
          <SandboxBadge />
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-green-500" />
            <span className="text-sm font-semibold text-foreground">Booking confirmed</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">PNR:</span>
            <span className="font-mono font-bold text-foreground tracking-wider">{confirmedEvent.pnr}</span>
          </div>
          {confirmedEvent.hold_expires_at && (
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Hold expires in:</span>
              <HoldCountdown expiresAt={confirmedEvent.hold_expires_at} />
            </div>
          )}
          {archetype && (
            <div className="text-xs text-muted-foreground rounded-lg border border-border/40 bg-muted/20 px-3 py-2">
              {archetype.flight.origin_iata} → {archetype.flight.destination_iata}
              {' · '}{archetype.flight.airline_code} {archetype.flight.flight_number}
              {archetype.flight.outbound_departure_at && archetype.flight.outbound_arrival_at && (
                <>
                  {' · '}
                  {archetype.flight.outbound_departure_at.split('T')[1]?.slice(0, 5) ?? '—'}
                  {' → '}
                  {archetype.flight.outbound_arrival_at.split('T')[1]?.slice(0, 5) ?? '—'}
                </>
              )}
            </div>
          )}
          <button
            onClick={() => booking.cancel(confirmedEvent.pnr)}
            className={cn(
              'inline-flex items-center justify-center rounded-lg border border-red-200 px-4 py-2',
              'text-sm font-medium text-red-600 hover:bg-red-50 transition-colors duration-150',
            )}
          >
            Cancel this booking
          </button>
        </div>
      )}

      {/* CANCELLING */}
      {status === 'cancelling' && (
        <div className="flex flex-col gap-3">
          <SandboxBadge />
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin text-foreground/60" />
            Cancelling…
          </div>
        </div>
      )}

      {/* CANCELLED */}
      {status === 'cancelled' && (
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-5 w-5 text-green-500" />
            <span className="text-sm font-semibold text-foreground">Booking cancelled</span>
          </div>
          <p className="text-xs text-muted-foreground">Hold released. No charges were made.</p>
          <button
            onClick={onClose}
            className={cn(
              'inline-flex items-center justify-center rounded-lg border border-border px-4 py-2',
              'text-sm font-medium text-foreground/70 hover:bg-muted/60 transition-colors duration-150 w-fit',
            )}
          >
            Close
          </button>
        </div>
      )}

      {/* ERROR */}
      {status === 'error' && (() => {
        const cfg = getErrorConfig(errorCode, error);
        return (
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2">
              <XCircle className="h-5 w-5 text-red-500" />
              <span className="text-sm font-semibold text-foreground">{cfg.heading}</span>
            </div>
            <p className="text-xs text-muted-foreground">{cfg.message}</p>
            {cfg.showDeeplink && archetype?.deeplink_url && (
              <a
                href={archetype.deeplink_url}
                target="_blank"
                rel="noopener noreferrer"
                className={cn(
                  'inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium',
                  'text-white transition-colors duration-150 w-fit',
                  archetype.label === 'best-value'
                    ? 'bg-teal-600 hover:bg-teal-700'
                    : 'bg-blue-600 hover:bg-blue-700',
                )}
              >
                Book on Aviasales
              </a>
            )}
            <div className="flex items-center gap-2">
              {cfg.showRetry && archetype && (
                <button
                  onClick={() => booking.book(archetype.flight.id)}
                  className={cn(
                    'inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium text-white',
                    'transition-colors duration-150',
                    archetype.label === 'best-value'
                      ? 'bg-teal-600 hover:bg-teal-700'
                      : 'bg-blue-600 hover:bg-blue-700',
                  )}
                >
                  Try again
                </button>
              )}
              <button
                onClick={onClose}
                className={cn(
                  'inline-flex items-center justify-center rounded-lg border border-border px-4 py-2',
                  'text-sm font-medium text-foreground/70 hover:bg-muted/60 transition-colors duration-150',
                )}
              >
                Go back
              </button>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
