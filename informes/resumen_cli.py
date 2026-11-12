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


def _pesos(n):
    n = round(n or 0)
    return ("-" if n < 0 else "") + "$" + f"{abs(n):,}".replace(",", ".")


EQUIPOS = {"tc": "TC", "rx": "RX", "eco": "Eco"}


DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _dia_cl(iso):
    """'2026-11-13' -> 'viernes 13/11/2026'."""
    try:
        d = date.fromisoformat(iso)
        return f"{DIAS_SEMANA[d.weekday()]} {d:%d/%m/%Y}"
    except (TypeError, ValueError):
        return iso or ""


def _texto_deposito(tj):
    """Depósito estimado de las ventas con tarjeta (comisión aproximada del proveedor, ej. Klap)."""
    deps = tj.get("depositos") or []
    cuando = " · ".join(f"{_dia_cl(d['fecha'])}" + (f" {_pesos(d['neto'])}" if len(deps) > 1 else "") for d in deps)
    return (f"tarjetas {_pesos(tj.get('bruto'))} − comisión {tj.get('proveedor') or ''} {_pesos(tj.get('comision'))}"
            + (f" − IVA {_pesos(tj.get('iva'))}" if tj.get("iva") else "")
            + f" → debería depositarse {_pesos(tj.get('neto'))} el {cuando} (aproximado)")


def _caja(nom, c, avisos):
    """Líneas de caja de un centro (y avisos si la caja no se cerró o hay diferencia)."""
    if not c:
        return []
    if c.get("error"):
        avisos.append(f"{nom}: no se pudo leer la caja ({c['error']})")
        return []
    if not c.get("atenciones") and not c.get("cierre"):
        return [f"{nom}: sin pagos registrados"]
    medios = " · ".join(f"{k} {_pesos(v['monto'])}" for k, v in sorted(c.get("por_medio", {}).items(),
                                                                       key=lambda x: -x[1]["monto"]) if v["monto"])
    prev = ", ".join(f"{k} {v['cantidad']}" for k, v in sorted(c.get("por_prevision", {}).items()))
    eq = ", ".join(f"{EQUIPOS.get(k, k)} {_pesos(v['monto'])}" for k, v in sorted(c.get("por_equipo", {}).items()))
    out = [f"{nom}: {_pesos(c.get('recaudado'))} recaudado en {c.get('atenciones', 0)} atenciones",
           f"{nom} · por medio de pago: {medios or 'sin montos'}"]
    if eq:
        out.append(f"{nom} · por equipo: {eq}")
    if prev:
        out.append(f"{nom} · previsión: {prev} · bonificación {_pesos(c.get('bonificacion'))}"
                   + (f" · descuentos {_pesos(c.get('descuentos'))}" if c.get("descuentos") else ""))
    tj = c.get("tarjetas")
    if tj and tj.get("neto"):
        out.append(f"{nom} · {_texto_deposito(tj)}")
    if c.get("anulados"):
        out.append(f"{nom} · {c['anulados']} pago{'s' if c['anulados'] != 1 else ''} anulado{'s' if c['anulados'] != 1 else ''}")
    ci = c.get("cierre")
    if ci:
        dif = ci.get("diferencia") or 0
        txt = (f"Cierre de caja{(' por ' + ci['por']) if ci.get('por') else ''}: efectivo esperado "
               f"{_pesos(c.get('efectivo_esperado'))}, contado {_pesos(ci.get('efectivo_contado'))}")
        if dif:
            avisos.append(f"{nom}: diferencia de caja de {_pesos(dif)} ({'sobra' if dif > 0 else 'falta'} efectivo)"
                          + (f" · {ci['observaciones']}" if ci.get("observaciones") else ""))
        out.append(f"{nom} · {txt}" + (" · sin diferencia" if not dif else ""))
    elif c.get("atenciones"):
        avisos.append(f"{nom}: la caja del día no se cerró (efectivo esperado {_pesos(c.get('efectivo_esperado'))})")
    return out


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
    caja = []
    for p in info.get("pacs", []):
        caja += _caja(p.get("centro") or p.get("nombre", ""), p.get("caja"), avisos)
    asunto = (f"⚠️ {len(avisos)} aviso{'s' if len(avisos) != 1 else ''}" if avisos else "✅ Todo OK") + \
        f" · Estado de los sistemas {_fecha_cl(fecha)}"
    texto = f"Estado de los sistemas · {_fecha_cl(fecha)}\n\n" + \
        "".join(f"⚠️ {a}\n" for a in avisos) + "".join(f"✅ {x}\n" for x in lineas) + \
        (("\nCaja\n" + "".join(f"💵 {x}\n" for x in caja)) if caja else "") + \
        f"\nVersión {info.get('version', '')} · © MSC26\n"
    filas = "".join(f'<tr><td style="padding:6px 8px;vertical-align:top">⚠️</td><td style="padding:6px 8px;color:#9A3412">'
                    f'<b>{html.escape(a)}</b></td></tr>' for a in avisos) + \
        "".join(f'<tr><td style="padding:6px 8px;vertical-align:top">✅</td><td style="padding:6px 8px">{html.escape(x)}</td></tr>'
                for x in lineas)
    cuerpo = (f'<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;font-size:15px;color:#1E293B;max-width:640px">'
              f'<h2 style="font-size:18px;margin:0 0 12px">Estado de los sistemas · {_fecha_cl(fecha)}</h2>'
              f'<table style="border-collapse:collapse;width:100%">{filas}</table>'
              + (f'<h3 style="font-size:16px;margin:18px 0 6px">Caja</h3><table style="border-collapse:collapse;width:100%">'
                 + "".join(f'<tr><td style="padding:5px 8px;vertical-align:top">💵</td><td style="padding:5px 8px">{html.escape(x)}</td></tr>'
                           for x in caja) + '</table>' if caja else '') +
              f'<p style="color:#64748B;font-size:12px;margin-top:16px">Versión {html.escape(info.get("version", ""))}'
              f' · © MSC26</p></div>')
    return asunto, texto, cuerpo


def armar_caja(info):
    """(asunto, texto, html) del correo de caja de las 20:00: la caja del día de cada centro."""
    fecha = info.get("fecha", date.today().isoformat())
    avisos, bloques, total, aten = [], [], 0, 0
    for p in info.get("pacs", []):
        nom = p.get("centro") or "Centro"
        lineas = _caja(nom, p.get("caja"), avisos)
        c = p.get("caja") or {}
        total += c.get("recaudado") or 0
        aten += c.get("atenciones") or 0
        if lineas:
            bloques.append((nom, lineas))
    asunto = (f"⚠️ Caja {_fecha_cl(fecha)} · {len(avisos)} aviso{'s' if len(avisos) != 1 else ''}" if avisos
              else f"💵 Caja {_fecha_cl(fecha)}") + f" · {_pesos(total)}"
    texto = f"Caja del día {_fecha_cl(fecha)}\n\nTotal de los centros: {_pesos(total)} en {aten} atenciones\n\n" + \
        "".join(f"⚠️ {a}\n" for a in avisos) + \
        "".join("\n" + "".join(f"💵 {x}\n" for x in ls) for _, ls in bloques) + \
        f"\nVersión {info.get('version', '')} · © MSC26\n"
    filas_av = "".join(f'<tr><td style="padding:6px 8px">⚠️</td><td style="padding:6px 8px;color:#9A3412"><b>'
                       f'{html.escape(a)}</b></td></tr>' for a in avisos)
    cuerpo = (f'<div style="font-family:system-ui,Segoe UI,Arial,sans-serif;font-size:15px;color:#1E293B;max-width:640px">'
              f'<h2 style="font-size:18px;margin:0 0 4px">Caja del día · {_fecha_cl(fecha)}</h2>'
              f'<p style="margin:0 0 12px;font-size:16px">Total de los centros: <b>{_pesos(total)}</b> en {aten} atenciones</p>'
              + (f'<table style="border-collapse:collapse;width:100%">{filas_av}</table>' if avisos else '')
              + "".join(f'<h3 style="font-size:16px;margin:16px 0 4px">{html.escape(nom)}</h3><table style="border-collapse:collapse;width:100%">'
                        + "".join(f'<tr><td style="padding:4px 8px">{html.escape(x.split(nom, 1)[-1].lstrip(" ·:"))}</td></tr>'
                                  for x in ls) + '</table>' for nom, ls in bloques)
              + f'<p style="color:#64748B;font-size:12px;margin-top:16px">Versión {html.escape(info.get("version", ""))} · © MSC26</p></div>')
    return asunto, texto, cuerpo


def pdf_caja(info):
    """PDF de la caja del día: resumen y detalle de cada pago, por centro (A4 horizontal)."""
    import io
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    fecha = _fecha_cl(info.get("fecha", ""))
    AZUL, GRIS, ROJO = colors.HexColor("#1E3A5F"), colors.HexColor("#F1F5F9"), colors.HexColor("#9A3412")
    st = {"t": ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=16, leading=20, textColor=AZUL, spaceAfter=4),
          "h": ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=12.5, textColor=AZUL, spaceBefore=10, spaceAfter=4),
          "n": ParagraphStyle("n", fontName="Helvetica", fontSize=9.5, leading=12),
          "c": ParagraphStyle("c", fontName="Helvetica", fontSize=8, leading=9.5),
          "a": ParagraphStyle("a", fontName="Helvetica-Bold", fontSize=9.5, leading=12, textColor=ROJO)}
    e = lambda x: html.escape(str(x or ""))  # noqa: E731
    total = sum((p.get("caja") or {}).get("recaudado") or 0 for p in info.get("pacs", []))
    aten = sum((p.get("caja") or {}).get("atenciones") or 0 for p in info.get("pacs", []))
    flow = [Paragraph(f"Caja del día · {fecha}", st["t"]),
            Paragraph(f"Total de los centros: <b>{_pesos(total)}</b> en {aten} atenciones", st["n"])]

    def tabla(filas, anchos, cab=True, num_desde=None, estilos=()):
        t = Table(filas, colWidths=anchos, repeatRows=1 if cab else 0, hAlign="LEFT")
        ts = [("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
              ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
              ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]
        if cab:
            ts += [("BACKGROUND", (0, 0), (-1, 0), GRIS), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5)]
        if num_desde is not None:
            ts.append(("ALIGN", (num_desde, 0), (-1, -1), "RIGHT"))
        t.setStyle(TableStyle(ts + list(estilos)))
        return t

    for p in info.get("pacs", []):
        nom, c = p.get("centro") or "Centro", p.get("caja") or {}
        flow.append(Paragraph(e(nom), st["h"]))
        if c.get("error"):
            flow.append(Paragraph(f"No se pudo leer la caja: {e(c['error'])}", st["a"]))
            continue
        ci = c.get("cierre")
        if not c.get("atenciones") and not ci and not c.get("pagos"):
            flow.append(Paragraph("Sin pagos registrados.", st["n"]))
            continue
        res = [["Atenciones", c.get("atenciones", 0), "Valor total", _pesos(c.get("valor"))],
               ["Recaudado", _pesos(c.get("recaudado")), "Bonificación", _pesos(c.get("bonificacion"))],
               ["Efectivo esperado", _pesos(c.get("efectivo_esperado")), "Descuentos", _pesos(c.get("descuentos"))],
               ["Efectivo contado", _pesos(ci["efectivo_contado"]) if ci else "caja sin cerrar",
                "Diferencia", _pesos(ci["diferencia"]) if ci else "—"]]
        flow.append(tabla(res, [38 * mm, 32 * mm, 30 * mm, 32 * mm], cab=False,
                          estilos=[("FONT", (0, 0), (0, -1), "Helvetica-Bold", 8.5), ("FONT", (2, 0), (2, -1), "Helvetica-Bold", 8.5),
                                   ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT")]))
        if ci and ci.get("diferencia"):
            flow.append(Paragraph(f"Diferencia de caja: {_pesos(ci['diferencia'])}"
                                  + (f" · {e(ci.get('observaciones'))}" if ci.get("observaciones") else ""), st["a"]))
        tj = c.get("tarjetas")
        if tj and tj.get("bruto"):
            fil = [["Tarjetas " + (tj.get("proveedor") or ""), "N°", "Ventas", "Comisión", "Descuento"]]
            for k, n in (("debito", "Débito"), ("credito", "Crédito")):
                x = tj.get(k) or {}
                if x.get("cantidad"):
                    tasa = f"{str(x.get('tasa')).replace('.', ',')} %" + (f" + {_pesos(tj.get('fijo'))}" if tj.get("fijo") else "")
                    fil.append([n, x["cantidad"], _pesos(x["bruto"]), tasa, "-" + _pesos(x["comision"])])
            if tj.get("iva"):
                fil.append(["IVA de la comisión", "", "", "19 %", "-" + _pesos(tj["iva"])])
            deps = tj.get("depositos") or []
            cuando = "<br/>".join(e(_dia_cl(d["fecha"])) + (f": {_pesos(d['neto'])}" if len(deps) > 1 else "") for d in deps)
            caja = Table([[Paragraph("DEBERÍA DEPOSITARSE (APROX.)", st["c"])],
                          [Paragraph(f"<b>{_pesos(tj.get('neto'))}</b>", ParagraphStyle("dep", fontName="Helvetica", fontSize=15, leading=18))],
                          [Paragraph(cuando, st["c"])]], colWidths=[62 * mm],
                         style=[("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#E1F0EE")), ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])
            flow += [Spacer(1, 6), Table([[tabla(fil, [34 * mm, 10 * mm, 24 * mm, 28 * mm, 24 * mm], estilos=[("ALIGN", (1, 0), (2, -1), "RIGHT"), ("ALIGN", (4, 0), (4, -1), "RIGHT")]), caja]],
                                         hAlign="LEFT", style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                                               ("RIGHTPADDING", (0, 0), (-1, -1), 10)])]
        partes = []
        for titulo, k in (("Medio de pago", "por_medio"), ("Equipo", "por_equipo"), ("Previsión", "por_prevision")):
            filas = [[titulo, "N°", "Monto"]] + [[EQUIPOS.get(n, n) if k == "por_equipo" else n, v["cantidad"], _pesos(v["monto"])]
                                                  for n, v in sorted((c.get(k) or {}).items(), key=lambda x: -x[1]["monto"])]
            if len(filas) > 1:
                partes.append(tabla(filas, [34 * mm, 12 * mm, 26 * mm], num_desde=1))
        if partes:
            flow += [Spacer(1, 5), Table([partes], hAlign="LEFT", style=[("VALIGN", (0, 0), (-1, -1), "TOP"),
                                                                         ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                                                         ("RIGHTPADDING", (0, 0), (-1, -1), 10)])]
        pagos = c.get("pagos") or []
        if pagos:
            filas = [["Hora", "Paciente", "RUT", "Examen", "Previsión", "Medio de pago", "Valor", "Bonif.", "Desc.", "Pagó", "Boleta / bono"]]
            anul = []
            for i, x in enumerate(pagos, 1):
                filas.append([x.get("hora", ""), Paragraph(e(x.get("paciente")), st["c"]), x.get("rut", ""),
                              Paragraph(e(x.get("examen")), st["c"]), x.get("prevision", ""), Paragraph(e(x.get("medio")), st["c"]),
                              _pesos(x.get("valor")), _pesos(x.get("bonificacion")),
                              _pesos(x.get("descuento")) if x.get("descuento") else "", _pesos(x.get("copago")),
                              Paragraph(e(" · ".join(v for v in (x.get("boleta"), x.get("folio_bono")) if v)
                                          + (f" ANULADO: {x.get('motivo_anulacion')}" if x.get("anulado") else "")), st["c"])])
                if x.get("anulado"):
                    anul.append(("TEXTCOLOR", (0, i), (-1, i), colors.HexColor("#94A3B8")))
            flow += [Spacer(1, 6), Paragraph(f"Detalle de pagos ({len(pagos)})", st["n"]),
                     tabla(filas, [11 * mm, 44 * mm, 21 * mm, 50 * mm, 20 * mm, 36 * mm, 18 * mm, 18 * mm, 14 * mm, 18 * mm, 27 * mm],
                           estilos=[("ALIGN", (6, 0), (9, -1), "RIGHT")] + anul)]
        else:
            flow.append(Paragraph("Sin pagos registrados.", st["n"]))

    def pie(canvas, doc):
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(12 * mm, 8 * mm, f"Caja del día {fecha} · versión {info.get('version', '')} · © MSC26")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 8 * mm, f"Página {doc.page}")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=14 * mm, title=f"Caja {fecha}", author="© MSC26")
    doc.build(flow, onFirstPage=pie, onLaterPages=pie)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("accion", choices=["datos", "enviar", "caja", "aviso", "probar"])
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
        elif a.accion == "caja":
            info = json.load(sys.stdin)
            asunto, texto, cuerpo = armar_caja(info)
            adjuntos = []
            try:
                adjuntos.append((f"caja-{info.get('fecha', '')}.pdf", pdf_caja(info), "application/pdf"))
            except Exception as e:  # noqa: BLE001 - sin PDF igual se envía el correo
                texto += f"\n(No se pudo armar el PDF: {e})\n"
            correo.enviar_mensaje(a.para, asunto, texto, cuerpo, nombre_remitente="Caja HISRIS", adjuntos=adjuntos)
            print(f"Caja enviada a {a.para}: {asunto}")
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
