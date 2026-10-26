"""Base de datos SQLite de la app de informes (las imágenes viven en Orthanc)."""
import os
import sqlite3
from datetime import datetime

from flask import current_app, g
from werkzeug.security import generate_password_hash

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY,
    usuario TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    clave_hash TEXT NOT NULL,
    rol TEXT NOT NULL DEFAULT 'radiologo',      -- admin | radiologo | tecnologo | secretaria | derivador
    titulo TEXT DEFAULT '',                      -- ej: Médico Radiólogo
    registro TEXT DEFAULT '',                    -- RUT o N° registro Superintendencia
    firma_png TEXT DEFAULT '',                   -- ruta a imagen de firma
    centros TEXT DEFAULT '',                     -- centros permitidos, separados por coma (vacío = todos)
    filtro_solicitante TEXT DEFAULT '',          -- derivador: su nombre como aparece en el DICOM
    activo INTEGER NOT NULL DEFAULT 1,
    creado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plantillas (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    modalidad TEXT NOT NULL DEFAULT '',          -- CT, CR/DX, US... vacío = todas
    cuerpo TEXT NOT NULL,
    activo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS informes (
    id INTEGER PRIMARY KEY,
    estudio_id TEXT UNIQUE NOT NULL,             -- ID interno de Orthanc
    estudio_uid TEXT NOT NULL,                   -- StudyInstanceUID
    paciente_nombre TEXT, paciente_id TEXT, paciente_fnac TEXT, paciente_sexo TEXT,
    fecha_estudio TEXT, modalidad TEXT, descripcion TEXT, accession TEXT,
    institucion TEXT, solicitante TEXT,
    cuerpo TEXT NOT NULL DEFAULT '',
    estado TEXT NOT NULL DEFAULT 'borrador',     -- borrador | firmado
    version INTEGER NOT NULL DEFAULT 1,
    creado_por INTEGER, creado TEXT,
    actualizado_por INTEGER, actualizado TEXT,
    firmado_por INTEGER, firmado_en TEXT
);

CREATE TABLE IF NOT EXISTS informe_versiones (
    id INTEGER PRIMARY KEY,
    informe_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    cuerpo TEXT NOT NULL,
    firmado_por INTEGER, firmado_en TEXT,
    reabierto_por INTEGER, reabierto_en TEXT, motivo TEXT
);

CREATE TABLE IF NOT EXISTS links (
    id INTEGER PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    informe_id INTEGER NOT NULL,
    clave TEXT DEFAULT '',
    creado_por INTEGER, creado TEXT NOT NULL,
    expira TEXT NOT NULL,
    revocado INTEGER NOT NULL DEFAULT 0,
    accesos INTEGER NOT NULL DEFAULT 0,
    ultimo_acceso TEXT
);

CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    usuario TEXT, accion TEXT NOT NULL, detalle TEXT, ip TEXT
);

CREATE INDEX IF NOT EXISTS ix_informes_estado ON informes(estado);
CREATE INDEX IF NOT EXISTS ix_links_informe ON links(informe_id);
CREATE INDEX IF NOT EXISTS ix_auditoria_ts ON auditoria(ts);
"""

PLANTILLAS_INICIALES = [
    ("Radiografía de tórax PA y lateral", "CR,DX", """TÉCNICA:
Radiografía de tórax en proyecciones PA y lateral.

HALLAZGOS:
Silueta cardiomediastínica de tamaño y morfología normal.
Hilios pulmonares de aspecto conservado.
Parénquima pulmonar sin consolidaciones, nódulos ni masas.
No se observa derrame pleural ni neumotórax.
Senos costofrénicos libres.
Estructuras óseas visibles sin alteraciones significativas.

IMPRESIÓN:
Radiografía de tórax sin hallazgos patológicos significativos."""),
    ("Radiografía genérica", "CR,DX", """TÉCNICA:
Radiografía de ______ en proyecciones ______.

HALLAZGOS:
Relaciones articulares conservadas.
No se observan líneas de fractura ni lesiones óseas focales.
Partes blandas sin alteraciones.

IMPRESIÓN:
Estudio radiográfico sin hallazgos patológicos significativos."""),
    ("TC de cerebro sin contraste", "CT", """TÉCNICA:
Tomografía computada de cerebro sin contraste endovenoso, con reconstrucciones multiplanares.

HALLAZGOS:
No se observan colecciones extraaxiales.
Parénquima encefálico de densidad normal, con adecuada diferenciación entre sustancia gris y blanca.
No hay signos de hemorragia, efecto de masa ni desviación de la línea media.
Sistema ventricular y espacios subaracnoideos de tamaño y morfología normal.
Fosa posterior sin alteraciones.
Calota y base de cráneo sin lesiones óseas.
Senos paranasales y celdillas mastoideas visibles neumatizados.

IMPRESIÓN:
TC de cerebro sin hallazgos patológicos agudos."""),
    ("TC de abdomen y pelvis con contraste", "CT", """TÉCNICA:
Tomografía computada de abdomen y pelvis con contraste endovenoso, en fase portal, con reconstrucciones multiplanares.

HALLAZGOS:
Hígado de tamaño normal, contornos regulares y densidad homogénea, sin lesiones focales.
Vía biliar intra y extrahepática no dilatada. Vesícula biliar sin alteraciones.
Páncreas, bazo y glándulas suprarrenales sin alteraciones.
Riñones de tamaño y posición normal, con adecuada captación de contraste. Sin litiasis ni hidroureteronefrosis.
Vejiga de paredes regulares.
Asas intestinales de calibre y distribución normal.
No se observan adenopatías ni líquido libre.
Estructuras óseas sin lesiones focales.

IMPRESIÓN:
TC de abdomen y pelvis sin hallazgos patológicos significativos."""),
    ("TC genérica", "CT", """TÉCNICA:
Tomografía computada de ______, ______ contraste endovenoso, con reconstrucciones multiplanares.

HALLAZGOS:


IMPRESIÓN:
"""),
    ("Ecografía abdominal", "US", """TÉCNICA:
Ecografía abdominal con transductor convexo.

HALLAZGOS:
Hígado de tamaño normal, ecogenicidad homogénea y contornos regulares, sin lesiones focales.
Vesícula biliar de paredes finas, sin cálculos en su interior.
Vía biliar no dilatada. Colédoco de calibre normal.
Páncreas visible sin alteraciones.
Bazo de tamaño y ecoestructura normal.
Riñones de tamaño y ecogenicidad normal, con adecuada diferenciación corticomedular. Sin litiasis ni dilatación pielocalicial.
Aorta abdominal de calibre normal.
No se observa líquido libre.

IMPRESIÓN:
Ecografía abdominal dentro de límites normales."""),
    ("Ecografía tiroidea", "US", """TÉCNICA:
Ecografía cervical con transductor lineal de alta frecuencia.

HALLAZGOS:
Glándula tiroides de tamaño normal y ecoestructura homogénea.
Lóbulo derecho: __ x __ x __ mm.
Lóbulo izquierdo: __ x __ x __ mm.
Istmo: __ mm.
No se observan nódulos.
Vascularización al Doppler color conservada.
No se observan adenopatías cervicales de aspecto patológico.

IMPRESIÓN:
Ecografía tiroidea dentro de límites normales."""),
    ("Ecografía genérica", "US", """TÉCNICA:
Ecografía de ______ con transductor ______.

HALLAZGOS:


IMPRESIÓN:
"""),
]


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    if "db" not in g:
        path = os.path.join(current_app.config["DATA_DIR"], "informes.db")
        g.db = sqlite3.connect(path, timeout=30)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(app):
    os.makedirs(app.config["DATA_DIR"], exist_ok=True)
    os.makedirs(os.path.join(app.config["DATA_DIR"], "firmas"), exist_ok=True)
    path = os.path.join(app.config["DATA_DIR"], "informes.db")
    con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    # Migraciones de versiones anteriores
    cols = {r[1] for r in con.execute("PRAGMA table_info(usuarios)")}
    for col in ("centros", "filtro_solicitante"):
        if col not in cols:
            con.execute(f"ALTER TABLE usuarios ADD COLUMN {col} TEXT DEFAULT ''")
    # Bloqueo para que varios procesos (gunicorn) no siembren datos dos veces
    con.execute("BEGIN IMMEDIATE")

    # Primer administrador
    if con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
        clave = app.config["ADMIN_PASSWORD"]
        if not clave:
            raise RuntimeError("Define ADMIN_PASSWORD en .env para crear el primer usuario.")
        con.execute(
            "INSERT INTO usuarios (usuario, nombre, clave_hash, rol, creado) VALUES (?,?,?,?,?)",
            (app.config["ADMIN_USER"], "Administrador", generate_password_hash(clave), "admin", ahora()),
        )

    if con.execute("SELECT COUNT(*) FROM plantillas").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO plantillas (nombre, modalidad, cuerpo) VALUES (?,?,?)", PLANTILLAS_INICIALES
        )
    con.commit()
    con.close()
    app.teardown_appcontext(close_db)


def auditar(usuario, accion, detalle="", ip=""):
    db = get_db()
    db.execute(
        "INSERT INTO auditoria (ts, usuario, accion, detalle, ip) VALUES (?,?,?,?,?)",
        (ahora(), usuario, accion, detalle, ip),
    )
    db.commit()
