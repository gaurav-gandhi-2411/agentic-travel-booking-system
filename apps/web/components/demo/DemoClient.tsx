'use client';

import { useEffect, useCallback } from 'react';
import { useSearchStream } from '@/hooks/useSearchStream';
import SearchInput from '@/components/demo/SearchInput';
import AgentProgressFeed from '@/components/demo/AgentProgressFeed';
import ArchetypeCard from '@/components/demo/ArchetypeCard';
import ErrorBanner from '@/components/demo/ErrorBanner';

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
  const { start, events, archetypes, status, error, reset, lastQuery } = useSearchStream();

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

  const handleRetry = useCallback(() => {
    reset();
    if (lastQuery) start(lastQuery);
  }, [reset, start, lastQuery]);

  const optimizerStarted = events.some(e => e.type === 'optimizer_started');
  const showSkeletons = status === 'streaming' && optimizerStarted && archetypes.length === 0;
  const showResults = archetypes.length > 0 || showSkeletons;

  return (
    <div className="max-w-3xl mx-auto px-6 py-12 flex flex-col gap-8">
      {/* Page header */}
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold tracking-tight text-foreground">DealHunter</h1>
        <p className="text-sm text-muted-foreground">
          Tell me where you want to go.{' '}
          <kbd className="inline-flex items-center rounded border border-border/60 bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground/70">
            ⌘K
          </kbd>
        </p>
      </div>

      {/* Search input */}
      <SearchInput onSearch={start} disabled={status === 'streaming'} />

      {/* Error banner */}
      {status === 'error' && error && (
        <ErrorBanner message={error} onRetry={handleRetry} />
      )}

      {/* Agent progress feed — stays visible once shown */}
      {status !== 'idle' && (
        <AgentProgressFeed events={events} status={status} />
      )}

      {/* Results section */}
      {showResults && (
        <div className="flex flex-col gap-3">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-widest">
            {archetypes.length > 0 ? 'Your options' : 'Preparing recommendations…'}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {archetypes.length > 0
              ? archetypes.map(a => <ArchetypeCard key={a.label} archetype={a} />)
              : [0, 1].map(i => <ArchetypeSkeleton key={i} />)}
          </div>
        </div>
      )}
    </div>
  );
}
