FROM viewport-corp/hermes-agent:v0.12.0

LABEL org.opencontainers.image.source="https://github.com/viewport-corp/fork-hermes-agent"
LABEL org.opencontainers.image.revision="4bbf4b0d20452c8e0b6c9aa5022d080612e09264"
LABEL viewport.hotfix="telegram-outbound-queue-health"

COPY gateway/platforms/telegram.py /opt/hermes/gateway/platforms/telegram.py
COPY gateway/status.py /opt/hermes/gateway/status.py
COPY hermes_cli/gateway.py /opt/hermes/hermes_cli/gateway.py

USER root
RUN chmod a+r /opt/hermes/gateway/platforms/telegram.py \
    /opt/hermes/gateway/status.py \
    /opt/hermes/hermes_cli/gateway.py