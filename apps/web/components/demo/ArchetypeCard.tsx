'use client';

import { useState } from 'react';
import { ExternalLink, ChevronDown, ChevronUp } from 'lucide-react';
import type { Archetype } from '@/lib/event-map';
import { cn } from '@/lib/utils';

interface ArchetypeCardProps {
  archetype: Archetype;
  onBook?: () => void;
  isBookingActive?: boolean;
}

function formatINR(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(amount);
}

function formatDuration(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function formatTime(isoString: string): string {
  try {
    const timePart = isoString.split('T')[1];
    if (!timePart) return '—';
    return timePart.slice(0, 5);
  } catch {
    return '—';
  }
}

function formatStops(count: number): string {
  if (count === 0) return 'Direct';
  return count === 1 ? '1 stop' : `${count} stops`;
}

const LABEL_CONFIG = {
  'best-value': {
    label: 'Best Value',
    badgeBg: 'bg-teal-100 text-teal-700',
    cardBg: 'bg-teal-50/60 border-teal-200/80',
    btnBg: 'bg-teal-600 hover:bg-teal-700',
    comparisonBg: 'bg-teal-50 border-teal-100',
  },
  'best-experience': {
    label: 'Best Experience',
    badgeBg: 'bg-blue-100 text-blue-700',
    cardBg: 'bg-blue-50/60 border-blue-200/80',
    btnBg: 'bg-blue-600 hover:bg-blue-700',
    comparisonBg: 'bg-blue-50 border-blue-100',
  },
} as const;

export default function ArchetypeCard({ archetype, onBook, isBookingActive = false }: ArchetypeCardProps) {
  const { flight, explanation, comparison_to_alternative, deeplink_url, label } = archetype;
  const config = LABEL_CONFIG[label];
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const hasComparison = !!comparison_to_alternative;

  return (
    <div
      className={cn(
        'rounded-xl border p-5 flex flex-col gap-4 hover:shadow-md transition-shadow duration-200',
        config.cardBg,
      )}
    >
      {/* Header: label + price */}
      <div className="flex items-start justify-between gap-2">
        <span className={cn('text-[11px] font-semibold uppercase tracking-wider px-2.5 py-1 rounded-md', config.badgeBg)}>
          {config.label}
        </span>
        <span className="text-xl font-bold text-foreground tabular-nums">
          {formatINR(flight.price_inr)}
        </span>
      </div>

      {/* Flight summary */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-1.5 text-sm font-medium text-foreground">
          <span>{flight.origin_iata}</span>
          <span className="text-muted-foreground text-xs">→</span>
          <span>{flight.destination_iata}</span>
          <span className="text-muted-foreground/40 ml-1">·</span>
          <span className="text-muted-foreground text-xs font-normal">
            {flight.airline_code} {flight.flight_number}
          </span>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span>{formatTime(flight.outbound_departure_at)}</span>
          <span>→</span>
          <span>{formatTime(flight.outbound_arrival_at)}</span>
          <span className="text-muted-foreground/40">·</span>
          <span>{formatDuration(flight.outbound_duration_minutes)}</span>
          <span className="text-muted-foreground/40">·</span>
          <span>{formatStops(flight.layover_count)}</span>
        </div>
      </div>

      {/* Optimizer explanation */}
      <p className="text-sm text-foreground/80 leading-relaxed border-t border-black/5 pt-3">
        {explanation}
      </p>

      {/* "Why this?" comparison — expand on click */}
      {hasComparison && (
        <div className="flex flex-col gap-2">
          <button
            onClick={() => setComparisonOpen(v => !v)}
            className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors w-fit"
          >
            {comparisonOpen ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            Why this over the other?
          </button>
          {comparisonOpen && (
            <p className={cn(
              'text-xs text-muted-foreground leading-relaxed italic rounded-lg border px-3 py-2',
              config.comparisonBg,
            )}>
              {comparison_to_alternative}
            </p>
          )}
        </div>
      )}

      {/* Book button */}
      {deeplink_url ? (
        <a
          href={deeplink_url}
          target="_blank"
          rel="noopener noreferrer"
          className={cn(
            'inline-flex items-center justify-center gap-1.5 rounded-lg px-4 py-2.5',
            'text-sm font-medium text-white transition-colors duration-150',
            config.btnBg,
          )}
        >
          Book on Aviasales
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      ) : (
        <div className="rounded-lg px-4 py-2.5 text-xs text-muted-foreground text-center border border-dashed border-muted-foreground/25">
          Booking link unavailable
        </div>
      )}

      {/* In-app booking button — only shown when parent wires up the handler */}
      {onBook && (
        <button
          onClick={onBook}
          disabled={isBookingActive}
          className={cn(
            'inline-flex items-center justify-center gap-1.5 rounded-lg px-4 py-2.5',
            'text-sm font-medium transition-colors duration-150',
            'border-2',
            label === 'best-value'
              ? 'border-teal-600 text-teal-700 hover:bg-teal-50 disabled:opacity-50 disabled:cursor-not-allowed'
              : 'border-blue-600 text-blue-700 hover:bg-blue-50 disabled:opacity-50 disabled:cursor-not-allowed',
          )}
        >
          {isBookingActive ? 'Booking in progress…' : 'Book this flight'}
        </button>
      )}
    </div>
  );
}
