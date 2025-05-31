FROM alpine:3.21

ENV DEBUG=false \
    TZDATA="Europe/Paris" \
    CITY="Paris" \
    LEGACY=false \
    CHECKSUM=1 \
    KEYS="ISOUSC BASE IINST" \
    PORT='ttyS0' \
    SLEEP_INTERVAL=60 \
    INFLUX_SEND=false \
    MYSQL_SEND=false \
    MQTT_SEND=false \
    MQTT_TOPIC="linky" \
    MQTT_QOS=0 \
    MQTT_RETAIN=False \
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

RUN apk add --no-cache  tzdata supervisor mariadb-common mariadb-client mariadb-connector-c \
    py3-mysqlclient python3 py3-pip gettext py3-pyserial py3-yaml py3-influxdb py3-paho-mqtt
RUN if [ false != ${DEBUG:-false} ]; then apk add bash vim; fi ; cp /etc/supervisord.conf /etc/supervisord.conf.package
RUN sed -i "s#;nodaemon=false#nodaemon=true#" /etc/supervisord.conf \
    && sed -i "s#;loglevel=info#loglevel=info#" /etc/supervisord.conf

RUN pip3 install --break-system-packages influxdb-client mysql-connector-python
ADD /src/app/ /app/
ADD /src/etc/ /etc/
RUN chmod +x /app/*.sh /app/*.py

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisord.conf"]
#ENTRYPOINT ["/app/entrypoint.sh"]

VOLUME /config
