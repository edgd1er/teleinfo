#!/bin/bash

h=${HTTP_SERVER:-false}
if [[ ! "nofalse0" =~ ${h,,} ]]; then
  echo "starting web service"
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

echo "starting linky service"
supervisorctl start linky
echo "end"
