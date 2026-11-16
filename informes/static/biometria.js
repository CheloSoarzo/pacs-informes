/* Portal del paciente: entrar con huella o cara (WebAuthn). Lo usa templates/paciente.html.
   En el teléfono se recuerda qué llaves hay (por RUT) para mostrar el botón; las llaves en sí las guarda el teléfono. */
(function () {
  const soporta = !!(window.PublicKeyCredential && navigator.credentials);
  const csrf = (document.querySelector('input[name="csrf"]') || {}).value || "";
  const aB = s => { s = s.replace(/-/g, "+").replace(/_/g, "/"); s += "===".slice((s.length + 3) % 4); return Uint8Array.from(atob(s), c => c.charCodeAt(0)); };
  const aS = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const leer = () => { try { return JSON.parse(localStorage.getItem("pt-bio") || "{}"); } catch (e) { return {}; } };
  const guardar = m => { try { localStorage.setItem("pt-bio", JSON.stringify(m)); } catch (e) { /* */ } };
  const post = async (url, datos) => {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF": csrf },
                                 body: JSON.stringify(datos || {}), credentials: "same-origin" });
    let j = {}; try { j = await r.json(); } catch (e) { /* */ }
    if (!r.ok && !j.error) j.error = "No se pudo completar. Inténtalo de nuevo.";
    j.ok = r.ok && j.ok !== false; return j;
  };
  const avisar = (el, txt, error) => { if (!el) return; el.textContent = txt; el.className = "pt-bio-msg" + (error ? " error" : ""); el.hidden = !txt; };

  // ---------- ingreso: botón «Entrar con huella o cara»
  const entrar = document.getElementById("bio-entrar");
  if (entrar && soporta && Object.keys(leer()).length) {
    document.getElementById("bio-ingreso").hidden = false;
    const enviar = document.querySelector('form button[type="submit"]');   // la clave pasa a segundo plano
    if (enviar) { enviar.classList.remove("prim"); enviar.classList.add("pt-sec"); }
    entrar.addEventListener("click", async () => {
      const msg = document.getElementById("bio-msg");
      avisar(msg, "");
      try {
        const op = await post("/paciente/biometria/opciones-ingreso");
        const ids = Object.values(leer());
        const cred = await navigator.credentials.get({ publicKey: {
          challenge: aB(op.challenge), rpId: op.rpId, userVerification: "required", timeout: op.timeout,
          allowCredentials: ids.map(id => ({ type: "public-key", id: aB(id) })) } });
        const r = await post("/paciente/biometria/ingresar", { id: cred.id,
          clientDataJSON: aS(cred.response.clientDataJSON), authenticatorData: aS(cred.response.authenticatorData),
          signature: aS(cred.response.signature) });
        if (r.ok) { location.href = r.ir; return; }
        if (r.olvidar) { const m = leer(); for (const k in m) if (m[k] === cred.id) delete m[k]; guardar(m);
          if (!Object.keys(m).length) document.getElementById("bio-ingreso").hidden = true; }
        avisar(msg, r.error, true);
      } catch (e) {
        avisar(msg, e && e.name === "NotAllowedError" ? "Se canceló. Puedes intentarlo de nuevo o usar tu RUT y clave." :
          "No se pudo entrar con huella o cara. Usa tu RUT y clave.", true);
      }
    });
  }

  // ---------- en «Mis exámenes»: activar o desactivar
  const caja = document.getElementById("bio-activar");
  if (caja && soporta) {
    const rut = caja.dataset.rut;
    PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable().then(hay => {
      if (!hay) return;
      const pintar = () => {
        const activo = !!leer()[rut];
        caja.hidden = false;
        caja.querySelector(".pt-bio-off").hidden = activo;
        caja.querySelector(".pt-bio-on").hidden = !activo;
      };
      pintar();
      caja.querySelector("#bio-si").addEventListener("click", async () => {
        const msg = caja.querySelector(".pt-bio-msg"); avisar(msg, "");
        try {
          const op = await post("/paciente/biometria/opciones-registro");
          if (!op.ok) throw new Error(op.error);
          const cred = await navigator.credentials.create({ publicKey: {
            challenge: aB(op.challenge), rp: op.rp, user: { ...op.user, id: aB(op.user.id) },
            pubKeyCredParams: op.pubKeyCredParams, authenticatorSelection: op.authenticatorSelection,
            attestation: "none", timeout: op.timeout,
            excludeCredentials: op.excludeCredentials.map(c => ({ type: "public-key", id: aB(c.id) })) } });
          const r = await post("/paciente/biometria/registrar", { id: cred.id,
            clientDataJSON: aS(cred.response.clientDataJSON), attestationObject: aS(cred.response.attestationObject) });
          if (!r.ok) throw new Error(r.error);
          const m = leer(); m[rut] = r.id; guardar(m); pintar();
          avisar(msg, "Listo. La próxima vez podrás entrar con tu huella o tu cara.");
        } catch (e) {
          avisar(msg, e && e.name === "NotAllowedError" ? "Se canceló." :
            (e && e.name === "InvalidStateError" ? "Este teléfono ya estaba activado." : "No se pudo activar. Inténtalo de nuevo."), true);
        }
      });
      caja.querySelector("#bio-no").addEventListener("click", async () => {
        const m = leer(), id = m[rut];
        if (id) await post("/paciente/biometria/quitar", { id });
        delete m[rut]; guardar(m); pintar();
        avisar(caja.querySelector(".pt-bio-msg"), "Desactivado. Para entrar usa tu RUT y clave.");
      });
    });
  }
})();
