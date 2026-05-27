'use client';

import { useRef, useEffect } from 'react';
import type { ChatMessage } from '@/lib/chat-types';
import ChatBubble from '@/components/demo/ChatMessage';

interface ChatLogProps {
  messages: ChatMessage[];
}

export default function ChatLog({ messages }: ChatLogProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages.length]);

  if (messages.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      {messages.map(msg => (
        <ChatBubble key={msg.id} message={msg} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
