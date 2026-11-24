"""Prueba de horarios de atención de los médicos (módulo clínico).

Requiere la agenda en :3200 con MODULO_CLINICO=1 DEMO_DATOS=true ADMIN_CLAVE=clave-agenda-123 (base nueva).
"""
import requests, datetime
A="http://localhost:3200"; s=requests.Session()
s.post(A+"/api/login",json={"usuario":"admin","clave":"clave-agenda-123"})
f=0
def chk(c,m):
    global f; f+= not c; print(("OK   " if c else "FALLA ")+m)
hoy=datetime.date.today()
# próximo miércoles y próximo sábado
def prox(wd): d=hoy+datetime.timedelta(days=1)
def prox(wd):
    d=hoy+datetime.timedelta(days=1)
    while d.weekday()!=wd: d+=datetime.timedelta(days=1)
    return d.isoformat()
mie, sab = prox(2), prox(5)
r=s.put(A+"/api/profesionales/1/horario",json={"bloques":[{"dia":d,"desde":"09:00","hasta":"13:00"} for d in range(1,6)]+[{"dia":3,"desde":"15:00","hasta":"18:00"}]})
chk(r.ok and len(r.json()["bloques"])==6, "se guarda el horario (lun–vie mañana + miércoles tarde)")
r=s.put(A+"/api/profesionales/1/horario",json={"bloques":[{"dia":1,"desde":"09:00","hasta":"13:00"},{"dia":1,"desde":"12:00","hasta":"14:00"}]})
chk(r.status_code==400 and "topan" in r.json()["error"], "bloques que se topan se rechazan: "+r.json().get("error",""))
s.put(A+"/api/profesionales/1/horario",json={"bloques":[{"dia":d,"desde":"09:00","hasta":"13:00"} for d in range(1,6)]+[{"dia":3,"desde":"15:00","hasta":"18:00"}]})
base={"paciente":"Paciente Horario","rut":"11.111.111-1","prevision":"Particular","equipo":"m1","duracion":20,"examenes":[{"nombre":"Consulta","valor":0}]}
r=s.post(A+"/api/citas",json={**base,"fecha":mie,"hora":"10:00"}); chk(r.status_code==201 and not r.json().get("sobrecupo"), "dentro de horario se agenda normal")
r=s.post(A+"/api/citas",json={**base,"fecha":mie,"hora":"14:00"}); chk(r.status_code==428, "fuera de horario: "+r.json().get("error",""))
r=s.post(A+"/api/citas",json={**base,"fecha":mie,"hora":"12:50"}); chk(r.status_code==428, "una cita que se pasa del bloque (12:50 + 20 min) también avisa")
r=s.post(A+"/api/citas",json={**base,"fecha":sab,"hora":"10:00"}); chk(r.status_code==428 and "sábados" in r.json()["error"], "día que no atiende: "+r.json().get("error",""))
r=s.post(A+"/api/citas",json={**base,"fecha":sab,"hora":"10:00","sobrecupo":True}); chk(r.status_code==201 and r.json()["sobrecupo"]==1, "con confirmación queda como sobrecupo")
cid=r.json()["id"]
r=s.put(A+f"/api/citas/{cid}",json={**base,"fecha":sab,"hora":"10:00","notas":"cambio de nota"}); chk(r.ok and r.json()["sobrecupo"]==1, "editar el sobrecupo sin mover la hora no vuelve a preguntar")
r=s.post(A+"/api/profesionales/1/bloqueos",json={"desde_fecha":mie,"hasta_fecha":mie,"motivo":"Congreso"}); chk(r.status_code==201 and len(r.json()["fuera"])>=1, f"bloqueo del {mie}: la cita de las 10:00 queda en la lista de reagendar")
r=s.post(A+"/api/citas",json={**base,"fecha":mie,"hora":"11:00"}); chk(r.status_code==428 and "Congreso" in r.json()["error"], "bloqueo por fecha: "+r.json().get("error",""))
r=s.post(A+"/api/citas",json={**base,"equipo":"tc","fecha":sab,"hora":"10:00","examenes":[{"nombre":"TC","valor":0}]}); chk(r.status_code==201, "TC no tiene restricción de horario")
r=s.post(A+"/api/citas",json={**base,"equipo":"m2","fecha":sab,"hora":"10:00"}); chk(r.status_code==201, "un médico sin horario configurado no tiene restricción")
rec=requests.Session(); rec.post(A+"/api/login",json={"usuario":"recepcion","clave":"clave-agenda-123"})
r=rec.put(A+"/api/profesionales/1/horario",json={"bloques":[]}); chk(r.status_code==403, "recepción no puede cambiar horarios")
r=rec.post(A+"/api/citas",json={**base,"fecha":sab,"hora":"11:00","sobrecupo":True}); chk(r.status_code==201, "recepción sí puede dar sobrecupo")
h=s.get(A+f"/api/clinica/horarios?desde={mie}&hasta={mie}").json(); chk(len(h["m1"]["bloqueos"])==1 and len(h["m1"]["bloques"])==6, "la agenda recibe horario y bloqueos")
print("FALLAS:",f); pass
