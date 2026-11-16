ARG BUILD_FROM
FROM ${BUILD_FROM}

# Copy application
COPY rootfs /
COPY severity_beacon.py /usr/bin/severity_beacon.py
RUN chmod a+x /usr/bin/severity_beacon.py \
    && chmod a+x /etc/services.d/severity_beacon/run

LABEL \
    io.hass.name="Severity Beacon Lights" \
    io.hass.description="Zabbix severity → Home Assistant RGB light" \
    io.hass.type="addon" \
    io.hass.version="0.1.0"
