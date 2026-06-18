'use client';

import { useEffect, useCallback, useState, useRef } from 'react';
import { useSearchStream, type NoDataState } from '@/hooks/useSearchStream';
import SearchInput from '@/components/demo/SearchInput';
import AgentProgressFeed from '@/components/demo/AgentProgressFeed';
import ArchetypeCard from '@/components/demo/ArchetypeCard';
import BookingPanel from '@/components/demo/BookingPanel';
import ErrorBanner from '@/components/demo/ErrorBanner';
import ProfileToggle, { useProfilePreference, type LLMProfile } from '@/components/demo/ProfileToggle';
import ChatLog from '@/components/demo/ChatLog';
import { useBookingStream } from '@/hooks/useBookingStream';
import type { ChatMessage } from '@/lib/chat-types';
import type { Archetype } from '@/lib/event-map';
import { cn } from '@/lib/utils';

const REFINE_CHIPS = [
  { label: 'Make it cheaper', value: 'cheaper' },
  { label: 'Skip red-eyes', value: 'skip_red_eyes' },
  { label: 'Non-stop only', value: 'non_stop' },
] as const;

function NoDataBanner({
  noData,
  onAlternativeClick,
}: {
  noData: NoDataState;
  onAlternativeClick: (query: string) => void;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border/50 bg-muted/20 px-4 py-4">
      <p className="text-sm font-medium text-foreground">
        No flights found for {noData.origin_iata} → {noData.destination_iata}
      </p>
      <p className="text-xs text-muted-foreground">
        We don&apos;t have data for this route. Try one of these instead:
      </p>
      <div className="flex flex-wrap gap-2">
        {noData.alternatives.map(alt => (
          <button
            key={alt.destination_iata}
            onClick={() => onAlternativeClick(
              `${alt.origin_iata} to ${alt.destination_iata} in June`
            )}
            className={cn(
              'inline-flex items-center rounded-full border px-3.5 py-1.5 text-xs font-medium',
              'bg-background hover:bg-muted/60 border-border/60 text-foreground/80',
              'transition-colors duration-150',
            )}
          >
            {alt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function ArchetypeSkeleton() {
  return (
    <div className="rounded-xl border border-muted/80 bg-muted/10 p-5 flex flex-col gap-4 animate-pulse">
      <div className="flex items-center justify-between">
        <div className="h-5 w-24 bg-muted rounded-md" />
        <div className="h-6 w-20 bg-muted rounded-md" />
      </div>
      <div className="flex flex-col gap-2">
        <div className="h-4 w-36 bg-muted rounded" />
        <div className="h-3 w-52 bg-muted rounded" />
      </div>
      <div className="h-12 w-full bg-muted rounded border-t border-muted/50 pt-3" />
      <div className="h-10 w-full bg-muted rounded-lg" />
    </div>
  );
}

export default function DemoClient() {
  const { start, refine, events, archetypes, status, error, noData, reset, lastQuery, requestId } = useSearchStream();
  const [profile, setProfile] = useProfilePreference();
  const [activeProfile, setActiveProfile] = useState<LLMProfile>('demo-gpt-oss-120b');
  const [refineInput, setRefineInput] = useState('');
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const lastProcessedIndex = useRef(0);
  const booking = useBookingStream();
  const [selectedArchetype, setSelectedArchetype] = useState<Archetype | null>(null);

  // Cmd+K / Ctrl+K focuses the search textarea
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        const textarea = document.querySelector<HTMLTextAreaElement>('textarea');
        textarea?.focus();
        textarea?.select();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  useEffect(() => {
    if (events.length <= lastProcessedIndex.current) return;

    const newEvents = events.slice(lastProcessedIndex.current);

    for (const event of newEvents) {
      if (event.type === 'conversation_thinking') {
        setChatMessages(prev => [...prev, {
          id: crypto.randomUUID(),
          kind: 'agent_thinking',
          text: '···',
          timestamp: Date.now(),
          isResolved: false,
        }]);
      } else if (event.type === 'conversation_action_classified') {
        setChatMessages(prev => prev.map(m =>
          m.kind === 'agent_thinking' && !m.isResolved
            ? { ...m, isResolved: true }
            : m
        ));
        if (event.args_summary) {
          const summary = event.args_summary;
          setChatMessages(prev => [...prev, {
            id: crypto.randomUUID(),
            kind: 'agent_action',
            text: summary,
            timestamp: Date.now(),
          }]);
        }
      } else if (event.type === 'conversation_message') {
        setChatMessages(prev => [...prev, {
          id: crypto.randomUUID(),
          kind: 'agent_message',
          text: event.text ?? '',
          timestamp: Date.now(),
        }]);
      }
    }

    lastProcessedIndex.current = events.length;
  }, [events]);

  const isStreaming = status === 'streaming';

  const handleSearch = useCallback((query: string) => {
    booking.reset();
    setSelectedArchetype(null);
    setActiveProfile(profile);
    setChatMessages([]);
    lastProcessedIndex.current = 0;
    start(query, profile);
  }, [start, profile, booking]);

  const handleRetry = useCallback(() => {
    reset();
    setChatMessages([]);
    lastProcessedIndex.current = 0;
    if (lastQuery) {
      setActiveProfile(profile);
      start(lastQuery, profile);
    }
  }, [reset, start, lastQuery, profile]);

  const handleChip = useCallback((chipValue: string) => {
    if (!requestId || isStreaming) return;
    const label = REFINE_CHIPS.find(c => c.value === chipValue)?.label ?? chipValue;
    setChatMessages(prev => [...prev, {
      id: crypto.randomUUID(),
      kind: 'user',
      text: label,
      timestamp: Date.now(),
    }]);
    refine(chipValue, profile);
  }, [requestId, isStreaming, refine, profile]);

  const handleRefineSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    const text = refineInput.trim();
    if (!text || !requestId || isStreaming) return;
    setChatMessages(prev => [...prev, {
      id: crypto.randomUUID(),
      kind: 'user',
      text,
      timestamp: Date.now(),
    }]);
    refine(text, profile);
    setRefineInput('');
  }, [refineInput, requestId, isStreaming, refine, profile]);
  const handleBook = useCallback((archetype: Archetype) => {
    setSelectedArchetype(archetype);
    booking.book(archetype.flight.id, requestId ?? undefined);
  }, [booking, requestId]);

  const handleBookingClose = useCallback(() => {
    booking.reset();
    setSelectedArchetype(null);
  }, [booking]);

  const optimizerStarted = events.some(e => e.type === 'optimizer_started');
  const showSkeletons = isStreaming && optimizerStarted && archetypes.length === 0;
  const showResults = archetypes.length > 0 || showSkeletons;
  const showRefinement = status === 'done' && archetypes.length > 0 && !!requestId;

  return (
    <div className="max-w-3xl mx-auto px-6 py-12 flex flex-col gap-8">
      {/* Page header */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">DealHunter</h1>
          <p className="text-sm text-muted-foreground">
            Tell me where you want to go.{' '}
            <kbd className="inline-flex items-center rounded border border-border/60 bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground/70">
              ⌘K
            </kbd>
          </p>
        </div>
        <ProfileToggle
          value={profile}
          onChange={setProfile}
          disabled={isStreaming}
        />
      </div>

      {/* Search input */}
      <SearchInput onSearch={handleSearch} disabled={isStreaming} />

      {/* Pending profile hint — shown when toggle changed after results rendered */}
      {status === 'done' && profile !== activeProfile && (
        <p className="text-[11px] text-center text-muted-foreground/50 -mt-4">
          Profile changed — applies to your next search
        </p>
      )}

      {/* Error banner */}
      {status === 'error' && error && (
        <ErrorBanner message={error} onRetry={handleRetry} />
      )}

      {/* No-data banner with alternative route chips */}
      {noData && status === 'done' && (
        <NoDataBanner noData={noData} onAlternativeClick={handleSearch} />
      )}

      {/* Agent progress feed — stays visible once shown */}
      {status !== 'idle' && (
        <AgentProgressFeed events={events} status={status} />
      )}

      {/* Chat log — appears after first refinement, above archetype results */}
      <ChatLog messages={chatMessages} />

      {/* Results section */}
      {showResults && (
        <div className="flex flex-col gap-3">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">
            {archetypes.length > 0 ? 'Your options' : 'Preparing recommendations…'}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {archetypes.length > 0
              ? archetypes.map(a => (
                <ArchetypeCard
                  key={a.label}
                  archetype={a}
                  onBook={() => handleBook(a)}
                  isBookingActive={booking.status !== 'idle'}
                />
              ))
              : [0, 1].map(i => <ArchetypeSkeleton key={i} />)}
          </div>
        </div>
      )}

      {/* Booking panel — appears when a booking is in progress */}
      {selectedArchetype && booking.status !== 'idle' && (
        <BookingPanel
          booking={booking}
          archetype={selectedArchetype}
          onClose={handleBookingClose}
        />
      )}

      {/* Refinement section — appears after first results */}
      {showRefinement && (
        <div className="flex flex-col gap-3 pt-2 border-t border-border/40">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">
            Refine your search
          </p>

          {/* Quick-action chips */}
          <div className="flex flex-wrap gap-2">
            {REFINE_CHIPS.map(chip => (
              <button
                key={chip.value}
                onClick={() => handleChip(chip.value)}
                disabled={isStreaming}
                className={cn(
                  'inline-flex items-center rounded-full border px-3.5 py-1.5 text-xs font-medium',
                  'bg-background hover:bg-muted/60 border-border/60 text-foreground/80',
                  'transition-colors duration-150 disabled:opacity-40 disabled:cursor-not-allowed',
                )}
              >
                {chip.label}
              </button>
            ))}
          </div>

          {/* Free-text refinement */}
          <form onSubmit={handleRefineSubmit} className="flex gap-2">
            <input
              type="text"
              value={refineInput}
              onChange={e => setRefineInput(e.target.value)}
              placeholder="Or describe what you'd like to change…"
              disabled={isStreaming}
              className={cn(
                'flex-1 rounded-lg border border-border/60 bg-background px-3 py-2',
                'text-sm placeholder:text-muted-foreground/50',
                'focus:outline-none focus:ring-2 focus:ring-ring/30',
                'disabled:opacity-40 disabled:cursor-not-allowed',
              )}
            />
            <button
              type="submit"
              disabled={!refineInput.trim() || isStreaming}
              className={cn(
                'rounded-lg px-4 py-2 text-sm font-medium',
                'bg-foreground text-background hover:bg-foreground/90',
                'transition-colors duration-150 disabled:opacity-40 disabled:cursor-not-allowed',
              )}
            >
              Go
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
