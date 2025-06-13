#!/bin/bash

h=${HTTP_SERVER:-false}

#Fonctions
stop_http_plot() {
  if [[ ! "nofalse0" =~ ${h,,} ]]; then
    echo "stopping http_plot service"
    supervisorctl stop http_plot
    echo "end"
  fi
}

start_http_plot() {
  if [[ ! "nofalse0" =~ ${h,,} ]]; then
    echo "starting http_plot service"
    supervisorctl start http_plot
    #wait for service to be up
    sleep 5
    s=${MYSQL_SEND:-false}
    if [[ "true1yes" =~ ${s,,} ]]; then
      echo "Populate graph with data"
      python3 /app/data_injector.py
      #let the injector inects before starting linky
      sleep 5
    fi
    echo "end"
  fi
}

start_linky() {
  echo "starting linky service"
  supervisorctl start linky
  echo "end"
}

#Main
while getopts ":rs" o; do
    case "${o}" in
        r) #restart
            supervisorctl stop linky
            stop_http_plot
            start_http_plot
            start_linky
            ;;
        s) #start
            start_http_plot
            start_linky

            ;;
        *)
            usage
            ;;
    esac
done

[[ -z ${*} ]] && /app/prepare.sh -s || true