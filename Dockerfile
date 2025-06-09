FROM alpine:3.22

ENV DEBUG=false \
    TZDATA="Europe/Paris" \
    CITY="Paris" \
    LEGACY=false \
    KEYS="ISOUSC BASE IINST" \
    PORT='ttyserial0' \
    SLEEP_INTERVAL=60 \
    HTTP_SERVER="False" \
    HTTP_IP="0.0.0.0" \
    HTTP_PORT="8080" \
    HTTP_NBPOINTS=1000 \
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

    # Liste des étiquettes transmises par la Téléinfo Client (TIC)

    # ADCO     : Adresse du compteur
    # OPTARIF  : Option tarifaire choisie
    # ISOUSC   : Intensité souscrite
    # BASE     : Index option Base
    # HCHC     : Index Heures Creuses
    # HCHP     : Index Heures Pleines
    # EJPHN    : Index option EJP Heures Normales
    # EJPHPM   : Index option EJP Heures de Pointe Mobile
    # BBRHCJB  : Index option Tempo Heures Creuses Jours Bleus
    # BBRHPJB  : Index option Tempo Heures Pleines Jours Bleus
    # BBRHCJW  : Index option Tempo Heures Creuses Jours Blancs
    # BBRHPJW  : Index option Tempo Heures Pleines Jours Blancs
    # BBRHCJR  : Index option Tempo Heures Creuses Jours Rouges
    # BBRHPJR  : Index option Tempo Heures Pleines Jours Rouges
    # PEJP     : Préavis Début EJP
    # PTEC     : Période Tarifaire en cours
    # DEMAIN   : Couleur du lendemain
    # IINST    : Intensité Instantanée
    # ADPS     : Avertissement de Dépassement De Puissance Souscrite
    # IMAX     : Intensité maximale appelée
    # PAPP     : Puissance apparente
    # HHPHC    : Horaire Heures Pleines Heures Creuses
    # MOTDETAT : Mot d'état du compteur

RUN apk add --no-cache bash tzdata supervisor mariadb-common mariadb-client mariadb-connector-c \
    py3-mysqlclient python3 py3-pip gettext py3-pyserial py3-yaml py3-influxdb py3-paho-mqtt \
    && cp /etc/supervisord.conf /etc/supervisord.conf.package \
    && sed -i "s#;nodaemon=false#nodaemon=true#" /etc/supervisord.conf \
    && sed -i "s#;loglevel=info#loglevel=info#" /etc/supervisord.conf \
    && pip3 install --break-system-packages influxdb-client mysql-connector-python http-plot-server

RUN adduser --disabled-password --gecos "" --home "$(pwd)" \
    --ingroup "www-data" --no-create-home --uid "123456"  www-data
RUN if [ false != ${DEBUG:-false} ]; then apk add bash vim; fi ;

ADD --chmod=750 /src/app/ /app/
ADD --chmod=640 /src/etc/ /etc/

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisord.conf"]
#ENTRYPOINT ["/app/entrypoint.sh"]
WORKDIR /app
VOLUME /config
