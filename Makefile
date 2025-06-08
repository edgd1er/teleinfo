
.PHONY: help build db logs

help: ## This help.
  	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)


build: ## build teleinfo
		@echo "build teleinfo"
		docker compose down -v teleinfo
		docker compose up -d --build teleinfo

db: ## start mysql + mqtt
		@echo "start mysql + mqtt"
		docker compose up -d mysql mqtt

logs: ## logs teleinfo
		@echo "logs teleinfo"
		docker compose logs -f teleinfo

mqttpasswd:
		docker compose exec mqtt /usr/bin/mosquitto_passwd -U /mosquitto/config/passwords.txt

make exe:
		docker compose exec teleinfo ash