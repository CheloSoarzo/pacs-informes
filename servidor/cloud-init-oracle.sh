#!/bin/bash
# Pegar en Oracle Cloud: Crear instancia → Mostrar opciones avanzadas → Gestión → "Pegar script de cloud-init".
# Instala todo automáticamente al crear el servidor (tarda unos 10-15 minutos).
export ADMIN_PASSWORD='CAMBIA-ESTA-CLAVE'          # clave inicial del usuario admin: cámbiala al entrar (Mi cuenta)
export CENTRO_NOMBRE='Centro de Imágenes'
export DICOM_PUERTO_PUBLICO=46429
export REPO=https://github.com/CheloSoarzo/pacs-informes
curl -fsSL https://raw.githubusercontent.com/CheloSoarzo/pacs-informes/main/servidor/instalar.sh -o /root/instalar.sh
bash /root/instalar.sh
