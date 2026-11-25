"""Prueba del bono externo (v1.1.84). Agenda en :3900, base nueva, ADMIN_CLAVE=clave-agenda-123."""
import requests, datetime
B="http://localhost:3900"; a=requests.Session(); a.post(B+"/api/login",json={"usuario":"admin","clave":"clave-agenda-123"})
f=0
def chk(c,m):
    global f; f+= not c; print(("OK   " if c else "FALLA ")+m)
hoy=datetime.date.today().isoformat()
def cita(hora,prev,rut,ctr=False):
    return a.post(B+"/api/citas",json={"fecha":hoy,"hora":hora,"equipo":"rx","paciente":"Paciente "+hora,"rut":rut,"prevision":prev,"duracion":15,
      "examenes":[{"nombre":"RX TÓRAX","valor":20000,"bonificacion":13500}],**({"contraste":True} if ctr else {})}).json()
c=cita("10:00","Fonasa","11.111.111-1"); chk(c["valor"]==20000 and c["copago"]==6500,"cita Fonasa: valor $20.000, bonificación $13.500, copago $6.500")
r=a.post(B+f"/api/citas/{c['id']}/pago",json={"bono_externo":True}); chk(r.status_code==400 and "folio" in r.json()["error"],"bono externo pide el folio: "+r.json()["error"])
r=a.post(B+f"/api/citas/{c['id']}/pago",json={"bono_externo":True,"folio_bono":"9988776","descuento_tipo":"monto","descuento_valor":1000,"descuento_motivo":"x"}); chk(r.status_code==400,"bono externo no admite descuento")
r=a.post(B+f"/api/citas/{c['id']}/pago",json={"bono_externo":True,"folio_bono":"9988776"}); j=r.json()
chk(r.status_code==201 and j["pago_medio"]=="Bono externo" and j["pago_monto"]==0 and j["pago_bono_externo"]==1,"registrado: medio «Bono externo», $0 en caja")
c2=cita("10:15","Fonasa","12.345.678-5"); a.post(B+f"/api/citas/{c2['id']}/pago",json={"medio":"Efectivo","folio_bono":"123"})
c3=cita("10:30","Particular","15.432.876-9")
r=a.post(B+f"/api/citas/{c3['id']}/pago",json={"bono_externo":True,"folio_bono":"1"}); chk(r.status_code==400,"solo para Fonasa: "+r.json()["error"])
d=a.get(B+f"/api/caja?fecha={hoy}").json(); t=d["totales"]
chk(t["valor"]==40000 and t["bonificacion"]==33500 and t["recaudado"]==6500,f"caja: valor $40.000 = recaudado $6.500 + bonificación $33.500 (20.000 del bono externo + 13.500)")
chk(t["bonos_externos"]=={"cantidad":1,"monto":20000} and t["efectivo_esperado"]==6500,"caja cuenta 1 bono externo de $20.000 y el efectivo esperado no cambia")
pid=[p for p in d["pagos"] if p["bono_externo"]][0]["id"]
r=a.post(B+f"/api/pagos/{pid}/anular",json={"motivo":"prueba"}); chk(r.ok,"se puede anular como cualquier pago")
h=a.get(B+f"/api/bitacora?desde={hoy}&hasta={hoy}&accion=cobro").json()["filas"]; chk(any("BONO EXTERNO" in x["detalle"] for x in h),"queda en la auditoría como BONO EXTERNO")
print("FALLAS:",f)
