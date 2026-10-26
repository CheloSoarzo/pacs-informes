"""Servidor SMTP mínimo para pruebas: guarda cada correo en /tmp/correos_prueba/."""
import os, socketserver, time
D = "/tmp/correos_prueba"; os.makedirs(D, exist_ok=True)
class H(socketserver.StreamRequestHandler):
    def w(self, s): self.wfile.write((s + "\r\n").encode())
    def handle(self):
        self.w("220 falso"); datos = None
        while True:
            l = self.rfile.readline()
            if not l: return
            c = l.decode(errors="replace").strip()
            if datos is not None:
                if c == ".":
                    open(f"{D}/{time.time_ns()}.eml", "w").write("".join(datos)); datos = None; self.w("250 ok")
                else: datos.append(l.decode(errors="replace"))
                continue
            u = c.upper()
            if u.startswith(("EHLO", "HELO")): self.w("250 falso")
            elif u.startswith("DATA"): datos = []; self.w("354 adelante")
            elif u.startswith("QUIT"): self.w("221 chao"); return
            else: self.w("250 ok")
socketserver.ThreadingTCPServer.allow_reuse_address = True
socketserver.ThreadingTCPServer(("127.0.0.1", 2525), H).serve_forever()
