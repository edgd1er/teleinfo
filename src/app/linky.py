#!/usr/bin/env python3
# https://github.com/hleroy/teleinfo-linky
# -*- coding: utf-8 -*-
# __author__ = "Hervé Le Roy"
# __licence__ = "GNU General Public License v3.0"
import json
#  apt install -y python3-serial python3-mysqldb python3-influxdb
# Python 3, pré-requis : pip install PyYAML pySerial influxdb-client

# TODO:
# * Ajouter un thread séparé pour Linky
# * Afficher des informations depuis le thread principal
# * Tester plusieurs scénarios d'erreurs InfluxDB (iptables sur serveur, arrêt du serveur)

import logging
from multiprocessing import Process, Queue
import signal
import termios
import time
from datetime import datetime
import argparse
import os
import random

import serial
import yaml
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.exceptions import InfluxDBError
from influxdb_client.client.write_api import SYNCHRONOUS, PointSettings
from influxdb_client.rest import ApiException
from urllib3 import Retry
from urllib3.exceptions import HTTPError
import mysql.connector
from paho.mqtt import client as mqtt_client

logger = logging.getLogger(__name__)
LDIR = os.path.dirname(os.path.realpath(__file__))
DEFAULT_KEYS = ('ISOUSC', 'BASE', 'IINST',)
DEFAULT_CHECKSUM_METHOD = 1
DEFAULT_INTERVAL = 60

START_FRAME = b'\x02'  # STX, Start of Text
STOP_FRAME = b'\x03'  # ETX, End of Text


#################################################################################################
# prepare connection to db
def create_influxdb_client_and_thread(influxdb_url: str = "", influxdb_token: str = "", influxdb_org: str = "",
                                      linky_location: str = "Paris", influxdb_bucket: str = "",
                                      myframe_queue: Queue = None):
  # Connexion à InfluxDB
  try:
    logger.info(f'Connexion à {influxdb_url}')
    retries = Retry(connect=4, read=2, redirect=5, backoff_factor=0.5)
    influxdb_client = InfluxDBClient(url=influxdb_url,
                                     token=influxdb_token,
                                     org=influxdb_org,
                                     retries=retries)

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
def create_mysql_client_and_thread(mysql_host, mysql_port, mysql_username, mysql_password, mysql_database,
                                   myframe_queue):
  # Connexion à mysql
  try:
    # Obtention du client d'API en écriture
    logger.info(f'Connexion à {mysql_username}@{mysql_host}:{mysql_port}')
    mycnx = mysql.connector.connect(host=mysql_host, user=mysql_username, password=mysql_password,
                                    database=mysql_database)

  except Exception as exc:
    logger.error(f'Erreur de connexion à mysql: {exc}')
    raise SystemExit(1)

  # Démarrage du thread d'envoi vers mysql
  logger.info(f'Démarrage du thread d\'envoi vers mysql')
  send_mysql_thread = Process(target=_send_data_to_mysql, args=(mycnx, myframe_queue,), daemon=True)
  send_mysql_thread.start()
  return mycnx, send_mysql_thread


#################################################################################################"
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
                                  mqtt_queue: Queue = None):
  client = mqtt_client.Client(f'linky-teleinfo-{random.randint(0, 1000)}')
  # For paho-mqtt 2.0.0, you need to set callback_api_version.
  # client = mqtt_client.Client(client_id=client_id, callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
  client.username_pw_set(mqtt_username, mqtt_password)
  client.on_connect = on_connect
  client.on_publish = on_publish
  client.on_disconnect = on_disconnect
  client.connect(mqtt_host, mqtt_port)

  # Démarrage du thread d'envoi vers mysql
  logger.info(f'Démarrage du thread d\'envoi vers mqtt #{mqtt_topic}')
  send_mqtt_thread = Process(target=_send_data_to_mqtt,
                             args=(client, mqtt_queue, mqtt_topic, mqtt_qos, mqtt_retain), daemon=True)
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


def _send_data_to_mysql(mycnx=None, mysqlframe_queue=None):
  """
  /  enregistre la puissance instantanée en V.A et en W
  :return:
  """
  while True:
    # handlePuissance
    framemysql = mysqlframe_queue.get()
    if 'TIME' in framemysql.keys():
      ftime = framemysql.pop('TIME')
      logger.debug(f'frame: {framemysql}, time: {ftime}')
    else:
      logger.error(f'no Time in framemysql')
      logger.debug(f'frame: {framemysql}')

    record = []
    for measure, value in framemysql.items():
      item = {measure: value}
      record.append(item)
    # logger.debug(f'items: {record}')
    mycursor = mycnx.cursor()
    mycursor.execute('''CREATE TABLE IF NOT EXISTS frame
                        (
                          ADSC        bigint unsigned,
                          VTIC        tinyint,
                          NGTF        VARCHAR(16),
                          LTARF       VARCHAR(16),
                          EAST        VARCHAR(9),
                          EASF01      VARCHAR(9),
                          EASF02      VARCHAR(9),
                          EASF03      VARCHAR(9),
                          EASF04      VARCHAR(9),
                          EASF05      VARCHAR(9),
                          EASF06      VARCHAR(9),
                          EASF07      VARCHAR(9),
                          EASF08      VARCHAR(9),
                          EASF09      VARCHAR(9),
                          EASF10      VARCHAR(9),
                          EASD01      VARCHAR(9),
                          EASD02      VARCHAR(9),
                          EASD03      VARCHAR(9),
                          EASD04      VARCHAR(9),
                          EAIT        VARCHAR(9),
                          IRMS1       TINYINT,
                          IRMS2       TINYINT,
                          IRMS3       TINYINT,
                          URMS1       TINYINT,
                          URMS2       TINYINT,
                          URMS3       TINYINT,
                          PREF        TINYINT,
                          PCOUP       TINYINT,
                          SINSTS      TINYINT,
                          SINSTS1     TINYINT,
                          SINSTS2     TINYINT,
                          SINSTS3     TINYINT,
                          SMAXSN      TINYINT,
                          SMAXSN1     TINYINT,
                          SMAXSN2     TINYINT,
                          SMAXSN3     TINYINT,
                          SMAXSNMIN1  TINYINT,
                          CCASN       TINYINT,
                          UMOY1       TINYINT,
                          UMOY2       TINYINT,
                          UMOY3       TINYINT,
                          STGE        VARCHAR(8),
                          MSG1        VARCHAR(32),
                          PRM         VARCHAR(14),
                          RELAIS      TINYINT,
                          NTARF       TINYINT,
                          NJOURF      TINYINT,
                          NJOURFPLUS1 TINYINT,
                          PJOURFPLUS1 VARCHAR(98),
                          TIME        VARCHAR
                        )''')

    add_frame = ("INSERT into frame "
                 "(ADSC, VTIC, NGTF, LTARF, EAST,EASF01,EASF02,EASF03,EASF04,EASF05,EASF06,EASF07,EASF08,EASF09,EASF10,EASD01,EASD02," +
                 "EASD03,EASD04,EAIT,IRMS1,IRMS2,IRMS3,URMS1,URMS2,URMS3,PREF, PCOUP , SINSTS ,SINSTS1 ,SINSTS2 ,SINSTS3 ,SMAXSN ,SMAXSN1 ,SMAXSN2 ,SMAXSN3 ," +
                 "SMAXSN-1 ,CCASN ,UMOY1 ,UMOY2 ,UMOY3 ,STGE,MSG1,PRM,RELAIS ,NTARF ,NJOURF ,NJOURF+1 ,PJOURF+1,TIME)")

    # {'ADSC': '811875789290', 'VTIC': '02', 'NGTF': '      BASE      ', 'LTARF': '      BASE      ', 'EAST': '028682292', EASF01': '028682292', EASF02': '000000000', 'EASF03': '000000000', 'EASF04': '000000000', 'EASF05': '000000000', 'EASF06': '000000000', 'EASF07': '000000000', 'EASF08': '000000000', 'EASF09': '000000000', 'EASF10': '000000000', 'EASD01': '009983667', 'EASD02': '008860305', 'EASD03': '003030091', 'EASD04': '006808229', 'IRMS1': '003', 'URMS1': '235', 'PREF': '06', 'PCOUP': '06', 'SINSTS': '00650', 'STGE': '003A0001', 'MSG1': 'PAS DE          MESSAGE         ', 'PRM': '01160057870354', 'RELAIS': '000', 'NTARF': '01', 'NJOURF': '00', 'NJOURF+1': '00', 'PJOURF+1': '00008001 NONUTILE NONUTILE NONUTILE NONUTILE NONUTILE NONUTILE NONUTILE NONUTILE NONUTILE NONUTILE', 'TIME': '2025-05-30T22:50:27Z'}

  mycnx.commit()
  mycursor.close()


def format_payload_for_teleinfo_jeedom(frame: {} = dict()) -> str:
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
        # Ajout de la valeur
        frame[key] = val
      else:
        logger.warning(f'Somme de contrôle erronée pour {key}, checksum: {checksum} / dataset: {dataset}')

  num_keys = len(frame)
  logger.info(f'Trame reçue ({num_keys} étiquettes traités)')
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
    ser = serial.Serial(port=raspberry_stty_port,
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
      idx_stop = current_bytes.find(STOP_FRAME)  # Recherche du caractère de fin de trame, c'est-à-dire STX 0x03
      # logger.debug(f'current_bytes: {idx_start},{idx_start},{current_bytes}')
      if idx_start == -1 or idx_stop == -1 or idx_start > idx_stop:
        logger.info(f'incomplete frame: start:{idx_start}, end: {idx_stop} , content: {current_bytes}')
        error = 1
      # exract payload
      wanted_bytes_line = current_bytes[idx_start + 1:idx_stop]
      # nouveau dataset demarre avec un Line Feed (LF) character (0x0A, \n)
      if wanted_bytes_line[0] != 0x0a:
        logger.error(
          f'début incorrect de trame, ne demarre pas avec un LF: {wanted_bytes_line[0]}, {wanted_bytes_line}')
        error = 1
      elif wanted_bytes_line[-1] not in [0x0d, 0x03]:
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
      logger.error('Erreur de permission sur le port série')
      logger.error('Avez-vous ajouté l\'utilisateur au groupe dialout ?')
      logger.error('  $ sudo usermod -G dialout $USER')
      logger.error(
        'Vous devez vous déconnecter de votre session puis vous reconnecter pour que les droits prennent effet.')
    else:
      logger.error(f'Erreur lors de l\'ouverture du port série : {exc}')
    ser.close()
    raise SystemExit(1)

  except termios.error:
    logger.error('Erreur lors de la configuration du port série')
    if raspberry_stty_port == '/dev/ttyS0':
      logger.error('Essayez d\'utiliser /dev/ttyAMA0 plutôt que /dev/ttyS0')
    ser.close()
    raise SystemExit(1)

  finally:
    ser.close()


def read_conf(conffile: str = "", debug: bool = False):
  try:
    with open(conffile, "r") as f:
      cfg = yaml.load(f, Loader=yaml.SafeLoader)
  except FileNotFoundError:
    logger.error('Il manque le fichier de configuration config.yml')
    raise SystemExit(1)
  except yaml.YAMLError as exc:
    if hasattr(exc, 'problem_mark'):
      mark = exc.problem_mark
      logger.error('Le fichier de configuration comporte une erreur de syntaxe')
      logger.error(f'La position de l\'erreur semble être en ligne {mark.line + 1} colonne {mark.column + 1}')
      raise SystemExit(1)
  except (OSError, IOError):
    logger.error('Erreur de lecture du fichier config.yml. Vérifiez les permissions ?')
    raise SystemExit(1)
  except Exception:
    logging.critical('Erreur lors de la lecture du fichier de configuration', exc_info=True)
    raise SystemExit(1)
  logger.debug(f'cfg')
  return cfg


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

  # Lecture du fichier de configuration
  cfg = read_conf(conffile=conffile)

  # Configuration du logger en mode debug
  debug = cfg.get('debug', False)
  log_level = logging.INFO
  if args.verbose or debug:
    log_level = logging.DEBUG
  if args.quiet:
    log_level = logging.ERROR
  # logging.getLogger().setLevel(log_level)
  logger.setLevel(log_level)
  logger.debug(f'log_level: {log_level}, debug: {debug}, conf: {conffile}, cfg: {cfg}')

  try:
    debug = cfg.get('debug', False)
    linky_location = cfg['linky']['location']
    linky_legacy_mode = cfg['linky']['legacy_mode']
    linky_checksum_method = cfg['linky'].get('checksum_method', DEFAULT_CHECKSUM_METHOD)
    linky_ignore_checksum_for_keys = cfg['linky'].get('ignore_checksum_for_keys', '[]')
    linky_keys = cfg['linky'].get('keys', DEFAULT_KEYS)
    linky_sleep_interval = cfg['linky'].get('sleep_interval', DEFAULT_INTERVAL)

    raspberry_stty_port = cfg['raspberry']['stty_port']

    influxdb_send_data = cfg['influxdb']['send_data']

    mysql_send_data = cfg['mysql']['send_data']

    mqtt_send_data = cfg['mqtt']['send_data']
    mqtt_teleinfo4jeedom = cfg['mqtt']['teleinfo4jeedom']

    if influxdb_send_data:
      influxdb_url = cfg['influxdb']['url']
      influxdb_bucket = cfg['influxdb']['bucket']
      influxdb_token = cfg['influxdb']['token']
      influxdb_org = cfg['influxdb']['org']
    if mysql_send_data:
      mysql_host = cfg['mysql']['host']
      mysql_port = cfg['mysql']['port']
      mysql_username = cfg['mysql']['username']
      mysql_password = cfg['mysql']['password']
      mysql_database = cfg['mysql']['database']
    if mqtt_send_data:
      mqtt_host = cfg['mqtt']['host']
      mqtt_port = cfg['mqtt']['port']
      mqtt_username = cfg['mqtt']['username']
      mqtt_password = cfg['mqtt']['password']
      mqtt_topic = cfg['mqtt']['topic']
      mqtt_retain = cfg['mqtt']['retain']
      mqtt_qos = cfg['mqtt']['qos']


  except KeyError as exc:
    logger.error(f'Erreur : il manque la clé {exc} dans le fichier de configuration')
    raise SystemExit(1)
  except Exception:
    logging.critical('Erreur lors de la lecture du fichier de configuration', exc_info=True)
    raise SystemExit(1)

  logger.info(f'linky_keys: {linky_keys}')
  # frame_sqlite_queue= queue.Queue()
  # frame_mqqt_queue= queue.Queue()

  write_client = None
  # InfluxDB
  if influxdb_send_data:
    # Création d'une queue FIFO pour stocker les données
    frame_influxdb_queue = Queue()
    write_client, send_influx_thread = create_influxdb_client_and_thread(influxdb_url=influxdb_url,
                                                                         influxdb_token=influxdb_bucket,
                                                                         influxdb_org=influxdb_org,
                                                                         linky_location=linky_location,
                                                                         myframe_queue=frame_influxdb_queue)

  if mysql_send_data:
    # Création d'une queue FIFO pour stocker les données
    frame_mysql_queue = Queue()
    mysql_client, send_mysql_thread = create_mysql_client_and_thread(mysql_host=mysql_host, mysql_port=mysql_port,
                                                                     mysql_username=mysql_username,
                                                                     mysql_password=mysql_password,
                                                                     mysql_database=mysql_database,
                                                                     frame_mysql_queue=frame_mysql_queue)

  if mqtt_send_data:
    # Création d'une queue FIFO pour stocker les données
    frame_mqtt_queue = Queue()
    mqtt_client, send_mqtt_thread = create_mqtt_client_and_thread(mqtt_host=mqtt_host, mqtt_port=mqtt_port,
                                                                  mqtt_username=mqtt_username,
                                                                  mqtt_password=mqtt_password,
                                                                  mqtt_topic=mqtt_topic, mqtt_qos=mqtt_qos,
                                                                  mqtt_retain=mqtt_retain,
                                                                  mqtt_queue=frame_mqtt_queue)
    mqtt_client.loop_start()

  # Lance la boucle infinie de lecture de la téléinfo
  linky(log_level=log_level)
