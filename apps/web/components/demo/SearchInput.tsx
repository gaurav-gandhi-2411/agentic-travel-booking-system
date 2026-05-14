'use client';

import { useState, useRef, useEffect, useCallback } from 'react';
import { Search } from 'lucide-react';
import { cn } from '@/lib/utils';

const EXAMPLES = [
  'Delhi to Dubai in June',
  'Delhi to Singapore in June',
  'Mumbai to Bangkok for 5 days in June',
] as const;

interface SearchInputProps {
  onSearch: (query: string) => void;
  disabled?: boolean;
}

export default function SearchInput({ onSearch, disabled = false }: SearchInputProps) {
  const [value, setValue] = useState('');
  const [placeholderIdx, setPlaceholderIdx] = useState(0);
  const [validationError, setValidationError] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (disabled) return;
    const id = setInterval(() => {
      setPlaceholderIdx(i => (i + 1) % EXAMPLES.length);
    }, 3500);
    return () => clearInterval(id);
  }, [disabled]);

  const handleSubmit = useCallback(() => {
    if (!value.trim()) {
      setValidationError(true);
      textareaRef.current?.focus();
      return;
    }
    setValidationError(false);
    onSearch(value.trim());
  }, [value, onSearch]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        handleSubmit();
      }
    },
    [handleSubmit],
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2 items-end">
        <div className="flex-1">
          <textarea
            ref={textareaRef}
            value={value}
            onChange={e => {
              setValue(e.target.value);
              if (validationError && e.target.value.trim()) setValidationError(false);
            }}
            onKeyDown={handleKeyDown}
            disabled={disabled}
            placeholder={EXAMPLES[placeholderIdx]}
            rows={3}
            className={cn(
              'w-full resize-none rounded-lg border bg-white px-4 py-3 text-sm leading-relaxed',
              'text-foreground placeholder:text-muted-foreground/50',
              'focus:outline-none focus:ring-2 focus:ring-teal-500/50 focus:border-teal-400',
              'disabled:opacity-50 disabled:cursor-not-allowed',
              'transition-colors duration-150',
              validationError
                ? 'border-destructive focus:ring-destructive/30 focus:border-destructive'
                : 'border-input',
            )}
          />
          {validationError && (
            <p className="text-xs text-destructive mt-1.5">Please describe your trip to search.</p>
          )}
        </div>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={disabled}
          className={cn(
            'flex items-center gap-1.5 rounded-lg px-5 h-12 text-sm font-medium text-white shrink-0',
            'bg-teal-600 hover:bg-teal-700 active:bg-teal-800',
            'focus:outline-none focus:ring-2 focus:ring-teal-500 focus:ring-offset-2',
            'disabled:opacity-50 disabled:cursor-not-allowed',
            'transition-colors duration-150',
          )}
        >
          <Search className="h-4 w-4" />
          Search
        </button>
      </div>

      {!disabled && (
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map(ex => (
            <button
              key={ex}
              type="button"
              onClick={() => {
                setValue(ex);
                if (validationError) setValidationError(false);
              }}
              className="text-xs text-muted-foreground border border-border/60 rounded-full px-3 py-1 hover:bg-muted/60 hover:text-foreground transition-colors duration-100"
            >
              {ex.length > 46 ? `${ex.slice(0, 46)}…` : ex}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
