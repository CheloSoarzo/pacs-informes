"""Carga datos de DEMOSTRACIÓN al arrancar (solo si el PACS está vacío):
- 4 estudios DICOM sintéticos (TC cerebro, TC abdomen, ecografía, RX tórax) de pacientes ficticios.
- Un usuario de prueba por rol, con la misma clave que ADMIN_PASSWORD.
Las imágenes son figuras geométricas, no pacientes reales.
"""
import io
import math
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta

import numpy as np
import requests
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, PYDICOM_IMPLEMENTATION_UID, generate_uid
from werkzeug.security import generate_password_hash

ORTHANC = os.environ.get("ORTHANC_URL", "http://127.0.0.1:8042")
APP = "http://127.0.0.1:8000"
DB = os.path.join(os.environ.get("DATA_DIR", "/data"), "informes.db")

CT, US, CR = "1.2.840.10008.5.1.4.1.1.2", "1.2.840.10008.5.1.4.1.1.6.1", "1.2.840.10008.5.1.4.1.1.1"


def esperar(url, nombre, maximo=180):
    for _ in range(maximo):
        try:
            if requests.get(url, timeout=3).status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    print(f"[demo] {nombre} no respondió", file=sys.stderr)
    return False


def dicom(pac, est, serie, n, sop, pixeles, extra):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = PYDICOM_IMPLEMENTATION_UID

    ds = Dataset()
    ds.file_meta = meta
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.SOPClassUID = sop
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.PatientName, ds.PatientID, ds.PatientBirthDate, ds.PatientSex = pac
    ds.StudyInstanceUID = est["uid"]
    ds.StudyDate = est["fecha"]
    ds.StudyTime = est["hora"]
    ds.StudyID = est["acc"]
    ds.AccessionNumber = est["acc"]
    ds.StudyDescription = est["desc"]
    ds.InstitutionName = est["centro"]
    ds.ReferringPhysicianName = est["solicitante"]
    ds.Modality = est["mod"]
    ds.SeriesInstanceUID = serie["uid"]
    ds.SeriesNumber = serie["num"]
    ds.SeriesDescription = serie["desc"]
    ds.InstanceNumber = n
    ds.Rows, ds.Columns = pixeles.shape
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelRepresentation = 0
    if pixeles.dtype == np.uint8:
        ds.BitsAllocated, ds.BitsStored, ds.HighBit = 8, 8, 7
    else:
        ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 12, 11
    for k, v in extra.items():
        setattr(ds, k, v)
    ds.PixelData = pixeles.tobytes()
    buf = io.BytesIO()
    ds.save_as(buf, write_like_original=False)
    return buf.getvalue()


def malla(n=256):
    y, x = np.mgrid[0:n, 0:n]
    return (x - n / 2) / (n / 2), (y - n / 2) / (n / 2)


def corte_cerebro(k, total):
    x, y = malla()
    t = math.sin(math.pi * (k + 2) / (total + 4))
    hu = np.full(x.shape, -1000.0)
    r = x ** 2 / (0.78 * t) ** 2 + y ** 2 / (0.9 * t) ** 2
    hu[r <= 1] = 1200                       # calota
    hu[r <= 0.86] = 35                      # parénquima
    if t > 0.6:
        for cx in (-0.12, 0.12):            # ventrículos
            hu[((x - cx) / 0.06) ** 2 + ((y + 0.02) / 0.22) ** 2 <= 1] = 5
    hu += np.random.normal(0, 4, hu.shape)
    return np.clip(hu + 1024, 0, 4095).astype("<u2")


def corte_abdomen(k, total):
    x, y = malla()
    hu = np.full(x.shape, -1000.0)
    hu[(x / 0.85) ** 2 + (y / 0.6) ** 2 <= 1] = -100       # grasa
    hu[(x / 0.8) ** 2 + (y / 0.55) ** 2 <= 1] = 40         # partes blandas
    hu[((x + 0.35) / 0.32) ** 2 + ((y + 0.05) / 0.3) ** 2 <= 1] = 60 + k  # hígado
    hu[(x / 0.07) ** 2 + ((y - 0.38) / 0.07) ** 2 <= 1] = 900             # vértebra
    hu[((x - 0.05) / 0.05) ** 2 + ((y - 0.22) / 0.05) ** 2 <= 1] = 180    # aorta
    for cx in (-0.3, 0.3):
        hu[((x - cx) / 0.1) ** 2 + ((y - 0.2) / 0.14) ** 2 <= 1] = 150    # riñones
    hu += np.random.normal(0, 6, hu.shape)
    return np.clip(hu + 1024, 0, 4095).astype("<u2")


def eco(k):
    x, y = malla()
    img = np.zeros(x.shape)
    ang = np.arctan2(x, y + 1.1)
    rad = np.sqrt(x ** 2 + (y + 1.1) ** 2)
    abanico = (np.abs(ang) < 0.55) & (rad > 0.25) & (rad < 2.0)
    img[abanico] = 70 + np.random.normal(0, 25, abanico.sum())
    org = ((x - 0.05 * k) / 0.35) ** 2 + ((y - 0.1) / 0.22) ** 2 <= 1
    img[abanico & org] = 140 + np.random.normal(0, 18, (abanico & org).sum())
    return np.clip(img, 0, 255).astype(np.uint8)


def torax(lateral):
    x, y = malla(512)
    img = np.full(x.shape, 300.0)
    img[(np.abs(x) < 0.85) & (np.abs(y) < 0.9)] = 1800
    if lateral:
        img[((x + 0.05) / 0.5) ** 2 + (y / 0.6) ** 2 <= 1] = 700
    else:
        for cx in (-0.4, 0.4):
            img[((x - cx) / 0.3) ** 2 + (y / 0.62) ** 2 <= 1] = 600
        img[((x + 0.08) / 0.24) ** 2 + ((y - 0.25) / 0.26) ** 2 <= 1] = 2300
    img[np.abs(x) < 0.04] = 2600            # columna/esternón
    img += np.random.normal(0, 30, img.shape)
    return np.clip(img, 0, 4095).astype("<u2")


def cargar_estudios():
    hoy = date.today()
    f = lambda d: (hoy - timedelta(days=d)).strftime("%Y%m%d")
    estudios = [
        (("PEREZ SOTO^JUAN ANDRES", "12.345.678-5", "19700312", "M"),
         dict(fecha=f(0), hora="093000", acc="D1001", desc="TC CEREBRO SIN CONTRASTE", centro="RANCAGUA",
              solicitante="ROJAS^CAROLINA", mod="CT"),
         [("Axial 5 mm", 30, lambda k, t: corte_cerebro(k, t))], CT, dict(ww=80, wc=40)),
        (("SILVA CACERES^ROBERTO", "7.654.321-0", "19550120", "M"),
         dict(fecha=f(1), hora="174500", acc="D1002", desc="TC ABDOMEN Y PELVIS C/C", centro="INSI",
              solicitante="VERA^FELIPE", mod="CT"),
         [("Portal 5 mm", 30, lambda k, t: corte_abdomen(k, t))], CT, dict(ww=400, wc=40)),
        (("GONZALEZ^MARIA JOSE", "9.876.543-2", "19881120", "F"),
         dict(fecha=f(1), hora="151500", acc="D1003", desc="ECOGRAFIA ABDOMINAL", centro="INSI",
              solicitante="VERA^FELIPE", mod="US"),
         [("Abdomen", 8, lambda k, t: eco(k))], US, {}),
        (("MUÑOZ^CATALINA", "21.111.222-K", "20230105", "F"),
         dict(fecha=f(2), hora="110000", acc="D1004", desc="RX TORAX PA Y LAT", centro="RANCAGUA",
              solicitante="ROJAS^CAROLINA", mod="CR"),
         [("PA", 1, lambda k, t: torax(False)), ("LAT", 1, lambda k, t: torax(True))], CR, dict(ww=4096, wc=2048)),
    ]
    total = 0
    for pac, est, series, sop, ventana in estudios:
        est["uid"] = generate_uid()
        frame = generate_uid()
        for num, (desc, cant, gen) in enumerate(series, 1):
            serie = {"uid": generate_uid(), "num": num, "desc": desc}
            for k in range(1, cant + 1):
                extra = {}
                if sop == CT:
                    extra = dict(FrameOfReferenceUID=frame, PixelSpacing=[0.9, 0.9], SliceThickness=5,
                                 ImageOrientationPatient=[1, 0, 0, 0, 1, 0],
                                 ImagePositionPatient=[-115.2, -115.2, 5.0 * k],
                                 RescaleIntercept=-1024, RescaleSlope=1, RescaleType="HU",
                                 WindowCenter=ventana["wc"], WindowWidth=ventana["ww"], KVP=120)
                elif sop == CR:
                    extra = dict(WindowCenter=ventana["wc"], WindowWidth=ventana["ww"],
                                 ViewPosition="PA" if desc == "PA" else "LL", BodyPartExamined="CHEST")
                data = dicom(pac, est, serie, k, sop, gen(k, cant), extra)
                r = requests.post(f"{ORTHANC}/instances", data=data, timeout=30,
                                  headers={"Content-Type": "application/dicom"})
                r.raise_for_status()
                total += 1
    print(f"[demo] {total} imágenes cargadas en {len(estudios)} estudios")


def crear_usuarios():
    clave = os.environ.get("ADMIN_PASSWORD", "")
    if not clave:
        return
    usuarios = [
        ("radiologo", "Dr. Andrés López", "radiologo", "Médico Radiólogo", "Reg. SIS 000000 (demo)", "", ""),
        ("tecnologo", "TM Daniela Fuentes", "tecnologo", "Tecnóloga Médica", "", "", ""),
        ("secretaria", "Ana Secretaría", "secretaria", "", "", "", ""),
        ("derivador", "Dra. Carolina Rojas", "derivador", "Médico Cirujano", "", "", "ROJAS CAROLINA"),
        ("radiologo_insi", "Dra. Paula Reyes", "radiologo", "Médico Radiólogo", "", "INSI", ""),
    ]
    con = sqlite3.connect(DB, timeout=30)
    for u in usuarios:
        con.execute(
            "INSERT OR IGNORE INTO usuarios (usuario, nombre, rol, titulo, registro, centros, filtro_solicitante, "
            "clave_hash, creado) VALUES (?,?,?,?,?,?,?,?,?)",
            u + (generate_password_hash(clave), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    con.commit()
    con.close()
    print("[demo] usuarios de prueba listos")


if __name__ == "__main__":
    if esperar(f"{APP}/salud", "app"):
        try:
            crear_usuarios()
        except Exception as e:  # la demo no debe botar la app
            print(f"[demo] error creando usuarios: {e}", file=sys.stderr)
    if esperar(f"{ORTHANC}/system", "Orthanc"):
        try:
            if not requests.get(f"{ORTHANC}/studies", timeout=10).json():
                cargar_estudios()
        except Exception as e:
            print(f"[demo] error cargando estudios: {e}", file=sys.stderr)
