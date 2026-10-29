"""Orthanc simulado para probar la app de informes sin equipos ni Docker.

    python dev/mock_orthanc.py      (escucha en http://localhost:8042)

Solo implementa los endpoints que usa la app, con 3 estudios de ejemplo.
"""
import io
import math
import zipfile
from datetime import date, timedelta

from flask import Flask, Response, abort, jsonify, request
from PIL import Image, ImageDraw

app = Flask(__name__)
hoy = date.today()


def d(dias):
    return (hoy - timedelta(days=dias)).strftime("%Y%m%d")


ESTUDIOS = {
    "est-ct": dict(StudyInstanceUID="1.2.826.0.1.1", StudyDate=d(0), StudyTime="093012",
                   StudyDescription="TC CEREBRO SIN CONTRASTE", AccessionNumber="A1001",
                   InstitutionName="CENTRO NORTE", ReferringPhysicianName="ROJAS^CAROLINA",
                   paciente=dict(PatientName="PEREZ SOTO^JUAN ANDRES", PatientID="12.345.678-5",
                                 PatientBirthDate="19700312", PatientSex="M"),
                   series=[("CT", "Axial 5mm", 40), ("CT", "Coronal", 24)]),
    "est-us": dict(StudyInstanceUID="1.2.826.0.1.2", StudyDate=d(1), StudyTime="151500",
                   StudyDescription="ECOGRAFIA ABDOMINAL", AccessionNumber="A1002",
                   InstitutionName="CENTRO SUR", ReferringPhysicianName="",
                   paciente=dict(PatientName="GONZALEZ^MARIA JOSE", PatientID="9876543-2",
                                 PatientBirthDate="19881120", PatientSex="F"),
                   series=[("US", "Abdomen", 12)]),
    "est-rx": dict(StudyInstanceUID="1.2.826.0.1.3", StudyDate=d(3), StudyTime="110000",
                   StudyDescription="RX TORAX PA Y LAT", AccessionNumber="A1003",
                   InstitutionName="CENTRO NORTE", ReferringPhysicianName="MUÑOZ^PEDRO",
                   paciente=dict(PatientName="MUÑOZ^CATALINA", PatientID="21.111.222-K",
                                 PatientBirthDate="20230105", PatientSex="F"),
                   series=[("CR", "PA", 1), ("CR", "LAT", 1)]),
    "est-ct-previo": dict(StudyInstanceUID="1.2.826.0.1.4", StudyDate=d(200), StudyTime="080000",
                   StudyDescription="TC CEREBRO SIN CONTRASTE", AccessionNumber="A0900",
                   InstitutionName="CENTRO NORTE", ReferringPhysicianName="ROJAS^CAROLINA",
                   paciente=dict(PatientName="PEREZ SOTO^JUAN ANDRES", PatientID="12.345.678-5",
                                 PatientBirthDate="19700312", PatientSex="M"),
                   series=[("CT", "Axial 5mm", 10)]),
}

SERIES, INSTANCIAS = {}, {}
for eid, e in ESTUDIOS.items():
    e["series_ids"] = []
    for n, (mod, desc, cant) in enumerate(e["series"], 1):
        sid = f"{eid}-s{n}"
        e["series_ids"].append(sid)
        SERIES[sid] = dict(estudio=eid, Modality=mod, SeriesDescription=desc, SeriesNumber=str(n),
                           instancias=[f"{sid}-i{k}" for k in range(1, cant + 1)])
        for k, iid in enumerate(SERIES[sid]["instancias"], 1):
            INSTANCIAS[iid] = dict(serie=sid, estudio=eid, InstanceNumber=str(k), total=cant, mod=mod)


def estudio_json(eid):
    e = ESTUDIOS[eid]
    tags = {k: v for k, v in e.items() if k[0].isupper()}
    mods = sorted({SERIES[s]["Modality"] for s in e["series_ids"]})
    return {"ID": eid, "Type": "Study", "MainDicomTags": tags,
            "PatientMainDicomTags": e["paciente"], "Series": e["series_ids"],
            "RequestedTags": {"ModalitiesInStudy": "\\".join(mods)}}


def coincide(valor, patron):
    import fnmatch
    return fnmatch.fnmatch((valor or "").lower(), patron.lower())


@app.post("/tools/find")
def find():
    q = request.get_json()["Query"]
    out = []
    for eid, e in ESTUDIOS.items():
        ok = True
        if "StudyDate" in q:
            a, _, b = q["StudyDate"].partition("-")
            ok &= (not a or e["StudyDate"] >= a) and (not b or e["StudyDate"] <= b)
        if "PatientName" in q:
            ok &= coincide(e["paciente"]["PatientName"], q["PatientName"])
        if "PatientID" in q:
            ok &= coincide(e["paciente"]["PatientID"], q["PatientID"])
        if "ReferringPhysicianName" in q:
            ok &= coincide(e["ReferringPhysicianName"], q["ReferringPhysicianName"])
        if "AccessionNumber" in q:
            ok &= e["AccessionNumber"] == q["AccessionNumber"]
        if "ModalitiesInStudy" in q:
            pedidas = set(q["ModalitiesInStudy"].split("\\"))
            ok &= bool(pedidas & {SERIES[s]["Modality"] for s in e["series_ids"]})
        if ok:
            out.append(estudio_json(eid))
    return jsonify(out)


MODALIDADES = {}


@app.get("/modalities")
def modalidades():
    return jsonify(MODALIDADES if "expand" in request.args else list(MODALIDADES))


@app.put("/modalities/<nombre>")
def poner_modalidad(nombre):
    MODALIDADES[nombre] = request.get_json()
    return jsonify({})


@app.delete("/modalities/<nombre>")
def borrar_modalidad(nombre):
    if MODALIDADES.pop(nombre, None) is None:
        abort(404)
    return jsonify({})


@app.post("/tools/lookup")
def lookup():
    uid = request.get_data(as_text=True).strip()
    return jsonify([{"ID": eid, "Type": "Study", "Path": f"/studies/{eid}"}
                    for eid, e in ESTUDIOS.items() if e["StudyInstanceUID"] == uid])


PACIENTE_TAGS = {"PatientName", "PatientID", "PatientBirthDate", "PatientSex"}


@app.post("/studies/<eid>/modify")
def modificar(eid):
    if eid not in ESTUDIOS:
        abort(404)
    rep = request.get_json().get("Replace", {})
    e = ESTUDIOS[eid]
    nuevo = eid
    if "PatientID" in rep and rep["PatientID"] != e["paciente"]["PatientID"]:
        nuevo = eid + "-mod"
    destino = dict(e, paciente=dict(e["paciente"]))
    for k, v in rep.items():
        (destino["paciente"] if k in PACIENTE_TAGS else destino)[k] = v
    ESTUDIOS[nuevo] = destino
    for sid in e["series_ids"]:
        SERIES[sid]["estudio"] = nuevo
        for iid in SERIES[sid]["instancias"]:
            INSTANCIAS[iid]["estudio"] = nuevo
    return jsonify({"ID": nuevo, "Path": f"/studies/{nuevo}", "Type": "Study"})


@app.delete("/studies/<eid>")
def borrar_estudio(eid):
    if ESTUDIOS.pop(eid, None) is None:
        abort(404)
    return jsonify({})


@app.get("/studies/<eid>")
def study(eid):
    if eid not in ESTUDIOS:
        abort(404)
    return jsonify(estudio_json(eid))


@app.get("/studies/<eid>/series")
def study_series(eid):
    if eid not in ESTUDIOS:
        abort(404)
    return jsonify([{"ID": s, "MainDicomTags": {k: v for k, v in SERIES[s].items() if k[0].isupper()},
                     "Instances": SERIES[s]["instancias"]} for s in ESTUDIOS[eid]["series_ids"]])


@app.get("/series/<sid>/instances")
def series_instances(sid):
    if sid not in SERIES:
        abort(404)
    lista = [{"ID": i, "MainDicomTags": {"InstanceNumber": INSTANCIAS[i]["InstanceNumber"]}}
             for i in SERIES[sid]["instancias"]]
    return jsonify(list(reversed(lista)))  # desordenado a propósito


@app.get("/instances/<iid>/study")
def instance_study(iid):
    if iid not in INSTANCIAS:
        abort(404)
    return jsonify({"ID": INSTANCIAS[iid]["estudio"]})


@app.get("/instances/<iid>/preview")
def preview(iid):
    if iid not in INSTANCIAS:
        abort(404)
    inst = INSTANCIAS[iid]
    k, total = int(inst["InstanceNumber"]), inst["total"]
    im = Image.new("L", (512, 512), 0)
    dr = ImageDraw.Draw(im)
    if inst["mod"] == "CT":
        r = 200 * math.sin(math.pi * (k + 2) / (total + 4))
        dr.ellipse([256 - r, 256 - r * 1.15, 256 + r, 256 + r * 1.15], fill=235)
        dr.ellipse([256 - r + 14, 256 - r * 1.15 + 14, 256 + r - 14, 256 + r * 1.15 - 14], fill=95)
        dr.ellipse([226, 236, 246, 276], fill=40); dr.ellipse([266, 236, 286, 276], fill=40)
    elif inst["mod"] == "US":
        dr.pieslice([56, -200, 456, 500], 55, 125, fill=60)
        dr.ellipse([200 + k * 4, 220, 300 + k * 4, 300], fill=150)
    else:
        dr.rectangle([60, 40, 452, 480], fill=70)
        dr.ellipse([100, 100, 240, 420], fill=25); dr.ellipse([272, 100, 412, 420], fill=25)
        dr.ellipse([210, 250, 330, 400], fill=170)
    dr.text((10, 490), f"{inst['mod']} {k}/{total}", fill=255)
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return Response(buf.getvalue(), mimetype="image/jpeg")


@app.get("/studies/<eid>/archive")
def archive(eid):
    if eid not in ESTUDIOS:
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("LEEME.txt", "ZIP de prueba (Orthanc simulado)")
    return Response(buf.getvalue(), mimetype="application/zip")


if __name__ == "__main__":
    app.run(port=8042)
