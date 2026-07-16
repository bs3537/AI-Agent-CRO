# Hermes TipRanks Runtime

The TipRanks refresh uses the Camoufox server already bundled in the Hermes
agent image. The base image lacks two Firefox runtime libraries on Debian 13,
so production must build from `deploy/hermes/Dockerfile`.

## Deployment

1. Configure Coolify to build the repository's `deploy/hermes/Dockerfile`.
2. Preserve the existing `/opt/data` volume and Hermes environment.
3. Pull the same Git revision into `/opt/data/sma-monitor`.
4. Run the five-ticker acceptance check:

   ```bash
   cd /opt/data/sma-monitor
   .venv/bin/python -m sma_monitor.orchestrator tipranks-refresh \
     --ticker AQST --ticker COGT --ticker PRAX --ticker INSM --ticker NBIS
   ```

5. Enable the Saturday target refresh only when all five pages parse correctly.
6. Run the EOD calculation once:

   ```bash
   .venv/bin/python -m sma_monitor.orchestrator target-upside-refresh \
     --retry-attempts 1 --retry-seconds 0
   ```

The scraper starts the local browser service only for the duration of the
weekly job. Scrape failures retain the last successful TipRanks value as stale;
FMP targets are never substituted. Holdings with FMP's explicit `isEtf=true`
classification are skipped and render no price-target metric.
