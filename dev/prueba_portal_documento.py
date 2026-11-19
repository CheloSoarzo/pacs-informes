"""v1.1.71: en el portal, un paciente con pasaporte o documento extranjero (clave «P…») ve sus informes validados
aunque su cita no haya quedado unida al estudio. Un RUT con los mismos dígitos no los ve.
Corre sin servidor: usa una base temporal."""
import os
import sys
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.setdefault("ADMIN_PASSWORD", "clave-prueba-123")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "informes"))
import app as _app  # noqa: E402,F401
from core import app, get_db  # noqa: E402
import integracion as ig  # noqa: E402

fallas = 0


def chk(c, m):
    global fallas
    fallas += (not c)
    print(("OK   " if c else "FALLA ") + m)


with app.app_context():
    db = get_db()
    filas = [("e1", "111178314-7", "ECO MAMARIA BILATERAL"),      # documento (RUT mal escrito) → clave P1111783147
             ("e2", "PAS AB123456", "RX TORAX"),                  # extranjero registrado con PAS
             ("e3", "12.345.678-5", "TC CEREBRO"),                # RUT válido
             ("e4", "12.345.678-0", "ECO ABDOMINAL")]             # RUT mal escrito (casi el mismo de arriba)
    for i, (eid, pid, desc) in enumerate(filas, 1):
        db.execute("""INSERT INTO informes (estudio_id, estudio_uid, paciente_id, fecha_estudio, descripcion, estado, firmado_en)
                      VALUES (?,?,?,?,?,'firmado','2026-11-18 12:05')""", (eid, f"1.2.{i}", pid, "20261116", desc))
    db.commit()
    v = lambda clave: {e["examen"] for e in ig._examenes_de(clave) if e["estado"] == "listo"}  # noqa: E731
    chk(v(ig.doc_norm("111178314-7")) == {"ECO MAMARIA BILATERAL"}, "documento «111178314-7» ve su informe validado")
    chk(v(ig.doc_norm("1111783147")) == {"ECO MAMARIA BILATERAL"}, "escrito sin guion, igual lo ve")
    chk(v("PPASAB123456") == {"RX TORAX"}, "extranjero registrado con PAS ve su informe")
    chk(v(ig.doc_norm("12.345.678-5")) == {"TC CEREBRO"}, "paciente con RUT válido: solo el suyo")
    chk(v(ig.doc_norm("12.345.678-0")) == {"ECO ABDOMINAL"}, "RUT mal escrito (documento): solo el suyo, no el del RUT parecido")
    # v1.1.72: extranjero registrado con «PAS» (botón de la v1.1.64 o casilla «Paciente extranjero»): la clave es
    # PPAS…, pero en un equipo sin worklist el tecnólogo escribe solo el número
    db.execute("""INSERT INTO informes (estudio_id, estudio_uid, paciente_id, fecha_estudio, descripcion, estado, firmado_en)
                  VALUES ('e5','1.2.5','AB-987654','20261117','ECO TIROIDES','firmado','2026-11-18 12:05')""")
    db.commit()
    chk(v("PPAS1111783147") == {"ECO MAMARIA BILATERAL"}, "clave PPAS + número: ve el informe cuyas imágenes traen solo el número")
    chk(v("PPASAB987654") == {"ECO TIROIDES"}, "clave PPAS + pasaporte con letras: ve su informe")
    chk(ig.mismo_doc(ig.doc_norm("AB-987654"), "PPASAB987654") and not ig.mismo_doc("P123456780", "123456785"),
        "mismo documento con y sin PAS; un RUT válido nunca se confunde")
    chk(ig.doc_claves("PPASAB987654") == ["PPASAB987654", "PAB987654"] and ig.doc_claves("123456785") == ["123456785"],
        "claves equivalentes del documento")
print("\nFALLAS:", fallas)
