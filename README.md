# Teleinfo

### But:
  * Récupérer l'info du TIC du compteur EDF depuis le port série
  * envoi optionnel sur mysql
  * envoi optionnel sur mqtt
  * envoi optionnel sur influxdb mais non operationnel.(pas de v2 disponible pour un rpi2, la v1.8 semble ne pas gerer les buckets)

## Le matériel

Ma solution se base sur le [PiTInfo](https://hallard.me/pitinfov12/) de [Charles Hallard](http://hallard.me), c'est une carte d'extension qui se branche sur un Raspberry Pi et qui permet de récupérer les données de la téléinformation sur son port série. Le PiTInfo est [en vente sur Tindie](https://www.tindie.com/products/Hallard/pitinfo/).

Concernant le Raspberry, un [Raspberry Pi Zero](https://www.raspberrypi.org/products/raspberry-pi-zero/) suffit largement, mais n'importe quel autre modèle devrait fonctionner.

NB : Ce projet fonctionne exclusivement sur un Raspberry Pi, mais si vous vous sentez l'âme d'un aventurier et que vous préférez l'utilisation d'un ESP8266, Charles Hallard (encore lui) a intégré la téléinfo à l'excellent [firmware Tasmota](https://github.com/arendst/Tasmota/blob/development/lib/lib_div/LibTeleinfo/README.md).

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

Toutes les étiquettes sont transmises par défaut, un filtre peut etre appliqué. De la même façon, 

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

### Crédits

Tous les crédits vont à Charles Hallard (http://hallard.me), je n'ai fait que reprendre son travail pour l'adapter à mes besoins.