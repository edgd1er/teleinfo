#!/usr/bin/env python3
import logging
import os
import time

import mysql.connector
import requests


def fetch_all_rows(subject: str = "", unit: str = "", cnx: mysql.connector = None, sql: str = ""):
  logger.info(f'Récupération des données de {subject}')
  mycursor = cnx.cursor(buffered=True, dictionary=False)
  mycursor.execute(sql)
  data = []
  for (time, value) in mycursor.fetchall():
    logger.debug(f'value: {value}, time: {time}')
    # '2015-10-20 22:24:46'
    data.append({'tag': subject, 'value': int(value), 'ts': int(time.timestamp()), 'unit': unit})
  mycursor.close()
  data.reverse()
  return data

def send_data_to_server(data: [] = None):
  try:
    for d in data:
      resp = requests.post(url, json=d)
      logger.debug(f'resp: {resp.status_code}')
  except Exception as e:
    logger.error(f'Exception: {e}')


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

  if 'TZ' in os.environ:
    time.tzset()

  # Creation du logger
  logging.basicConfig(format='%(asctime)s;%(levelname)s:%(name)s:%(funcName)s - %(lineno)s:%(message)s',
                      level=logging.INFO)
  logger = logging.getLogger(__name__)
  logger.setLevel(log_level)

  logger.info('Démarrage de l\'injection des données pour les graphs')

  # Connexion à mysql
  try:
    # Obtention du client d'API en écriture
    logger.info(f'Connexion à {mysql_username}@{mysql_host}:{mysql_port}')
    mycnx = mysql.connector.connect(host=mysql_host, user=mysql_username, password=mysql_password,
                                    database=mysql_database)

    sql1 = """select f.TIME, f.SINSTS
              from frame f
              where date_add(now(), interval '-1' hour) < f.time
                and DATE(f.`TIME`) <= DATE(now())
              order by f.time desc
              limit 1,100;"""
    res1 = fetch_all_rows(subject="Linky - VA inst", unit="VA", cnx=mycnx, sql=sql1)
    logger.debug(f'data#: {len(res1)}')
    send_data_to_server(res1)

  except Exception as exc:
    logger.error(f'Erreur de connexion à mysql: {exc}')
    raise SystemExit(1)

  finally:
    mycnx.close()
