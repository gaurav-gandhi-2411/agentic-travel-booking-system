'use client';

import { useRef, useEffect } from 'react';
import { Brain, Plane, Sparkles, Check, Loader2 } from 'lucide-react';
import { buildProgressRows, type SseEvent, type IconName } from '@/lib/event-map';
import type { SearchStatus } from '@/hooks/useSearchStream';
import { cn } from '@/lib/utils';

const ICON_MAP: Record<IconName, React.ElementType> = {
  Brain,
  Plane,
  Sparkles,
};

interface AgentProgressFeedProps {
  events: SseEvent[];
  status: SearchStatus;
}

export default function AgentProgressFeed({ events, status }: AgentProgressFeedProps) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const rows = buildProgressRows(events);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [events.length]);

  if (rows.length === 0) return null;

  return (
    <div className="rounded-lg border border-border/50 bg-muted/20 px-4 py-3 flex flex-col gap-0.5">
      <p className="text-[11px] font-semibold text-muted-foreground uppercase tracking-widest mb-2">
        Agent progress
      </p>
      {rows.map(row => {
        const Icon = ICON_MAP[row.iconName];
        const showSpinner = !row.isDone && status === 'streaming';

        return (
          <div
            key={row.id}
            className={cn(
              'flex items-start gap-3 py-2 transition-opacity duration-300',
              row.isDone ? 'opacity-60' : 'opacity-100',
            )}
          >
            <Icon className="h-4 w-4 text-teal-600 mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-foreground leading-tight">{row.title}</p>
              {row.subtitle && (
                <p className="text-xs text-muted-foreground mt-0.5">{row.subtitle}</p>
              )}
            </div>
            <div className="mt-0.5 shrink-0 w-4">
              {row.isDone ? (
                <Check className="h-4 w-4 text-teal-600" />
              ) : showSpinner ? (
                <Loader2 className="h-4 w-4 text-muted-foreground animate-spin" />
              ) : null}
            </div>
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
