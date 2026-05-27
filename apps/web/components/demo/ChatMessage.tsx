'use client';

import { cn } from '@/lib/utils';
import type { ChatMessage } from '@/lib/chat-types';

interface ChatBubbleProps {
  message: ChatMessage;
}

export default function ChatBubble({ message }: ChatBubbleProps) {
  const { kind, text, isResolved } = message;

  if (kind === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-2xl rounded-tr-sm bg-foreground text-background text-sm px-3.5 py-2 leading-relaxed">
          {text}
        </div>
      </div>
    );
  }

  if (kind === 'agent_thinking') {
    return (
      <div className="flex justify-start">
        <div
          className={cn(
            'rounded-2xl rounded-tl-sm px-3.5 py-2',
            'bg-muted/20 border border-border/50 text-sm text-muted-foreground tracking-widest',
            isResolved ? 'opacity-40' : 'animate-pulse',
          )}
        >
          ···
        </div>
      </div>
    );
  }

  if (kind === 'agent_action') {
    return (
      <div className="flex justify-start">
        <div className="max-w-[80%] rounded-2xl rounded-tl-sm bg-teal-50/60 border border-teal-200/80 text-sm text-foreground/80 px-3.5 py-2 leading-relaxed">
          {text}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-2xl rounded-tl-sm bg-muted/20 border border-border/50 text-sm text-muted-foreground px-3.5 py-2 leading-relaxed">
        {text}
      </div>
    </div>
  );
}
