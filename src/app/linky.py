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

START_FRAME = b'\x02'  # STX, Start of Text
STOP_FRAME = b'\x03'  # ETX, End of Text


#################################################################################################
# prepare connection to db
def create_influxdb_client_and_thread(influxdb_url, influxdb_token, influxdb_org, linky_location, influxdb_bucket,
                                      myframe_queue):
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

  # Démarrage du thread d'envoi vers InfluxDB
  logger.debug(f'Démarrage du thread d\'envoi vers InfluxDB')
  send_influx_thread = Process(target=_send_frames_to_influx, args=(myframe_queue, influxdb_bucket,),
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
  logger.debug(f'Démarrage du thread d\'envoi vers mysql')
  send_mysql_thread = Process(target=_send_data_to_mysql, args=(mycnx, myframe_queue,), daemon=True)
  send_mysql_thread.start()
  return mycnx, send_mysql_thread


#################################################################################################"
def create_mqtt_client_and_thread(mqtt_host, mqtt_port, mqtt_username, mqtt_password, mqtt_topic, myframe_mqtt_queue):
  def on_connect(client, userdata, flags, rc):
    # For paho-mqtt 2.0.0, you need to add the properties parameter.
    # def on_connect(client, userdata, flags, rc, properties):
    if rc == 0:
      logger.info(f"Connected to MQTT Broker {mqtt_host}:{mqtt_port} on {mqtt_topic}")
    else:
      logger.error(f"Failed to connect to {mqtt_host}:{mqtt_port} on {mqtt_topic}, return code: {rc}")
    # Set Connecting Client ID

  client = mqtt_client.Client(f'linky-teleinfo-{random.randint(0, 1000)}')
  # For paho-mqtt 2.0.0, you need to set callback_api_version.
  # client = mqtt_client.Client(client_id=client_id, callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
  client.username_pw_set(mqtt_username, mqtt_password)
  client.on_connect = on_connect
  client.connect(mqtt_host, mqtt_port)

  # Démarrage du thread d'envoi vers mysql
  logger.debug(f'Démarrage du thread d\'envoi vers mqtt')
  send_mqtt_thread = Process(target=_send_data_to_mqtt, args=(client, myframe_mqtt_queue), daemon=True)
  send_mqtt_thread.start()

  return client, send_mqtt_thread


#################################################################################################"
#################################################################################################"
#################################################################################################"


def _handler(signum, frame):
  logger.info('Programme interrompu par CTRL+C')
  if influxdb_send_data:
    write_client.close()

  if mysql_send_data:
    mysql_client.close()

  if mqtt_send_data:
    mqtt_client.loop_stop()

  raise SystemExit(0)


def _checksum(key, val, separator, checksum, linky_checksum_method):
  """Vérifie la somme de contrôle du groupe d'information. Réf Enedis-NOI-CPT_02E, page 19."""
  data = f'{key}{separator}{val}'
  if linky_checksum_method == 2:
    data += separator
  s = sum([ord(c) for c in data])
  s = (s & 0x3F) + 0x20
  return (checksum == chr(s))


def _send_frames_to_influx(influxdb_frame_queue: Queue = None, influxdb_bucket=None, write_client=None):
  """Ecrit les mesures dans un bucket InfluxDB."""
  logger.debug(f'Thread d\'envoi vers InfluxDB démarré')

  while True:
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
    #logger.debug(f'items: {record}')
    mycursor = mycnx.cursor()
    mycursor.execute('''CREATE TABLE IF NOT EXISTS puissance
                        (
                          timestamp INTEGER,
                          hchp      TEXT,
                          va        REAL,
                          iinst     REAL,
                          watt      REAL
                        );''')  # cree la table puissance si elle n existe pas
    # $datas = array();
    # $datas['timestamp'] = time();
    # $datas['hchp']      = substr($trame['PTEC'],0,2); // indicateur heure pleine/creuse, on garde seulement les carateres HP (heure pleine) et HC (heure creuse)
    # $datas['va']        = preg_replace('`^[0]*`','',$trame['PAPP']); // puissance en V.A, on supprime les 0 en debut de chaine
    # $datas['iinst']     = preg_replace('`^[0]*`','',$trame['IINST']); // intensité instantanée en A, on supprime les 0 en debut de chaine
    # $datas['watt']      = $datas['iinst']*220; // intensite en A X 220 V

    # if($db->busyTimeout(5000)){ // stock les donnees
    # $db->exec("INSERT INTO puissance (timestamp, hchp, va, iinst, watt) VALUES (".$datas['timestamp'].", '".$datas['hchp']."', ".$datas['va'].", ".$datas['iinst'].", ".$datas['watt'].");");
    # }

    # return 1;
    # }

    # //
    # //  enregistre la consommation en Wh
    #                                  //
    #                                  function handleConso () {
    # global $sqlite;
    # $db = new SQLite3($sqlite);
    # $db->exec('CREATE TABLE IF NOT EXISTS conso (timestamp INTEGER, total_hc INTEGER, total_hp INTEGER, daily_hc REAL, daily_hp REAL);'); // cree la table conso si elle n'existe pas

    # $trame     = getTeleinfo (); // recupere une trame teleinfo

    # $today     = strtotime('today 00:00:00');
    # $yesterday = strtotime("-1 day 00:00:00");

    # // recupere la conso totale enregistree la veille pour pouvoir calculer la difference et obtenir la conso du jour
    # if($db->busyTimeout(5000)){
    # $previous = $db->query("SELECT * FROM conso WHERE timestamp = '".$yesterday."';")->fetchArray(SQLITE3_ASSOC);
    # }
    # if(empty($previous)){
    # $previous = array();
    # $previous['timestamp'] = $yesterday;
    # $previous['total_hc']  = 0;
    # $previous['total_hp']  = 0;
    # $previous['daily_hc']  = 0;
    # $previous['daily_hp']  = 0;
    # }

    # $datas = array();
    # $datas['query']     = 'hchp';
    # $datas['timestamp'] = $today;
    # $datas['total_hc']  = preg_replace('`^[0]*`','',$trame['HCHC']); // conso total en Wh heure creuse, on supprime les 0 en debut de chaine
    # $datas['total_hp']  = preg_replace('`^[0]*`','',$trame['HCHP']); // conso total en Wh heure pleine, on supprime les 0 en debut de chaine

    # if($previous['total_hc'] == 0){
    # $datas['daily_hc'] = 0;
    # }
    # else{
    # $datas['daily_hc']  = ($datas['total_hc']-$previous['total_hc'])/1000; // conso du jour heure creuse = total aujourd'hui - total hier, on divise par 1000 pour avec un resultat en kWh
    # }

    # if($previous['total_hp'] == 0){
    # $datas['daily_hp'] = 0;
    # }
    # else{
    # $datas['daily_hp']  = ($datas['total_hp']-$previous['total_hp'])/1000; // conso du jour heure pleine = total aujourd'hui - total hier, on divise par 1000 pour avec un resultat en kWh
    # }

    # if($db->busyTimeout(5000)){ // stock les donnees
    # $db->exec("INSERT INTO conso (timestamp, total_hc, total_hp, daily_hc, daily_hp) VALUES (".$datas['timestamp'].", ".$datas['total_hc'].", ".$datas['total_hp'].", ".$datas['daily_hc'].", ".$datas['daily_hp'].");");
    # }
    # }
    # mysqlframe_queue.task_done()
  mycnx.commit()
  mycursor.close()


def _send_data_to_mqtt(mymqttclient, myframe_queue=None):
  while True:
    # handlePuissance
    framemqtt = myframe_queue.get()
    if 'TIME' in framemqtt.keys():
      ftime = framemqtt.pop('TIME')
      logger.debug(f'frame: {framemqtt}, time: {ftime}')
    else:
      logger.error(f'no Time in framemqtt')
      logger.debug(f'frame: {framemqtt}')

    record = []
    for measure, value in framemqtt.items():
      item = {measure: value}
      record.append(item)
    #logger.debug(f'items: {record}')

    # client.message('puissance', record)
    # myframe_queue.task_done()


def linky(log_level=logging.INFO):
  logger = logging.getLogger('linky')
  logger.setLevel(log_level)
  # Ouverture du port série
  try:
    baudrate = 1200 if linky_legacy_mode else 9600
    logger.info(
      f'Ouverture du port série {raspberry_stty_port} à {baudrate} Bd')
    with serial.Serial(port=raspberry_stty_port,
                       baudrate=baudrate,
                       parity=serial.PARITY_EVEN,
                       stopbits=serial.STOPBITS_ONE,
                       bytesize=serial.SEVENBITS,
                       timeout=1) as ser:

      # Boucle pour partir sur un début de trame
      logger.info(f'linky_keys: {linky_keys}')
      logger.info('Attente d\'une première trame...')
      line = ser.readline()
      while START_FRAME not in line:  # Recherche du caractère de début de trame, c'est-à-dire STX 0x02
        line = ser.readline()

      # Initialisation d'une trame vide
      frame = dict()

      # Boucle infinie
      while True:

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
        # *Le séparateur peut être SP (0x20) ou HT (0x09)

        try:
          # Lecture de la première ligne de la première trame
          line = ser.readline()

          # Décodage ASCII et nettoyage du retour à la ligne
          line_str = line.decode('ascii').rstrip()
          # logger.debug(f'Groupe d\'information brut : {line_str}')

          # Récupération de la somme de contrôle (qui est le dernier caractère de la ligne)
          checksum = line_str[-1]

          # Identification du séparateur en vigueur (espace ou tabulation)
          separator = line_str[-2]

          # Position du séparateur entre le champ étiquette et le champ données
          pos = line_str.find(separator)

          # Extraction de l'étiquette
          key = line_str[0:pos]

          # Extraction de la donnée
          val = line_str[pos + 1:-2]

          # Est-ce une étiquette qui nous intéresse ?
          if key in linky_keys:
            # logger.debug(f'captured decoded key : {key}={val}')
            # Vérification de la somme de contrôle
            if _checksum(key, val, separator, checksum, linky_checksum_method):
              # Ajout de la valeur
              frame[key] = val
            else:
              logger.warning(f'Somme de contrôle erronée pour {key}={val}')

          # Si caractère de fin de trame dans la ligne, on écrit les données dans InfluxDB
          if STOP_FRAME in line:
            num_keys = len(frame)
            logger.info(f'Trame reçue ({num_keys} étiquettes traités)')

            # Horodatage de la trame reçue
            frame['TIME'] = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

            logger.debug(f'FRAME: {frame}')
            # Ajout à la queue d'envoi vers InfluxDB
            if influxdb_send_data:
              frame_influxdb_queue.put(frame)
              if frame_influxdb_queue.qsize() >1:
                logger.debug(f'queue: {frame_influxdb_queue.qsize()}')

            # Ajout à la queue d'envoi vers mysql
            if mysql_send_data:
              frame_mysql_queue.put(frame)
              if frame_mysql_queue.qsize() > 1:
                logger.warning(f'frame_mysql_queue: {frame_mysql_queue.qsize()}')

            # Ajout à la queue d'envoi vers mqtt
            if mqtt_send_data:
              frame_mqtt_queue.put(frame)
              if frame_mqtt_queue.qsize() > 1:
                logger.warning(f'frame_mqtt_queue: {frame_mqtt_queue.qsize()}')

            # On réinitialise une nouvelle trame
            frame = dict()

        except Exception as e:
          logger.error(f'Etiquette : {key}  Donnée : {val}')
          logger.error(f'Une exception s\'est produite : {e}', exc_info=True)

  except termios.error:
    logger.error('Erreur lors de la configuration du port série')
    if raspberry_stty_port == '/dev/ttyS0':
      logger.error('Essayez d\'utiliser /dev/ttyAMA0 plutôt que /dev/ttyS0')
    raise SystemExit(1)

  except serial.SerialException as exc:
    if exc.errno == 13:
      logger.error('Erreur de permission sur le port série')
      logger.error('Avez-vous ajouté l\'utilisateur au groupe dialout ?')
      logger.error('  $ sudo usermod -G dialout $USER')
      logger.error(
        'Vous devez vous déconnecter de votre session puis vous reconnecter pour que les droits prennent effet.')
    else:
      logger.error(f'Erreur lors de l\'ouverture du port série : {exc}')
    raise SystemExit(1)


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
    linky_keys = cfg['linky'].get('keys', DEFAULT_KEYS)
    raspberry_stty_port = cfg['raspberry']['stty_port']
    influxdb_send_data = cfg['influxdb']['send_data']
    mysql_send_data = cfg['mysql']['send_data']
    mqtt_send_data = cfg['mqtt']['send_data']
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
      mqtt_database = cfg['mqtt']['database']


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
    write_client, send_influx_thread = create_influxdb_client_and_thread(influxdb_url, influxdb_token, influxdb_org,
                                                                         linky_location, frame_influxdb_queue)

  if mysql_send_data:
    # Création d'une queue FIFO pour stocker les données
    frame_mysql_queue = Queue()
    mysql_client, send_mysql_thread = create_mysql_client_and_thread(mysql_host, mysql_port, mysql_username,
                                                                     mysql_password, mysql_database, frame_mysql_queue)

  if mqtt_send_data:
    # Création d'une queue FIFO pour stocker les données
    frame_mqtt_queue = Queue()
    mqtt_client, send_mqtt_thread = create_mqtt_client_and_thread(mqtt_host, mqtt_port, mqtt_username, mqtt_password,
                                                                  mqtt_database, frame_mqtt_queue)
    mqtt_client.loop_start()

  # Lance la boucle infinie de lecture de la téléinfo
  linky(log_level=log_level)
