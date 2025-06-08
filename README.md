# Teleinfo

### But:
  * Récupérer l'info du TIC du compteur EDF depuis le port série
  * envoi optionnel sur mysql
  * envoi optionnel sur mqtt
  * envoi optionnel sur influxdb mais non operationnel.(pas de v2 disponible pour un rpi2, la v1.8 semble ne pas gerer les buckets)

### Testé sur:
  * rpi2 + module gpio [PITInfo](https://www.tindie.com/products/hallard/pitinfo/)
  * Lecture en continu du port série avec calage sur le début d'une trame
  * espacement des lectures paramétrables (en secondes)

Le fichier compose-dist.yml est un exemple de la conf pour avoir un système en fonction.

Un simple appel à son fournisseur d'énérgie permet de demander le passage en mode standard. (effectif le lendemain, le plus souvent)

le blog du createur du module: https://hallard.me/pitinfov12-light/

## Test du module via la port série

TIC mode historique (par défaut)

    $ screen /dev/ttyAMA0 1200,cs7

ou

    $ stty </dev/ttyAMA0

TIC mode standard

    $ screen /dev/ttyAMA0 9600,cs7,parenb,-parodd,-cstopb

La doc en anglais sur le [port série du raspberry](https://www.raspberrypi.com/documentation/computers/configuration.html#configure-uarts)

## RPI2

- supprimer les paramètres suivants : /boot/cmdline.txt
  console=ttyAMA0,115200 kgdboc=ttyAMA0,115200
- /etc/inittab :

- commentez la ligne suivante (tout en bas du fichier) en ajoutant un # devant :
  #T0:23:respawn:/sbin/getty -L ttyAMA0 115200 vt100


## RPI3+

Sur Raspberry Pi3, l’UART PL011 (full UART) du BCM2837 a été ré-alloué au WLAN/BT combo. Et le mini UART est mis à disposition des utilisateurs (sous le nom de /dev/ttyS0). Ce mini uart est sensible a la charge CPU avec pour conséquence la perte des données. il faut désactiver le bluetooth pour libérer le PL011 via raspi-config.


Pour plus d’information sur ces changements : http://spellfoundry.com/2016/05/29/configuring-gpio-serial-port-raspbian-jessie-including-pi-3/

## Construction de l'image et lancement

* docker buildx build -t edgd1er/teleinfo:latest https://github.com/edgd1er/teleinfo.git
* récupération du docker compose: `curl https://raw.githubusercontent.com/edgd1er/teleinfo/refs/heads/main/compose.dist.yml -O compose.yml`
* ajustement des variables, suppressions des parties inutiles (services et conf) selon la conf voulue (influxdb, mqtt, mysql) dans le compose.yml
* lancement: `docker compose up -d`


## Liste des étiquettes transmises par la Téléinfo Client (TIC HISTORIQUE)
```
ADCO     : Adresse du compteur
OPTARIF  : Option tarifaire choisie
ISOUSC   : Intensité souscrite
BASE     : Index option Base
HCHC     : Index Heures Creuses
HCHP     : Index Heures Pleines
EJPHN    : Index option EJP Heures Normales
EJPHPM   : Index option EJP Heures de Pointe Mobile
BBRHCJB  : Index option Tempo Heures Creuses Jours Bleus
BBRHPJB  : Index option Tempo Heures Pleines Jours Bleus
BBRHCJW  : Index option Tempo Heures Creuses Jours Blancs
BBRHPJW  : Index option Tempo Heures Pleines Jours Blancs
BBRHCJR  : Index option Tempo Heures Creuses Jours Rouges
BBRHPJR  : Index option Tempo Heures Pleines Jours Rouges
PEJP     : Préavis Début EJP
PTEC     : Période Tarifaire en cours
DEMAIN   : Couleur du lendemain
IINST    : Intensité Instantanée
ADPS     : Avertissement de Dépassement De Puissance Souscrite
IMAX     : Intensité maximale appelée
PAPP     : Puissance apparente
HHPHC    : Horaire Heures Pleines Heures Creuses
MOTDETAT : Mot d'état du compteur
```

## Visualisation simple

* la variable HTTP_SEND permet de lancer un serveur d'affichage simple.
* une requete SQL recueille les 100 dernières mesure d'intensité 
* les données sont envoyés au serveur.
* A chaque collecte, l'intensité est envoyé au serveur.

<img src="newplot.png">


## MYSQL

la table frame est crée dans la bdd donnée en conf.
1 ligne par trame (38 valeurs en mode standard)
Tous les tags sont ajoutés dans la bdd sans traitement, hormis une suppression des espaces en trop.

# MQTT

Envoi de la trame sur un broker mqtt.
une variable d'environnement permet d'activer l'envoi des données au format jeedom compatible avec le plugin téléinfo.

## Influxdb v1: WIP

docker compose exec influxdb bash -c influx < "create database linky; use linky; CREATE USER linky WITH PASSWORD '123456' WITH ALL PRIVILEGES;"

### V1
curl --request GET "INFLUX_URL/api/v2/buckets" --header "Authorization: Token INFLUX_API_TOKEN"


influx config create --active -n config-link -u http://localhost:8086 -t Linky_t0ken -o linky.org

influx bucket create -n bucketname --org-id 044dc0bcf1860000 -r 10h -t yoursecrettoken
influx org find -t yoursecrettoken

influx -host localhost -port 8086 -username 'linky' -password 'linky' -database linky