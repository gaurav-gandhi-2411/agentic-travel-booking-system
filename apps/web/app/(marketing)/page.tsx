import { CalendarDays, BarChart3, MessageSquare } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import WaitlistForm from "@/components/waitlist-form";

const GITHUB_URL =
  "https://github.com/gaurav-gandhi-2411/agentic-travel-booking-system";

const features = [
  {
    Icon: CalendarDays,
    title: "7-day window optimization",
    description:
      "Identifies the best 7-day travel window across a 30-day horizon using hierarchical sampling. Stage 1 coarse-sweeps candidate windows with lightweight provider calls; Stage 2 drills into the top-ranked. The Pareto frontier extracts two archetype winners with natural-language explanations. Not cheapest for a fixed date — best window across the month.",
    status:
      "Algorithm documented in ADR-0005. Implementation due in Phase 2.",
  },
  {
    Icon: BarChart3,
    title: "Two-archetype ranking",
    description:
      "Every result surfaces two packages from the Pareto frontier: best-value and best-experience. Scoring accounts for flight quality, hotel rating, window desirability, and refundability — not just price. The two archetypes are guaranteed to represent a genuine tradeoff, not two slices of the same ranking.",
    status:
      "Scoring design in ADR-0006. Optimizer agent due in Phase 3.",
  },
  {
    Icon: MessageSquare,
    title: "Refinement at the right re-entry point",
    description:
      "\"Cheaper\" triggers a window re-search. \"Skip red-eyes\" adds a departure-time filter and re-runs FlightHunter forward. \"Different hotel area\" re-ranks hotels against the existing flight set. The coordinator re-enters at the correct stage — no wasted provider calls, no context loss across turns.",
    status:
      "Coordinator pattern in ADR-0001. ConversationManager agent due in Phase 6.",
  },
] as const;

export default function HomePage() {
  return (
    <div className="flex flex-col">
      {/* Hero */}
      <section className="border-b border-border/40 bg-muted/30">
        <div className="mx-auto max-w-5xl px-6 py-24 flex flex-col items-center text-center gap-6">
          <Badge variant="outline">Building in public</Badge>
          <h1 className="text-4xl sm:text-5xl font-bold tracking-tight max-w-3xl leading-tight">
            The reasoning layer for travel platforms
          </h1>
          <p className="text-lg text-muted-foreground max-w-2xl leading-relaxed">
            A multi-agent system that turns a natural-language travel query into a ranked,
            explained recommendation — and refines it conversationally across multiple
            turns. Plug in your inventory via the adapter pattern; we bring the agent.
          </p>
          <div className="flex flex-col sm:flex-row items-center gap-3 mt-2">
            <Button asChild size="lg">
              <a href="#waitlist">Join the waitlist</a>
            </Button>
            <Button asChild size="lg" variant="outline">
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
              >
                View on GitHub
              </a>
            </Button>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section>
        <div className="mx-auto max-w-5xl px-6 py-20">
          <div className="mb-10 flex flex-col gap-1.5">
            <h2 className="text-2xl font-semibold tracking-tight">How it works</h2>
            <p className="text-muted-foreground">
              Three capabilities the adapter pattern exposes to your platform.
            </p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {features.map(({ Icon, title, description, status }) => (
              <Card key={title} className="flex flex-col">
                <CardHeader className="pb-2">
                  <Icon className="h-5 w-5 text-muted-foreground mb-1.5" />
                  <CardTitle className="text-base leading-snug">{title}</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col flex-1 gap-4">
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {description}
                  </p>
                  <p className="text-xs text-muted-foreground/60 mt-auto pt-3 border-t border-border/50">
                    {status}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      <Separator />

      {/* Open-source models + Eval */}
      <section>
        <div className="mx-auto max-w-5xl px-6 py-20 grid grid-cols-1 md:grid-cols-2 gap-12">
          <div className="flex flex-col gap-4">
            <h2 className="text-xl font-semibold tracking-tight">
              Fine-tuned on open-source models
            </h2>
            <p className="text-sm text-muted-foreground leading-relaxed">
              The agent targets fine-tuned Qwen 2.5 7B and 14B models, benchmarked against
              Qwen 2.5 72B and Llama 3.3 70B. Per-agent acceptance thresholds are defined
              before training begins (ADR-0009). Results will publish as win/loss numbers,
              not marketing claims. Agents that don&apos;t reach threshold ship on the 70B
              fallback — documented, not hidden.
            </p>
            <p className="text-sm text-muted-foreground/70 leading-relaxed">
              Fine-tuning begins in Phase 6.6, after the eval harness and baseline
              benchmarks are in place. LoRA adapters and a 20% eval sample will publish
              to Hugging Face under open licenses (CC-BY-NC-4.0 and CC-BY-4.0).
            </p>
            <a
              href="https://huggingface.co/gaurav-gandhi-2411"
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium hover:underline underline-offset-4 w-fit"
            >
              Hugging Face profile — adapters publishing in Phase 11.5 →
            </a>
          </div>
          <div className="flex flex-col gap-4">
            <h2 className="text-xl font-semibold tracking-tight">
              Reproducible eval methodology
            </h2>
            <p className="text-sm text-muted-foreground leading-relaxed">
              The eval harness (ADR-0010) runs against golden datasets that are never used
              for training. Two CI modes: a 20-example quick check on every PR, and a
              full nightly run on main. Regression threshold is a 2% drop on any metric.
            </p>
            <p className="text-sm text-muted-foreground/70 leading-relaxed">
              Baseline benchmarks due in Phase 3.5. When adapters publish,{" "}
              <code className="text-xs bg-muted px-1 py-0.5 rounded font-mono">
                make eval
              </code>{" "}
              against the published HF checkpoints and published eval sample should
              reproduce reported numbers within ±2%.
            </p>
          </div>
        </div>
      </section>

      <Separator />

      {/* Built for travel platforms + Production posture */}
      <section>
        <div className="mx-auto max-w-5xl px-6 py-20 grid grid-cols-1 md:grid-cols-2 gap-12">
          <div className="flex flex-col gap-4">
            <h2 className="text-xl font-semibold tracking-tight">
              Built for travel platforms
            </h2>
            <p className="text-sm text-muted-foreground leading-relaxed">
              Platforms like Skyscanner and MakeMyTrip already have inventory. What they
              don&apos;t have is a reasoning agent that does multi-window arbitrage,
              two-archetype scoring with natural-language explanations, and conversational
              refinement that survives multiple turns.
            </p>
            <p className="text-sm text-muted-foreground leading-relaxed">
              The adapter pattern (ADR-0002) means you plug in your existing flight and
              hotel inventory. The agent handles window search, scoring, ranking,
              explanation, and refinement routing. Adding a new inventory provider is a
              new class with no orchestration changes.
            </p>
          </div>
          <div className="flex flex-col gap-4">
            <h2 className="text-xl font-semibold tracking-tight">
              Production posture from commit one
            </h2>
            <p className="text-sm text-muted-foreground leading-relaxed">
              The codebase reaches 40+ engineering-discipline commits before the first
              agent business logic lands: multi-tenant Postgres RLS (ADR-0004),
              AES-256-GCM credential encryption (ADR-0007), OpenTelemetry tracing,
              WIF-based CI/CD, load-tested to 50 concurrent users. Every load-bearing
              decision has a written ADR with rationale, alternatives considered, and
              consequences.
            </p>
            <a
              href={`${GITHUB_URL}/tree/main/docs/architecture/adr`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium hover:underline underline-offset-4 w-fit"
            >
              Read ADRs 0001–0014 on GitHub →
            </a>
          </div>
        </div>
      </section>

      <Separator />

      {/* Waitlist */}
      <section id="waitlist">
        <div className="mx-auto max-w-5xl px-6 py-20">
          <div className="max-w-sm flex flex-col gap-6">
            <div className="flex flex-col gap-1.5">
              <h2 className="text-2xl font-semibold tracking-tight">
                Join the waitlist
              </h2>
              <p className="text-sm text-muted-foreground">
                Early access for platform engineering teams. One email when sandbox API
                keys are available.
              </p>
            </div>
            <WaitlistForm />
          </div>
        </div>
      </section>
    </div>
  );
}
