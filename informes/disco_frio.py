"""Disco frío: pasa los estudios de meses anteriores a un disco lento y barato (HDD), sin cambiar nada para el usuario.

Los estudios siguen apareciendo igual en la lista, el visor y el portal del paciente: solo cambia el disco donde
están guardados sus archivos (los mueve el plugin «Advanced Storage» de Orthanc). Se abren un poco más lento.

Qué se pasa (cada mes, el día 1 a las 04:00, con `pacs disco-frio mover`):
  - Los estudios de meses anteriores al mes en curso (según la fecha del estudio)...
  - ...que ya estén informados (informe firmado). Un estudio pendiente o en borrador se queda en el disco rápido
    para que el médico lo informe sin esperas, y pasa en una próxima vuelta, cuando ya esté firmado.
  - Los importados (fecha anterior a FRIO_IMPORTADOS_HASTA, por defecto 01/10/2026) pasan siempre: se informaron
    en el sistema anterior.
  - Un estudio sin informe con más de FRIO_MAX_DIAS días (por defecto 90) pasa igual, para que nada quede pegado.

Uso (dentro del contenedor de la app de informes; en el servidor: `pacs disco-frio ...`):
    python disco_frio.py --simular          muestra qué se pasaría y cuánto pesa, sin mover nada
    python disco_frio.py --probar           pasa un solo estudio (para revisarlo en el visor)
    python disco_frio.py --mover            pasa todo lo que corresponde
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

import requests

URL = os.environ.get("ORTHANC_URL", "http://localhost:8042").rstrip("/")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
DESTINO = os.environ.get("FRIO_STORAGE", "frio")
IMPORTADOS_HASTA = os.environ.get("FRIO_IMPORTADOS_HASTA", "20261001").replace("-", "")
MAX_DIAS = int(os.environ.get("FRIO_MAX_DIAS", "90") or 90)
POR_TANDA = 10                     # estudios por trabajo de Orthanc (de a poco: el HDD es lento)
REGISTRO = os.path.join(DATA_DIR, "disco-frio.json")
S = requests.Session()
if os.environ.get("ORTHANC_USER"):
    S.auth = (os.environ["ORTHANC_USER"], os.environ.get("ORTHANC_PASSWORD", ""))


def _get(path, **kw):
    r = S.get(URL + path, timeout=kw.pop("timeout", 300), **kw)
    r.raise_for_status()
    return r.json()


def _gb(b):
    return f"{b / 1e9:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def _fecha_cl(f):
    return f"{f[6:8]}/{f[4:6]}/{f[:4]}" if len(f) == 8 else f


def leer_registro():
    try:
        with open(REGISTRO) as f:
            r = json.load(f)
    except (OSError, ValueError):
        r = {}
    r.setdefault("movidos", {})
    r.setdefault("historial", [])
    return r


def guardar_registro(r):
    tmp = REGISTRO + ".tmp"
    with open(tmp, "w") as f:
        json.dump(r, f)
    os.replace(tmp, REGISTRO)


def plugin_activo():
    try:
        return "advanced-storage" in _get("/plugins", timeout=30)
    except requests.RequestException:
        return False


def estados_informes():
    """{estudio_id de Orthanc: estado del informe}."""
    try:
        db = sqlite3.connect(os.path.join(DATA_DIR, "informes.db"), timeout=30)
        return dict(db.execute("SELECT estudio_id, estado FROM informes").fetchall())
    except sqlite3.Error:
        return {}


def candidatos(registro, hoy=None):
    """Estudios que corresponde pasar al disco frío: lista de (estudio, motivo)."""
    hoy = hoy or date.today()
    corte_mes = hoy.replace(day=1).strftime("%Y%m%d")                    # antes del mes en curso
    tope = (hoy - timedelta(days=MAX_DIAS)).strftime("%Y%m%d")
    llegando = (datetime.now() - timedelta(days=2)).strftime("%Y%m%dT%H%M%S")
    informes = estados_informes()
    out, esperan = [], 0
    for e in _get("/studies", params={"expand": "true"}):
        f = (e.get("MainDicomTags", {}).get("StudyDate") or "")[:8]
        if len(f) != 8 or not f.isdigit() or f >= corte_mes:
            continue
        if (e.get("LastUpdate") or "") > llegando:
            continue                                   # todavía llegando imágenes
        if registro["movidos"].get(e["ID"]) == e.get("LastUpdate"):
            continue                                   # ya está en el disco frío y no cambió
        if f < IMPORTADOS_HASTA:
            out.append((e, "importado"))
        elif informes.get(e["ID"]) == "firmado":
            out.append((e, "informado"))
        elif f < tope:
            out.append((e, f"más de {MAX_DIAS} días"))
        else:
            esperan += 1                               # sin informar: se queda en el disco rápido por ahora
    return out, esperan


def tamano(estudio_id):
    try:
        return int(_get(f"/studies/{estudio_id}/statistics", timeout=120).get("DiskSize") or 0)
    except (requests.RequestException, ValueError):
        return 0


def mover(ids):
    """Pide a Orthanc pasar los estudios al disco frío y espera a que termine. Devuelve (ok, detalle)."""
    r = S.post(URL + "/plugins/advanced-storage/move-storage", timeout=600,
               json={"Resources": ids, "TargetStorageId": DESTINO, "Asynchronous": True})
    if r.status_code != 200:
        return False, f"Orthanc respondió {r.status_code}: {r.text[:200]}"
    try:
        trabajo = r.json().get("ID")
    except ValueError:
        trabajo = None
    if not trabajo:
        return True, ""                                # respondió sin trabajo: ya lo hizo
    for _ in range(3600):                              # hasta 2 horas por tanda
        j = _get(f"/jobs/{trabajo}", timeout=60)
        if j.get("State") == "Success":
            return True, ""
        if j.get("State") in ("Failure", "Paused"):
            return False, f"el trabajo terminó en {j.get('State')}: {j.get('ErrorDescription') or j.get('ErrorDetails') or ''}"
        time.sleep(2)
    return False, "el trabajo no terminó en 2 horas"


def main():
    ap = argparse.ArgumentParser(description="Pasa los estudios de meses anteriores al disco frío (HDD)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--simular", action="store_true", help="muestra qué se pasaría, sin mover nada")
    g.add_argument("--probar", action="store_true", help="pasa un solo estudio")
    g.add_argument("--mover", action="store_true", help="pasa todo lo que corresponde")
    g.add_argument("--contar", action="store_true", help="(interno) cuántos estudios hay en el disco frío")
    g.add_argument("--ultimo", action="store_true", help="(interno) última vez que se pasaron estudios")
    a = ap.parse_args()

    registro = leer_registro()
    if a.contar:
        print(len(registro["movidos"]))
        return 0
    if a.ultimo:
        h = [x for x in registro["historial"] if not x.get("prueba")] or registro["historial"]
        if not h:
            print(f"{len(registro['movidos'])} estudios · todavía no se pasa nada")
        else:
            u = h[-1]
            print(f"{len(registro['movidos'])} estudios · última vez el {u['fecha'][8:10]}/{u['fecha'][5:7]}/{u['fecha'][:4]}: "
                  f"{u['estudios']} estudios, {_gb(u['bytes'])}" + (f", {u['errores']} tandas con problemas" if u.get("errores") else ""))
        return 0
    try:
        lista, esperan = candidatos(registro)
    except requests.RequestException as x:
        print(f"No se pudo conectar con el PACS: {x}")
        return 1
    if a.probar:
        lista = lista[:1]

    if a.simular:
        total, por_motivo = 0, {}
        for e, motivo in lista:
            b = tamano(e["ID"])
            total += b
            n, bb = por_motivo.get(motivo, (0, 0))
            por_motivo[motivo] = (n + 1, bb + b)
        print(f"Se pasarían al disco frío {len(lista)} estudios · {_gb(total)}")
        for motivo, (n, b) in sorted(por_motivo.items()):
            print(f"  {motivo}: {n} estudios · {_gb(b)}")
        print(f"Se quedan en el disco rápido hasta que se informen: {esperan} estudios de meses anteriores")
        return 0

    if not plugin_activo():
        print("El disco frío no está activo en este PACS (pacs disco-frio activar <nombre>)")
        return 1
    if not lista:
        print(f"No hay estudios por pasar al disco frío. Esperando informe: {esperan}")
        return 0

    t0, n_ok, bytes_ok, errores = time.time(), 0, 0, []
    for i in range(0, len(lista), POR_TANDA):
        tanda = lista[i:i + POR_TANDA]
        tam = {e["ID"]: tamano(e["ID"]) for e, _ in tanda}
        try:
            ok, detalle = mover([e["ID"] for e, _ in tanda])
        except requests.RequestException as x:
            ok, detalle = False, str(x)[:200]
        if not ok:
            errores.append(detalle)
            print(f"  aviso: no se pudo pasar una tanda de {len(tanda)} estudios: {detalle}", flush=True)
            if len(errores) >= 3:
                print("  tres tandas con problemas: se detiene y se reintenta en la próxima vuelta")
                break
            continue
        for e, motivo in tanda:
            registro["movidos"][e["ID"]] = e.get("LastUpdate")
            n_ok += 1
            bytes_ok += tam[e["ID"]]
            t = e.get("MainDicomTags", {})
            if a.probar:
                print(f"Estudio de prueba: {_fecha_cl(t.get('StudyDate', ''))} {t.get('StudyDescription', '')} "
                      f"· N° de acceso {t.get('AccessionNumber') or '(sin número)'} · {_gb(tam[e['ID']])}\n"
                      f"Ábrelo en el visor para revisar que se ve bien (dirección: /estudio/{e['ID']})")
        guardar_registro(registro)
        print(f"  {n_ok} de {len(lista)} estudios · {_gb(bytes_ok)}", flush=True)

    registro["historial"] = (registro["historial"] + [{
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"), "estudios": n_ok, "bytes": bytes_ok,
        "esperan": esperan, "errores": len(errores), "prueba": bool(a.probar)}])[-36:]
    guardar_registro(registro)
    print("-" * 60)
    print(f"Pasados al disco frío: {n_ok} estudios · {_gb(bytes_ok)} · {time.time() - t0:.0f} s")
    print(f"Se quedan en el disco rápido hasta que se informen: {esperan}")
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
