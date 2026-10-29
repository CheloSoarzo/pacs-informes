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
    rol TEXT NOT NULL DEFAULT '',               -- (versión antigua; ahora se usa perfil_id)
    perfil_id INTEGER,
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
    estado TEXT NOT NULL DEFAULT 'borrador',     -- borrador | por_validar | firmado
    hora_estudio TEXT DEFAULT '',
    transcrito_por INTEGER, transcrito_en TEXT,
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

CREATE TABLE IF NOT EXISTS perfiles (
    id INTEGER PRIMARY KEY,
    clave TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    descripcion TEXT DEFAULT '',
    permisos TEXT NOT NULL DEFAULT '',           -- claves separadas por coma (ver permisos.py)
    solo_solicitados INTEGER NOT NULL DEFAULT 0, -- solo ve estudios donde figura como solicitante
    activo INTEGER NOT NULL DEFAULT 1
);

-- Datos de trabajo por estudio que no van en el DICOM
CREATE TABLE IF NOT EXISTS estudio_meta (
    estudio_id TEXT PRIMARY KEY,
    radiologo_id INTEGER, asignado_por INTEGER, asignado_en TEXT,
    prioridad TEXT NOT NULL DEFAULT 'normal',    -- normal | urgente
    paciente_email TEXT DEFAULT '', paciente_telefono TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS dictados (
    id INTEGER PRIMARY KEY,
    estudio_id TEXT NOT NULL,
    paciente_nombre TEXT, paciente_id TEXT, descripcion TEXT, modalidad TEXT,
    fecha_estudio TEXT, institucion TEXT, solicitante TEXT,
    archivo TEXT NOT NULL, mime TEXT, duracion REAL DEFAULT 0,
    creado_por INTEGER NOT NULL, creado TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente',    -- pendiente | tomado | transcrito | anulado
    transcriptor_id INTEGER, tomado_en TEXT, transcrito_en TEXT,
    texto_auto TEXT DEFAULT '',                  -- borrador automático (Whisper), si está configurado
    nota TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS frases (
    id INTEGER PRIMARY KEY,
    tipo TEXT NOT NULL DEFAULT 'hallazgo',       -- hallazgo | conclusion
    modalidad TEXT NOT NULL DEFAULT '',          -- vacío = todas
    atajo TEXT DEFAULT '',                       -- se escribe .atajo + espacio en el editor
    titulo TEXT NOT NULL,
    texto TEXT NOT NULL,
    activo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS entregas (
    id INTEGER PRIMARY KEY,
    estudio_id TEXT NOT NULL,
    tipo TEXT NOT NULL,                          -- retiro | cd | placas | impreso | email | otro
    detalle TEXT DEFAULT '',
    usuario_id INTEGER, creado TEXT NOT NULL,
    anulado INTEGER NOT NULL DEFAULT 0, anulado_por INTEGER, anulado_en TEXT
);

CREATE TABLE IF NOT EXISTS notas (
    id INTEGER PRIMARY KEY,
    estudio_id TEXT NOT NULL,
    usuario_id INTEGER NOT NULL,
    texto TEXT DEFAULT '',
    archivo TEXT DEFAULT '', archivo_nombre TEXT DEFAULT '',
    creado TEXT NOT NULL,
    eliminado INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS filtros (
    id INTEGER PRIMARY KEY,
    usuario_id INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    query TEXT NOT NULL
);

-- Equipos autorizados a enviar estudios por internet (lista blanca DICOM)
CREATE TABLE IF NOT EXISTS equipos (
    id INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,                        -- ej: TC Rancagua
    centro TEXT DEFAULT '',
    aet TEXT NOT NULL,                           -- AE Title propio del equipo
    ip TEXT NOT NULL,                            -- IP pública fija del centro
    activo INTEGER NOT NULL DEFAULT 1,
    notas TEXT DEFAULT '',
    creado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ajustes (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_informes_estado ON informes(estado);
CREATE INDEX IF NOT EXISTS ix_dictados_estudio ON dictados(estudio_id);
CREATE INDEX IF NOT EXISTS ix_dictados_estado ON dictados(estado);
CREATE INDEX IF NOT EXISTS ix_entregas_estudio ON entregas(estudio_id);
CREATE INDEX IF NOT EXISTS ix_notas_estudio ON notas(estudio_id);
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


FRASES_INICIALES = [
    ("hallazgo", "CT", "hemo", "Sin hemorragia",
     "No hay signos de hemorragia intra ni extraaxial, efecto de masa ni desviación de la línea media."),
    ("hallazgo", "CT", "vent", "Ventrículos normales",
     "Sistema ventricular y espacios subaracnoideos de tamaño y morfología normal para la edad."),
    ("hallazgo", "CT", "isq", "Leucoaraiosis",
     "Hipodensidades de la sustancia blanca periventricular, sugerentes de enfermedad de pequeño vaso."),
    ("hallazgo", "CT", "quiste", "Quiste hepático simple",
     "Lesión hipodensa de ___ mm en el segmento ___ hepático, de densidad líquida y sin realce, "
     "compatible con quiste simple."),
    ("hallazgo", "CT", "litiasis", "Litiasis renal",
     "Litiasis de ___ mm en el grupo calicial ___ del riñón ___, sin dilatación de la vía excretora."),
    ("hallazgo", "US", "higado", "Hígado normal",
     "Hígado de tamaño normal, contornos regulares y ecoestructura homogénea, sin lesiones focales."),
    ("hallazgo", "US", "esteatosis", "Esteatosis hepática",
     "Hígado de tamaño normal con aumento difuso de la ecogenicidad, compatible con esteatosis."),
    ("hallazgo", "US", "colelitiasis", "Colelitiasis",
     "Vesícula biliar con cálculos móviles en su interior, el mayor de ___ mm, sin engrosamiento parietal."),
    ("hallazgo", "US", "nodulo", "Nódulo tiroideo",
     "Nódulo ___ en el lóbulo ___ de ___ x ___ mm, ___ (TI-RADS ___)."),
    ("hallazgo", "CR,DX", "torax", "Tórax normal",
     "Silueta cardiomediastínica de tamaño normal. Parénquima pulmonar sin consolidaciones ni nódulos. "
     "Sin derrame pleural."),
    ("hallazgo", "CR,DX", "fx", "Sin fractura", "No se observan líneas de fractura ni lesiones óseas focales."),
    ("conclusion", "", "normal", "Sin hallazgos",
     "Estudio sin hallazgos patológicos significativos."),
    ("conclusion", "", "control", "Sugiere control", "Se sugiere control evolutivo en ___ meses."),
    ("conclusion", "", "correlacion", "Correlación clínica", "Se sugiere correlación clínica y de laboratorio."),
    ("conclusion", "CT", "noagudo", "Sin patología aguda", "Sin hallazgos patológicos agudos."),
    ("conclusion", "US", "esteatosis", "Esteatosis", "Esteatosis hepática."),
    ("conclusion", "US", "colelitiasis", "Colelitiasis", "Colelitiasis sin signos ecográficos de colecistitis."),
]

AJUSTES_INICIALES = {
    "correo_asunto": "Su informe de {centro} está disponible",
    "correo_cuerpo": (
        "Estimado(a) {paciente}:\n\n"
        "El informe de su examen {examen} del {fecha} ya está disponible.\n\n"
        "Puede verlo, descargarlo y revisar sus imágenes aquí:\n{link}\n{linea_clave}\n"
        "Adjuntamos también el informe en PDF.\n\n"
        "Atentamente,\n{centro}\n{telefono_centro}"),
    "envio_auto_email": "0",                     # 1 = enviar correo al validar si hay email del paciente
    "webhook_url": "",                           # URL que recibe un aviso (POST JSON) al validar
    "horas_alerta": "24",                        # horas sin informar antes de marcar en rojo
    "horas_alerta_urgente": "2",
}


def ajuste(clave, defecto=""):
    fila = get_db().execute("SELECT valor FROM ajustes WHERE clave=?", (clave,)).fetchone()
    return fila["valor"] if fila else AJUSTES_INICIALES.get(clave, defecto)


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _columnas(con, tabla):
    return {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}


def _migrar(con):
    """Actualiza bases de datos creadas por versiones anteriores."""
    from permisos import PERFILES_INICIALES, PERMISOS_NUEVOS, ROL_A_PERFIL
    cols = _columnas(con, "usuarios")
    for col in ("centros", "filtro_solicitante"):
        if col not in cols:
            con.execute(f"ALTER TABLE usuarios ADD COLUMN {col} TEXT DEFAULT ''")
    if "perfil_id" not in cols:
        con.execute("ALTER TABLE usuarios ADD COLUMN perfil_id INTEGER")
    cols = _columnas(con, "informes")
    for col, tipo in (("hora_estudio", "TEXT DEFAULT ''"), ("transcrito_por", "INTEGER"),
                      ("transcrito_en", "TEXT")):
        if col not in cols:
            con.execute(f"ALTER TABLE informes ADD COLUMN {col} {tipo}")

    # Permisos nuevos de esta versión → a los perfiles de fábrica que ya existían (una sola vez por permiso)
    hay_perfiles = con.execute("SELECT COUNT(*) FROM perfiles").fetchone()[0] > 0
    fila = con.execute("SELECT valor FROM ajustes WHERE clave='permisos_migrados'").fetchone()
    migrados = set((fila[0] if fila else "").split(","))
    for permiso in PERMISOS_NUEVOS:
        if permiso in migrados:
            continue
        if hay_perfiles:
            for clave, _, _, permisos, _ in PERFILES_INICIALES:
                if permiso not in permisos:
                    continue
                f = con.execute("SELECT id, permisos FROM perfiles WHERE clave=?", (clave,)).fetchone()
                if f and permiso not in f[1].split(","):
                    con.execute("UPDATE perfiles SET permisos=? WHERE id=?",
                                (",".join(p for p in (f[1].split(",") + [permiso]) if p), f[0]))
        migrados.add(permiso)
    con.execute("INSERT OR REPLACE INTO ajustes (clave, valor) VALUES ('permisos_migrados', ?)",
                (",".join(sorted(p for p in migrados if p)),))

    # Perfiles iniciales (solo si no existen; después los edita el administrador)
    for clave, nombre, desc, permisos, solo in PERFILES_INICIALES:
        con.execute("INSERT OR IGNORE INTO perfiles (clave, nombre, descripcion, permisos, solo_solicitados) "
                    "VALUES (?,?,?,?,?)", (clave, nombre, desc, ",".join(permisos), solo))
    # Usuarios de la versión con roles fijos → perfil equivalente
    for uid, rol in con.execute("SELECT id, rol FROM usuarios WHERE perfil_id IS NULL").fetchall():
        clave = ROL_A_PERFIL.get(rol or "", "tecnico")
        pid = con.execute("SELECT id FROM perfiles WHERE clave=?", (clave,)).fetchone()[0]
        con.execute("UPDATE usuarios SET perfil_id=? WHERE id=?", (pid, uid))


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
    for sub in ("firmas", "dictados", "adjuntos"):
        os.makedirs(os.path.join(app.config["DATA_DIR"], sub), exist_ok=True)
    path = os.path.join(app.config["DATA_DIR"], "informes.db")
    con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    # Bloqueo para que varios procesos (gunicorn) no migren ni siembren datos dos veces
    con.execute("BEGIN IMMEDIATE")
    _migrar(con)

    # Primer administrador
    if con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0:
        clave = app.config["ADMIN_PASSWORD"]
        if not clave:
            raise RuntimeError("Define ADMIN_PASSWORD en .env para crear el primer usuario.")
        admin_id = con.execute("SELECT id FROM perfiles WHERE clave='admin'").fetchone()[0]
        con.execute(
            "INSERT INTO usuarios (usuario, nombre, clave_hash, rol, perfil_id, creado) VALUES (?,?,?,?,?,?)",
            (app.config["ADMIN_USER"], "Administrador", generate_password_hash(clave), "admin", admin_id, ahora()),
        )

    if con.execute("SELECT COUNT(*) FROM plantillas").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO plantillas (nombre, modalidad, cuerpo) VALUES (?,?,?)", PLANTILLAS_INICIALES
        )
    if con.execute("SELECT COUNT(*) FROM frases").fetchone()[0] == 0:
        con.executemany(
            "INSERT INTO frases (tipo, modalidad, atajo, titulo, texto) VALUES (?,?,?,?,?)", FRASES_INICIALES
        )
    for k, v in AJUSTES_INICIALES.items():
        con.execute("INSERT OR IGNORE INTO ajustes (clave, valor) VALUES (?,?)", (k, v))
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
