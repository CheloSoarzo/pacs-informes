"""Cliente mínimo para la API REST de Orthanc."""
import re
import unicodedata
from datetime import date

import requests


class OrthancError(Exception):
    pass


def nombre_dicom(valor):
    """'PEREZ SOTO^JUAN ANDRES' -> 'JUAN ANDRES PEREZ SOTO'."""
    if not valor:
        return ""
    partes = [p.strip() for p in valor.split("^")]
    apellidos = partes[0] if partes else ""
    nombres = " ".join(p for p in partes[1:3] if p)
    return " ".join(p for p in (nombres, apellidos) if p).strip()


def fecha_dicom(valor):
    """'20261023' -> '23/10/2026' (formato chileno)."""
    if valor and len(valor) >= 8 and valor[:8].isdigit():
        return f"{valor[6:8]}/{valor[4:6]}/{valor[0:4]}"
    return valor or ""


def edad(fnac, fecha_ref):
    try:
        n = date(int(fnac[0:4]), int(fnac[4:6]), int(fnac[6:8]))
        r = date(int(fecha_ref[0:4]), int(fecha_ref[4:6]), int(fecha_ref[6:8]))
    except (TypeError, ValueError, IndexError):
        return ""
    anos = r.year - n.year - ((r.month, r.day) < (n.month, n.day))
    if anos >= 2:
        return f"{anos} años"
    meses = (r.year - n.year) * 12 + r.month - n.month - (r.day < n.day)
    return f"{max(meses, 0)} meses"


class Orthanc:
    def __init__(self, url, user="", password="", timeout=30):
        self.url = url.rstrip("/")
        self.s = requests.Session()
        if user:
            self.s.auth = (user, password)
        self.timeout = timeout

    # ---- bajo nivel ----
    def _req(self, method, path, **kw):
        kw.setdefault("timeout", self.timeout)
        try:
            r = self.s.request(method, self.url + path, **kw)
        except requests.RequestException as e:
            raise OrthancError(f"No se pudo conectar con el PACS: {e}") from e
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise OrthancError(f"PACS respondió {r.status_code} en {path}")
        return r

    def get_json(self, path, **kw):
        r = self._req("GET", path, **kw)
        return r.json() if r is not None else None

    def post_json(self, path, data):
        r = self._req("POST", path, json=data)
        return r.json() if r is not None else None

    # ---- estudios ----
    def buscar_estudios(self, desde=None, hasta=None, texto="", modalidad="", limite=300, solicitante=""):
        """Busca estudios por rango de fechas (YYYYMMDD), texto (nombre o ID) y modalidad."""
        base = {}
        if desde or hasta:
            base["StudyDate"] = f"{desde or ''}-{hasta or ''}"
        if modalidad:
            base["ModalitiesInStudy"] = modalidad
        if solicitante:
            base["ReferringPhysicianName"] = f"*{solicitante}*"

        consultas = []
        texto = (texto or "").strip()
        palabras = [_normal(p) for p in texto.split() if p.strip()]
        if texto:
            # Nombre: busca por la palabra más larga y luego filtra por todas
            larga = max(texto.split(), key=len)
            consultas.append(dict(base, PatientName=f"*{larga}*"))
            # RUT/ID: tolera puntos y guion ("12.345.678-9" = "123456789")
            alnum = re.sub(r"[^0-9A-Za-z]", "", texto)
            if alnum:
                consultas.append(dict(base, PatientID="*" + "*".join(alnum) + "*"))
            consultas.append(dict(base, AccessionNumber=texto))
        else:
            consultas.append(base)

        vistos, resultado = set(), []
        for q in consultas:
            res = self.post_json("/tools/find", {
                "Level": "Study",
                "Query": q,
                "Expand": True,
                "CaseSensitive": False,
                "RequestedTags": ["ModalitiesInStudy"],
                "Limit": limite,
            }) or []
            for est in res:
                if est["ID"] in vistos:
                    continue
                vistos.add(est["ID"])
                r = self._resumen(est)
                if palabras:
                    nombre = _normal(r["paciente_nombre"])
                    pid = _normal(re.sub(r"[^0-9A-Za-z]", "", r["paciente_id"]))
                    q_id = _normal(re.sub(r"[^0-9A-Za-z]", "", texto))
                    ok = (all(p in nombre for p in palabras)
                          or (q_id and q_id in pid)
                          or _normal(r["accession"]) == _normal(texto))
                    if not ok:
                        continue
                resultado.append(r)
        resultado.sort(key=lambda e: (e["fecha_raw"], e["hora_raw"]), reverse=True)
        return resultado

    def estudios_de_paciente(self, paciente_id, limite=50):
        """Todos los estudios con exactamente este PatientID (historial)."""
        res = self.post_json("/tools/find", {
            "Level": "Study", "Query": {"PatientID": paciente_id}, "Expand": True,
            "RequestedTags": ["ModalitiesInStudy"], "Limit": limite,
        }) or []
        out = [self._resumen(e) for e in res]
        out.sort(key=lambda e: (e["fecha_raw"], e["hora_raw"]), reverse=True)
        return out

    def estudio(self, estudio_id):
        est = self.get_json(f"/studies/{estudio_id}")
        if not est:
            return None
        resumen = self._resumen(est)
        if not resumen["modalidad"]:
            series = self.series(estudio_id)
            mods = sorted({s["modalidad"] for s in series if s["modalidad"]})
            resumen["modalidad"] = "/".join(mods)
        return resumen

    def series(self, estudio_id):
        """Series del estudio con sus instancias ordenadas (para visor simple)."""
        series = self.get_json(f"/studies/{estudio_id}/series") or []
        out = []
        for s in series:
            tags = s.get("MainDicomTags", {})
            out.append({
                "id": s["ID"],
                "modalidad": tags.get("Modality", ""),
                "descripcion": tags.get("SeriesDescription", "") or tags.get("BodyPartExamined", ""),
                "region": tags.get("BodyPartExamined", ""),
                "fecha": fecha_dicom(tags.get("SeriesDate", "")),
                "hora": _hora(tags.get("SeriesTime", "")),
                "numero": _int(tags.get("SeriesNumber")),
                "n_instancias": len(s.get("Instances", [])),
            })
        out.sort(key=lambda s: s["numero"])
        return out

    def series_visor(self, estudio_id):
        """Series para el visor simple: con sus imágenes ordenadas y la ventana original del equipo
        (centro, ancho) para ajustar brillo y contraste. Sin informes estructurados ni objetos que no son imágenes."""
        out = []
        for s in self.series(estudio_id):
            if s["modalidad"] in ("SR", "PR", "KO"):
                continue
            s["instancias"] = self.instancias_ordenadas(s["id"])
            if s["instancias"]:
                s["ventana"] = self.ventana(s["instancias"][len(s["instancias"]) // 2])
                out.append(s)
        return out

    def _tag(self, instancia_id, tag):
        try:
            r = self._req("GET", f"/instances/{instancia_id}/content/{tag}")
            return r.text.strip() if r is not None else ""
        except OrthancError:
            return ""

    def ventana(self, instancia_id):
        """{centro, ancho, invertida} de la ventana que trae la imagen (en las mismas unidades que usa Orthanc al dibujarla,
        ej. Hounsfield en el TC). None si es una imagen en color (ecografía), que no se ventanea."""
        foto = self._tag(instancia_id, "0028-0004").upper()
        if foto and not foto.startswith("MONOCHROME"):
            return None

        def primero(v):
            try:
                return float(v.split("\\")[0])
            except (ValueError, AttributeError):
                return None
        c, a = primero(self._tag(instancia_id, "0028-1050")), primero(self._tag(instancia_id, "0028-1051"))
        inv = foto == "MONOCHROME1"          # se dibuja invertida (ej. CR Fujifilm)
        if c is not None and a and a > 0:
            return {"centro": c, "ancho": a, "invertida": inv}
        # Sin ventana en la imagen: todo el rango posible de valores
        bits = primero(self._tag(instancia_id, "0028-0101")) or 12
        con_signo = self._tag(instancia_id, "0028-0103") == "1"
        pendiente = primero(self._tag(instancia_id, "0028-1053")) or 1
        intercepto = primero(self._tag(instancia_id, "0028-1052")) or 0
        bajo, alto = (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1) if con_signo else (0, 2 ** bits - 1)
        bajo, alto = bajo * pendiente + intercepto, alto * pendiente + intercepto
        return {"centro": (bajo + alto) / 2, "ancho": max(1.0, alto - bajo), "invertida": inv}

    def instancias_ordenadas(self, serie_id):
        inst = self.get_json(f"/series/{serie_id}/instances") or []
        inst.sort(key=lambda i: _int(i.get("MainDicomTags", {}).get("InstanceNumber")))
        return [i["ID"] for i in inst]

    def instancia_pertenece(self, instancia_id, estudio_id):
        info = self.get_json(f"/instances/{instancia_id}/study")
        return bool(info) and info.get("ID") == estudio_id

    def estudio_de_instancia(self, instancia_id):
        info = self.get_json(f"/instances/{instancia_id}/study")
        return info.get("ID") if info else None

    def estudio_por_uid(self, uid):
        """StudyInstanceUID -> resumen del estudio (o None)."""
        r = self._req("POST", "/tools/lookup", data=uid)
        for item in (r.json() if r is not None else []):
            if item.get("Type") == "Study":
                return self.estudio(item["ID"])
        return None

    def modificar_estudio(self, estudio_id, reemplazos):
        """Corrige tags DICOM de todo el estudio (datos del paciente y del examen).

        Se conservan los UID (el estudio sigue siendo el mismo para visores y equipos). Si cambia el RUT/ID del
        paciente, Orthanc le asigna otro ID interno: se devuelve el ID nuevo y se borra la copia anterior."""
        r = self._req("POST", f"/studies/{estudio_id}/modify", timeout=900, json={
            "Replace": reemplazos,
            "Keep": ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"],
            "Force": True,            # necesario para conservar UID y cambiar datos del paciente
            "KeepSource": True,       # se borra el original solo si quedó con otro ID
        })
        if r is None:
            raise OrthancError("El estudio ya no existe en el PACS")
        nuevo = r.json().get("ID")
        if not nuevo:
            raise OrthancError("El PACS no devolvió el estudio modificado")
        if nuevo != estudio_id:
            self._req("DELETE", f"/studies/{estudio_id}")
        return nuevo

    def borrar_estudio(self, estudio_id):
        """Borra el estudio completo del PACS (todas sus series e imágenes). No se puede deshacer."""
        self._req("DELETE", f"/studies/{estudio_id}", timeout=300)

    # ---- equipos remotos (modalidades DICOM) ----
    def modalidades(self):
        return self.get_json("/modalities", params={"expand": ""}) or {}

    def guardar_modalidad(self, nombre, aet, host, permitir_store=True):
        r = self._req("PUT", f"/modalities/{nombre}", json={
            "AET": aet, "Host": host, "Port": 104, "Manufacturer": "Generic",
            "AllowEcho": True, "AllowStore": permitir_store, "AllowMove": False,
            # C-GET: Horos/OsiriX recuperan estudios por su misma conexión (sirve detrás de un router)
            "AllowGet": True,
            # Consultar la lista de trabajo (worklist) que arma la agenda
            "AllowFind": True, "AllowFindWorklist": True,
            "AllowStorageCommitment": False})
        if r is None:
            raise OrthancError("El PACS no aceptó el equipo (¿DicomModalitiesInDatabase activado?)")

    def estudio_de(self, nivel, objeto_id):
        """ID del estudio al que pertenece una imagen (nivel 'instances') o una serie (nivel 'series')."""
        e = self.get_json(f"/{nivel}/{objeto_id}/study")
        return (e or {}).get("ID", "")

    def borrar_instancia(self, instancia_id):
        """Borra una imagen del PACS. No se puede deshacer."""
        self._req("DELETE", f"/instances/{instancia_id}", timeout=120)

    def borrar_serie(self, serie_id):
        """Borra una serie completa del PACS. No se puede deshacer."""
        self._req("DELETE", f"/series/{serie_id}", timeout=300)

    def borrar_modalidad(self, nombre):
        self._req("DELETE", f"/modalities/{nombre}")

    VISTA_MAX = 2000          # lado máximo (px) de las imágenes de la vista rápida, el portal y el CD
    _dims = {}

    def _dimensiones(self, instancia_id):
        if instancia_id not in self._dims:
            try:
                f = self._req("GET", f"/instances/{instancia_id}/content/0028-0010")
                c = self._req("GET", f"/instances/{instancia_id}/content/0028-0011")
                self._dims[instancia_id] = (int(f.text.strip()), int(c.text.strip())) if f is not None and c is not None else None
            except (OrthancError, ValueError):
                self._dims[instancia_id] = None
            if len(self._dims) > 20000:
                self._dims.clear()
        return self._dims[instancia_id]

    def preview(self, instancia_id, centro=None, ancho=None):
        """Imagen JPEG para ver en el navegador (vista rápida, portal del paciente, CD).

        Usa la ventana (brillo/contraste) que define el equipo en cada imagen —ej. partes blandas en el TC, la
        ventana del Fuji en los RX— y no el rango completo de valores, que deja las imágenes grises y lavadas.
        Las imágenes muy grandes se achican a 2000 px para que carguen rápido en el celular."""
        params = {"quality": 90}
        if centro is not None and ancho:                 # brillo/contraste elegido en el visor
            params.update({"window-center": f"{centro:g}", "window-width": f"{ancho:g}"})
        dims = self._dimensiones(instancia_id)
        if dims and max(dims) > self.VISTA_MAX:
            params.update(width=self.VISTA_MAX, height=self.VISTA_MAX, smooth=1)
        try:
            r = self._req("GET", f"/instances/{instancia_id}/rendered", params=params,
                          headers={"Accept": "image/jpeg"})
            if r is not None and r.content:
                return self._recortar(instancia_id, r)
        except OrthancError:
            pass
        return self._req("GET", f"/instances/{instancia_id}/preview", headers={"Accept": "image/jpeg"})

    _campos = {}

    def campo_expuesto(self, instancia_id):
        """Zona expuesta de una radiografía (CR/DX) como fracciones (x0, y0, x1, y1) de la imagen, o None.

        Algunos lectores (ej. el CR Fujifilm) envían la placa completa de 35 cm con todo lo que queda fuera del
        colimador en negro: unos dedos quedan como una figura chica en un rincón. Se busca el rectángulo que no es
        negro (en una copia chica, con la ventana del equipo) y se guarda por imagen."""
        if instancia_id in self._campos:
            return self._campos[instancia_id]
        caja = None
        try:
            if self._tag(instancia_id, "0008-0060").upper() in ("CR", "DX"):
                from io import BytesIO
                from PIL import Image
                r = self._req("GET", f"/instances/{instancia_id}/rendered", params={"width": 256, "height": 256},
                              headers={"Accept": "image/jpeg"})
                im = Image.open(BytesIO(r.content)).convert("L")
                w, h = im.size
                px = im.load()
                filas, cols = [0] * h, [0] * w
                for y in range(h):
                    for x in range(w):
                        if px[x, y] > 20:
                            filas[y] += 1
                            cols[x] += 1
                ys = [i for i, v in enumerate(filas) if v > w * 0.01]
                xs = [i for i, v in enumerate(cols) if v > h * 0.01]
                if xs and ys:
                    m = 0.015                                   # un poco de borde alrededor
                    x0, x1 = max(0.0, xs[0] / w - m), min(1.0, (xs[-1] + 1) / w + m)
                    y0, y1 = max(0.0, ys[0] / h - m), min(1.0, (ys[-1] + 1) / h + m)
                    if (x1 - x0) * (y1 - y0) < 0.85 and (x1 - x0) > 0.05 and (y1 - y0) > 0.05:
                        caja = (x0, y0, x1, y1)
        except Exception:                                       # nunca romper la imagen por esto
            caja = None
        if len(self._campos) > 20000:
            self._campos.clear()
        self._campos[instancia_id] = caja
        return caja

    def _recortar(self, instancia_id, r):
        """Deja solo la zona expuesta (ver campo_expuesto). Devuelve algo con .content y .headers como la respuesta."""
        caja = self.campo_expuesto(instancia_id)
        if not caja:
            return r
        try:
            from io import BytesIO
            from types import SimpleNamespace
            from PIL import Image
            im = Image.open(BytesIO(r.content))
            w, h = im.size
            x0, y0, x1, y1 = caja
            im = im.crop((int(x0 * w), int(y0 * h), int(round(x1 * w)), int(round(y1 * h))))
            buf = BytesIO()
            im.save(buf, "JPEG", quality=90)
            return SimpleNamespace(content=buf.getvalue(), headers={"Content-Type": "image/jpeg"})
        except Exception:
            return r

    def archivo_zip(self, estudio_id):
        """Devuelve la respuesta en streaming con el ZIP del estudio."""
        return self._req("GET", f"/studies/{estudio_id}/archive", stream=True, timeout=600)

    # ---- util ----
    def _resumen(self, est):
        st = est.get("MainDicomTags", {})
        pt = est.get("PatientMainDicomTags", {})
        req = est.get("RequestedTags", {})
        mods = (req.get("ModalitiesInStudy") or st.get("ModalitiesInStudy") or "")
        mods = "/".join(m for m in mods.split("\\") if m and m not in ("SR", "PR", "KO"))
        return {
            "id": est["ID"],
            "uid": st.get("StudyInstanceUID", ""),
            "fecha_raw": st.get("StudyDate", ""),
            "hora_raw": st.get("StudyTime", ""),
            "fecha": fecha_dicom(st.get("StudyDate", "")),
            "hora": _hora(st.get("StudyTime", "")),
            "descripcion": st.get("StudyDescription", ""),
            "accession": st.get("AccessionNumber", ""),
            "institucion": st.get("InstitutionName", ""),
            "solicitante": nombre_dicom(st.get("ReferringPhysicianName", "")),
            "paciente_nombre": nombre_dicom(pt.get("PatientName", "")),
            "paciente_nombre_raw": pt.get("PatientName", ""),
            "paciente_sexo_raw": pt.get("PatientSex", ""),
            "solicitante_raw": st.get("ReferringPhysicianName", ""),
            "paciente_id": pt.get("PatientID", ""),
            "paciente_fnac_raw": pt.get("PatientBirthDate", ""),
            "paciente_fnac": fecha_dicom(pt.get("PatientBirthDate", "")),
            "paciente_sexo": {"M": "Masculino", "F": "Femenino"}.get(pt.get("PatientSex", ""), pt.get("PatientSex", "")),
            "edad": edad(pt.get("PatientBirthDate", ""), st.get("StudyDate", "")),
            "modalidad": mods,
            "n_series": len(est.get("Series", [])),
        }


def _normal(s):
    """Minúsculas y sin tildes, para comparar nombres."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def _hora(v):
    if v and len(v) >= 4:
        return f"{v[0:2]}:{v[2:4]}"
    return ""
