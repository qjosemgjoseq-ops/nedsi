# NEDSI — project status & resume point

_Paused 2026-07-15 (vacation). DOT-NL meeting: **August 2026**._

The project pivoted from the EV dashboard toward what the DOT-NL team actually
asked for: **charging-station data quality + faster CPO onboarding**. Most of
that is built and verified; the one open thread is making it privately reachable
for the team.

## Done & verified

| Piece | Where |
|---|---|
| Onboarding gap → classified targets (real CPOs missing from DOT-NL) | `scripts/build_onboarding_gap.py`, `build_onboarding_targets.py` |
| Per-CPO **datakwaliteit scorecard** + AFIR framing | `scripts/load_datakwaliteit_snapshot.py`, `DOT_NL_DATAKWALITEIT_CHECKLIST.md` |
| **Datakwaliteit view** in the webapp (+ id-stability panel) | `webapp/app/backend/routers/datakwaliteit.py`, `frontend/.../DatakwaliteitCard.tsx`, `IdStabiliteitPanel.tsx` |
| **Audit report** for the DOT team (shareable) | Artifact: https://claude.ai/code/artifact/6115d8cc-6fe3-43fd-893a-b9a7c0c002fe |
| **Self-updating id-stability monitor** (registry + churn log) piggybacking the 15-min cron | `scripts/dotnl_registry.py`, `seed_registry.py`, `snapshot_dotnl_occupancy.py` |
| Reportable data-quality findings (incl. 54% id-churn = colon→hyphen format migration) | `NDW_DATA_QUALITY_REPORT.md` |
| **Containerized single-origin deploy**, verified at http://localhost:8080 | `webapp/app/docker-compose.deploy.yml`, `DEPLOY.md` |

## Open thread — resume here: make NEDSI privately reachable

Goal: DOT team opens NEDSI themselves after the August meeting. Chosen approach:
Docker Compose (already working locally) + **Cloudflare Tunnel + Access**.

State: Cloudflare free account created (Gmail). **No domain yet** — that's the blocker.

**Decision pending:** register a cheap domain (~€/yr, Cloudflare Registrar) for a
**stable, gated URL** (recommended — a quick trycloudflare URL was rejected: random
+ changes every restart + ungated), _or_ a managed cloud host for 24/7 (leaves the
machine). Any self-host is only live while the PC is on; a named tunnel gives the
**same** URL each time, a quick tunnel gives a new one.

### Next steps (full guide: `webapp/app/DEPLOY.md`)
1. Register a domain in Cloudflare (Account Home → Domain Registration).
2. Zero Trust → Networks → Tunnels → create `nedsi` → copy token into
   `webapp/app/.env.deploy` (`TUNNEL_TOKEN=`).
3. Tunnel Public Hostname → Service **HTTP → `web:80`**.
4. Zero Trust → Access → self-hosted app on that hostname → policy Allow →
   emails ending in `@ndw.nu` + the DOT-team addresses.
5. `docker compose --env-file .env.deploy -f docker-compose.deploy.yml --profile tunnel up --build -d`

## Run it locally meanwhile
```
cd webapp/app
docker compose --env-file .env.deploy -f docker-compose.deploy.yml up --build -d
# http://localhost:8080
```
DB = the existing `nedis_postgis` container (data already loaded; reached via
host.docker.internal — no migration). The GitHub Actions cron keeps extending the
id-stability history in Neon unattended.

_Note: `webapp/` is not a git repo (deploy files live locally); this repo
(`C:\projects\nedsi`) holds the data-platform scripts, docs, and the cron._
