import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Modal, Popconfirm, type ModalProps, type PopconfirmProps } from "antd";

import {
  type AuthSessionIdentity,
  captureAuthSessionIdentity,
  getAuthSessionGeneration,
  isCurrentAuthSessionIdentity,
  subscribeAuthSessionGeneration,
} from "../sessionIdentity";

export interface SessionIntent {
  identity: AuthSessionIdentity | null;
  isCurrent: boolean;
  run: <T>(effect: () => T) => T | undefined;
}

/**
 * Capture the authenticated identity on the rising edge of a multi-step intent.
 * The same captured identity is retained until the intent closes, including across ABA switches.
 */
export function useSessionIntent(active: boolean): SessionIntent {
  const generation = useSyncExternalStore(
    subscribeAuthSessionGeneration,
    getAuthSessionGeneration,
    getAuthSessionGeneration,
  );
  const intentRef = useRef<{ active: boolean; identity: AuthSessionIdentity | null }>({
    active: false,
    identity: null,
  });

  if (active && !intentRef.current.active) {
    intentRef.current = {
      active: true,
      identity: captureAuthSessionIdentity(),
    };
  } else if (!active && intentRef.current.active) {
    intentRef.current = { active: false, identity: null };
  }

  const identity = intentRef.current.identity;
  const isCurrent = Boolean(active && identity && isCurrentAuthSessionIdentity(identity));
  const run = useCallback(
    <T,>(effect: () => T): T | undefined => {
      if (!identity || !isCurrentAuthSessionIdentity(identity)) {
        return undefined;
      }
      return effect();
    },
    [identity],
  );

  // generation is intentionally read even though identity equality decides validity: the external
  // store subscription makes a still-open portal re-render and disappear immediately on a switch.
  void generation;
  return { identity, isCurrent, run };
}

interface SessionInvalidationProps {
  onSessionInvalidated?: () => void;
}

export type SessionBoundModalProps = ModalProps & SessionInvalidationProps;

export function SessionBoundModal({
  open = false,
  onOk,
  onSessionInvalidated,
  ...props
}: SessionBoundModalProps) {
  const intent = useSessionIntent(open);
  const invalidationNotifiedRef = useRef(false);

  useEffect(() => {
    if (!open || intent.isCurrent) {
      invalidationNotifiedRef.current = false;
      return;
    }
    if (!invalidationNotifiedRef.current) {
      invalidationNotifiedRef.current = true;
      onSessionInvalidated?.();
    }
  }, [intent.isCurrent, onSessionInvalidated, open]);

  return (
    <Modal
      {...props}
      open={open && intent.isCurrent}
      onOk={(event) => {
        intent.run(() => onOk?.(event));
      }}
    />
  );
}

export type SessionBoundPopconfirmProps = PopconfirmProps & SessionInvalidationProps;

export function SessionBoundPopconfirm({
  open: controlledOpen,
  defaultOpen = false,
  onOpenChange,
  onConfirm,
  onSessionInvalidated,
  ...props
}: SessionBoundPopconfirmProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const isControlled = controlledOpen !== undefined;
  const requestedOpen = isControlled ? controlledOpen : uncontrolledOpen;
  const intent = useSessionIntent(requestedOpen);
  const invalidationNotifiedRef = useRef(false);
  const mountIdentity = useRef(captureAuthSessionIdentity()).current;

  useEffect(() => {
    if (!requestedOpen || intent.isCurrent) {
      invalidationNotifiedRef.current = false;
      return;
    }
    if (invalidationNotifiedRef.current) {
      return;
    }
    invalidationNotifiedRef.current = true;
    if (!isControlled) {
      setUncontrolledOpen(false);
    }
    onOpenChange?.(false);
    onSessionInvalidated?.();
  }, [intent.isCurrent, isControlled, onOpenChange, onSessionInvalidated, requestedOpen]);

  return (
    <Popconfirm
      {...props}
      open={requestedOpen && intent.isCurrent}
      onOpenChange={(nextOpen) => {
        if (!isControlled) {
          setUncontrolledOpen(nextOpen);
        }
        onOpenChange?.(nextOpen);
      }}
      onConfirm={(event) => {
        const confirmIdentity = intent.identity ?? mountIdentity;
        if (!isCurrentAuthSessionIdentity(confirmIdentity)) {
          return undefined;
        }
        return onConfirm?.(event);
      }}
    />
  );
}
