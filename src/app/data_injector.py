#!/usr/bin/env python3
import logging
import os
import time
from datetime import datetime
from typing import Any

import mysql.connector
import requests


def fetch_all_rows(subject: str = "", unit: str = "", cnx: mysql.connector = None, sql: str = "") -> list[
  dict[Any, Any]]:
  logger.info(f'Récupération des données de {subject} sur {nbpoints} mesures')
  mycursor = cnx.cursor(buffered=True, dictionary=False)
  mycursor.execute(sql, [nbpoints])
  data = [{}]
  for (time, value) in mycursor.fetchall():
    logger.debug(f'value: {value}, time: {time}')
    if value is not None:
      # '2015-10-20 22:24:46'
      data.append({'tag': subject, 'value': int(value), 'ts': int(time.timestamp()), 'unit': unit})
  mycursor.close()
  data.reverse()
  logger.info(f'# of data: {len(data)}, expected: {nbpoints}')
  return data


def send_data_to_server(data: list[dict[Any, Any]] = None) -> None:
  # empty result dict
  res = {}
  if data is None:
    data = list()

  for d in data:
    if len(d) > 1:
      try:
        resp = requests.post(url, json=d)
        logger.debug(f'resp: {resp.status_code}, data: {d}')
        res[resp.status_code] = res.get(resp.status_code, 1) + 1
      except Exception as exc:
        logger.error(f'Exception: {exc}')
    else:
      logger.error(f'invalid data: {d}')

  tss = [x['ts'] for x in data if x.get('ts', None) is not None]
  if len(tss) > 0:
    logger.debug(f'tss: {tss}')
    mymin = datetime.fromtimestamp(min(tss))
    mymax = datetime.fromtimestamp(max(tss))
    logger.info(f'mesures envoyées (code, nb): {res} , de {mymin} à {mymax}')
  else:
    logger.info(f'Pas de mesures dans la bdd a envoyer au serveur.')


if __name__ == '__main__':

  mysql_host = os.getenv('MYSQL_HOST', 'localhost')
  mysql_port = int(os.getenv('MYSQL_PORT', 3306))
  mysql_username = os.getenv('MYSQL_USERNAME', '')
  mysql_password = os.getenv('MYSQL_PASSWORD', '')
  mysql_database = os.getenv('MYSQL_DB', 'linky')
  log_level = logging.DEBUG if os.getenv('DEBUG', 'false').lower() in ('true', 't', 'y', '1') else logging.INFO
  host = os.getenv('HTTP_IP', '0.0.0.0')
  port = os.getenv('HTTP_PORT', 8080)
  url = f"http://{host}:{port}"
  nbpoints = int(os.getenv('HTTP_NBPOINTS', 1000))
  mycnx = None

  if 'TZ' in os.environ:
    time.tzset()

  # Creation du logger
  logging.basicConfig(format='%(asctime)s;%(levelname)s:%(name)s:%(funcName)s - %(lineno)s:%(message)s',
                      level=logging.INFO)
  logger = logging.getLogger(__name__)
  logger.setLevel(log_level)

  logger.info(f'Démarrage de l\'injection des données pour les graphs ({nbpoints} mesures)')

  # Connexion à mysql
  res1 = None
  try:
    # Obtention du client d'API en écriture
    logger.info(f'Connexion à {mysql_username}@{mysql_host}:{mysql_port}')
    mycnx = mysql.connector.connect(host=mysql_host, user=mysql_username, password=mysql_password,
                                    database=mysql_database)

    sql1 = "select f.TIME, f.SINSTS from frame f order by f.time desc limit 1,%s;"
    sql2 = "select f.TIME, ceil(avg(f.SINSTS)) as `SINSTS` from frame f GROUP BY hour(f.TIME) , day(f.TIME), month(f.TIME), year(f.TIME) order by f.time desc limit 1,%s;"
    res1 = fetch_all_rows(subject="Linky - VA inst", unit="VA", cnx=mycnx, sql=sql2)

  except Exception as exc:
    logger.error(f'Erreur de connexion à mysql: {exc}')
    quit(1)

  finally:
    if mycnx is not None:
      mycnx.close()

  logger.debug(f'data#: {len(res1)}')
  send_data_to_server(res1)
