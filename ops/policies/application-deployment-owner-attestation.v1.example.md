# Application deployment owner attestation v1

This repository contains only a **TEST-ONLY** example public-key policy. Its RFC 8032
test-vector key is publicly reproducible and must never be copied into staging or production
policy. Production uses a separately reviewed, read-only public-key policy supplied by the
protected environment. Its runner-local path is injected through the protected environment
variable `APPLICATION_DEPLOYMENT_OWNER_POLICY_PATH`; it must not point at the repository
example or a nonexistent placeholder. The protected environment independently pins the exact
raw policy SHA-256 through `APPLICATION_DEPLOYMENT_OWNER_POLICY_SHA256`; the live workflow
and final release gate must compare that anchor before trusting the policy.
No production trust key or policy is stored in this repository. The corresponding private key
stays in the owner's HSM/KMS or equivalent signing boundary and is never available to a GitHub
runner or this
repository.

Before signing, the environment owner must independently observe the deployment platform and
OCI metadata and confirm that the running deployment references the exact main-CI bundle
artifact ID/digest and Git SHA. The signer then constructs an exact
`knowledge-uploader.application-deployment-owner-attestation.v1` document. The Ed25519
signature covers canonical JSON containing `schema`, `version`, `algorithm`, `key_id`, and
this exact `payload`:

```json
{
  "owner_role": "application_deployment_owner",
  "permission": "confirm.application-deployment",
  "environment": "test",
  "repository": "example/knowledge-uploader",
  "git_sha": "<full lowercase Git SHA>",
  "nonce": "<single-use 32-128 character base64url value>",
  "workflow_run_id": 1,
  "workflow_run_attempt": 1,
  "app_endpoint_identity_sha256": "<canonical HTTPS app endpoint identity SHA-256 hex>",
  "app_tls_spki_sha256": "<same-connection TLS SPKI SHA-256 hex>",
  "main_ci_run_id": 1,
  "main_ci_run_attempt": 1,
  "main_bundle_artifact_id": 1,
  "main_bundle_artifact_digest": "sha256:<64 lowercase hex characters>",
  "deployment_identity_sha256": "<environment/control-plane deployment identity SHA-256>",
  "artifact_deployed": true,
  "issued_at": "2026-07-18T08:00:00Z",
  "not_before": "2026-07-18T08:00:00Z",
  "expires_at": "2026-07-18T08:15:00Z"
}
```

The signer obtains the signed bytes from
`canonical_signed_bytes(signed_document)`: UTF-8 JSON with recursively sorted keys,
ASCII escaping, compact separators and no non-finite numbers. The final envelope adds only
`signature`, encoded as unpadded base64url of the 64-byte Ed25519 signature.

The protected workflow receives only the already-signed JSON through
`APPLICATION_DEPLOYMENT_ATTESTATION_JSON`, writes it to a mode `0600` temporary file, and
passes that file plus the reviewed production policy to
`scripts/verify_application_deployment_attestation.py`. The expected deployment identity
must come independently from the protected environment/control plane; copying it only from
the attestation does not prove a context match. A runner-generated or runner-self-reported
`deployment_identity_sha256` cannot replace the owner's deployment-platform/OCI observation.
This verifier proves the owner's signature and exact bindings, not deployment-platform state
by itself. The protected evidence artifact must retain the signed attestation JSON and the
public-key policy so the final release gate can independently repeat verification. It should
also record their SHA-256 digests and the signed deployment identity SHA-256. The artifact
must never contain the signing private key, original endpoint URL, credentials, original
deployment control-plane/OCI responses, or application responses.

The CLI deliberately has no default policy path. Callers must provide `--policy` and every
expected context field, including the exact main-CI run, attempt, bundle artifact ID and
digest. Success and failure output contain only generic status/error codes.
