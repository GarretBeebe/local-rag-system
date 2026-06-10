# Secret Rotation Runbook

## API_KEY

1. Generate a new value: `openssl rand -hex 32`.
2. Replace `API_KEY` in `.env`.
3. Restart the API container: `docker compose up -d api`.
4. Update API clients with the new bearer token.

## QDRANT_API_KEY

1. Generate a new value: `openssl rand -hex 32`.
2. Replace `QDRANT_API_KEY` in `.env`.
3. Restart Qdrant and dependent services: `docker compose up -d qdrant api watcher`.
4. Confirm the API and watcher can reach Qdrant and unauthenticated Qdrant calls fail.

## Leak Check

After every rotation, search for the old values and their prefixes/suffixes with `rg` before committing.
