"""Resumen diario del estado de los sistemas y avisos por correo (lo usa el comando `pacs`).

    python resumen_cli.py datos --fecha 2026-11-10
        JSON con lo de este PACS ese día: estudios por equipo, último envío de cada equipo, informes
        pendientes y estudios que el visor diagnóstico no puede abrir.
    python resumen_cli.py enviar --para correo@x.cl   (lee de la entrada el JSON armado por `pacs resumen`)
    python resumen_cli.py aviso --para correo@x.cl --asunto "..."   (lee el texto del aviso de la entrada)
    python resumen_cli.py probar --para correo@x.cl
"""
import argparse
import html
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta

import requests

import correo
from config import Config

URL = Config.ORTHANC_URL.rstrip("/")
S = requests.Session()
if Config.ORTHANC_USER:
    S.auth = (Config.ORTHANC_USER, Config.ORTHANC_PASSWORD)
DIAS_SIN_ENVIO = 3          # aviso si un equipo que envió en el último mes lleva estos días sin enviar


def _get(path, **kw):
    r = S.get(URL + path, timeout=kw.pop("timeout", 60), **kw)
    r.raise_for_status()
    return r.json()


def _equipo(serie_tags):
    mod = serie_tags.get("Modality", "?")
    fab = (serie_tags.get("Manufacturer") or "").strip()
    fab = fab.split()[0].title() if fab else "sin marca"
    return f"{mod} {fab}"


def _estudios(desde, hasta):
    """Estudios con StudyDate entre desde y hasta (YYYYMMDD), con su equipo y sus series."""
    res = S.post(URL + "/tools/find", json={"Level": "Study", "Expand": True, "Limit": 3000,
                                            "Query": {"StudyDate": f"{desde}-{hasta}"}}, timeout=120).json()
    out = []
    for e in res:
        series = e.get("Series") or []
        eq = "?"
        if series:
            try:
                eq = _equipo(_get(f"/series/{series[0]}").get("MainDicomTags", {}))
            except requests.RequestException:
                pass
        out.append({"id": e["ID"], "fecha": e.get("MainDicomTags", {}).get("StudyDate", ""),
                    "uid": e.get("MainDicomTags", {}).get("StudyInstanceUID", ""), "equipo": eq, "series": series})
    return out


def _visor_falla(est):
    """True si el visor diagnóstico (DICOMweb) no puede leer alguna serie del estudio."""
    for se in est["series"]:
        try:
            s = _get(f"/series/{se}")
            inst = s.get("Instances") or []
            if not inst:
                continue
            sop = _get(f"/instances/{inst[0]}").get("MainDicomTags", {}).get("SOPInstanceUID", "")
            r = S.get(URL + f"/dicom-web/studies/{est['uid']}/series/{s['MainDicomTags'].get('SeriesInstanceUID', '')}"
                            f"/instances/{sop}/metadata", timeout=120)
            if r.status_code != 200:
                return True
        except requests.RequestException:
            return True
    return False


def datos(fecha):
    d = datetime.strptime(fecha, "%Y-%m-%d").date()
    dia = d.strftime("%Y%m%d")
    out = {"centro": Config.CENTRO_NOMBRE or "", "fecha": fecha}
    try:
        mes = _estudios((d - timedelta(days=30)).strftime("%Y%m%d"), dia)
        del_dia = [e for e in mes if e["fecha"] == dia]
        por_equipo = {}
        for e in del_dia:
            por_equipo[e["equipo"]] = por_equipo.get(e["equipo"], 0) + 1
        ultimo = {}
        for e in mes:
            ultimo[e["equipo"]] = max(ultimo.get(e["equipo"], ""), e["fecha"])
        out["estudios"] = len(del_dia)
        out["por_equipo"] = por_equipo
        out["ultimo_envio"] = {k: f"{v[:4]}-{v[4:6]}-{v[6:]}" for k, v in ultimo.items()}
        out["visor_falla"] = [e["id"] for e in del_dia if _visor_falla(e)]
    except Exception as e:  # noqa: BLE001
        out["error_pacs"] = str(e)[:200]
    try:
        db = sqlite3.connect(os.path.join(Config.DATA_DIR, "informes.db"), timeout=10)

        def n(sql, *a):
            try:
                return db.execute(sql, a).fetchone()[0] or 0
            except sqlite3.Error:
                return 0
        out["por_validar"] = n("SELECT COUNT(*) FROM informes WHERE estado='por_validar'")
        out["dictados_pendientes"] = n("SELECT COUNT(*) FROM dictados WHERE estado IN ('pendiente','tomado')")
        out["validados_dia"] = n("SELECT COUNT(*) FROM informes WHERE estado='firmado' AND substr(firmado_en,1,10)=?", fecha)
        out["citas_dia"] = n("SELECT COUNT(*) FROM ordenes WHERE eliminada=0 AND fecha=? "
                             "AND estado_agenda NOT IN ('cancelado','ausente')", fecha)
    except Exception as e:  # noqa: BLE001
        out["error_db"] = str(e)[:200]
    return out


# ---------- armar el correo ----------
def _fecha_cl(f):
    return f"{f[8:10]}/{f[5:7]}/{f[:4]}" if f and len(f) >= 10 else f


def _gb(b):
    return f"{(b or 0) / 1e9:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def armar(info):
    """Devuelve (asunto, texto, html) a partir del JSON que arma `pacs resumen`."""
    fecha = info.get("fecha", date.today().isoformat())
    avisos, lineas = [], []
    srv = info.get("servidor", {})
    total, usado = srv.get("disco_total") or 0, srv.get("disco_usado") or 0
    pct = round(100 * usado / total) if total else 0
    (avisos if pct >= 80 else lineas).append(
        f"Disco del servidor: {pct} % usado ({_gb(total - usado)} libres de {_gb(total)})")
    r = info.get("respaldo") or {}
    if not r.get("configurado"):
        avisos.append("Respaldo externo: no está configurado")
    elif r.get("estado", "").startswith("ok"):
        partes = r["estado"].split()
        cuando = f"{_fecha_cl(partes[1])} {partes[2][:5]}" if len(partes) >= 3 else ""
        viejo = len(partes) >= 2 and partes[1] < (datetime.strptime(fecha, "%Y-%m-%d").date() - timedelta(days=1)).isoformat()
        (avisos if viejo else lineas).append(f"Respaldo externo: último OK el {cuando}" + (" (hace más de un día)" if viejo else ""))
    else:
        avisos.append(f"Respaldo externo: falló ({r.get('estado') or 'sin datos'})")
    for c in info.get("contenedores_caidos", []):
        avisos.append(f"Servicio detenido: {c}")
    for p in info.get("pacs", []):
        nom = p.get("centro") or p.get("nombre", "")
        if p.get("error_pacs") or p.get("error_db"):
            avisos.append(f"{nom}: no se pudo revisar ({p.get('error_pacs') or p.get('error_db')})")
            continue
        eq = ", ".join(f"{k} {v}" for k, v in sorted(p.get("por_equipo", {}).items())) or "sin estudios"
        lineas.append(f"{nom}: {p.get('estudios', 0)} estudios ({eq}) · {p.get('citas_dia', 0)} citas en la agenda · "
                      f"{p.get('validados_dia', 0)} informes validados")
        pend = p.get("por_validar", 0) + p.get("dictados_pendientes", 0)
        if pend:
            lineas.append(f"{nom}: {p.get('dictados_pendientes', 0)} dictados por transcribir · "
                          f"{p.get('por_validar', 0)} informes por validar")
        if p.get("visor_falla"):
            avisos.append(f"{nom}: {len(p['visor_falla'])} estudios del día que el visor diagnóstico no abre")
        hoy = datetime.strptime(fecha, "%Y-%m-%d").date()
        for equipo, ult in sorted(p.get("ultimo_envio", {}).items()):
            dias = (hoy - datetime.strptime(ult, "%Y-%m-%d").date()).days
            if dias >= DIAS_SIN_ENVIO:
                avisos.append(f"{nom}: el equipo {equipo} no envía imágenes desde el {_fecha_cl(ult)} ({dias} días)")
    asunto = (f"⚠️ {len(avisos)} aviso{'s' if len(avisos) != 1 else ''}" if avisos else "✅ Todo OK") + \
        f" · Estado de los sistemas {_fecha_cl(fecha)}"
    texto = f"Estado de los sistemas · {_fecha_cl(fecha)}\n\n" + \
        "".join(f"⚠️ {a}\n" for a in avisos) + "".join(f"✅ {x}\n" for x in lineas) + \
        f"\nVersión {info.get('version', '')} · © MSC26\n"
    filas = "".join(f'<tr><td style="padding:6px 8px;vertical-align:top">⚠️</td><td style="padding:6px 8px;color:#9A3412">'
                    f'<b>{html.escape(a)}</b></td></tr>' for a in avisos) + \
        "".join(f'<tr><td style="padding:6px 8px;vertical-align:top">✅</td><td style="padding:6px 8px">{html.escape(x)}</td></tr>'
                for x in lineas)
    cuerpo = (f'<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;font-size:15px;color:#1E293B;max-width:640px">'
              f'<h2 style="font-size:18px;margin:0 0 12px">Estado de los sistemas · {_fecha_cl(fecha)}</h2>'
              f'<table style="border-collapse:collapse;width:100%">{filas}</table>'
              f'<p style="color:#64748B;font-size:12px;margin-top:16px">Versión {html.escape(info.get("version", ""))}'
              f' · © MSC26</p></div>')
    return asunto, texto, cuerpo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["datos", "enviar", "aviso", "probar"])
    ap.add_argument("--fecha", default=date.today().isoformat())
    ap.add_argument("--para", default="")
    ap.add_argument("--asunto", default="")
    a = ap.parse_args()
    try:
        if a.accion == "datos":
            print(json.dumps(datos(a.fecha)))
        elif a.accion == "enviar":
            asunto, texto, cuerpo = armar(json.load(sys.stdin))
            correo.enviar_mensaje(a.para, asunto, texto, cuerpo, nombre_remitente="Sistemas HISRIS")
            print(f"Resumen enviado a {a.para}: {asunto}")
        elif a.accion == "aviso":
            texto = sys.stdin.read().strip()
            correo.enviar_mensaje(a.para, f"⚠️ {a.asunto}", texto + "\n\n© MSC26", nombre_remitente="Sistemas HISRIS")
            print(f"Aviso enviado a {a.para}")
        elif a.accion == "probar":
            correo.enviar_mensaje(a.para, "✅ Prueba del correo de los sistemas",
                                  "El correo saliente del PACS funciona. Desde ahora te llegará el resumen diario.\n\n© MSC26",
                                  nombre_remitente="Sistemas HISRIS")
            print(f"Correo de prueba enviado a {a.para}")
    except correo.CorreoError as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
