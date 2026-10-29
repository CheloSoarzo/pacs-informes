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
    """'20261023' -> '23-10-2026'."""
    if valor and len(valor) >= 8 and valor[:8].isdigit():
        return f"{valor[6:8]}-{valor[4:6]}-{valor[0:4]}"
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
                "numero": _int(tags.get("SeriesNumber")),
                "n_instancias": len(s.get("Instances", [])),
            })
        out.sort(key=lambda s: s["numero"])
        return out

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

    # ---- equipos remotos (modalidades DICOM) ----
    def modalidades(self):
        return self.get_json("/modalities", params={"expand": ""}) or {}

    def guardar_modalidad(self, nombre, aet, host, permitir_store=True):
        r = self._req("PUT", f"/modalities/{nombre}", json={
            "AET": aet, "Host": host, "Port": 104, "Manufacturer": "Generic",
            "AllowEcho": True, "AllowStore": permitir_store, "AllowFind": False, "AllowMove": False,
            "AllowStorageCommitment": False})
        if r is None:
            raise OrthancError("El PACS no aceptó el equipo (¿DicomModalitiesInDatabase activado?)")

    def borrar_modalidad(self, nombre):
        self._req("DELETE", f"/modalities/{nombre}")

    def preview(self, instancia_id):
        r = self._req("GET", f"/instances/{instancia_id}/preview",
                      headers={"Accept": "image/jpeg"})
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
