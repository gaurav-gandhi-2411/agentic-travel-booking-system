'use client';

import { useState, useEffect } from 'react';
import { cn } from '@/lib/utils';

export type LLMProfile =
  | 'demo-llama'
  | 'demo-gpt-oss-120b'
  | 'demo-deepseek-v4'
  | 'demo-haiku';

const STORAGE_KEY = 'preferred_llm_profile';
const DEFAULT_PROFILE: LLMProfile = 'demo-gpt-oss-120b';

export function useProfilePreference(): [LLMProfile, (p: LLMProfile) => void] {
  const [profile, setProfile] = useState<LLMProfile>(DEFAULT_PROFILE);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (
        stored === 'demo-llama' ||
        stored === 'demo-gpt-oss-120b' ||
        stored === 'demo-deepseek-v4' ||
        stored === 'demo-haiku'
      ) {
        setProfile(stored as LLMProfile);
      } else {
        // Stale (demo-qwen from before May 16, or any unknown value) —
        // fall through to DEFAULT_PROFILE, no setProfile call needed.
        if (stored) localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // localStorage unavailable (SSR or private browsing)
    }
  }, []);

  const setAndStore = (p: LLMProfile) => {
    setProfile(p);
    try {
      localStorage.setItem(STORAGE_KEY, p);
    } catch {
      // ignore write errors
    }
  };

  return [profile, setAndStore];
}

const OPTIONS: Array<{
  value: LLMProfile;
  label: string;
  sublabel: string;
  hint: string;
}> = [
  {
    value: 'demo-llama',
    label: 'Llama',
    sublabel: 'Meta open-weight (Groq)',
    hint: 'Free',
  },
  {
    value: 'demo-gpt-oss-120b',
    label: 'GPT-OSS',
    sublabel: 'OpenAI open-weight (Groq)',
    hint: 'Free',
  },
  {
    value: 'demo-deepseek-v4',
    label: 'DeepSeek',
    sublabel: 'DeepSeek V4 Flash (NIM)',
    hint: 'Free',
  },
  {
    value: 'demo-haiku',
    label: 'Haiku',
    sublabel: 'Anthropic Haiku',
    hint: '≈ $0.005/query',
  },
];

interface ProfileToggleProps {
  value: LLMProfile;
  onChange: (profile: LLMProfile) => void;
  disabled?: boolean;
}

export default function ProfileToggle({ value, onChange, disabled = false }: ProfileToggleProps) {
  const active = OPTIONS.find(o => o.value === value) ?? OPTIONS[0];

  return (
    <div className="flex flex-col items-end gap-1">
      <div
        className={cn(
          'inline-flex rounded-lg border border-border/60 bg-muted/30 p-0.5 gap-0.5',
          disabled && 'opacity-50 pointer-events-none',
        )}
        role="group"
        aria-label="LLM provider"
      >
        {OPTIONS.map(opt => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            disabled={disabled}
            aria-pressed={value === opt.value}
            className={cn(
              'rounded-md px-3 py-1.5 text-xs font-medium transition-all duration-150',
              'focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
              value === opt.value
                ? 'bg-background shadow-sm text-foreground'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <p className="text-[10px] text-muted-foreground/60 tabular-nums">
        {active.sublabel} · {active.hint}
      </p>
    </div>
  );
}
