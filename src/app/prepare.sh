#!/bin/sh

if [[ ${HTTP_SERVER:-false} =~ "nofalse0" ]]; then
   echo "Generate web server configuration"
  envsubst < /app/param.cfg.template > /config/param.cfg
  echo "starting web service"
  supervisorctl start http_plot
echo "end"
fi

echo "starting linky service"
supervisorctl start linky
echo "end"
