/* Private workspace connections page. Never persist or log certificate input. */
(function () {
  "use strict";

  var tokenMeta = document.querySelector('meta[name="csrf-token"]');
  var csrfToken = tokenMeta ? tokenMeta.content : "";
  var list = document.getElementById("sources-list");
  var errorBox = document.getElementById("connections-error");
  var toastTimer = null;
  var noticeKey = '';
  var sourceSaving = false;
  var certificateSaving = false;
  var editor = document.getElementById("source-editor");
  var sourceForm = document.getElementById("source-form");
  var certForm = document.getElementById("certificate-form");
  var editingId = null;
  var sources = [];

  var labels = {
    "Connections · AuctionRadar": "Fuentes y accesos · AuctionRadar",
    "Skip to main content": "Saltar al contenido principal",
    "Back to radar": "← Volver al radar",
    "Your sources and access": "Tus fuentes y accesos",
    "Manage sources privately for this account. New sources are disabled and are never fetched when saved.": "Gestiona fuentes privadas de esta cuenta. Las nuevas fuentes quedan desactivadas y no se consultan al guardarlas.",
    "Language": "Idioma",
    "Private sources": "Fuentes privadas",
    "Only selected public HTML adapters are available. Arbitrary JavaScript sites and portal logins are not implemented.": "Solo están disponibles los adaptadores HTML públicos indicados. No se implementan portales JavaScript arbitrarios ni inicios de sesión.",
    "Add source": "Añadir fuente",
    "Source details": "Datos de la fuente",
    "Source ID": "ID de fuente",
    "Name": "Nombre",
    "Public HTTPS listing URL": "URL pública HTTPS",
    "Adapter": "Adaptador",
    "Asset types (select with Ctrl or Command)": "Tipos de activo (selecciona con Ctrl o Comando)",
    "Housing": "Vivienda",
    "Vehicles": "Vehículos",
    "Land": "Terreno",
    "Commercial property": "Local comercial",
    "Boats": "Embarcaciones",
    "Machinery": "Maquinaria",
    "Electronics": "Electrónica",
    "Furniture": "Mobiliario",
    "Collectibles": "Coleccionables",
    "Other": "Otros",
    "Enable for a future run": "Activar para futuras búsquedas",
    "Saving does not contact the portal. URLs must use HTTPS and resolve only to public IP addresses; redirect destinations are checked again at fetch time.": "Guardar no contacta el portal. La URL debe usar HTTPS y resolver solo a direcciones IP públicas; los destinos de redirección se vuelven a comprobar al consultar.",
    "Save source": "Guardar fuente",
    "Cancel": "Cancelar",
    "Optional BOE certificate": "Certificado opcional del BOE",
    "A personal P12/PFX is used only to access authenticated BOE asset photos. It does not retrieve documents or perform bidding, account changes, or access other portals.": "Un P12/PFX personal solo se usa para acceder a fotografías autenticadas de activos del BOE. No descarga documentos, puja, cambia la cuenta ni accede a otros portales.",
    "P12 or PFX file": "Archivo P12 o PFX",
    "Certificate passphrase": "Contraseña del certificado",
    "I explicitly consent to use this certificate for authenticated BOE asset photos. I can disable or delete it at any time.": "Autorizo expresamente el uso de este certificado para acceder a fotografías autenticadas de activos del BOE. Puedo desactivarlo o borrarlo en cualquier momento.",
    "Encrypt and save certificate": "Cifrar y guardar certificado",
    "Uploading transfers a signing identity to this server. Encryption at rest does not protect against its administrator; use only an instance you trust.": "Subir el certificado entrega una identidad de firma a este servidor. El cifrado en reposo no protege frente a su administrador; utiliza solo una instancia de confianza.",
    "Delete certificate": "Borrar certificado",
    "The passphrase is never returned by the API. The encrypted file requires a separately provisioned private key file; no certificate is preconfigured or shared between accounts.": "La API nunca devuelve la contraseña. El archivo cifrado requiere una clave privada provisionada por separado; no hay certificados preconfigurados ni compartidos entre cuentas.",
    "Signed in as": "Sesión:",
    "Return to radar": "Volver al radar",
    "No sources configured yet. Add a public source to start; saving it does not run a search.": "Todavía no hay fuentes configuradas. Añade una fuente pública; guardarla no inicia una búsqueda.",
    "Enabled": "Activa",
    "Disabled": "Desactivada",
    "Edit": "Editar",
    "Delete": "Borrar",
    "Delete this source from your private workspace?": "¿Borrar esta fuente de tu espacio privado?",
    "Delete this certificate and revoke BOE access?": "¿Borrar este certificado y revocar el acceso al BOE?",
    "Generic public HTML (no login)": "HTML público genérico (sin inicio de sesión)",
    "Certificate is not configured.": "No hay certificado configurado.",
    "Certificate is stored but disabled.": "El certificado está guardado, pero desactivado.",
    "Certificate is enabled for authenticated BOE photo access.": "El certificado está activado para acceder a fotografías autenticadas del BOE.",
    "Certificate expiry: {date}": "Caducidad del certificado: {date}",
    "Private encryption key is not configured or is insecure. Certificate operations are unavailable.": "La clave privada de cifrado no está configurada o no es segura. Las operaciones con certificados no están disponibles.",
    "Enable certificate": "Activar certificado",
    "Disable certificate": "Desactivar certificado",
    "Source saved. No search was started.": "Fuente guardada. No se ha iniciado ninguna búsqueda.",
    "Source deleted.": "Fuente borrada.",
    "Certificate saved encrypted.": "Certificado guardado cifrado.",
    "Certificate deleted.": "Certificado borrado.",
    "Certificate consent updated.": "Consentimiento del certificado actualizado.",
    "Source URL must be HTTPS, public, and must not contain credentials or secret query parameters.": "La URL debe ser HTTPS y pública, y no puede contener credenciales ni parámetros secretos.",
    "This source adapter is not supported.": "Este adaptador de fuente no está disponible.",
    "Certificate was rejected. Check the P12/PFX file and passphrase; details are not recorded.": "Se rechazó el certificado. Comprueba el archivo P12/PFX y la contraseña; no se registran detalles.",
    "The private certificate store is not configured.": "El almacén privado de certificados no está configurado.",
    "Certificate actions require HTTPS in production.": "Las operaciones con certificados requieren HTTPS en producción.",
    "Production certificate actions require secure session cookies.": "Las operaciones de certificados en producción requieren cookies de sesión seguras.",
    "This action requires explicit certificate consent.": "Esta acción requiere consentimiento explícito para el certificado.",
    "This source ID is already in use.": "Este ID de fuente ya está en uso.",
    "Sign in to manage private connections.": "Inicia sesión para gestionar conexiones privadas.",
    "Request failed. Please try again.": "La solicitud falló. Inténtalo de nuevo.",
    "generic_html": "HTML público genérico (sin inicio de sesión)",
    "boe": "BOE (consulta pública)",
    "aeat": "AEAT / BOE (consulta pública)",
    "Source must be enabled before a future run can use it.": "La fuente debe activarse para que una búsqueda futura pueda usarla."
  };

  var adapters = [
    "generic_html", "boe", "aeat", "escogecasa", "segipsa_pnsd_property", "pnsd_vehicles",
    "actcon", "ayvens", "northgate", "arval", "coches_net", "autoscout24", "mobile_de",
    "truckscout24", "truck1", "idealista", "pisos_com", "fotocasa", "indomio", "eactivos",
    "solvia", "diglo", "altamira", "gobid", "escrapalia"
  ];

  function language() {
    return window.AuctionRadarUI && typeof window.AuctionRadarUI.language === "function" ? window.AuctionRadarUI.language() : "es";
  }

  function t(value, params) {
    var out = language() === "es" ? (labels[value] || value) : value;
    Object.keys(params || {}).forEach(function (key) {
      out = out.replace("{" + key + "}", String(params[key]));
    });
    return out;
  }

  function translateStatic() {
    document.querySelectorAll("[data-connection-i18n]").forEach(function (element) {
      var key = element.getAttribute("data-connection-i18n");
      if (element.tagName === "TITLE") document.title = t(key);
      else element.textContent = t(key);
    });
  }

  function showError(message) {
    if (toastTimer) { window.clearTimeout(toastTimer); toastTimer = null; }
    errorBox.className = "alert alert-error connections-error";
    noticeKey = message || '';
    errorBox.textContent = message ? t(message) : "";
  }

  function api(path, options) {
    options = options || {};
    var headers = new Headers(options.headers || {});
    if (options.method && !["GET", "HEAD"].includes(options.method.toUpperCase())) {
      headers.set("X-CSRFToken", csrfToken);
    }
    if (options.json !== undefined) {
      headers.set("Content-Type", "application/json");
      options.body = JSON.stringify(options.json);
      delete options.json;
    }
    options.headers = headers;
    return fetch(path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) {
          var error = new Error(body && body.error ? body.error : "request_failed");
          error.status = response.status;
          throw error;
        }
        return body;
      });
    });
  }

  function reportApiError(error) {
    var messages = {
      invalid_source_url: "Source URL must be HTTPS, public, and must not contain credentials or secret query parameters.",
      source_dns_unavailable: "Source URL must be HTTPS, public, and must not contain credentials or secret query parameters.",
      unsupported_adapter: "This source adapter is not supported.",
      invalid_certificate: "Certificate was rejected. Check the P12/PFX file and passphrase; details are not recorded.",
      credential_store_unavailable: "The private certificate store is not configured.",
      credential_store_failed: "The private certificate store is not configured.",
      https_required: "Certificate actions require HTTPS in production.",
      secure_cookie_required: "Production certificate actions require secure session cookies.",
      explicit_consent_required: "This action requires explicit certificate consent.",
      certificate_and_consent_required: "This action requires explicit certificate consent.",
      source_exists: "This source ID is already in use.",
      auth_required: "Sign in to manage private connections."
    };
    showError(messages[error && error.message] || "Request failed. Please try again.");
  }

  function node(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function renderSources() {
    list.replaceChildren();
    if (!sources.length) {
      list.appendChild(node("p", "text-muted", t("No sources configured yet. Add a public source to start; saving it does not run a search.")));
      return;
    }
    sources.forEach(function (source) {
      var row = node("article", "connections-source");
      var heading = node("div", "connections-toolbar");
      var title = node("strong", "", source.name + " · " + source.id);
      var enabled = node("span", source.enabled ? "chip chip-status-ok" : "chip chip-status-pending", t(source.enabled ? "Enabled" : "Disabled"));
      heading.append(title, enabled);
      var url = node("div", "connections-source-url", source.url);
      var details = node("div", "text-small text-muted", source.asset_types.join(", ") + " · " + t(source.adapter_id));
      var actions = node("div", "connections-actions");
      var edit = node("button", "btn", t("Edit"));
      edit.type = "button";
      edit.addEventListener("click", function () { editSource(source); });
      var remove = node("button", "btn", t("Delete"));
      remove.type = "button";
      remove.addEventListener("click", function () { deleteSource(source); });
      actions.append(edit, remove);
      row.append(heading, url, details, actions);
      list.appendChild(row);
    });
  }

  function populateAdapters() {
    var select = document.getElementById("source-adapter");
    var selected = select.value;
    select.replaceChildren();
    adapters.forEach(function (id) {
      var option = document.createElement("option");
      option.value = id;
      option.textContent = t(id);
      select.appendChild(option);
    });
    if (selected && adapters.indexOf(selected) !== -1) select.value = selected;
  }

  function resetEditor() {
    sourceForm.reset();
    document.getElementById("source-id").disabled = false;
    document.getElementById("source-editor-heading").textContent = t("Source details");
    editingId = null;
    editor.hidden = true;
  }

  function editSource(source) {
    editingId = source.id;
    document.getElementById("source-editor-heading").textContent = t("Source details");
    document.getElementById("source-id").value = source.id;
    document.getElementById("source-id").disabled = true;
    document.getElementById("source-name").value = source.name;
    document.getElementById("source-url").value = source.url;
    document.getElementById("source-adapter").value = source.adapter_id;
    Array.from(document.getElementById("source-types").options).forEach(function (option) {
      option.selected = source.asset_types.includes(option.value);
    });
    document.getElementById("source-enabled").checked = !!source.enabled;
    editor.hidden = false;
    document.getElementById("source-name").focus();
  }

  function deleteSource(source) {
    if (!window.confirm(t("Delete this source from your private workspace?"))) return;
    api("/api/connections/sources/" + encodeURIComponent(source.id), { method: "DELETE" })
      .then(function () { showError(""); showToast("Source deleted."); return loadSources(); })
      .catch(reportApiError);
  }

  function loadSources() {
    return api("/api/connections/sources").then(function (data) {
      sources = Array.isArray(data.sources) ? data.sources : [];
      renderSources();
    }).catch(reportApiError);
  }

  function renderCertificate(status) {
    var box = document.getElementById("certificate-status");
    var toggle = document.getElementById("toggle-certificate");
    var remove = document.getElementById("delete-certificate");
    document.querySelectorAll('#certificate-form input, #certificate-form button[type="submit"]').forEach(function (control) {
      control.disabled = !status.key_available;
    });
    box.replaceChildren();
    toggle.hidden = !status.configured;
    remove.hidden = !status.configured;
    if (!status.key_available) {
      box.textContent = t("Private encryption key is not configured or is insecure. Certificate operations are unavailable.");
      toggle.disabled = true;
      remove.disabled = false;
      return;
    }
    toggle.disabled = false;
    if (!status.configured) {
      box.textContent = t("Certificate is not configured.");
      return;
    }
    var state = status.enabled ? "Certificate is enabled for authenticated BOE photo access." : "Certificate is stored but disabled.";
    box.appendChild(node("div", "", t(state)));
    if (status.expires_at) {
      var date = new Date(status.expires_at);
      box.appendChild(node("div", "text-small", t("Certificate expiry: {date}", { date: Number.isNaN(date.getTime()) ? "—" : date.toLocaleDateString() })));
    }
    toggle.textContent = t(status.enabled ? "Disable certificate" : "Enable certificate");
    toggle.dataset.enabled = status.enabled ? "false" : "true";
  }

  function loadCertificate() {
    return api("/api/connections/certificate").then(renderCertificate).catch(reportApiError);
  }

  function showToast(message) {
    if (toastTimer) window.clearTimeout(toastTimer);
    // Reuse the accessible alert, then clear it after the next interaction.
    errorBox.className = "alert alert-info connections-error";
    noticeKey = message;
    errorBox.textContent = t(message);
    toastTimer = window.setTimeout(function () { errorBox.className = "alert alert-error connections-error"; errorBox.textContent = ""; noticeKey = ''; toastTimer = null; }, 3500);
  }

  document.getElementById("add-source").addEventListener("click", function () {
    resetEditor();
    editor.hidden = false;
    document.getElementById("source-id").focus();
  });
  document.getElementById("cancel-source").addEventListener("click", resetEditor);

  sourceForm.addEventListener("submit", function (event) {
    event.preventDefault();
    if (sourceSaving) return;
    sourceSaving = true;
    var submit = sourceForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    showError("");
    var payload = {
      id: document.getElementById("source-id").value.trim(),
      name: document.getElementById("source-name").value.trim(),
      url: document.getElementById("source-url").value.trim(),
      adapter_id: document.getElementById("source-adapter").value,
      asset_types: Array.from(document.getElementById("source-types").selectedOptions).map(function (option) { return option.value; }),
      enabled: document.getElementById("source-enabled").checked
    };
    var path = "/api/connections/sources" + (editingId ? "/" + encodeURIComponent(editingId) : "");
    var method = editingId ? "PATCH" : "POST";
    if (editingId) delete payload.id;
    api(path, { method: method, json: payload })
      .then(function () { resetEditor(); showToast("Source saved. No search was started."); return loadSources(); })
      .catch(reportApiError).finally(function () { sourceSaving = false; submit.disabled = false; });
  });

  certForm.addEventListener("submit", function (event) {
    event.preventDefault();
    if (certificateSaving) return;
    showError("");
    if (!document.getElementById("certificate-consent").checked) {
      showError("This action requires explicit certificate consent.");
      return;
    }
    var data = new FormData(certForm);
    certificateSaving = true;
    var submit = certForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    api("/api/connections/certificate", { method: "POST", body: data })
      .then(function () {
        certForm.reset();
        showToast("Certificate saved encrypted.");
        return loadCertificate();
      }).catch(function (error) {
        document.getElementById("certificate-passphrase").value = "";
        reportApiError(error);
      }).finally(function () { certificateSaving = false; submit.disabled = false; });
  });

  document.getElementById("toggle-certificate").addEventListener("click", function (event) {
    var enabled = event.currentTarget.dataset.enabled === "true";
    api("/api/connections/certificate", { method: "PATCH", json: { enabled: enabled, consent: enabled } })
      .then(function () { showError(""); showToast("Certificate consent updated."); return loadCertificate(); })
      .catch(reportApiError);
  });

  document.getElementById("delete-certificate").addEventListener("click", function () {
    if (!window.confirm(t("Delete this certificate and revoke BOE access?"))) return;
    api("/api/connections/certificate", { method: "DELETE" })
      .then(function () { showError(""); showToast("Certificate deleted."); return loadCertificate(); })
      .catch(reportApiError);
  });

  document.addEventListener("auctionradar:languagechange", function () {
    translateStatic();
    populateAdapters();
    renderSources();
    if (noticeKey) errorBox.textContent = t(noticeKey);
    loadCertificate();
  });

  translateStatic();
  populateAdapters();
  loadSources();
  loadCertificate();
})();
