FROM alpine:3.22

ENV DEBUG=false \
    QUIET=false \
    TZDATA="Europe/Paris" \
    CITY="Paris" \
    LEGACY=false \
    KEYS="ISOUSC BASE IINST" \
    PORT='ttyserial0' \
    SLEEP_INTERVAL=60 \
    SINSTS_PERCENT_CHANGE=.5 \
    HTTP_SERVER="False" \
    HTTP_IP="0.0.0.0" \
    HTTP_PORT="8080" \
    HTTP_NBPOINTS=500 \
    FILE_SEND=false \
    INFLUX_SEND=false \
    MYSQL_SEND=false \
    MQTT_SEND=false \
    MQTT_TOPIC="linky" \
    MQTT_QOS=0 \
    MQTT_RETAIN=False \
    MQTT_TLS=False \
    MQTT_TLS_CA="" \
    MQTT_TLS_INSECURE=True \
    MQTT_TLS_CLIENT_CERT="" \
    MQTT_TLS_CLIENT_KEY="" \
    MQTT_TLS_CLIENT_PASSWORD="" \
    MQTT_4JEEDOM=False \
    IGNORE_KEYS_CHEKSUM="[]"

# TODO: Setuptools<81
RUN apk add --no-cache bash doas tzdata supervisor mariadb-common mariadb-client mariadb-connector-c \
    py3-mysqlclient python3 py3-pip gettext py3-pyserial py3-yaml py3-influxdb py3-paho-mqtt \
    && cp /etc/supervisord.conf /etc/supervisord.conf.package \
    && sed -i "s#;nodaemon=false#nodaemon=true#" /etc/supervisord.conf \
    && sed -i "s#;loglevel=info#loglevel=info#" /etc/supervisord.conf \
    && chmod 444 /etc/supervisord.conf \
    && pip3 install --break-system-packages influxdb-client mysql-connector-python http-plot-server

RUN adduser --disabled-password --gecos "" --home "$(pwd)" \
    --ingroup "www-data" --no-create-home --uid "123456"  www-data \
    && adduser myuser -D -G wheel \
    && addgroup myuser dialout \
    && echo 'myuser:123' | chpasswd \
    && echo 'permit nopass :wheel as root' > /etc/doas.d/doas.conf
RUN if [ "false" != ${DEBUG:-"false"} ]; then apk add bash vim; fi ;

ADD --chmod=755 /src/linky/daily_update_graph.sh /etc/periodic/daily/
ADD --chown=myuser:users --chmod=750 /src/linky/ /app/
ADD --chmod=644 /src/etc/ /etc/

CMD [ "/usr/bin/supervisord", "-c", "/etc/supervisord.conf"]
#ENTRYPOINT ["/app/entrypoint.sh"]
#USER myuser
WORKDIR /app
VOLUME /config
