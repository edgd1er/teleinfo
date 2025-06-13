#!/usr/bin/env bash

# resend every day, a mean value for each hour then let linky send every minute.
h=${HTTP_SERVER:-false}
if [[ ! "nofalse0" =~ ${h,,} ]]; then
  echo "stopping linky service"
  supervisorctl stop linky
  echo "stopping http_plot service"
  supervisorctl stop http_plot
  /app/prepare.sh
fi

