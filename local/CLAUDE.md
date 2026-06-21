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
- `upstream` → https://github.com/pewdiepie-archdaemon/odysseus (public)

## Upstream sync
```
git fetch upstream
git rebase upstream/dev
```

## Branch conventions
- `feat/*`  — work intended for an upstream PR (keep clean, no local secrets)
- `local/*` — private-only changes that never leave this repo
