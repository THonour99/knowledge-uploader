import { useCallback, useRef } from "react";
import {
  type DefaultError,
  type MutationFunctionContext,
  type QueryClient,
  type UseMutationOptions,
  type UseMutationResult,
  useMutation,
} from "@tanstack/react-query";

import {
  type AuthSessionCallbackContext,
  type AuthSessionIdentity,
  assertCurrentAuthSessionIdentity,
  captureAuthSessionIdentity,
  isCurrentAuthSessionIdentity,
  runAuthSessionCallback,
} from "../sessionIdentity";

export interface SessionMutationFunctionContext
  extends AuthSessionCallbackContext, MutationFunctionContext {}

export type UseSessionMutationOptions<TData, TError, TVariables, TOnMutateResult> = Omit<
  UseMutationOptions<TData, TError, TVariables, TOnMutateResult>,
  "mutationFn"
> & {
  mutationFn?: (variables: TVariables, context: SessionMutationFunctionContext) => Promise<TData>;
};

/**
 * Bind a mutation hook to the authenticated identity that mounted it.
 *
 * App remounts the provider subtree for every security-context generation. Keeping this mount
 * identity inside the hook also protects isolated page renders and callbacks retained by portals:
 * a mutate function captured by session A becomes a no-op after A is superseded, while an async
 * mutation that has not reached its transport yet fails before invoking the API.
 *
 * Mutation functions that await before starting transport must route that await through the
 * supplied context.waitFor (and pass context.signal to cancellable transports). Direct API calls
 * may simply return their promise.
 */
export function useSessionMutation<
  TData = unknown,
  TError = DefaultError,
  TVariables = void,
  TOnMutateResult = unknown,
>(
  options: UseSessionMutationOptions<TData, TError, TVariables, TOnMutateResult>,
  queryClient?: QueryClient,
): UseMutationResult<TData, TError, TVariables, TOnMutateResult> {
  const mountIdentityRef = useRef<AuthSessionIdentity | null>(null);
  mountIdentityRef.current ??= captureAuthSessionIdentity();
  const mountIdentity = mountIdentityRef.current;
  const originalMutationFn = options.mutationFn;

  const mutation = useMutation<TData, TError, TVariables, TOnMutateResult>(
    {
      ...options,
      mutationFn:
        originalMutationFn === undefined
          ? undefined
          : (variables, mutationContext) =>
              runAuthSessionCallback(mountIdentity, (sessionContext) =>
                originalMutationFn(variables, {
                  ...mutationContext,
                  ...sessionContext,
                }),
              ),
    },
    queryClient,
  );

  const mutate = useCallback<typeof mutation.mutate>(
    (variables, mutateOptions) => {
      if (!isCurrentAuthSessionIdentity(mountIdentity)) {
        return;
      }
      mutation.mutate(variables, mutateOptions);
    },
    [mountIdentity, mutation.mutate],
  );
  const mutateAsync = useCallback<typeof mutation.mutateAsync>(
    async (variables, mutateOptions) => {
      assertCurrentAuthSessionIdentity(mountIdentity);
      return mutation.mutateAsync(variables, mutateOptions);
    },
    [mountIdentity, mutation.mutateAsync],
  );

  return {
    ...mutation,
    mutate,
    mutateAsync,
  };
}
