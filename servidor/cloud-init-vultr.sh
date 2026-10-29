#!/bin/bash
# Pegar en Vultr: Deploy → Additional Features → "Cloud-Init User-Data".
# Instala todo automáticamente al crear el servidor (tarda unos 10-15 minutos).
# Cada PACS queda en https://<nombre>.<ip-del-servidor>.sslip.io (o en tu dominio, con DOMINIO_BASE).
export PACS_INICIALES='rancagua:Centro Médico Rancagua;insi:INSI'   # nombre:Nombre del centro; separados por ;
export ADMIN_PASSWORD='CAMBIA-ESTA-CLAVE'   # clave inicial del admin de cada PACS: cámbiala al entrar (Mi cuenta)
export REPO=https://github.com/CheloSoarzo/pacs-informes

# Memoria de intercambio (2 GB) para servidores chicos: evita cortes al construir la app.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

curl -fsSL https://raw.githubusercontent.com/CheloSoarzo/pacs-informes/main/servidor/instalar.sh -o /root/instalar.sh
bash /root/instalar.sh
