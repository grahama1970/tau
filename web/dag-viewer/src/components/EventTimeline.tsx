import { Activity, ShieldAlert } from "lucide-react";
import type { JournalEvent } from "../types";

export function EventTimeline({ events, onSelect }: { events: JournalEvent[]; onSelect: (id: string) => void }) {
  return <section className="event-timeline" aria-label="Event timeline" data-qid="dag:timeline:events">
    <header><Activity aria-hidden="true" size={16} /><strong>Journal timeline</strong><span>{events.length} recent events</span></header>
    <div className="event-timeline__scroll">
      {events.map((event) => <button
        key={`${event.seq}-${event.event_type}`}
        type="button"
        className="event-row"
        data-qid={`dag:event:${event.seq}`}
        data-qs-action="DAG_SELECT_EVENT"
        title={`Inspect journal event ${event.seq}`}
        onClick={() => onSelect(event.entity_id)}
      >
        <span className="event-row__seq">#{event.seq}</span>
        <span className="event-row__kind">{event.event_type}</span>
        <span>{event.entity_id}</span>
        {event.event_type.includes("diagnostic") && <span className="event-row__diagnostic"><ShieldAlert size={12} />diagnostic only</span>}
      </button>)}
    </div>
  </section>;
}
