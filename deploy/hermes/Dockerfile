# Hermes image extension required by the bundled Camoufox Firefox runtime.
FROM nousresearch/hermes-agent:latest

USER root

# Debian 13 renamed GTK 3 to the time64 package; Camoufox loads it dynamically.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgtk-3-0t64 \
        libdbus-glib-1-2 \
    && rm -rf /var/lib/apt/lists/*
