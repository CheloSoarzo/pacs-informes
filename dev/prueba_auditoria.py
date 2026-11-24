"""Prueba de la auditoría de la agenda (v1.1.81).
Requiere la agenda en :3600 con base nueva y ADMIN_CLAVE=clave-agenda-123."""
import requests, datetime
B="http://localhost:3600"; f=0
def chk(c,m):
    global f; f+= not c; print(("OK   " if c else "FALLA ")+m)
hoy=datetime.date.today().isoformat()
requests.post(B+"/api/login",json={"usuario":"admin","clave":"mala"})
a=requests.Session(); a.post(B+"/api/login",json={"usuario":"admin","clave":"clave-agenda-123"})
a.post(B+"/api/usuarios",json={"usuario":"camila","clave":"clave-camila-1","nombre":"Camila Prueba","rol":"recepcion"})
r=requests.Session(); r.post(B+"/api/login",json={"usuario":"camila","clave":"clave-camila-1"})
base={"fecha":hoy,"hora":"10:00","equipo":"tc","paciente":"Paciente Auditoría","rut":"11.111.111-1","prevision":"Particular","duracion":15,"examenes":[{"nombre":"TC CEREBRO","valor":30000}]}
c=r.post(B+"/api/citas",json=base).json(); cid=c["id"]
r.put(B+f"/api/citas/{cid}",json={**base,"hora":"11:30","notas":"Trae exámenes"})
r.put(B+f"/api/citas/{cid}/estado",json={"estado":"llego"})
p=r.post(B+f"/api/citas/{cid}/pago",json={"medio":"Efectivo"}).json()
pid=p.get("pago_id")
a.post(B+f"/api/pagos/{pid}/anular",json={"motivo":"Error de prueba"})
c2=r.post(B+"/api/citas",json={**base,"hora":"12:00","paciente":"Paciente Borrado","rut":"12.345.678-5"}).json()
r.delete(B+f"/api/citas/{c2['id']}")
a.get(B+"/api/respaldo")
r.post(B+"/api/logout")
d=a.get(B+f"/api/bitacora?desde={hoy}&hasta={hoy}").json()
acc=[x["accion"] for x in d["filas"]]
for k in ["ingreso_fallido","ingreso","crear_usuario","crear_cita","modificar_cita","estado_cita","cobro","anular_pago","eliminar_cita","respaldo","salida"]:
    chk(k in acc, f"registra «{d['acciones'][k]}»")
mod=[x for x in d["filas"] if x["accion"]=="modificar_cita"][0]
chk("hora: 10:00 → 11:30" in mod["detalle"] and "Camila" in mod["usuario"], "detalle del cambio: "+mod["detalle"]+" · por "+mod["usuario"])
el=[x for x in d["filas"] if x["accion"]=="eliminar_cita"][0]
chk(el["paciente"]=="Paciente Borrado" and "12:00" in el["detalle"], "la cita eliminada queda descrita: "+el["detalle"])
h=a.get(B+f"/api/citas/{cid}/historial").json()
chk([x["accion"] for x in h]==["crear_cita","modificar_cita","estado_cita","cobro","anular_pago"], "historial de la cita en orden: "+", ".join(x["accion_txt"] for x in h))
chk(r.get(B+"/api/bitacora").status_code in (401,403), "recepción no puede ver la auditoría")
r.post(B+"/api/login",json={"usuario":"camila","clave":"clave-camila-1"})
chk(r.get(B+"/api/bitacora").status_code==403 and r.get(B+f"/api/citas/{cid}/historial").status_code==403, "ni el historial de la cita")
csv=a.get(B+f"/api/bitacora?desde={hoy}&hasta={hoy}&csv=1")
chk(csv.ok and "Paciente Auditoría" in csv.text and csv.text.startswith("﻿"), "descarga CSV")
q=a.get(B+f"/api/bitacora?desde={hoy}&hasta={hoy}&q=12345678").json()
chk(all("12.345.678-5"==x["rut"] for x in q["filas"]) and q["filas"], "buscar por RUT")
print("FALLAS:",f)
