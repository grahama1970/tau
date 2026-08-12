import { Pause, Play, Radio, Target } from "lucide-react";
import { useRegisterAction } from "../useRegisterAction";

export type LiveSyncStatus = "CONNECTED" | "RECONNECTING" | "OFFLINE";

type Props = {
  status: LiveSyncStatus;
  paused: boolean;
  autoFollow: boolean;
  historical: boolean;
  sequence: number;
  onTogglePaused: () => void;
  onToggleAutoFollow: () => void;
};

export function LiveSyncControls({
  status,
  paused,
  autoFollow,
  historical,
  sequence,
  onTogglePaused,
  onToggleAutoFollow,
}: Props) {
  useRegisterAction("dag:stream:toggle-ingestion", {
    action: "DAG_TOGGLE_LIVE_INGESTION",
    label: "Toggle Live Ingestion",
    description: "Pause or resume live DAG projection ingestion.",
  });
  useRegisterAction("dag:stream:toggle-follow", {
    action: "DAG_TOGGLE_LIVE_FOLLOW",
    label: "Toggle Follow Latest",
    description: "Follow the latest live journal sequence or stay on the selected prefix.",
  });

  const visibleStatus = paused ? "PAUSED" : status;
  const statusLabel = visibleStatus === "CONNECTED" ? "LIVE SYNC" : visibleStatus;

  return <section className="live-sync" aria-label="Live DAG synchronization" data-qid="dag:stream:controls">
    <span
      className={`live-sync__badge live-sync__badge--${visibleStatus.toLowerCase()}`}
      data-qid="dag:stream:status"
      data-stream-status={visibleStatus}
      title={`Live sync status: ${visibleStatus}`}
    >
      <i aria-hidden="true" />
      <strong>{statusLabel}</strong>
      <code>#{sequence}</code>
    </span>
    <button
      type="button"
      className={paused ? "" : "active"}
      data-qid="dag:stream:toggle-ingestion"
      data-qs-action="DAG_TOGGLE_LIVE_INGESTION"
      title={paused ? "Resume live projection ingestion" : "Pause live projection ingestion"}
      aria-pressed={!paused}
      onClick={onTogglePaused}
    >
      {paused ? <Play aria-hidden="true" size={14} /> : <Pause aria-hidden="true" size={14} />}
      {paused ? "Resume" : "Pause"}
    </button>
    <button
      type="button"
      className={autoFollow && !historical ? "active" : ""}
      data-qid="dag:stream:toggle-follow"
      data-qs-action="DAG_TOGGLE_LIVE_FOLLOW"
      title={autoFollow && !historical ? "Stop following latest live sequence" : "Follow latest live sequence"}
      aria-pressed={autoFollow && !historical}
      onClick={onToggleAutoFollow}
    >
      {autoFollow && !historical ? <Radio aria-hidden="true" size={14} /> : <Target aria-hidden="true" size={14} />}
      Follow
    </button>
  </section>;
}
