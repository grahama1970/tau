import type { JsonValue } from "../types";

export function JsonInspector({ value, label }: { value: JsonValue; label: string }) {
  return <pre className="json-inspector" aria-label={label}>{JSON.stringify(value, null, 2)}</pre>;
}
