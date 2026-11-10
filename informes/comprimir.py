"""Compresión SIN PÉRDIDA de las imágenes guardadas en el PACS, verificada pixel a pixel.

Pasa cada imagen sin comprimir a JPEG-LS sin pérdida (sintaxis DICOM 1.2.840.10008.1.2.4.80), el formato
estándar de compresión sin pérdida: el CT queda típicamente 2 a 3 veces más liviano y los pixeles son
exactamente los mismos.

Por cada imagen:
  1. Calcula la huella (SHA-256) de los pixeles decodificados.
  2. Pide a Orthanc la versión comprimida y la guarda encima (mismos UID: los informes, links y la agenda
     siguen apuntando al mismo examen).
  3. Vuelve a calcular la huella de los pixeles. Si no es idéntica, repone la imagen original al tiro.
Las imágenes que ya vienen comprimidas (ej. ecografías en JPEG) no se tocan.

Uso (dentro del contenedor de la app de informes; en el servidor: `pacs comprimir`):
    python comprimir.py                 comprime todo lo pendiente
    python comprimir.py --estudios 1    solo el primer estudio pendiente (para probar)
    python comprimir.py --desde-horas 2 solo estudios sin cambios hace al menos 2 horas (lo usa la tarea nocturna)
"""
import argparse
import hashlib
import os
import sys
import time
from datetime import datetime, timedelta

import requests

DESTINO = os.environ.get("COMPRESION_SINTAXIS", "1.2.840.10008.1.2.4.80")   # JPEG-LS sin pérdida
SIN_COMPRIMIR = {"1.2.840.10008.1.2", "1.2.840.10008.1.2.1", "1.2.840.10008.1.2.2", "1.2.840.10008.1.2.1.99"}
MAX_BYTES = 400 * 1024 * 1024        # imágenes más grandes se dejan como están (memoria del servidor)
AHORRO_MINIMO = 0.05                 # si comprimida no ahorra al menos 5 %, no vale la pena

URL = os.environ.get("ORTHANC_URL", "http://localhost:8042").rstrip("/")
S = requests.Session()
if os.environ.get("ORTHANC_USER"):
    S.auth = (os.environ["ORTHANC_USER"], os.environ.get("ORTHANC_PASSWORD", ""))


class Omitir(Exception):
    pass


def _get(path, **kw):
    r = S.get(URL + path, timeout=kw.pop("timeout", 120), **kw)
    r.raise_for_status()
    return r


def huella_pixeles(iid):
    """SHA-256 de los pixeles decodificados (valores guardados, sin reescalar), leída por partes."""
    h = hashlib.sha256()
    with S.get(URL + f"/instances/{iid}/numpy", params={"rescale": "false", "compress": "false"},
               stream=True, timeout=600) as r:
        if r.status_code != 200:
            raise Omitir(f"no se pudieron leer los pixeles ({r.status_code})")
        for parte in r.iter_content(1 << 20):
            h.update(parte)
    return h.hexdigest()


def subir(datos):
    r = S.post(URL + "/instances", data=datos, headers={"Content-Type": "application/dicom"}, timeout=600)
    r.raise_for_status()
    return r.json()


def comprimir_instancia(iid):
    """Devuelve (bytes antes, bytes después) o lanza Omitir si no corresponde."""
    sintaxis = S.get(URL + f"/instances/{iid}/metadata/TransferSyntax", timeout=60).text.strip()
    if sintaxis not in SIN_COMPRIMIR:
        raise Omitir("ya comprimida")
    original = _get(f"/instances/{iid}/file", timeout=600).content
    if len(original) > MAX_BYTES:
        raise Omitir("muy grande")
    antes = huella_pixeles(iid)
    r = S.post(URL + f"/instances/{iid}/modify", timeout=600,
               json={"Transcode": DESTINO, "Force": True,
                     "Keep": ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"]})
    if r.status_code != 200 or not r.content:
        raise Omitir(f"Orthanc no pudo comprimirla ({r.status_code})")
    nuevo = r.content
    if len(nuevo) > len(original) * (1 - AHORRO_MINIMO):
        raise Omitir("no se achica")
    res = subir(nuevo)
    if S.get(URL + f"/instances/{iid}/metadata/TransferSyntax", timeout=60).text.strip() != DESTINO:
        raise Omitir("Orthanc no reemplazó la imagen (revisa OverwriteInstances)")
    if res.get("ID") != iid:
        # no debería pasar (mismos UID = mismo ID): por seguridad se repone la original y se borra la copia
        subir(original)
        if res.get("ID"):
            S.delete(URL + f"/instances/{res['ID']}", timeout=120)
        raise Omitir("cambió el identificador")
    try:
        despues = huella_pixeles(iid)
    except Exception:  # noqa: BLE001
        despues = ""
    if despues != antes:
        subir(original)                       # repone la imagen tal como estaba
        raise Omitir("los pixeles no coincidían: se dejó la original")
    return len(original), len(nuevo)


def estudios_pendientes(desde_horas):
    limite = (datetime.now() - timedelta(hours=desde_horas)).strftime("%Y%m%dT%H%M%S") if desde_horas else None
    out = []
    for e in _get("/studies", params={"expand": "true"}, timeout=300).json():
        if limite and (e.get("LastUpdate") or "") > limite:
            continue      # todavía llegando imágenes: queda para la próxima vez
        out.append(e)
    return out


def mb(n):
    return f"{n / 1048576:,.1f} MB".replace(",", "X").replace(".", ",").replace("X", ".")


def main():
    ap = argparse.ArgumentParser(description="Compresión sin pérdida de las imágenes del PACS")
    ap.add_argument("--estudios", type=int, default=0, help="máximo de estudios a revisar (0 = todos)")
    ap.add_argument("--desde-horas", type=float, default=0, help="solo estudios sin cambios hace al menos N horas")
    a = ap.parse_args()

    t0 = time.time()
    try:
        est0 = _get("/statistics").json()
    except requests.RequestException as e:
        print(f"No se pudo conectar con el PACS: {e}")
        return 1
    total_antes = total_despues = n_ok = n_ya = n_err = n_est = 0
    for e in estudios_pendientes(a.desde_horas):
        if a.estudios and n_est >= a.estudios:
            break
        tags = e.get("MainDicomTags", {})
        nombre = f"{tags.get('StudyDate', '')} {tags.get('StudyDescription', '') or tags.get('AccessionNumber', '')}".strip()
        ok_est = antes_est = despues_est = 0
        for inst in _get(f"/studies/{e['ID']}/instances", timeout=300).json():
            try:
                b, d = comprimir_instancia(inst["ID"])
                ok_est += 1
                antes_est += b
                despues_est += d
            except Omitir as x:
                if str(x) == "ya comprimida" or str(x) == "no se achica":
                    n_ya += 1
                else:
                    n_err += 1
                    print(f"  aviso {inst['ID']}: {x}")
            except requests.RequestException as x:
                n_err += 1
                print(f"  aviso {inst['ID']}: {x}")
        if ok_est:
            n_est += 1
            n_ok += ok_est
            total_antes += antes_est
            total_despues += despues_est
            print(f"{nombre or e['ID']}: {ok_est} imágenes · {mb(antes_est)} → {mb(despues_est)} "
                  f"({100 - 100 * despues_est / antes_est:.0f} % menos)", flush=True)
    est1 = _get("/statistics").json()
    print("-" * 60)
    if n_ok:
        print(f"Comprimidas y verificadas: {n_ok} imágenes de {n_est} estudios · {mb(total_antes)} → {mb(total_despues)}")
    else:
        print("No había imágenes por comprimir.")
    print(f"Ya estaban comprimidas o no se achican: {n_ya} · Avisos: {n_err}")
    print(f"Espacio del PACS: {est0.get('TotalDiskSizeMB', '?')} MB → {est1.get('TotalDiskSizeMB', '?')} MB "
          f"· {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
