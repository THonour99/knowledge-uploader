import {
  type CurrentUser,
  type Role,
  hasAssignedDepartment,
  useAuthStore,
} from "./store/auth.store";

export interface AuthSecurityContext {
  accessToken: string | null;
  userId: string | null;
  role: Role | null;
  departmentId: string | null;
  emailVerified: boolean;
  hasAssignedDepartment: boolean;
  departmentAssigned: boolean | null;
  departmentCode: string | null;
}

export interface AuthSecurityState {
  accessToken: string | null;
  user: CurrentUser | null;
}

export interface AuthSessionIdentity extends AuthSecurityContext {
  generation: number;
}
export interface AuthSessionAbortScope {
  signal: AbortSignal;
  dispose: () => void;
}

export interface AuthSessionCallbackContext {
  identity: AuthSessionIdentity;
  signal: AbortSignal;
  assertCurrent: () => void;
  waitFor: <T>(work: PromiseLike<T> | (() => PromiseLike<T>)) => Promise<T>;
  run: <T>(effect: () => T) => T;
  runIfCurrent: (effect: () => void) => boolean;
}

export const SESSION_SUPERSEDED_CODE = "SESSION_SUPERSEDED";

export class SessionSupersededError extends Error {
  readonly code = SESSION_SUPERSEDED_CODE;

  constructor() {
    super("请求所属登录会话已变更");
    this.name = "SessionSupersededError";
  }
}

export function isSessionSupersededError(error: unknown): error is SessionSupersededError {
  return (
    error instanceof SessionSupersededError ||
    (typeof error === "object" &&
      error !== null &&
      "code" in error &&
      error.code === SESSION_SUPERSEDED_CODE)
  );
}

export function authSecurityContextFromState(state: AuthSecurityState): AuthSecurityContext {
  return {
    accessToken: state.accessToken,
    userId: state.user?.id ?? null,
    role: state.user?.role ?? null,
    departmentId: state.user?.department_id ?? null,
    emailVerified: state.user?.email_verified === true,
    hasAssignedDepartment: hasAssignedDepartment(state.user),
    departmentAssigned: state.user?.department_assigned ?? null,
    departmentCode: state.user?.department_code ?? null,
  };
}

export function sameAuthSecurityContext(
  left: AuthSecurityContext,
  right: AuthSecurityContext,
): boolean {
  return (
    left.accessToken === right.accessToken &&
    left.userId === right.userId &&
    left.role === right.role &&
    left.departmentId === right.departmentId &&
    left.emailVerified === right.emailVerified &&
    left.hasAssignedDepartment === right.hasAssignedDepartment &&
    left.departmentAssigned === right.departmentAssigned &&
    left.departmentCode === right.departmentCode
  );
}

function readSecurityContext(): AuthSecurityContext {
  return authSecurityContextFromState(useAuthStore.getState());
}

let trackedSecurityContext = readSecurityContext();
let sessionGeneration = 0;
const sessionGenerationListeners = new Set<() => void>();

function synchronizeSecurityContext(nextContext = readSecurityContext()): void {
  if (sameAuthSecurityContext(trackedSecurityContext, nextContext)) {
    return;
  }
  trackedSecurityContext = nextContext;
  sessionGeneration += 1;
  for (const listener of sessionGenerationListeners) {
    listener();
  }
}

const removeSessionIdentitySubscription = useAuthStore.subscribe((state) => {
  synchronizeSecurityContext(authSecurityContextFromState(state));
});

export function getAuthSessionGeneration(): number {
  synchronizeSecurityContext();
  return sessionGeneration;
}

export function subscribeAuthSessionGeneration(listener: () => void): () => void {
  sessionGenerationListeners.add(listener);
  return () => {
    sessionGenerationListeners.delete(listener);
  };
}

export function captureAuthSessionIdentity(): AuthSessionIdentity {
  synchronizeSecurityContext();
  return {
    ...trackedSecurityContext,
    generation: sessionGeneration,
  };
}

export function isCurrentAuthSessionIdentity(identity: AuthSessionIdentity): boolean {
  const currentIdentity = captureAuthSessionIdentity();
  return (
    identity.generation === currentIdentity.generation &&
    sameAuthSecurityContext(identity, currentIdentity)
  );
}

export function assertCurrentAuthSessionIdentity(identity: AuthSessionIdentity): void {
  if (!isCurrentAuthSessionIdentity(identity)) {
    throw new SessionSupersededError();
  }
}
/**
 * Abort fetch/body work as soon as its authenticated session is superseded.
 * The optional external signal lets component lifecycle cancellation share the same transport.
 */
export function createAuthSessionAbortScope(
  identity: AuthSessionIdentity,
  externalSignal?: AbortSignal,
): AuthSessionAbortScope {
  const controller = new AbortController();
  let disposed = false;
  let removeStoreSubscription: () => void = () => undefined;

  const abort = (reason: unknown) => {
    if (!controller.signal.aborted) {
      controller.abort(reason);
    }
  };
  const abortIfSuperseded = () => {
    if (!isCurrentAuthSessionIdentity(identity)) {
      abort(new SessionSupersededError());
    }
  };
  const abortFromExternalSignal = () => {
    abort(externalSignal?.reason ?? new DOMException("请求已取消", "AbortError"));
  };

  removeStoreSubscription = useAuthStore.subscribe(abortIfSuperseded);
  if (externalSignal) {
    if (externalSignal.aborted) {
      abortFromExternalSignal();
    } else {
      externalSignal.addEventListener("abort", abortFromExternalSignal, { once: true });
    }
  }
  abortIfSuperseded();

  return {
    signal: controller.signal,
    dispose: () => {
      if (disposed) {
        return;
      }
      disposed = true;
      removeStoreSubscription();
      externalSignal?.removeEventListener("abort", abortFromExternalSignal);
    },
  };
}

function throwAbortReason(signal: AbortSignal): never {
  if (isSessionSupersededError(signal.reason)) {
    throw signal.reason;
  }
  if (signal.reason instanceof Error) {
    throw signal.reason;
  }
  throw new DOMException("请求已取消", "AbortError");
}

/**
 * Bind an asynchronous lifecycle callback to one security context.
 *
 * JavaScript cannot interrupt arbitrary callback code after an `await`. Callers must route every
 * awaited operation through `waitFor` and every state/navigation/toast effect through `run` (or
 * `runIfCurrent` for fire-and-forget progress events). The context signal is aborted immediately
 * when any security-relevant auth field changes.
 */
export async function runAuthSessionCallback<T>(
  identity: AuthSessionIdentity,
  callback: (context: AuthSessionCallbackContext) => PromiseLike<T> | T,
  externalSignal?: AbortSignal,
): Promise<T> {
  const scope = createAuthSessionAbortScope(identity, externalSignal);

  const assertCurrent = () => {
    assertCurrentAuthSessionIdentity(identity);
    if (scope.signal.aborted) {
      throwAbortReason(scope.signal);
    }
  };
  const context: AuthSessionCallbackContext = {
    identity,
    signal: scope.signal,
    assertCurrent,
    waitFor: async <Value>(work: PromiseLike<Value> | (() => PromiseLike<Value>)) => {
      assertCurrent();
      try {
        const value = await (typeof work === "function" ? work() : work);
        assertCurrent();
        return value;
      } catch (error) {
        assertCurrent();
        throw error;
      }
    },
    run: <Value>(effect: () => Value) => {
      assertCurrent();
      return effect();
    },
    runIfCurrent: (effect) => {
      if (!isCurrentAuthSessionIdentity(identity) || scope.signal.aborted) {
        return false;
      }
      effect();
      return true;
    },
  };

  try {
    context.assertCurrent();
    const result = await callback(context);
    context.assertCurrent();
    return result;
  } catch (error) {
    if (!isCurrentAuthSessionIdentity(identity)) {
      throw new SessionSupersededError();
    }
    if (scope.signal.aborted) {
      throwAbortReason(scope.signal);
    }
    throw error;
  } finally {
    scope.dispose();
  }
}

/**
 * Mutation lifecycle callbacks do not have a caller that can reliably consume a stale-session
 * rejection (especially onError/onSettled). Suppress only that expected cancellation while
 * preserving real callback failures.
 */
export async function runAuthSessionLifecycleCallback<T>(
  identity: AuthSessionIdentity,
  callback: (context: AuthSessionCallbackContext) => PromiseLike<T> | T,
): Promise<T | undefined> {
  try {
    return await runAuthSessionCallback(identity, callback);
  } catch (error) {
    if (isSessionSupersededError(error)) {
      return undefined;
    }
    throw error;
  }
}
if (import.meta.hot) {
  import.meta.hot.dispose(removeSessionIdentitySubscription);
}
