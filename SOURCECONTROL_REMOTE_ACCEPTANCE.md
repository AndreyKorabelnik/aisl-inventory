# SourceControl remote acquisition acceptance — 0.1.0a36

## Observed industrial contract

The demonstrated SourceControl installation exposes organization discovery at:

`GET https://api.sc-ci.sber.ru/api/v1/orgs/PPRB_CPC/repos?limit=2`

Observed response properties:
- HTTP 200 with Basic Auth;
- `X-Total-Count: 256`;
- RFC-style `Link` pagination with `page`;
- JSON array rows containing `name`, `full_name`, `clone_url`, `default_branch`, `empty`, `archived`, and remote numeric `id`;
- HTTPS clone URLs such as `https://api.sc-ci.sber.ru/PPRB_CPC/<repo>.git`.

## Implemented owner/path

`discover_sourcecontrol_organization_repositories` is a provider adapter inside `repository_inventory.remote_acquisition`. It produces the same `RemoteRepositorySet` consumed by the existing sequential remote batch path. Git checkout, resume, v7 build, verification, Reduced build, candidate build, and index build are not duplicated.

## Acceptance invariants

1. Pages use `limit` + one-based `page`; `Link` and `X-Total-Count` are consumed when present.
2. SourceControl-provided `clone_url` is used; credentials embedded in URLs are stripped.
3. `default_branch` becomes the requested repository ref.
4. `empty=true` is an observed operational fact: the row is explicitly skipped with `sourcecontrol_repository_empty`; no Git checkout is attempted.
5. `archived=true` is retained as metadata and is not silently excluded.
6. Duplicate normalized repository IDs fail explicitly.
7. Repository selectors support normalized id/name/full-name/remote numeric id.
8. Repository knowledge remains independent `repository-inventory/v7`; no cross-repository claim is introduced.

## Real-run command

```bash
repository-inventory build-sourcecontrol-organization \
  --sourcecontrol-api-url https://api.sc-ci.sber.ru \
  --organization PPRB_CPC \
  --auth-mode basic \
  --username-env BITBUCKET_USERNAME \
  --password-env BITBUCKET_PASSWORD \
  --output outputs/kpk-repo-inventory \
  --reduce \
  --force \
  --repository-limit 10
```

## 0.1.0a37 auth hardening follow-up

The first real invocation omitted the explicit credential environment overrides and therefore used no SourceControl-named HTTP credentials; the remote server returned HTTP 500. This does not change SourceControl discovery semantics. `0.1.0a37` hardens the shared auth owner: ambiguous/incomplete `auto` credentials fail locally, explicit Basic ignores stale token variables, and HTTP discovery failures report provider/status/effective auth mode. The accepted PPRB_CPC invocation remains the explicit Basic command above.
