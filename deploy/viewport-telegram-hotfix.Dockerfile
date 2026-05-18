FROM viewport-corp/hermes-agent:v0.12.0

LABEL org.opencontainers.image.source="https://github.com/viewport-corp/fork-hermes-agent"
LABEL org.opencontainers.image.revision="46ee2df4ee37e65e69a5abdc22bbdeaef15cc751"
LABEL viewport.hotfix="telegram-outbound-queue-health"

COPY gateway/platforms/telegram.py /opt/hermes/gateway/platforms/telegram.py
COPY gateway/status.py /opt/hermes/gateway/status.py
COPY hermes_cli/gateway.py /opt/hermes/hermes_cli/gateway.py

USER root
RUN chmod a+r /opt/hermes/gateway/platforms/telegram.py \
    /opt/hermes/gateway/status.py \
    /opt/hermes/hermes_cli/gateway.py \
    && printf '%s\n' \
      '#!/bin/sh' \
      'exec python3 -c '\''import json,sys; s=json.load(open("/opt/data/gateway_state.json")); sys.exit(0 if s.get("gateway_state") == "running" and s.get("platforms", {}).get("telegram", {}).get("state") == "connected" else 1)'\''' \
      > /usr/local/bin/hermes-health \
    && chmod 0755 /usr/local/bin/hermes-health

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 CMD ["/usr/local/bin/hermes-health"]
