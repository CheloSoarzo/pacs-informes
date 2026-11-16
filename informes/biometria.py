"""Ingreso al portal del paciente con huella o cara (WebAuthn / «llaves de acceso»).

Cómo funciona:
1. El paciente entra la primera vez con su RUT y la clave de la hoja.
2. En «Mis exámenes» puede activar «Entrar con huella o cara»: el teléfono crea un par de llaves;
   la privada queda dentro del teléfono (protegida por la huella o la cara) y aquí solo se guarda la PÚBLICA.
3. Las veces siguientes, el teléfono firma un desafío con la llave privada (después de pedir huella o cara)
   y aquí se comprueba la firma con la llave pública. La huella o la cara nunca salen del teléfono.

La clave de la hoja sigue sirviendo siempre (por ejemplo, si cambia de teléfono).
Tabla nueva: portal_biometria (solo llaves públicas). No modifica ninguna tabla existente.
"""
import base64
import hashlib
import json
import os
import struct
from datetime import datetime, timedelta

from flask import abort, jsonify, request, session

from core import Config, app
from db import ahora, auditar, get_db

SCHEMA_BIOMETRIA = """
CREATE TABLE IF NOT EXISTS portal_biometria (
    cred_id TEXT PRIMARY KEY,                -- identificador de la llave (base64url)
    rut_norm TEXT NOT NULL,
    clave_publica BLOB NOT NULL,             -- llave pública en formato COSE (la privada nunca sale del teléfono)
    contador INTEGER NOT NULL DEFAULT 0,     -- contador de firmas (detecta llaves copiadas)
    dispositivo TEXT DEFAULT '',             -- descripción del teléfono, para que el paciente la reconozca
    creado TEXT NOT NULL,
    usado TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_portal_biometria_rut ON portal_biometria(rut_norm);
"""


def preparar_biometria():
    import sqlite3
    con = sqlite3.connect(os.path.join(Config.DATA_DIR, "informes.db"), timeout=30)
    con.executescript(SCHEMA_BIOMETRIA)
    con.commit()
    con.close()


try:
    preparar_biometria()
except Exception as e:  # noqa: BLE001
    print("biometria: no se pudo preparar la tabla:", e)


# ---------------------------------------------------------------- utilidades
def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def de_b64u(s):
    s = str(s or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def leer_cbor(datos, i=0):
    """Lector CBOR mínimo (lo justo para WebAuthn): enteros, bytes, texto, listas, mapas y simples."""
    ini = datos[i]; i += 1
    tipo, info = ini >> 5, ini & 0x1F
    if info < 24:
        val = info
    elif info == 24:
        val = datos[i]; i += 1
    elif info == 25:
        val = struct.unpack(">H", datos[i:i + 2])[0]; i += 2
    elif info == 26:
        val = struct.unpack(">I", datos[i:i + 4])[0]; i += 4
    elif info == 27:
        val = struct.unpack(">Q", datos[i:i + 8])[0]; i += 8
    else:
        raise ValueError("CBOR no soportado")
    if tipo == 0:
        return val, i
    if tipo == 1:
        return -1 - val, i
    if tipo == 2:
        return bytes(datos[i:i + val]), i + val
    if tipo == 3:
        return bytes(datos[i:i + val]).decode(), i + val
    if tipo == 4:
        out = []
        for _ in range(val):
            v, i = leer_cbor(datos, i); out.append(v)
        return out, i
    if tipo == 5:
        out = {}
        for _ in range(val):
            k, i = leer_cbor(datos, i); v, i = leer_cbor(datos, i); out[k] = v
        return out, i
    if tipo == 7:
        return {20: False, 21: True, 22: None}.get(val), i
    raise ValueError("CBOR no soportado")


def rp_id():
    return request.host.split(":")[0]


def origen_valido(origen):
    host = request.host
    permitidos = {f"https://{host}"}
    if rp_id() in ("localhost", "127.0.0.1"):           # pruebas locales
        permitidos.add(f"http://{host}")
    return origen in permitidos


def leer_auth_data(ad):
    """authenticatorData: hash del sitio, banderas, contador y (al registrar) la llave nueva."""
    if len(ad) < 37:
        raise ValueError("datos cortos")
    rp_hash, flags, contador = ad[:32], ad[32], struct.unpack(">I", ad[33:37])[0]
    out = {"rp_hash": rp_hash, "presente": bool(flags & 0x01), "verificado": bool(flags & 0x04), "contador": contador}
    if flags & 0x40:                                    # trae llave nueva
        largo = struct.unpack(">H", ad[53:55])[0]
        out["cred_id"] = ad[55:55 + largo]
        out["clave_cose"] = ad[55 + largo:]
        leer_cbor(out["clave_cose"])                    # que sea CBOR válido
    return out


def verificar_firma(clave_cose, firma, datos):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    k, _ = leer_cbor(clave_cose)
    alg = k.get(3)
    if alg == -7 and k.get(1) == 2:                     # ES256 (la mayoría de los teléfonos)
        pub = ec.EllipticCurvePublicNumbers(int.from_bytes(k[-2], "big"), int.from_bytes(k[-3], "big"),
                                            ec.SECP256R1()).public_key()
        pub.verify(firma, datos, ec.ECDSA(hashes.SHA256()))
    elif alg == -257 and k.get(1) == 3:                 # RS256 (algunos Windows)
        pub = rsa.RSAPublicNumbers(int.from_bytes(k[-2], "big"), int.from_bytes(k[-1], "big")).public_key()
        pub.verify(firma, datos, padding.PKCS1v15(), hashes.SHA256())
    else:
        raise ValueError("algoritmo no soportado")


def revisar_client_data(cd_json, tipo):
    cd = json.loads(cd_json)
    reto = session.pop("bio_reto", None)
    vence = session.pop("bio_reto_vence", "")
    if cd.get("type") != tipo or not reto or vence < ahora():
        raise ValueError("desafío vencido")
    if cd.get("challenge") != reto:
        raise ValueError("desafío distinto")
    if not origen_valido(cd.get("origin", "")):
        raise ValueError("origen")


def nuevo_reto():
    reto = b64u(os.urandom(32))
    session["bio_reto"] = reto
    session["bio_reto_vence"] = (datetime.now() + timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S")
    return reto


def rut_paciente():
    from integracion import _rut_paciente
    return _rut_paciente()


# ---------------------------------------------------------------- activar (paciente ya ingresó con su clave)
@app.route("/paciente/biometria/opciones-registro", methods=["POST"])
def bio_opciones_registro():
    rut = rut_paciente()
    if not rut:
        abort(401)
    db = get_db()
    o = db.execute("SELECT paciente FROM ordenes WHERE rut_norm=? ORDER BY fecha DESC LIMIT 1", (rut,)).fetchone()
    nombre = (o["paciente"] if o else "") or rut
    ya = [r["cred_id"] for r in db.execute("SELECT cred_id FROM portal_biometria WHERE rut_norm=?", (rut,))]
    return jsonify({
        "challenge": nuevo_reto(),
        "rp": {"id": rp_id(), "name": f"Mis exámenes · {Config.CENTRO_NOMBRE}"},
        "user": {"id": b64u(hashlib.sha256(("paciente:" + rut).encode()).digest()), "name": rut, "displayName": nombre},
        "pubKeyCredParams": [{"type": "public-key", "alg": -7}, {"type": "public-key", "alg": -257}],
        "authenticatorSelection": {"authenticatorAttachment": "platform", "userVerification": "required",
                                   "residentKey": "required", "requireResidentKey": True},
        "attestation": "none", "timeout": 120000,
        "excludeCredentials": [{"type": "public-key", "id": c} for c in ya],
    })


@app.route("/paciente/biometria/registrar", methods=["POST"])
def bio_registrar():
    rut = rut_paciente()
    if not rut:
        abort(401)
    d = request.get_json(silent=True) or {}
    try:
        cd_json = de_b64u(d.get("clientDataJSON"))
        revisar_client_data(cd_json, "webauthn.create")
        att, _ = leer_cbor(de_b64u(d.get("attestationObject")))
        ad = leer_auth_data(att["authData"])
        if ad["rp_hash"] != hashlib.sha256(rp_id().encode()).digest():
            raise ValueError("sitio")
        if not (ad["presente"] and ad["verificado"]) or "cred_id" not in ad:
            raise ValueError("sin verificación")
    except Exception as e:  # noqa: BLE001
        auditar(rut, "portal_biometria_fallida", f"registro: {e}", request.remote_addr or "")
        return jsonify({"ok": False, "error": "No se pudo activar. Inténtalo de nuevo."}), 400
    db = get_db()
    db.execute("INSERT OR REPLACE INTO portal_biometria (cred_id, rut_norm, clave_publica, contador, dispositivo, creado) "
               "VALUES (?,?,?,?,?,?)", (b64u(ad["cred_id"]), rut, ad["clave_cose"], ad["contador"],
                                        (request.user_agent.string or "")[:160], ahora()))
    db.commit()
    auditar(rut, "portal_biometria_activada", "", request.remote_addr or "")
    return jsonify({"ok": True, "id": b64u(ad["cred_id"])})


@app.route("/paciente/biometria/quitar", methods=["POST"])
def bio_quitar():
    rut = rut_paciente()
    if not rut:
        abort(401)
    cid = (request.get_json(silent=True) or {}).get("id", "")
    db = get_db()
    db.execute("DELETE FROM portal_biometria WHERE rut_norm=? AND cred_id=?", (rut, cid))
    db.commit()
    auditar(rut, "portal_biometria_quitada", "", request.remote_addr or "")
    return jsonify({"ok": True})


# ---------------------------------------------------------------- entrar con huella o cara
@app.route("/paciente/biometria/opciones-ingreso", methods=["POST"])
def bio_opciones_ingreso():
    return jsonify({"challenge": nuevo_reto(), "rpId": rp_id(), "userVerification": "required",
                    "timeout": 120000, "allowCredentials": []})


@app.route("/paciente/biometria/ingresar", methods=["POST"])
def bio_ingresar():
    d = request.get_json(silent=True) or {}
    ipx = request.remote_addr or ""
    db = get_db()
    hace15 = (datetime.now() - timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
    if db.execute("SELECT COUNT(*) FROM auditoria WHERE accion IN ('portal_fallido','portal_biometria_fallida') "
                  "AND ip=? AND ts>?", (ipx, hace15)).fetchone()[0] >= 8:
        return jsonify({"ok": False, "error": "Demasiados intentos. Espera 15 minutos."}), 429
    cid = str(d.get("id", ""))
    c = db.execute("SELECT * FROM portal_biometria WHERE cred_id=?", (cid,)).fetchone()
    try:
        if not c:
            raise ValueError("llave desconocida")
        cd_json = de_b64u(d.get("clientDataJSON"))
        revisar_client_data(cd_json, "webauthn.get")
        ad_bytes = de_b64u(d.get("authenticatorData"))
        ad = leer_auth_data(ad_bytes)
        if ad["rp_hash"] != hashlib.sha256(rp_id().encode()).digest():
            raise ValueError("sitio")
        if not (ad["presente"] and ad["verificado"]):
            raise ValueError("sin verificación")
        verificar_firma(c["clave_publica"], de_b64u(d.get("signature")), ad_bytes + hashlib.sha256(cd_json).digest())
        if c["contador"] and ad["contador"] and ad["contador"] <= c["contador"]:
            raise ValueError("contador")            # posible llave copiada
    except Exception as e:  # noqa: BLE001
        auditar(c["rut_norm"] if c else "?", "portal_biometria_fallida", f"ingreso: {e}", ipx)
        quitar = not c                              # la llave ya no existe aquí: el teléfono debe olvidar el aviso
        return jsonify({"ok": False, "olvidar": quitar,
                        "error": "No se pudo entrar con huella o cara. Usa tu RUT y clave."}), 400
    rut = c["rut_norm"]
    db.execute("UPDATE portal_biometria SET contador=?, usado=? WHERE cred_id=?", (ad["contador"], ahora(), cid))
    db.commit()
    from integracion import PORTAL_MINUTOS
    for k in [k for k in session if k.startswith("lk_")]:
        session.pop(k, None)
    session["paciente_rut"] = rut
    session["paciente_hasta"] = (datetime.now() + timedelta(minutes=PORTAL_MINUTOS)).strftime("%Y-%m-%d %H:%M:%S")
    auditar(rut, "portal_ingreso", "huella o cara", ipx)
    return jsonify({"ok": True, "ir": "/paciente/examenes"})
