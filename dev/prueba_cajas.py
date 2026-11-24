"""Prueba de varias cajas con cierre propio y selección libre de agendas (v1.1.82).
Requiere la agenda en :3800 con base nueva, MODULO_CLINICO=1 DEMO_DATOS=true ADMIN_CLAVE=clave-agenda-123 y DATA_DIR=/tmp/cj."""
import requests, datetime, subprocess, json
B="http://localhost:3800"; a=requests.Session(); a.post(B+"/api/login",json={"usuario":"admin","clave":"clave-agenda-123"})
f=0
def chk(c,m):
    global f; f+= not c; print(("OK   " if c else "FALLA ")+m)
hoy=datetime.date.today().isoformat()
def cita(eq,hora,valor,pac,rut):
    c=a.post(B+"/api/citas",json={"fecha":hoy,"hora":hora,"equipo":eq,"paciente":pac,"rut":rut,"prevision":"Particular","duracion":15,"examenes":[{"nombre":"Examen "+eq,"valor":valor}],"sobrecupo":True}).json()
    return c["id"]
ids={k:cita(*v) for k,v in {"tc":("tc","16:00",50000,"Pac TC","11.111.111-1"),"rx":("rx","16:15",20000,"Pac RX","12.345.678-5"),"m1":("m1","13:00",25000,"Pac Soto","15.432.876-9"),"m2":("m2","12:00",35000,"Pac Mora","19.876.543-0")}.items()}
for k,i in ids.items():
    r=a.post(B+f"/api/citas/{i}/pago",json={"medio":"Efectivo"}); chk(r.status_code==201, f"cobro {k}")
# sin cajas: igual que siempre
d=a.get(B+f"/api/caja?fecha={hoy}").json()
chk(d["caja"]==0 and d["cajas"]==[] and d["totales"]["recaudado"]==130000,"sin cajas: una sola caja con todo ($130.000), como siempre")
# selección libre
d=a.get(B+f"/api/caja?fecha={hoy}&agendas=tc,rx").json(); chk(d["totales"]["recaudado"]==70000 and d["libre"],"selección libre TC+RX: $70.000")
# crear cajas
r=a.post(B+"/api/cajas",json={"nombre":"Imágenes","agendas":["tc","rx","eco"]}); chk(r.ok,"crear caja Imágenes")
r=a.post(B+"/api/cajas",json={"nombre":"Consultas","agendas":["m1","tc"]}); chk(r.status_code==409,"una agenda no puede estar en dos cajas: "+r.json()["error"])
r=a.post(B+"/api/cajas",json={"nombre":"Dra. Soto","agendas":["m1"]}); chk(r.ok,"crear caja Dra. Soto")
cj={c["nombre"]:c["id"] for c in r.json()}
d=a.get(B+f"/api/caja?fecha={hoy}").json(); ec={x["nombre"]:x for x in d["estado_cajas"]}
chk(ec["Imágenes"]["recaudado"]==70000 and ec["Dra. Soto"]["recaudado"]==25000 and ec["General"]["recaudado"]==35000,"estado del día: Imágenes $70.000 · Dra. Soto $25.000 · General (Dr. Mora) $35.000")
r=a.post(B+"/api/caja/cerrar",json={"fecha":hoy,"efectivo_contado":70000}); chk(r.status_code==400,"con varias cajas hay que elegir cuál cerrar")
r=a.post(B+"/api/caja/cerrar",json={"fecha":hoy,"caja":cj["Imágenes"],"efectivo_contado":70000}); chk(r.ok and r.json()["cierre"]["diferencia"]==0,"cerrar caja Imágenes sin diferencia")
c2=cita("rx","17:00",20000,"Pac RX2","20.345.678-6"); r=a.post(B+f"/api/citas/{c2}/pago",json={"medio":"Efectivo"})
chk(r.status_code==409,"Imágenes cerrada: no deja cobrar otro RX")
c3=cita("m1","13:30",25000,"Pac Soto2","8.765.432-K"); r=a.post(B+f"/api/citas/{c3}/pago",json={"medio":"Efectivo"})
chk(r.status_code==201,"pero la Dra. Soto (otra caja) sí se puede cobrar")
d=a.get(B+f"/api/caja?fecha={hoy}&caja={cj['Dra. Soto']}").json(); chk(d["totales"]["recaudado"]==50000 and not d["cierre"],"caja Dra. Soto: $50.000, abierta")
r=a.post(B+"/api/caja/cerrar",json={"fecha":hoy,"caja":0,"efectivo_contado":30000}); chk(r.ok and r.json()["cierre"]["diferencia"]==-5000,"cerrar General con faltante de $5.000")
csv=a.get(B+f"/api/caja/csv?fecha={hoy}&caja={cj['Imágenes']}").text; chk("Caja: Imágenes" in csv and "Pac TC" in csv and "Pac Soto" not in csv,"CSV de la caja Imágenes")
csv=a.get(B+f"/api/caja/csv?fecha={hoy}&agendas=m1").text; chk("Ana Soto Rojas" in csv,"CSV por agenda muestra el nombre del médico")
r=a.post(B+"/api/caja/reabrir",json={"fecha":hoy,"caja":cj["Imágenes"]}); chk(r.ok and not r.json()["cierre"],"reabrir solo la caja Imágenes")
d=a.get(B+f"/api/caja?fecha={hoy}").json(); ec={x["nombre"]:x for x in d["estado_cajas"]}
chk(not ec["Imágenes"]["cerrada"] and ec["General"]["cerrada"] and not ec["Dra. Soto"]["cerrada"],"estado: Imágenes abierta, General cerrada, Dra. Soto abierta")
r=a.delete(B+f"/api/cajas/{cj['Dra. Soto']}") if False else None
a.post(B+"/api/caja/cerrar",json={"fecha":hoy,"caja":cj["Dra. Soto"],"efectivo_contado":50000})
r=a.delete(B+f"/api/cajas/{cj['Dra. Soto']}"); chk(r.status_code==409,"una caja con cierres guardados no se puede eliminar: "+r.json()["error"])
out=subprocess.run(["node","scripts/resumen-caja.js","--fecha",hoy],capture_output=True,text=True,cwd="/home/claude/agenda-imagenes",env={"DATA_DIR":"/tmp/cj","PATH":"/usr/bin:/bin:/usr/local/bin"}).stdout.strip().splitlines()[-1]
j=json.loads(out); chk(j["cajas"] and len(j["cajas"])==3 and j["nombres"].get("m1")=="Ana Soto Rojas","correo diario: detalle por caja y nombres de médicos")
print("FALLAS:",f)
