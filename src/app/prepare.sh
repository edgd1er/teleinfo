#!/bin/sh

echo "Generate linky conf file"
envsubst < /app/config.yaml.template > /config/config.yml

echo "starting linky service"
supervisorctl start linky
echo "end"
