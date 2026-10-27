import re, requests
B="http://localhost:8000"; M="http://localhost:8042"; fallas=0
def chk(c,m):
    global fallas; fallas+=(not c); print(("OK   " if c else "FALLA ")+m)
def sesion(u,c):
    s=requests.Session(); s.post(B+"/login",data={"usuario":u,"clave":c}); t=s.get(B+"/").text
    s.csrf=re.search(r'name="csrf" value="([^"]+)"',t).group(1); return s
adm=sesion("admin","clave-prueba-123")
t=adm.get(B+"/admin/equipos").text
chk("203.0.113.50" in t and "46429" in t and ">PACS<" in t, "pantalla muestra IP, puerto y AE Title del PACS")
chk("mientras la lista esté vacía" in t, "lista vacía explicada")
def nuevo(**d):
    base={"csrf":adm.csrf,"nombre":"TC Rancagua","centro":"Rancagua","aet":"ct_rancagua","ip":"200.29.15.195","activo":"on"}; base.update(d)
    return adm.post(B+"/admin/equipos",data=base).text
chk("IP es privada" in nuevo(ip="192.168.1.20"), "rechaza IP privada")
chk("no es válida" in nuevo(ip="200.29.15"), "rechaza IP mal escrita")
chk("AE Title debe tener" in nuevo(aet="NOMBRE-DEMASIADO-LARGO-XYZ"), "rechaza AE Title largo")
t=nuevo(); chk("habilitado en el PACS" in t and "CT_RANCAGUA" in t, "agrega equipo (AE Title en mayúsculas)")
mods=requests.get(M+"/modalities?expand").json()
chk(mods.get("equipo-1",{}).get("AET")=="CT_RANCAGUA" and mods["equipo-1"]["Host"]=="200.29.15.195" and mods["equipo-1"]["AllowStore"] is True and mods["equipo-1"]["AllowFind"] is False, "Orthanc recibe equipo con AET+IP, solo envío")
chk("Ya existe un equipo" in nuevo(nombre="Otro"), "no permite duplicar AE Title + IP")
nuevo(nombre="Eco Rancagua", aet="US_RANCAGUA")
nuevo(nombre="RX INSI", centro="INSI", aet="CR_INSI", ip="190.100.20.30")
chk(len(requests.get(M+"/modalities?expand").json())==3, "3 equipos en el PACS")
t=adm.get(B+"/admin/equipos").text; chk(t.count("badge firmado")==3, "los 3 aparecen como Habilitados")
# editar IP
adm.post(B+"/admin/equipos",data={"csrf":adm.csrf,"id":"3","nombre":"RX INSI","centro":"INSI","aet":"CR_INSI","ip":"190.100.20.31","activo":"on"})
chk(requests.get(M+"/modalities?expand").json()["equipo-3"]["Host"]=="190.100.20.31", "cambio de IP llega al PACS")
# deshabilitar
adm.post(B+"/admin/equipos",data={"csrf":adm.csrf,"id":"2","nombre":"Eco Rancagua","centro":"Rancagua","aet":"US_RANCAGUA","ip":"200.29.15.195"})
chk("equipo-2" not in requests.get(M+"/modalities?expand").json(), "equipo deshabilitado se quita del PACS")
chk("Deshabilitado" in adm.get(B+"/admin/equipos").text, "se ve como deshabilitado")
# PACS reiniciado pierde la lista -> sincronizar la repone
requests.delete(M+"/modalities/equipo-1")
r=adm.post(B+"/admin/equipos",data={"csrf":adm.csrf,"sincronizar":"1"}); chk("2 equipo(s) activos" in r.text, "sincronizar repone equipos")
chk("equipo-1" in requests.get(M+"/modalities?expand").json(), "equipo repuesto en el PACS")
# modalidades ajenas (configuradas a mano) no se tocan
requests.put(M+"/modalities/workstation", json={"AET":"WS","Host":"10.0.0.5","Port":104})
adm.post(B+"/admin/equipos",data={"csrf":adm.csrf,"sincronizar":"1"})
chk("workstation" in requests.get(M+"/modalities?expand").json(), "no borra modalidades configuradas a mano")
# API firewall
chk(requests.get(B+"/api/equipos/ips").status_code==404, "API firewall sin token: 404")
chk(requests.get(B+"/api/equipos/ips",headers={"X-Token":"malo"}).status_code==404, "API firewall token malo: 404")
r=requests.get(B+"/api/equipos/ips",headers={"X-Token":"token-secreto-firewall-123"})
chk(r.status_code==200 and r.text.split()==["190.100.20.31","200.29.15.195"], "API firewall entrega IPs únicas de equipos activos")
requests.delete(M+"/modalities/equipo-3")
requests.get(B+"/api/equipos/ips",headers={"X-Token":"token-secreto-firewall-123"})
chk("equipo-3" in requests.get(M+"/modalities?expand").json(), "el cron también resincroniza el PACS")
# borrar
r=adm.post(B+"/admin/equipos",data={"csrf":adm.csrf,"borrar":"3"}); chk("ya no puede enviar" in r.text and "equipo-3" not in requests.get(M+"/modalities?expand").json(), "eliminar equipo")
# permisos
perf=dict((n,int(i)) for i,n in re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', adm.get(B+"/admin/usuarios").text))
adm.post(B+"/admin/usuarios",data={"csrf":adm.csrf,"usuario":"rad","nombre":"Rad","perfil_id":perf["Radiólogos"],"clave":"clave-larga-123","activo":"on"})
rad=sesion("rad","clave-larga-123")
chk(rad.get(B+"/admin/equipos").status_code==403 and "Equipos DICOM" not in rad.get(B+"/").text, "radiólogo sin acceso a equipos")
chk("Equipos DICOM (envío por internet)" in adm.get(B+"/").text, "menú de administración muestra equipos")
t=adm.get(B+"/admin/auditoria").text; chk("editar_equipo" in t and "eliminar_equipo" in t, "auditoría de cambios de equipos")
print("FALLAS:",fallas)
