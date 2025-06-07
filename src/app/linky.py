#!/usr/bin/env python3
# https://github.com/hleroy/teleinfo-linky
# -*- coding: utf-8 -*-
# __author__ = "Hervé Le Roy"
# __licence__ = "GNU General Public License v3.0"
#  apt install -y python3-serial python3-mysqldb python3-influxdb
# Python 3, pré-requis : pip install PyYAML pySerial influxdb-client

# TODO:
# * Ajouter un thread séparé pour Linky
# * Afficher des informations depuis le thread principal
# * Tester plusieurs scénarios d'erreurs InfluxDB (iptables sur serveur, arrêt du serveur)

import argparse
import json
import logging
import os
import random
import re
import signal
import ssl
import termios
import time
from datetime import datetime
from multiprocessing import Process, Queue

import mysql.connector
import serial
from influxdb_client import InfluxDBClient, Point, BucketRetentionRules
from influxdb_client.client.exceptions import InfluxDBError
from influxdb_client.client.write_api import SYNCHRONOUS, PointSettings
from influxdb_client.rest import ApiException
from mysql.connector.pooling import PooledMySQLConnection
from paho.mqtt import client as mqtt_client
from urllib3 import Retry
from urllib3.exceptions import HTTPError

logger = logging.getLogger(__name__)
LDIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_KEYS = ('ISOUSC', 'BASE', 'IINST',)
DEFAULT_CHECKSUM_METHOD = 1
DEFAULT_INTERVAL = 60

START_FRAME = b'\x02'  # STX, Start of Text
STOP_FRAME = b'\x03'  # ETX, End of Text

if 'TZ' in os.environ:
  time.tzset()

MYSQL_DB_DATATYPE = {"ADSC": ["bigint unsigned", 0], "VTIC": ["tinyint  UNSIGNED", 2], "DATE": ["VARCHAR(13)", ""],
                     "NGTF": ["VARCHAR(16)", ""],
                     "LTARF": ["VARCHAR(16)", ""],
                     "EAST": ["VARCHAR(9)", ""],
                     "EASF01": ["VARCHAR(9)", ""],
                     "EASF02": ["VARCHAR(9)", ""],
                     "EASF03": ["VARCHAR(9)", ""],
                     "EASF04": ["VARCHAR(9)", ""],
                     "EASF05": ["VARCHAR(9)", ""],
                     "EASF06": ["VARCHAR(9)", ""],
                     "EASF07": ["VARCHAR(9)", ""],
                     "EASF08": ["VARCHAR(9)", ""],
                     "EASF09": ["VARCHAR(9)", ""],
                     "EASF10": ["VARCHAR(9)", ""],
                     "EASD01": ["VARCHAR(9)", ""],
                     "EASD02": ["VARCHAR(9)", ""],
                     "EASD03": ["VARCHAR(9)", ""],
                     "EASD04": ["VARCHAR(9)", ""],
                     "EAIT": ["VARCHAR(9)", ""],
                     "IRMS1": ["TINYINT UNSIGNED", 0],
                     "IRMS2": ["TINYINT UNSIGNED", 0],
                     "IRMS3": ["TINYINT UNSIGNED", 0],
                     "URMS1": ["TINYINT UNSIGNED", 0],
                     "URMS2": ["TINYINT UNSIGNED", 0],
                     "URMS3": ["TINYINT UNSIGNED", 0],
                     "PREF": ["TINYINT UNSIGNED", 0],
                     "PCOUP": ["TINYINT UNSIGNED", 0],
                     "SINSTS": ["SMALLINT UNSIGNED", 0],
                     "SINSTS1": ["SMALLINT UNSIGNED", 0],
                     "SINSTS2": ["SMALLINT UNSIGNED", 0],
                     "SINSTS3": ["SMALLINT UNSIGNED", 0],
                     "SMAXSN": ["SMALLINT UNSIGNED", 0],
                     "SMAXSN1": ["SMALLINT UNSIGNED", 0],
                     "SMAXSN2": ["SMALLINT UNSIGNED", 0],
                     "SMAXSN3": ["SMALLINT UNSIGNED", 0],
                     "SMAXSNMIN1": ["SMALLINT UNSIGNED", 0],
                     "CCASN": ["SMALLINT UNSIGNED", 0],
                     "CCASNMIN1": ["SMALLINT UNSIGNED", 0],
                     "UMOY1": ["TINYINT UNSIGNED", 0],
                     "UMOY2": ["TINYINT UNSIGNED", 0],
                     "UMOY3": ["TINYINT UNSIGNED", 0],
                     "STGE": ["VARCHAR(8)", ""],
                     "MSG1": ["VARCHAR(32)", ""],
                     "PRM": ["VARCHAR(14)", ""],
                     "RELAIS": ["TINYINT UNSIGNED", 0],
                     "NTARF": ["TINYINT UNSIGNED", 0],
                     "NJOURF": ["TINYINT UNSIGNED", 0],
                     "NJOURFPLUS1": ["TINYINT UNSIGNED", 0],
                     "PJOURFPLUS1": ["VARCHAR(98)", ""],
                     "TIME": ["TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (`TIME`)", "NULL"]}


#################################################################################################
# prepare connection to db
def create_influxdb_client_and_thread(influxdb_url: str = "", influxdb_token: str = "", influxdb_org: str = "",
                                      linky_location: str = "Paris", influxdb_bucket: str = "",
                                      myframe_queue: Queue = None):
  mybucket = None
  # Connexion à InfluxDB
  try:
    logger.info(f'Connexion à {influxdb_url}')
    retries = Retry(connect=4, read=2, redirect=5, backoff_factor=0.5)
    influxdb_client = InfluxDBClient(url=influxdb_url,
                                     token=influxdb_token,
                                     org=influxdb_org,
                                     retries=retries)

    logger.debug(f'creation de l\'api orgs')
    orgs_api = influxdb_client.organizations_api()
    # Create organization
    org = orgs_api.create_organization(name=influxdb_org)
    logger.info(f'Organization {influxdb_org} created')

    logger.debug(f'creation de l\'api bucket')
    buckets_api = influxdb_client.buckets_api()
    bucket_retention_seconds = None  # days * 86400  # Convert days to seconds
    if bucket_retention_seconds is None:
      retention_rules = []
    else:
      retention_rules = [BucketRetentionRules(type="expire", every_seconds=bucket_retention_seconds)]

    # create bucket

    bucket = buckets_api.create_bucket(name=influxdb_bucket, org_id=org.id, retention_rules=retention_rules)
    logger.info(f"Bucket '{influxdb_bucket}' dans l'organisation '{influxdb_org}' crée.")

  except InfluxDBError as exc:
    logger.error(f'Erreur de connexion à InfluxDB: {exc}')
    raise SystemExit(1)

    # Obtention du client d'API en écriture
  point_settings = PointSettings()
  point_settings.add_default_tag('location', linky_location)
  write_client = influxdb_client.write_api(write_options=SYNCHRONOUS, point_settings=point_settings)
  if write_client is None:
    logger.error(f'Cannot create influxdb client. Ignoring that export.')
    influxdb_send_data = False

  # Démarrage du thread d'envoi vers InfluxDB
  logger.info(f'Démarrage du thread d\'envoi vers InfluxDB')
  send_influx_thread = Process(target=_send_frames_to_influx, args=(myframe_queue, influxdb_bucket, write_client),
                               daemon=True)
  send_influx_thread.start()
  return write_client, send_influx_thread


#################################################################################################
def create_mysql_client_and_thread(mysql_host: str = "", mysql_port: int = 3306, mysql_username: str = "",
                                   mysql_password: str = "", mysql_database: str = "",
                                   myframe_queue: Queue = None):
  # Connexion à mysql
  try:
    # Obtention du client d'API en écriture
    logger.info(f'Connexion à {mysql_username}@{mysql_host}:{mysql_port}')
    mycnx = mysql.connector.connect(host=mysql_host, user=mysql_username, password=mysql_password,
                                    database=mysql_database)
    f = ""
    for k in MYSQL_DB_DATATYPE.keys():
      f += f'`{k}` {MYSQL_DB_DATATYPE[k][0]},'

    logger.debug(f'table datatype: {f[:-1]}')
    logger.info(f'Creation de la table frame dans la bdd {mysql_database}')
    mycursor = mycnx.cursor()
    mycursor.execute(f'CREATE TABLE IF NOT EXISTS frame ( {f[:-1]} );')
    mycursor.close()

  except Exception as exc:
    logger.error(f'Erreur de connexion à mysql: {exc}')
    raise SystemExit(1)

  # Démarrage du thread d'envoi vers mysql
  logger.info(f'Démarrage du thread d\'envoi vers mysql')
  send_mysql_thread = Process(target=_send_data_to_mysql, args=(mycnx, myframe_queue,), daemon=True)
  send_mysql_thread.start()
  return mycnx, send_mysql_thread


#################################################################################################
# mqtt-client

def on_connect(client, userdata, flags, rc):
  # For paho-mqtt 2.0.0, you need to add the properties parameter.
  # def on_connect(client, userdata, flags, rc, properties):
  if rc == 0:
    logger.info(f"Connected to MQTT Broker {mqtt_host}:{mqtt_port} on {client}")
  else:
    logger.error(f"Failed to connect to {mqtt_host}:{mqtt_port} on {client}, return code: {rc}")
  # Set Connecting Client ID


def on_publish(client, userdata, rc):
  # Qos=0 fire and firget
  # Qos=1 at least once
  # Qos=2 only once
  if userdata != None and rc != 0:
    logger.error(f'Error ({rc}) publishing data: {userdata} on client {client}')
  pass


def on_disconnect(client, userdata, rc):
  if userdata != None and rc != 0:
    logger.debug(f"return code: {rc}, client disconnected: {userdata} on client {client} ")
  pass


def create_mqtt_client_and_thread(mqtt_host: str = None, mqtt_port: int = 1883, mqtt_username: str = "",
                                  mqtt_password: str = "",
                                  mqtt_topic: str = "mytopic", mqtt_qos: int = 0, mqtt_retain: bool = False,
                                  myframe_queue: Queue = None, mytls: {} = None):
  logger.debug(f'MQTT Broker {mqtt_username}@{mqtt_host}:{mqtt_port}')

  client = mqtt_client.Client(f'linky-teleinfo-{random.randint(0, 1000)}')
  # For paho-mqtt 2.0.0, you need to set callback_api_version.
  # client = mqtt_client.Client(client_id=client_id, callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
  client.on_connect = on_connect
  client.on_publish = on_publish
  client.on_disconnect = on_disconnect

  logger.debug(f'mytls: {mytls}')
  ca_certs = mytls['mqtt_tls_ca']
  certfile = mytls['mqtt_tls_client_certificate']
  keyfile = mytls['mqtt_tls_client_key']
  keyfile_password = mytls['mqtt_tls_client_password']
  insecure = mytls['mqtt_tls_insecure']
  tls_enabled = mytls['mqtt_tls'].lower() in ('1','t','true')

  if tls_enabled:
    if os.path.exists(keyfile):
      # Connect to the MQTT broker using a client certificate
      client.tls_set(ca_certs=ca_certs,
                     certfile=certfile,
                     keyfile=keyfile,
                     keyfile_password=keyfile_password,
                     tls_version=ssl.PROTOCOL_TLSv1_2)
    else:
      # Connect to the MQTT broker using a username/password with tls
      client.tls_set(ca_certs=ca_certs, tls_version=ssl.PROTOCOL_TLSv1_2)
      client.username_pw_set(mqtt_username, mqtt_password)  # specify the username and password

    client.tls_insecure_set(insecure)
  else:
    # Connect to the MQTT broker using a username/password
    client.username_pw_set(mqtt_username, mqtt_password)

  client.connect(mqtt_host, mqtt_port)
  client.reconnect_delay_set(min_delay=5, max_delay=5)


# Démarrage du thread d'envoi vers mysql
  logger.info(f'Démarrage du thread d\'envoi vers mqtt #{mqtt_topic}')
  send_mqtt_thread = Process(target=_send_data_to_mqtt,
                             args=(client, myframe_queue, mqtt_topic, mqtt_qos, mqtt_retain), daemon=True)
  send_mqtt_thread.start()

  return client, send_mqtt_thread


#################################################################################################"
#################################################################################################"
#################################################################################################"


def _handler(signum, frame):
  logger.info('Programme interrompu par CTRL+C')
  if influxdb_send_data:
    write_client.close()
    frame_influxdb_queue.close()
    send_influx_thread.join(5)

  if mysql_send_data:
    mysql_client.close()
    frame_mysql_queue.close()
    send_mysql_thread.join(5)

  if mqtt_send_data:
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    frame_mqtt_queue.close()
    send_mqtt_thread.join(5)

  raise SystemExit(0)


def _checksum(data, checksum):
  """Vérifie la somme de contrôle du groupe d'information. Réf Enedis-NOI-CPT_54E, page 14."""
  s1 = sum([ord(c) for c in data])
  s2 = (s1 & 0x3F) + 0x20
  return (checksum == chr(s2))


def _send_frames_to_influx(influxdb_frame_queue: Queue = None, influxdb_bucket=None, write_client=None):
  """Ecrit les mesures dans un bucket InfluxDB."""
  logger.debug(f'Thread d\'envoi vers InfluxDB démarré')
  if write_client is None:
    logger.error(f'influxdb client is not defined')
    influxdb_send_data = False
  else:
    influxdb_send_data = True

  while True and influxdb_send_data:
    # Récupère une trame dans la file d'attente
    frameinflux = influxdb_frame_queue.get()
    if 'TIME' in frameinflux.keys():
      ftime = frameinflux.pop('TIME')
      logger.debug(f'frame: {frameinflux}, time: {ftime}')
    else:
      logger.error(f'no Time in frameinflux')
      logger.debug(f'frame: {frameinflux}')

    record = []
    for measure, value in frameinflux.items():
      point = Point(measure).field('value', value).time(ftime)
      record.append(point)

    # Envoie vers InfluxDB et ré-essaie en boucle tant que cela ne fonctionne pas
    logger.info(f'Ecriture dans InfluxDB')
    written = False
    x = 0
    while not written:
      try:
        write_client.write(bucket=influxdb_bucket, record=record)
        written = True
      except ApiException as exc:
        if exc.status == 404:
          logger.error(f'Le bucket {influxdb_bucket} n\'existe pas')
        elif exc.status == 403:
          logger.error(f'Permissions insufissantes pour écrire dans {influxdb_bucket}')
        else:
          logger.error(f'Erreur lors de l\'écriture dans {influxdb_bucket}')
      except InfluxDBError as exc:
        logger.error(f'Erreur InfluxDB', exc_info=True)
      except (OSError, HTTPError) as exc:
        logger.error(f'Serveur injoignable', exc_info=True)
      finally:
        if not written:
          # Attends de plus en plus longtemps
          sleep = 2 ** x
          logger.error(f'Nouvel essai dans {sleep} seconde(s)')
          time.sleep(2 ** x)
          # Mais pas trop quand même. max = 2**10 secondes, soit environ 17 minutes
          if x < 10:
            x += 1

    # influxdb_frame_queue.task_done()


def _send_data_to_mysql(mycnx: PooledMySQLConnection = None, mysqlframe_queue=None):
  """
  /  enregistre la puissance instantanée en V.A et en W
  :return:
  """

  while True:
    framemysql = mysqlframe_queue.get()
    tcolumns = ""
    tvalues = []
    tformat = ""
    mysql_value = ""
    if len(framemysql) > 0:
      for label, value in framemysql.items():
        tlabel = label.replace('+', 'PLUS').replace('-', 'MIN').strip()
        tcolumns += f'{tlabel},'
        tformat += "%s,"
        gui = ""
        if label.lower() == 'DATE':
          mysql_value = linky_decode_date(value)
        else:
          mysql_value = re.sub(r"\s+", " ", value)
        if MYSQL_DB_DATATYPE[tlabel][0].lower().startswith("varchar") or MYSQL_DB_DATATYPE[tlabel][
          0].lower() == "datetime":
          gui = ''
        tvalues.append(f'{gui}{mysql_value.strip()}{gui}')
      logger.debug(f'tcolumns: {tcolumns[:-1]}, tvalues: {tvalues}')
      try:
        insert_stmt = (
          f'INSERT INTO frame ({tcolumns[:-1]}) '
          f'VALUES ({tformat[:-1]})'
        )
        with mycnx.cursor() as mycursor:
          mycursor.execute(insert_stmt, tvalues)
        mycnx.commit()
      except mysql.connector.errors.ProgrammingError as exc:
        logger.error(f'Erreur MySQL insert: {tcolumns} ## {tvalues}')
        logger.error(f'Erreur MySQL insert: {exc}', exc_info=True)
      except Exception as e:
        logger.error(f'Erreur MySQL: {e}', exc_info=True)


def format_payload_for_teleinfo_jeedom(frame: {} = {}) -> str:
  output_string = {}
  payload = {k: v for k, v in frame.items()}
  output_string['TIC'] = payload
  logger.debug(f'output_string: {output_string}')
  return json.dumps(output_string)


def _send_data_to_mqtt(mymqttclient: mqtt_client = None, myframe_queue: Queue = None, mqtt_topic: str = "linky",
                       mqtt_qos: int = 0, mqtt_retain: bool = False):
  while True:
    framemqtt = myframe_queue.get()

    record = {}
    if 'TIME' in framemqtt.keys():
      ftime = framemqtt['TIME']
      # logger.debug(f'frame: {framemqtt}, time: {ftime}')
    else:
      logger.error(f'no Time in framemqtt')
      # logger.debug(f'frame: {framemqtt}')

    # la trame n'est pas vide
    if len(framemqtt) > 1:
      # mise au format pour jeedom
      if mqtt_teleinfo4jeedom:
        payload = format_payload_for_teleinfo_jeedom(framemqtt)
      else:
        # envoi brut de la trame
        payload = json.dumps(framemqtt)

      logger.debug(f'qos: {mqtt_qos}, retain: {mqtt_retain}, 4jeedom: {mqtt_teleinfo4jeedom}, payload: {payload}')
      mymqttclient.publish(mqtt_topic, payload=payload, qos=mqtt_qos, retain=mqtt_retain)


#####################################################################################################################
def linky_decode_date(value: str = ""):
  # E250603110050
  if len(value) < len("E250603110050"):
    logger.error(f'donnée incorrecte: {value} pas decodable SYYMMDDHHSS')
    return datetime.now()
  try:
    saison = value[0]
    annee = 2000 + int(value[1:3])
    mois = int(value[3:5])
    jour = int(value[5:7])
    heure = int(value[7:9])
    minute = int(value[9:11])
    sec = int(value[11:])
    dt = datetime(year=annee, month=mois, day=jour, hour=heure, minute=minute, second=sec)
  except ValueError as e:
    logger.debug(f'value:{value} => {e}')
    dt = datetime.now()

  logger.debug(f'val: {value}, s: {saison}, a:{annee}, m:{mois}, j:{jour}, h:{heure}, m:{minute}, s:{sec}, dt: {dt}')

  if saison in ['e', 'h', ' ']:
    logger.error(f'Compteur en mode dégradé pour l\'heure: {saison}')
  return datetime(year=annee, month=mois, day=jour, hour=heure, minute=minute, second=sec)


def linky_decode_status(hex_str: str = ""):
  if len(hex_str) != 8:
    raise ValueError("Le registre doit contenir exactement 8 caractères hexadécimaux.")

  # Conversion en entier
  registre = int(hex_str, 16)
  resultats = {}

  # Bit 0 : contact sec
  contact_sec = (registre >> 0) & 0b1
  resultats['Contact sec'] = 'fermé' if contact_sec == 0 else 'ouvert'

  # Bits 1 à 3 : organe de coupure
  organe_coupure = (registre >> 1) & 0b111
  raisons_coupure = {
    0: 'fermé',
    1: 'ouvert sur surpuissance',
    2: 'ouvert sur surtension',
    3: 'ouvert sur délestage',
    4: 'ouvert sur ordre CPL ou Euridis',
    5: 'ouvert sur surchauffe (courant > courant max)',
    6: 'ouvert sur surchauffe (courant < courant max)'
  }
  resultats['Organe de coupure'] = raisons_coupure.get(organe_coupure, 'inconnu')

  # Bit 4 : état du cache-bornes distributeur
  cache_bornes = (registre >> 4) & 0b1
  resultats["État du cache-bornes distributeur"] = 'fermé' if cache_bornes == 0 else 'ouvert'

  # Bit 5 : surtension sur une des phases
  surtension = (registre >> 5) & 0b1
  resultats['Surtension'] = 'pas de surtension' if surtension == 0 else 'surtension détectée'

  # Bit 6 : dépassement de puissance de référence
  depassement = (registre >> 6) & 0b1
  resultats['Dépassement puissance'] = 'pas de dépassement' if depassement == 0 else 'dépassement en cours'

  # Bit 7 : fonctionnement producteur / consommateur
  role = (registre >> 7) & 0b1
  resultats['Fonctionnement'] = 'consommateur' if role == 0 else 'producteur'

  # Bit 8 : sens de l’énergie active
  sens_energie = (registre >> 8) & 0b1
  resultats["Sens de l'énergie active"] = 'énergie active positive' if sens_energie == 0 else 'énergie active négative'

  # Bits 9 à 10 : tarif en cours (sur contrat de fourniture)
  tarif_fourniture = (registre >> 9) & 0b11
  index_mapping = {
    0: "Index 1",
    1: "Index 2",
    2: "Index 3",
    3: "Index 4",
    4: "Index 5",
    5: "Index 6",
  }
  resultats['Tarif en cours (fourniture)'] = index_mapping.get(tarif_fourniture, 'inconnu')

  # Bits 11 à 13 : ignorés (non utilisés)

  # Bits 14-15 : tarif en cours (contrat distributeur)
  tarif_distributeur = (registre >> 14) & 0b11
  resultats['Tarif en cours (distributeur)'] = index_mapping.get(tarif_distributeur, 'inconnu')

  # Bit 16 : horloge dégradée
  horloge = (registre >> 16) & 0b1
  resultats["Horloge"] = "correcte" if horloge == 0 else "mode dégradée"

  # Bit 17 : sortie télé-information
  teleinfo = (registre >> 17) & 0b1
  resultats["Sortie télé-information"] = "mode historique" if teleinfo == 0 else "mode standard"

  # Bits 18 : non utilisé

  # Bits 19-20 : sortie communication Euridis
  euridis = (registre >> 19) & 0b11
  euridis_mapping = {
    0b00: "désactivée",
    0b01: "activée sans sécurité",
    0b11: "activée avec sécurité"
  }
  resultats["Communication Euridis"] = euridis_mapping.get(euridis, "inconnu")

  # Bits 21-22 : statut CPL
  cpl_statut = (registre >> 21) & 0b11
  cpl_mapping = {
    0b00: "New/Unlock",
    0b01: "New/Lock",
    0b10: "Registered"
  }
  resultats["Statut CPL"] = cpl_mapping.get(cpl_statut, "inconnu")

  # Bit 23 : synchronisation CPL
  sync_cpl = (registre >> 23) & 0b1
  resultats["Synchronisation CPL"] = "non synchronisé" if sync_cpl == 0 else "synchronisé"

  # Bits 24-25 : couleur du jour (Tempo)
  couleur_jour = (registre >> 24) & 0b11
  couleur_mapping = {
    0: "Pas d’annonce",
    1: "Bleu",
    2: "Blanc",
    3: "Rouge"
  }
  resultats["Couleur du jour (Tempo)"] = couleur_mapping.get(couleur_jour, "inconnu")

  # Bits 26-27 : couleur du lendemain
  couleur_demain = (registre >> 26) & 0b11
  resultats["Couleur du lendemain (Tempo)"] = couleur_mapping.get(couleur_demain, "inconnu")

  # Bits 28-29 : préavis pointes mobiles
  preavis_pm = (registre >> 28) & 0b11
  preavis_mapping = {
    0: "pas de préavis en cours",
    1: "préavis PM1 en cours",
    2: "préavis PM2 en cours",
    3: "préavis PM3 en cours"
  }
  resultats["Préavis pointe mobile"] = preavis_mapping.get(preavis_pm, "inconnu")

  # Bits 30-31 : pointe mobile en cours
  pm_cours = (registre >> 30) & 0b11
  pm_mapping = {
    0: "pas de pointe mobile",
    1: "PM1 en cours",
    2: "PM2 en cours",
    3: "PM3 en cours"
  }
  resultats["Pointe mobile en cours"] = pm_mapping.get(pm_cours, "inconnu")
  return resultats


# -------------------------------------------------------------------------------------------------------------
# |                                 Etendue d'un groupe d'information                                         |
# -------------------------------------------------------------------------------------------------------------
# | LF (0x0A) | Champ 'étiquette' | Séparateur* | Champ 'donnée' | Séparateur* | Champ 'contrôle' | CR (0x0D) |
# -------------------------------------------------------------------------------------------------------------
#             | Etendue checksum mode n°1                        |                                            |
# -------------------------------------------------------------------------------------------------------------
#             | Etendue checksum mode n°2                                      |                              |
# -------------------------------------------------------------------------------------------------------------
#

def process_teleinfo(bytes: bytes = None):
  # Initialisation d'une trame vide
  frame = dict()

  # un caractère "Line Feed" LF (0x0 A) indiquant le début du groupe => split sur ce separateur.
  datasets = list(filter(lambda x: len(x) > 1, bytes.split(b'\n')))
  tagsdataset = list(map(lambda x: x.decode('ascii').strip('\r').split()[0], datasets))

  logger.debug(f'tagsdataset: {tagsdataset}')

  logger.debug(f'#datasets: {len(datasets)}')
  for i, dataset in enumerate(datasets):
    # un caractère "Carriage Return" CR (0x0 D) indiquant la fin du groupe d'information => suppression du cr
    str_dataset = dataset.decode('ascii').strip('\r')
    logger.debug(f'datasets[{i}]: {str_dataset}')

    # Identification du séparateur en vigueur (espace ou tabulation) #bug in PFJOUR+1
    separator = str_dataset[-2] if str_dataset[-2] in ['\t', ' '] else '\t'
    splitted_dataset = str_dataset.split(separator)
    key = splitted_dataset[0]
    idx = 1
    if len(splitted_dataset) < 2:
      logger.error(f'format incorrect de la trame, nb: {len(splitted_dataset)}<2, str: {dataset.decode("ascii")}')
      return None
    # Horodatage présent, on décale
    if len(splitted_dataset) > 3:
      idx = 2
    val = splitted_dataset[idx]
    # pas de donnée pour date mais un horodatage
    if key == 'DATE':
      val = splitted_dataset[idx - 1]

    checksum = splitted_dataset[idx + 1][0]

    # Est-ce une étiquette qui nous intéresse ?
    if key in linky_keys or linky_keys[0] == "ALL":
      # logger.debug(f'captured decoded key #{i}: {key}={val}')

      # Vérification de la somme de contrôle
      if key in linky_ignore_checksum_for_keys or _checksum(str_dataset[0:-1], checksum):
        if key == 'STGE':
          decoded_status = linky_decode_status(val)
          logger.info(f'status: {', '.join([f'{k}={v}' for k, v in decoded_status.items()])}')
          # val = json.dumps(decoded_status)
        if key in ['NGTF', 'MSG1', 'LTARF', 'BASE']:
          # suppression des doubles espaces
          val = re.sub(r"\s+", " ", val)
        # Ajout de la valeur
        frame[key] = val
      else:
        logger.warning(f'Somme de contrôle erronée pour {key}, checksum: {checksum} / dataset: {dataset}')

  tagsprocessed = frame.keys()
  errorstags = [x for x in tagsdataset if x not in tagsprocessed]
  if len(errorstags) > 0:
    logger.error(f'tags en erreurs: {errorstags}')

  num_keys = len(frame)
  if 'SINSTS' in frame.keys():
    sinsts = frame["SINSTS"]
  else:
    sinsts = "vide"
  if 'SMAXSN' in frame.keys():
    smaxsn = frame["SMAXSN"]
  else:
    smaxsn = "vide"

  logger.info(
    f'Trame reçue ({num_keys} étiquettes traités, sinsts: {sinsts}, SMAXSN: {smaxsn}, ({len(tagsdataset)}-{len(tagsprocessed)}={len(errorstags)}, {errorstags})')
  # for i,(k,v) in enumerate(frame.items()):
  #  logger.debug(f'frame[{i}]: {k}={v}')

  # Horodatage de la trame reçue
  frame['TIME'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
  logger.debug(f'FRAME: {frame}')

  # Ajout à la queue d'envoi vers InfluxDB
  if influxdb_send_data:
    frame_influxdb_queue.put(frame)
    if frame_influxdb_queue.qsize() > 1:
      logger.debug(f'influxdb queue: {frame_influxdb_queue.qsize()}')

  # Ajout à la queue d'envoi vers mysql
  if mysql_send_data:
    frame_mysql_queue.put(frame)
    if frame_mysql_queue.qsize() > 1:
      logger.warning(f'mysql queue: {frame_mysql_queue.qsize()}')

  # Ajout à la queue d'envoi vers mqtt
  if mqtt_send_data:
    frame_mqtt_queue.put(frame)
    if frame_mqtt_queue.qsize() > 1:
      logger.warning(f'mqtt queue: {frame_mqtt_queue.qsize()}')


def linky(log_level=logging.INFO):
  logger = logging.getLogger('linky')
  logger.setLevel(log_level)
  # Ouverture du port série
  try:
    baudrate = 1200 if linky_legacy_mode else 9600
    logger.info(f'Ouverture du port série {raspberry_stty_port} à {baudrate} Bd')
    ser = serial.Serial(port=f'/dev/{raspberry_stty_port}',
                        baudrate=baudrate,
                        parity=serial.PARITY_EVEN,
                        stopbits=serial.STOPBITS_ONE,
                        bytesize=serial.SEVENBITS,
                        timeout=None)

    # Boucle pour partir sur un début de trame
    logger.info(f'linky_keys: {linky_keys}, ignore checksum for keys: {linky_ignore_checksum_for_keys}')
    logger.info('Attente d\'une première trame...')
    while True:
      error = 0
      # not_wanted_line = ser.read_until(START_FRAME)
      # logger.debug(f'nouvelle trame trouvée, ignore: {not_wanted_line}')
      current_bytes = ser.read_until(STOP_FRAME)  # lecture jusqu a la fin de la trame
      idx_start = current_bytes.find(START_FRAME)  # Recherche du caractère de début de trame, c'est-à-dire STX 0x02
      idx_stop = current_bytes.find(STOP_FRAME,
                                    idx_start)  # Recherche du caractère de fin de trame, c'est-à-dire STX 0x03
      # logger.debug(f'current_bytes: {idx_start},{idx_start},{current_bytes}')
      if idx_start == -1 or idx_stop == -1 or idx_start > idx_stop:
        logger.info(f'incomplete frame: start:{idx_start}, end: {idx_stop} , content: {current_bytes}')
        error = 1
        wanted_bytes_line = ['']
      else:
        # extract payload
        wanted_bytes_line = current_bytes[idx_start + 1:idx_stop]
        # nouveau dataset demarre avec un Line Feed (LF) character (0x0A, \n)
      if wanted_bytes_line[0] != 0x0a and error == 0:
        logger.error(
          f'début incorrect de trame, ne demarre pas avec un LF: {wanted_bytes_line[0]}, {wanted_bytes_line}')
        error = 1
      elif wanted_bytes_line[-1] not in [0x0d, 0x03] and error == 0:
        # dataset termine avec CR(10) sauf si bug: Carriage Return (CR) character (0x0D, \r)
        logger.error(f'fin incorrecte de trame, ne finit pas avec un CR: {wanted_bytes_line[-1]}, {wanted_bytes_line}')
        error = 1
      if error == 0:
        # remove terminal \x03
        process_teleinfo(wanted_bytes_line[0:-1])
        time.sleep(linky_sleep_interval)
  #      else:
  #        logger.error(f'Cannot process: {wanted_bytes_line}')

  except serial.SerialException as exc:
    if exc.errno == 13:
      logger.error(f'Erreur de permission sur le port série: {exc}')
      logger.error('Avez-vous ajouté l\'utilisateur au groupe dialout ?')
      logger.error('  $ sudo usermod -G dialout $USER')
      logger.error(
        'Vous devez vous déconnecter de votre session puis vous reconnecter pour que les droits prennent effet.')
    else:
      logger.error(f'Erreur lors de l\'ouverture du port série : {exc}')
    ser.close()
    raise SystemExit(1)

  except termios.error as e:
    logger.error(f'Erreur lors de la configuration du port série: {e}')
    if raspberry_stty_port == '/dev/ttyS0':
      logger.error('Essayez d\'utiliser /dev/ttyAMA0 plutôt que /dev/ttyS0')
    ser.close()
    raise SystemExit(1)

  finally:
    ser.close()


if __name__ == '__main__':

  # Creation du logger
  logging.basicConfig(format='%(asctime)s;%(levelname)s:%(name)s:%(funcName)s - %(lineno)s:%(message)s',
                      level=logging.INFO)

  logger.info('Démarrage Linky Téléinfo')

  # parser arguments
  # argParser
  parser = argparse.ArgumentParser(description='read serial port from linky and send to databases')
  parser.add_argument('-c', '--conf', action='store', help='get conf from <file>')
  parser.add_argument('-v', '--verbose', action='store_true', help='verbose mode')
  parser.add_argument('-q', '--quiet', action='store_true', help='logs error only')
  args = parser.parse_args()
  log_level = logger.info

  if args.conf:
    conffile = args.conf
  else:
    conffile = './config/config.yml'

  # Capture élégamment une interruption par CTRL+C
  signal.signal(signal.SIGINT, _handler)

  # Configuration du logger en mode debug
  debug = os.getenv('DEBUG', False)
  log_level = logging.INFO
  if args.verbose or debug:
    log_level = logging.DEBUG
  if args.quiet:
    log_level = logging.ERROR
  # logging.getLogger().setLevel(log_level)
  logger.setLevel(log_level)
  logger.debug(f'log_level: {log_level}, debug: {debug}')

  linky_location = os.getenv('CITY', 'Paris')
  linky_legacy_mode = os.getenv('LEGACY', False) in ('true', '1', 't')
  linky_ignore_checksum_for_keys = os.getenv('IGNORE_KEYS_CHEKSUM', '[]')
  linky_keys = list(map(lambda  x:x.strip(), os.getenv('KEYS', "ISOUSC BASE IINST").split(',')))
  #linky_keys = [ x.strip() for x in os.getenv('KEYS', "ISOUSC BASE IINST").split(',') ]
  linky_sleep_interval = int(os.getenv('SLEEP_INTERVAL', DEFAULT_INTERVAL))

  raspberry_stty_port = os.getenv('PORT', 'ttyS0').replace("/dev/", "")

  # exporters
  influxdb_send_data = os.getenv('INFLUX_SEND', 'false').lower() in ('true', '1', 't')
  mysql_send_data = os.getenv('MYSQL_SEND', 'false') in ('true', '1', 't')
  mqtt_send_data = os.getenv('MQTT_SEND', 'False') in ('true', '1', 't')
  mqtt_teleinfo4jeedom = os.getenv('MQTT_4JEEDOM', 'False') in ('true', '1', 't')

  # prepare needed informations
  write_client = None
  if influxdb_send_data:
    logger.debug(f'influxdb_send_data: {influxdb_send_data}, {type(influxdb_send_data)}')
    influxdb_url = os.getenv('INFLUX_URL', '')
    influxdb_bucket = os.getenv('INFLUX_BUCKET', '')
    influxdb_token = os.getenv('INFLUX_TOKEN', '')
    influxdb_org = os.getenv('INFLUX_ORG', 'org')
    # Création d'une queue FIFO pour stocker les données
    frame_influxdb_queue = Queue()
    write_client, send_influx_thread = create_influxdb_client_and_thread(influxdb_url=influxdb_url,
                                                                         influxdb_token=influxdb_bucket,
                                                                         influxdb_org=influxdb_org,
                                                                         linky_location=linky_location,
                                                                         myframe_queue=frame_influxdb_queue)

  if mysql_send_data:
    mysql_host = os.getenv('MYSQL_HOST', 'localhost')
    mysql_port = int(os.getenv('MYSQL_PORT', 3306))
    mysql_username = os.getenv('MYSQL_USERNAME', '')
    mysql_password = os.getenv('MYSQL_PASSWORD', '')
    mysql_database = os.getenv('MYSQL_DB', 'linky')
    # Création d'une queue FIFO pour stocker les données
    frame_mysql_queue = Queue()
    mysql_client, send_mysql_thread = create_mysql_client_and_thread(mysql_host=mysql_host, mysql_port=mysql_port,
                                                                     mysql_username=mysql_username,
                                                                     mysql_password=mysql_password,
                                                                     mysql_database=mysql_database,
                                                                     myframe_queue=frame_mysql_queue)
  if mqtt_send_data:
    mqtt_host = os.getenv('MQTT_HOST', 'localhost')
    mqtt_port = int(os.getenv('MQTT_PORT', 1883))
    mqtt_username = os.getenv('MQTT_USERNAME', '')
    mqtt_password = os.getenv('MQTT_PASSWORD', '')
    mqtt_topic = os.getenv('MQTT_TOPIC', 'linky')
    mqtt_retain = os.getenv('MQTT_RETAIN', False) in ('true', '1', 't')
    mqtt_qos = int(os.getenv('MQTT_QOS', 0))
    mytls = dict()
    mytls['mqtt_tls'] = os.getenv('MQTT_TLS', False)
    mytls['mqtt_tls_insecure'] = os.getenv('MQTT_TLS_INSECURE', 'False') in ('true', '1', 't')
    mytls['mqtt_tls_ca'] = os.getenv('MQTT_TLS_CA', '')
    mytls['mqtt_tls_client_certificate'] = os.getenv('MQTT_TLS_CLIENT_CERT', '')
    mytls['mqtt_tls_client_key'] = os.getenv('MQTT_TLS_CLIENT_KEY', '')
    mytls['mqtt_tls_client_password'] = os.getenv('MQTT_TLS_CLIENT_PASSWORD', '')
    # Création d'une queue FIFO pour stocker les données
    frame_mqtt_queue = Queue()
    mymqtt_client, send_mqtt_thread = create_mqtt_client_and_thread(mqtt_host=mqtt_host, mqtt_port=mqtt_port,
                                                                  mqtt_username=mqtt_username,
                                                                  mqtt_password=mqtt_password,
                                                                  mqtt_topic=mqtt_topic, mqtt_qos=mqtt_qos,
                                                                  mqtt_retain=mqtt_retain,
                                                                  myframe_queue=frame_mqtt_queue, mytls=mytls)
    mymqtt_client.loop_start()
  # Lance la boucle infinie de lecture de la téléinfo
  linky(log_level=log_level)
