"""Números de este PACS para el panel de servidores (lo ejecuta `pacs estado` cada 5 minutos).

    python estado_cli.py      → imprime un JSON con estudios, dictados, informes, etc.
"""
import json
import os
import sqlite3
from datetime import date

import requests

from config import Config

out = {}
try:
    auth = (Config.ORTHANC_USER, Config.ORTHANC_PASSWORD) if Config.ORTHANC_USER else None
    r = requests.get(Config.ORTHANC_URL + "/statistics", auth=auth, timeout=15).json()
    out.update(estudios=r.get("CountStudies", 0), pacientes=r.get("CountPatients", 0),
               imagenes=r.get("CountInstances", 0), orthanc_mb=r.get("TotalDiskSizeMB", 0))
except Exception as e:  # noqa: BLE001
    out["error_pacs"] = str(e)[:200]

try:
    db = sqlite3.connect(os.path.join(Config.DATA_DIR, "informes.db"), timeout=10)

    def n(sql, *a):
        try:
            return db.execute(sql, a).fetchone()[0] or 0
        except sqlite3.Error:
            return 0

    hoy = date.today().strftime("%Y%m%d")
    out.update(
        dictados=n("SELECT COUNT(*) FROM dictados WHERE estado != 'anulado'"),
        dictados_pendientes=n("SELECT COUNT(*) FROM dictados WHERE estado IN ('pendiente','tomado')"),
        validados=n("SELECT COUNT(*) FROM informes WHERE estado = 'firmado'"),
        por_validar=n("SELECT COUNT(*) FROM informes WHERE estado = 'por_validar'"),
        validados_hoy=n("SELECT COUNT(*) FROM informes WHERE estado = 'firmado' AND substr(replace(firmado_en,'-',''),1,8) = ?", hoy),
        usuarios=n("SELECT COUNT(*) FROM usuarios WHERE activo = 1"),
        ordenes_hoy=n("SELECT COUNT(*) FROM ordenes WHERE eliminada = 0 AND fecha = ?", date.today().isoformat()),
    )
except Exception as e:  # noqa: BLE001
    out["error_db"] = str(e)[:200]

print(json.dumps(out))
