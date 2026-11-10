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
import fcntl
import hashlib
import os
import sys
import time
import zlib
from datetime import datetime, timedelta

import requests

DESTINO = os.environ.get("COMPRESION_SINTAXIS", "1.2.840.10008.1.2.4.80")   # JPEG-LS sin pérdida
SIN_COMPRIMIR = {"1.2.840.10008.1.2", "1.2.840.10008.1.2.1", "1.2.840.10008.1.2.2", "1.2.840.10008.1.2.1.99"}
SIN_PERDIDA = {"1.2.840.10008.1.2.4.80", "1.2.840.10008.1.2.4.70", "1.2.840.10008.1.2.4.57", "1.2.840.10008.1.2.4.90"}
SIN_COMPRIMIR_DESTINO = "1.2.840.10008.1.2.1"      # Explicit VR Little Endian (sin comprimir)
# Equipos que NO se comprimen (fabricante, separados por coma). Los CR Fujifilm declaran menos bits de los que
# usan: el visor diagnóstico no los lee bien comprimidos.
EXCLUIR = [x.strip().upper() for x in os.environ.get("COMPRESION_EXCLUIR", "FUJI").split(",") if x.strip()]
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


def limpiar_cache_visor(serie_id):
    """Borra el caché que guarda el visor diagnóstico (DICOMweb) de la serie; Orthanc lo vuelve a armar solo.
    Hay que hacerlo después de reemplazar imágenes: el caché puede haber quedado vacío mientras se cambiaban."""
    try:
        S.delete(URL + f"/series/{serie_id}/attachments/4301", timeout=60)
    except requests.RequestException:
        pass


def cache_visor_malo(serie_id):
    """True si el caché DICOMweb de la serie existe pero no es una lista de imágenes (el visor muestra error)."""
    r = S.get(URL + f"/series/{serie_id}/attachments/4301/data", timeout=60)
    if r.status_code != 200:
        return False                      # no hay caché: está bien, se arma solo
    partes = r.content.split(b";", 2)
    if len(partes) != 3 or partes[0] != b"2":
        return False                      # formato que no conocemos: no se toca
    try:
        texto = zlib.decompress(partes[2], 16 + zlib.MAX_WBITS).lstrip()
    except zlib.error:
        return True
    return not texto.startswith(b"[") or texto.rstrip() == b"[]"


# Campos que algunos equipos mandan con un formato que no corresponde y que impiden al visor diagnóstico
# (DICOMweb) leer la imagen: «Cannot convert DICOM to DICOMweb». El CR Fujifilm manda TargetExposureIndex
# (0018,1412) como texto («876 ») cuando el estándar dice número binario de 8 bytes. No se usa para diagnosticar.
CAMPOS_MALOS = ["0018,1412"]
ARCHIVO_CAMBIOS = os.path.join(os.environ.get("DATA_DIR", "/data"), "reparar-visor.seq")


IMPLICITA = "1.2.840.10008.1.2"


def dicomweb_ok(iid):
    """True si el visor diagnóstico (DICOMweb) puede leer la imagen."""
    t = _get(f"/instances/{iid}/tags", params={"short": ""}, timeout=60).json()
    r = S.get(URL + f"/dicom-web/studies/{t.get('0020,000d')}/series/{t.get('0020,000e')}"
                    f"/instances/{t.get('0008,0018')}/metadata", timeout=120)
    return r.status_code == 200


def _probar(iid, original, antes, cambios):
    """Aplica los cambios a la imagen original y la guarda encima. True si el visor ya la puede leer."""
    r = S.post(URL + f"/instances/{iid}/modify", timeout=600,
               json={**cambios, "Force": True, "Keep": ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"]})
    if r.status_code != 200 or not r.content:
        return False
    res = subir(r.content)
    if res.get("ID") != iid:
        subir(original)
        if res.get("ID"):
            S.delete(URL + f"/instances/{res['ID']}", timeout=120)
        raise Omitir("cambió el identificador")
    if huella_pixeles(iid) != antes:
        subir(original)
        raise Omitir("los pixeles no coincidían: se dejó la original")
    return dicomweb_ok(iid)


def arreglar_instancia(iid):
    """Si el visor diagnóstico no puede leer la imagen, prueba arreglos de menos a más (siempre con los mismos
    pixeles y UID) hasta que pueda. Devuelve el arreglo usado, o "" si no hacía falta."""
    if dicomweb_ok(iid):
        return ""
    tags = _get(f"/instances/{iid}/tags", params={"short": ""}, timeout=60).json()
    sintaxis = S.get(URL + f"/instances/{iid}/metadata/TransferSyntax", timeout=60).text.strip()
    if sintaxis not in SIN_COMPRIMIR:
        sintaxis = SIN_COMPRIMIR_DESTINO
    quitar = [t for t in CAMPOS_MALOS if t in tags]
    original = _get(f"/instances/{iid}/file", timeout=600).content
    antes = huella_pixeles(iid)
    intentos = [
        ("formato implícito", {"Transcode": IMPLICITA, "Remove": quitar}),
        ("sin campos privados", {"Transcode": sintaxis, "Remove": quitar, "RemovePrivateTags": True}),
        ("sin campos privados, formato implícito", {"Transcode": IMPLICITA, "Remove": quitar, "RemovePrivateTags": True}),
    ]
    if quitar:
        intentos.insert(0, ("sin " + ", ".join(quitar), {"Transcode": sintaxis, "Remove": quitar}))
    for nombre, cambios in intentos:
        if _probar(iid, original, antes, cambios):
            return nombre
    subir(original)
    raise Omitir("el visor diagnóstico sigue sin poder leerla (se dejó como estaba)")


def arreglar_serie(serie_id):
    """Arregla las imágenes de la serie que lo necesiten y renueva su caché. Devuelve cuántas arregló."""
    serie = _get(f"/series/{serie_id}", timeout=60).json()
    if not excluido(serie.get("MainDicomTags", {}).get("Manufacturer")):
        return 0                           # solo los equipos con este problema (COMPRESION_EXCLUIR, ej. Fujifilm)
    n = 0
    for iid in serie.get("Instances", []):
        try:
            como = arreglar_instancia(iid)
            if como:
                n += 1
                print(f"  imagen {iid}: arreglada ({como})")
        except Omitir as x:
            print(f"  aviso {iid}: {x}")
    limpiar_cache_visor(serie_id)       # siempre: el caché pudo armarse antes de arreglarla
    return n


def _candado_reparar(esperar):
    """Una sola reparación a la vez por PACS (la de cada minuto y la completa no se pisan)."""
    fh = open(os.path.join(os.environ.get("DATA_DIR", "/data"), "reparar-visor.lock"), "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX if esperar else fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def reparar_nuevos():
    """Para la revisión de cada minuto: arregla las series que terminaron de llegar desde la última vez."""
    candado = _candado_reparar(esperar=False)
    if not candado:
        return                             # hay otra reparación en curso: lo toma la próxima vez
    try:
        with open(ARCHIVO_CAMBIOS) as fh:
            desde = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        desde = None
    if desde is None:                      # primera vez: lo antiguo lo arregla la revisión completa
        desde = _get("/changes", params={"last": ""}, timeout=60).json().get("Last", 0)
    else:
        while True:
            c = _get("/changes", params={"since": desde, "limit": 500}, timeout=120).json()
            for cambio in c.get("Changes", []):
                if cambio.get("ChangeType") == "StableSeries":
                    try:
                        n = arreglar_serie(cambio["ID"])
                        if n:
                            print(f"{datetime.now():%d/%m/%Y %H:%M} serie {cambio['ID']}: {n} imágenes arregladas para el visor")
                    except requests.RequestException as x:
                        if getattr(x.response, "status_code", 0) != 404:   # 404: la serie ya no existe
                            raise
            desde = c.get("Last", desde)
            if c.get("Done", True):
                break
    with open(ARCHIVO_CAMBIOS, "w") as fh:
        fh.write(str(desde))


def reparar_visor():
    """Revisa todas las series: arregla las imágenes con campos malos y borra los cachés del visor dañados."""
    candado = _candado_reparar(esperar=True)  # noqa: F841 - se suelta al terminar
    n = 0
    for se in _get("/series", params={"expand": "true"}, timeout=300).json():
        if excluido(se.get("MainDicomTags", {}).get("Manufacturer")):
            try:
                k = arreglar_serie(se["ID"])
                if k:
                    print(f"  serie {se['ID']}: {k} imágenes arregladas")
            except requests.RequestException as x:
                print(f"  aviso serie {se['ID']}: {x}")
    for se in _get("/series", timeout=300).json():
        try:
            if cache_visor_malo(se):
                limpiar_cache_visor(se)
                n += 1
        except requests.RequestException:
            pass
    print(f"Visor diagnóstico: {n} series con caché dañado reparadas" if n else "Visor diagnóstico: caché sin problemas")
    return n


def excluido(fabricante):
    f = (fabricante or "").upper()
    return any(x in f for x in EXCLUIR)


def comprimir_instancia(iid, destino=DESTINO, descomprimir=False):
    """Devuelve (bytes antes, bytes después) o lanza Omitir si no corresponde.
    descomprimir=True: devuelve a sin comprimir una imagen comprimida sin pérdida (también verificada)."""
    sintaxis = S.get(URL + f"/instances/{iid}/metadata/TransferSyntax", timeout=60).text.strip()
    if descomprimir:
        if sintaxis not in SIN_PERDIDA:
            raise Omitir("ya comprimida")      # sin comprimir, o con pérdida (no se toca)
    elif sintaxis not in SIN_COMPRIMIR:
        raise Omitir("ya comprimida")
    original = _get(f"/instances/{iid}/file", timeout=600).content
    if len(original) > MAX_BYTES:
        raise Omitir("muy grande")
    antes = huella_pixeles(iid)
    r = S.post(URL + f"/instances/{iid}/modify", timeout=600,
               json={"Transcode": destino, "Force": True,
                     "Keep": ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"]})
    if r.status_code != 200 or not r.content:
        raise Omitir(f"Orthanc no pudo comprimirla ({r.status_code})")
    nuevo = r.content
    if not descomprimir and len(nuevo) > len(original) * (1 - AHORRO_MINIMO):
        raise Omitir("no se achica")
    res = subir(nuevo)
    if S.get(URL + f"/instances/{iid}/metadata/TransferSyntax", timeout=60).text.strip() != destino:
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
    ap.add_argument("--descomprimir-excluidos", action="store_true",
                    help="devuelve a sin comprimir las imágenes de los equipos excluidos (ej. Fujifilm)")
    ap.add_argument("--reparar-visor", action="store_true",
                    help="solo revisa y repara las imágenes y el caché del visor diagnóstico (no comprime nada)")
    ap.add_argument("--reparar-nuevos", action="store_true",
                    help="arregla para el visor las series que llegaron desde la última vez (tarea de cada minuto)")
    a = ap.parse_args()
    if a.reparar_nuevos:
        try:
            reparar_nuevos()
        except requests.RequestException as e:
            print(f"Reparar nuevos: no se pudo conectar con el PACS: {e}")
            return 1
        return 0
    if a.reparar_visor:
        try:
            reparar_visor()
        except requests.RequestException as e:
            print(f"No se pudo conectar con el PACS: {e}")
            return 1
        return 0
    if a.descomprimir_excluidos:
        print(f"Devolviendo a sin comprimir las imágenes de: {', '.join(EXCLUIR) or '(ninguno)'}")

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
        instancias = []
        for se in _get(f"/studies/{e['ID']}/series", params={"expand": "true"}, timeout=300).json():
            fuera = excluido(se.get("MainDicomTags", {}).get("Manufacturer"))
            if a.descomprimir_excluidos == fuera:      # comprimir: los no excluidos; descomprimir: solo los excluidos
                instancias += [{"ID": i, "serie": se["ID"]} for i in se.get("Instances", [])]
        series_tocadas = set()
        for inst in instancias:
            try:
                if a.descomprimir_excluidos:
                    b, d = comprimir_instancia(inst["ID"], SIN_COMPRIMIR_DESTINO, descomprimir=True)
                else:
                    b, d = comprimir_instancia(inst["ID"])
                series_tocadas.add(inst["serie"])
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
        for serie in series_tocadas:
            limpiar_cache_visor(serie)
        if ok_est:
            n_est += 1
            n_ok += ok_est
            total_antes += antes_est
            total_despues += despues_est
            print(f"{nombre or e['ID']}: {ok_est} imágenes · {mb(antes_est)} → {mb(despues_est)}", flush=True)
    est1 = _get("/statistics").json()
    print("-" * 60)
    try:
        reparar_visor()
    except requests.RequestException as x:
        print(f"  aviso: no se pudo revisar el caché del visor: {x}")
    if n_ok:
        hecho = "Devueltas a sin comprimir" if a.descomprimir_excluidos else "Comprimidas"
        print(f"{hecho} y verificadas: {n_ok} imágenes de {n_est} estudios · {mb(total_antes)} → {mb(total_despues)}")
    else:
        print("No había imágenes por comprimir.")
    print(f"Ya estaban comprimidas o no se achican: {n_ya} · Avisos: {n_err}")
    print(f"Espacio del PACS: {est0.get('TotalDiskSizeMB', '?')} MB → {est1.get('TotalDiskSizeMB', '?')} MB "
          f"· {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
