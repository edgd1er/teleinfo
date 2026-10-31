
.PHONY: help build db logs htmlupdate

PYVER := $(shell grep -oP '(?<=PYVER: )[0-9\.]+' ./compose.yml)

help: ## This help.
		@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)


build: ## build teleinfo
		@echo "build teleinfo"
		docker compose build teleinfo
		@echo "stopping teleinfo"
		docker compose down -v teleinfo
		@echo "starting teleinfo"
		docker compose up -d teleinfo

htmlupdate: ## add html to container
		@echo "update html to container (pyver: $(PYVER))"
		@docker compose cp src/html/plotly_minimal.html teleinfo:/usr/lib/python$(PYVER)/site-packages/plot_server/plotly/minimal.html

db: ## start mysql + mqtt
		@echo "start mysql + mqtt"
		docker compose up -d mysql mqtt

logs: ## logs teleinfo
		@echo "logs teleinfo"
		docker compose logs -f teleinfo

mqttpasswd: ## update mqtt password
		docker compose exec mqtt /usr/bin/mosquitto_passwd -U /mosquitto/config/passwords.txt

exe: ## run ash
		docker compose exec teleinfo ash