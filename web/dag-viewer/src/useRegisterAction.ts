import { useEffect } from "react";

export type RegisteredAction = {
  qid: string;
  action: string;
  label: string;
  description: string;
};

declare global {
  interface Window {
    __tauRegisteredActions?: Map<string, RegisteredAction>;
  }
}

export function useRegisterAction(qid: string, metadata: Omit<RegisteredAction, "qid">): void {
  useEffect(() => {
    const action: RegisteredAction = { qid, ...metadata };
    const registry = window.__tauRegisteredActions ?? new Map<string, RegisteredAction>();
    window.__tauRegisteredActions = registry;
    registry.set(qid, action);
    window.dispatchEvent(new CustomEvent("tau:action-registered", { detail: action }));
    return () => {
      if (window.__tauRegisteredActions === registry) registry.delete(qid);
    };
  }, [metadata.action, metadata.description, metadata.label, qid]);
}
