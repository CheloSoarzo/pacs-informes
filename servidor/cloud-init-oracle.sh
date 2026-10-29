#!/bin/bash
# Pegar en Oracle Cloud: Crear instancia → Mostrar opciones avanzadas → Gestión → "Pegar script de cloud-init".
# Instala todo automáticamente al crear el servidor (tarda unos 10-15 minutos).
# Cada PACS queda en https://<nombre>.<ip-del-servidor>.sslip.io (o en tu dominio, con DOMINIO_BASE).
export PACS_INICIALES='rancagua:Centro Médico Rancagua;insi:INSI'   # nombre:Nombre del centro; separados por ;
export ADMIN_PASSWORD='CAMBIA-ESTA-CLAVE'   # clave inicial del admin de cada PACS: cámbiala al entrar (Mi cuenta)
export REPO=https://github.com/CheloSoarzo/pacs-informes
curl -fsSL https://raw.githubusercontent.com/CheloSoarzo/pacs-informes/main/servidor/instalar.sh -o /root/instalar.sh
bash /root/instalar.sh
