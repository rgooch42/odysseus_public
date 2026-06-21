# odysseus_local — Private Overlay

This directory contains private configuration and documentation that is
committed to `rgooch42/odysseus_local` but never upstreamed to
`pewdiepie-archdaemon/odysseus`.

## What lives here
- `docs/` — setup guides for local bridge wiring (docker.local MCPs)
- `patches/` — local fixes pending upstream PR (remove when PR merges)
- `secrets/` — gitignored; never commit actual secrets here

## Git remotes
- `origin`   → https://github.com/rgooch42/odysseus_local (private)
- `public`   → https://github.com/rgooch42/odysseus_public (public staging fork)
- `upstream` → https://github.com/pewdiepie-archdaemon/odysseus (upstream)

## Upstream sync
```
git fetch upstream
git rebase upstream/dev
```

## Contribution flow
```
feat/* branch → git push public feat/<name> → PR from odysseus_public to upstream
```
Never push `local/*` branches or `.env.local` to `public` or `upstream`.

## Branch conventions
- `feat/*`  — work intended for upstream PR; keep clean, no local secrets
- `local/*` — private-only; never pushed beyond `origin`
