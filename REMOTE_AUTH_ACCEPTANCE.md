# Remote auth hardening acceptance — 0.1.0a37

## Observed failures

- SourceControl invocation without explicit credential-env overrides used the SourceControl defaults while the operator credentials were stored in `BITBUCKET_USERNAME` / `BITBUCKET_PASSWORD`; the server returned HTTP 500.
- Bitbucket invocation returned HTTP 401 under `auto`; a configured token can previously take precedence over otherwise valid Basic credentials.

## Fix

The existing shared remote-auth owner is hardened; no second auth/clone/discovery path is added.

- `auto` + token + complete Basic -> local ambiguity error; caller must choose.
- `auto` + partial Basic -> local incomplete-credentials error.
- explicit `basic` -> Basic is used even if a token environment variable is also populated.
- HTTP discovery errors are contextualized with provider/status/effective mode without secrets.
- Provider credential environment names are not silently aliased.

## Accepted operator commands

SourceControl with the already existing credential env variables:

```bash
repository-inventory build-sourcecontrol-organization \
  --sourcecontrol-api-url https://api.sc-ci.sber.ru \
  --organization PPRB_CPC \
  --auth-mode basic \
  --username-env BITBUCKET_USERNAME \
  --password-env BITBUCKET_PASSWORD \
  --output outputs/kpk-repo-inventory \
  --reduce --force --repository-limit 10
```

Bitbucket:

```bash
repository-inventory build-bitbucket-project \
  --bitbucket-project-url https://stash.delta.sbrf.ru/projects/UCP \
  --auth-mode basic \
  --output outputs/ucp-repo-inventory \
  --reduce --force --repository-limit 10
```
