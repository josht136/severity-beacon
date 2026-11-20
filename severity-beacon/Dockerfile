ARG BUILD_FROM
FROM ${BUILD_FROM}

# Copy application + Ingress static UI
COPY rootfs /
COPY severity_beacon.py /usr/bin/severity_beacon.py
COPY features_v7.py /usr/bin/features_v7.py
COPY www /usr/share/severity_beacon/www
RUN chmod a+x /usr/bin/severity_beacon.py \
    && chmod a+x /etc/services.d/severity_beacon/run

LABEL \
    io.hass.name="Severity Beacon Lights" \
    io.hass.description="Zabbix severity → Home Assistant RGB lights" \
    io.hass.type="addon" \
    io.hass.version="0.8.0"
# build 1794917803
# build 1794917931
# build 0.8.0
