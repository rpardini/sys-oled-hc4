# Same distro packages as the bare-metal installer: runtime libraries are always the distro's, never pip's.
FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-luma.oled python3-psutil python3-pil python3-smbus2 python3-yaml \
    && rm -rf /var/lib/apt/lists/*

COPY src/ /opt/sys-oled/src/
COPY bin/ /opt/sys-oled/bin/

# Container defaults: host / is bind-mounted at /host; idle (re-probe) instead of exiting when no display.
ENV SYS_OLED_HOST_ROOT=/host \
    SYS_OLED_HOST_MISSING_DISPLAY=wait \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["/opt/sys-oled/bin/sys-oled"]
CMD ["run"]
