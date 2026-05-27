/**
 * SSE event types and progress row builder.
 * Pure utilities — no React imports.
 */

export type IconName = 'Brain' | 'Plane' | 'Sparkles';

export interface ProgressRow {
  id: string;
  iconName: IconName;
  title: string;
  subtitle: string | null;
  isDone: boolean;
}

export interface TravelIntent {
  origin_iata: string;
  destination_iata: string;
  trip_duration_days: number;
}

export interface FlightOption {
  id: string;
  origin_iata: string;
  destination_iata: string;
  outbound_departure_at: string;
  outbound_arrival_at: string;
  airline_code: string;
  flight_number: string;
  cabin_class: string;
  price_inr: number;
  outbound_duration_minutes: number;
  layover_count: number;
  return_departure_at?: string | null;
  return_arrival_at?: string | null;
}

export interface Archetype {
  label: 'best-value' | 'best-experience';
  flight: FlightOption;
  explanation: string;
  comparison_to_alternative: string;
  deeplink_url: string;
  score_breakdown: {
    value_score: number;
    experience_score: number;
  };
}

export interface RouteAlternative {
  origin_iata: string;
  destination_iata: string;
  label: string;
}

export interface SseEvent {
  type: string;
  intent?: TravelIntent;
  windows?: Array<{ start: string; end: string }>;
  window_idx?: number;
  flights_found?: number;
  total_options?: number;
  archetype?: Archetype;
  message?: string;
  request_id?: string;
  refinement?: string;
  change_type?: string;
  // no_data_for_route fields
  origin_iata?: string;
  destination_iata?: string;
  alternatives?: RouteAlternative[];
  // conversation_action_classified
  args_summary?: string;
  // conversation_message
  text?: string;
}

/**
 * Collapse raw SSE events into consolidated display rows.
 * Called on every render — pure function, no side effects.
 */
export function buildProgressRows(events: SseEvent[]): ProgressRow[] {
  const rows: ProgressRow[] = [];
  let windowCount = 0;
  let windowsSearched = 0;
  let totalFlightsFound = 0;

  const find = (id: string): ProgressRow | undefined => rows.find(r => r.id === id);

  for (const event of events) {
    switch (event.type) {
      case 'planner_started': {
        if (!find('planner')) {
          rows.push({ id: 'planner', iconName: 'Brain', title: 'Understanding your trip', subtitle: null, isDone: false });
        }
        break;
      }
      case 'planner_done': {
        const row = find('planner');
        if (row) {
          row.isDone = true;
          row.title = 'Trip understood';
          if (event.intent) {
            row.subtitle = `${event.intent.origin_iata} → ${event.intent.destination_iata} · ${event.intent.trip_duration_days} days`;
          }
        }
        break;
      }
      case 'search_started': {
        windowCount = event.windows?.length ?? 0;
        if (!find('search')) {
          rows.push({ id: 'search', iconName: 'Plane', title: `Searching ${windowCount} date windows`, subtitle: null, isDone: false });
        }
        break;
      }
      case 'search_progress': {
        const row = find('search');
        if (row) {
          windowsSearched = (event.window_idx ?? windowsSearched - 1) + 1;
          totalFlightsFound += event.flights_found ?? 0;
          row.subtitle = `Window ${windowsSearched} of ${windowCount} — ${totalFlightsFound} flights found`;
        }
        break;
      }
      case 'search_done': {
        const row = find('search');
        if (row) {
          row.isDone = true;
          row.title = `${event.total_options ?? totalFlightsFound} flights found`;
          row.subtitle = null;
        }
        break;
      }
      case 'optimizer_started': {
        if (!find('optimizer')) {
          rows.push({ id: 'optimizer', iconName: 'Sparkles', title: 'Ranking options', subtitle: 'Finding the Pareto-optimal set', isDone: false });
        }
        break;
      }
      case 'done': {
        const row = find('optimizer');
        if (row) {
          row.isDone = true;
          row.title = 'Results ready';
          row.subtitle = null;
        }
        break;
      }
      case 'no_data_for_route': {
        const row = find('search');
        if (row) {
          row.isDone = true;
          row.title = 'No flights found';
          row.subtitle = null;
        }
        break;
      }
    }
  }

  return rows;
}
