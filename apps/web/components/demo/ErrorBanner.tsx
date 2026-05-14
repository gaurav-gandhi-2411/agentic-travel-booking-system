import { AlertCircle, RotateCcw } from 'lucide-react';

interface ErrorBannerProps {
  message: string;
  onRetry: () => void;
}

export default function ErrorBanner({ message, onRetry }: ErrorBannerProps) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3">
      <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-destructive">Search failed</p>
        <p className="text-xs text-muted-foreground mt-0.5 break-words">{message}</p>
      </div>
      <button
        type="button"
        onClick={onRetry}
        className="shrink-0 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        <RotateCcw className="h-3.5 w-3.5" />
        Retry
      </button>
    </div>
  );
}
