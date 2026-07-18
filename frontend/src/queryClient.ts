import { QueryClient } from "@tanstack/react-query";

import {
  type AuthSecurityContext,
  authSecurityContextFromState,
  sameAuthSecurityContext,
} from "./sessionIdentity";
import { useAuthStore } from "./store/auth.store";

type AuthStore = Pick<typeof useAuthStore, "getState" | "subscribe">;

interface ResettableMutationObserver {
  reset: () => void;
}

function currentAuthSecurityContext(store: AuthStore): AuthSecurityContext {
  return authSecurityContextFromState(store.getState());
}

/**
 * Destroy every cached query and mutation as soon as the authenticated security context changes.
 * Query destruction also prevents a late response from an old identity from re-entering the cache.
 */
export function installAuthQueryCacheGuard(
  client: QueryClient,
  store: AuthStore = useAuthStore,
): () => void {
  let previousIdentity = currentAuthSecurityContext(store);
  let sessionGeneration = 0;
  const activeMutationObservers = new Set<ResettableMutationObserver>();
  const mutationGenerations = new WeakMap<object, number>();
  const mutationCache = client.getMutationCache();

  const removeMutationCacheSubscription = mutationCache.subscribe((event) => {
    if (event.type === "observerAdded") {
      activeMutationObservers.add(event.observer);
      return;
    }
    if (event.type === "observerRemoved") {
      activeMutationObservers.delete(event.observer);
      return;
    }
    if (event.type !== "added") {
      return;
    }

    const mutationGeneration = sessionGeneration;
    mutationGenerations.set(event.mutation, mutationGeneration);
    // This generic guard can suppress callbacks only before they enter. Async callbacks that cross
    // an await must additionally use runAuthSessionCallback so their continuation is session-bound.
    const { onSuccess, onError, onSettled } = event.mutation.options;
    event.mutation.setOptions({
      ...event.mutation.options,
      onSuccess:
        onSuccess === undefined
          ? undefined
          : (...args: Parameters<NonNullable<typeof onSuccess>>) => {
              if (sessionGeneration !== mutationGeneration) {
                return undefined;
              }
              return onSuccess(...args);
            },
      onError:
        onError === undefined
          ? undefined
          : (...args: Parameters<NonNullable<typeof onError>>) => {
              if (sessionGeneration !== mutationGeneration) {
                return undefined;
              }
              return onError(...args);
            },
      onSettled:
        onSettled === undefined
          ? undefined
          : (...args: Parameters<NonNullable<typeof onSettled>>) => {
              if (sessionGeneration !== mutationGeneration) {
                return undefined;
              }
              return onSettled(...args);
            },
    });
  });

  const removeStoreSubscription = store.subscribe(() => {
    const nextIdentity = currentAuthSecurityContext(store);
    if (sameAuthSecurityContext(previousIdentity, nextIdentity)) {
      return;
    }

    previousIdentity = nextIdentity;
    sessionGeneration += 1;

    for (const observer of Array.from(activeMutationObservers)) {
      observer.reset();
    }
    for (const mutation of mutationCache.getAll()) {
      if (mutationGenerations.get(mutation) === sessionGeneration) {
        continue;
      }
      mutation.setOptions({
        ...mutation.options,
        onSuccess: undefined,
        onError: undefined,
        onSettled: undefined,
      });
    }
    client.clear();
  });

  return () => {
    removeStoreSubscription();
    removeMutationCacheSubscription();
    activeMutationObservers.clear();
  };
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

const removeAuthQueryCacheGuard = installAuthQueryCacheGuard(queryClient);

if (import.meta.hot) {
  import.meta.hot.dispose(removeAuthQueryCacheGuard);
}
