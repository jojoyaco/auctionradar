/* AuctionRadar · app.js
 * Vanilla JS · no dependencies · sanitizes external values with textContent
 * and validates http(s) URLs before assigning them to href/src.
 */
(function () {
  "use strict";

  // ----------------- Configuration and utilities -----------------

  var META = document.querySelector('meta[name="csrf-token"]');
  var USER_META = document.querySelector('meta[name="user-admin"]');
  var csrfToken = META ? META.getAttribute("content") || "" : "";
  var isAdmin = USER_META ? USER_META.getAttribute("content") === "true" : false;
  var WORKSPACE_META = document.querySelector('meta[name="workspace-private"]');
  var privateWorkspace = !!WORKSPACE_META && WORKSPACE_META.getAttribute("content") === "true";

  function canManageWorkspace() { return privateWorkspace || isAdmin; }

  /** Build a normalized absolute or relative URL. */
  function apiUrl(path) {
    if (typeof path !== "string" || !path) return "";
    if (path.charAt(0) === "/") return path;
    return "/" + path;
  }

  /** Wrapper sobre fetch con cookies y cabeceras JSON + CSRF. */
  function apiFetch(path, options) {
    var opts = options || {};
    var method = (opts.method || "GET").toUpperCase();
    var init = {
      method: method,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
      },
    };
    if (method !== "GET" && method !== "HEAD") {
      init.headers["Content-Type"] = "application/json";
      init.headers["X-CSRFToken"] = csrfToken;
    }
    if (opts.body !== undefined && opts.body !== null) {
      init.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
    }
    if (opts.signal) init.signal=opts.signal;
    return fetch(apiUrl(path), init).then(function (resp) {
      var contentType = resp.headers.get("content-type") || "";
      var isJson = contentType.indexOf("application/json") !== -1;
      if (resp.status === 401) {
        return (isJson ? resp.json() : Promise.resolve({})).then(function (body) {
          if (body.error !== "invalid_current_password" && window.location.pathname !== "/login") window.location.href = "/login";
          return Promise.reject({ status: resp.status, error: window.AuctionRadarUI.errorMessage(body.error || "auth_required") });
        });
      }
      if (resp.status === 403) {
        // Prohibido: no redirigir; mostrar error en contexto (permisos insuficientes).
        var msg403 = t("Acceso denegado");
        if (isJson) {
          return resp.json().then(function (body) {
            return Promise.reject({ status: 403, error: body && body.error ? window.AuctionRadarUI.errorMessage(body.error) : msg403 });
          });
        }
        return Promise.reject({ status: 403, error: msg403 });
      }
      if (!resp.ok) {
        if (isJson) {
          return resp.json().then(function (body) {
            return Promise.reject({ status: resp.status, error: window.AuctionRadarUI.errorMessage((body && body.error) || ("HTTP " + resp.status)) });
          });
        }
        return Promise.reject({ status: resp.status, error: "HTTP " + resp.status });
      }
      if (isJson) return resp.json();
      return resp.text();
    });
  }

  /** Check whether a URL uses http(s). */
  function safeHttpUrl(value) {
    if (typeof value !== "string") return null;
    var trimmed = value.trim();
    if (!trimmed) return null;
    if (/^javascript:/i.test(trimmed)) return null;
    if (/^data:/i.test(trimmed)) return null;
    if (!/^https?:\/\//i.test(trimmed)) {
      // Allow relative paths beginning with "/", but only http(s) for external hrefs.
      if (trimmed.charAt(0) === "/") return trimmed;
      return null;
    }
    try {
      var u = new URL(trimmed);
      if (u.protocol !== "http:" && u.protocol !== "https:") return null;
      return u.toString();
    } catch (e) {
      return null;
    }
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /**
   * Read a field from the item, checking the top level first
   * (as returned by _item_payload), then item.data as a fallback.
   * Return undefined if the field is absent from both.
   */
  function itemField(item, name) {
    if (!item) return undefined;
    if (item[name] !== undefined && item[name] !== null) return item[name];
    if (item.data && typeof item.data === "object" && item.data[name] !== undefined && item.data[name] !== null) {
      return item.data[name];
    }
    return undefined;
  }

  /**
   * Convert an arbitrary item value to a human-readable string, avoiding
   * "[object Object]" when the backend returns an object (location, possession,
   * availability, etc.).
   *
   * Recursive rules:
   *  - null / undefined / ""  -> null (the caller decides what to display)
   *  - string / number / boolean -> String(value), trimmed
   *  - array -> join humanized elements with ", "
   *  - object -> prioritize key fields according to a semantic map
   *    (location, possession, availability, generic); if none match,
   *    fall back to "key: value" pairs without unbounded recursion.
   *  - sensitive fields (password / token / etc.) -> redact their values.
   *
   * Return null when there is nothing readable to display.
   */
  var HUMAN_KEY_ORDER = {
    location: ["address", "postal_code", "locality", "city", "municipality", "province", "region", "country"],
    possession: ["status", "documented_delivery_free"],
    availability: ["status", "verification", "checked_at"],
    generic: ["label", "name", "title", "value", "text", "status", "type", "code"],
  };

  var HUMAN_LABELS_ES = {
    address: "dirección",
    locality: "localidad",
    city: "ciudad",
    municipality: "municipio",
    province: "provincia",
    region: "región",
    country: "país",
    postal_code: "CP",
    status: "estado",
    documented_delivery_free: "entrega documentada libre",
    verification: "verificación",
    checked_at: "verificado",
    label: "etiqueta",
    name: "nombre",
    title: "título",
    value: "valor",
    text: "texto",
    type: "tipo",
    code: "código",
  };

  // Sensitive keys: their values must NEVER appear in the UI.
  var HUMAN_SENSITIVE_KEYS = {
    password: 1, passwd: 1, pwd: 1, token: 1, api_key: 1, apikey: 1,
    secret: 1, authorization: 1, bearer: 1, cookie: 1, csrf: 1,
    csrf_token: 1, session: 1, private_key: 1, fingerprint: 1,
    access_token: 1, refresh_token: 1, client_secret: 1,
  };

  function humanLabel(key) {
    var src = HUMAN_LABELS_ES[key];
    return src ? t(src) : key;
  }

  function isHumanSensitive(key) {
    if (!key) return false;
    return HUMAN_SENSITIVE_KEYS[String(key).toLowerCase()] === 1;
  }

  function possessionLabel(value) {
    if (!value || typeof value !== "string") return null;
    var v = value.toLowerCase();
    var sources = {
      unknown: "desconocida",
      vacant: "vacante",
      vacant_free: "vacante libre",
      vacant_documented_free: "vacante documentada libre",
      seller_claim_free: "anunciada libre por el vendedor",
      occupied: "ocupada",
      occupied_tenants_right: "ocupada con derecho de arrendamiento",
      occupied_tenants_old_rent: "ocupada (renta antigua)",
      occupied_squatters: "ocupada por okupación",
      occupied_unknown_rights: "ocupada (derechos por confirmar)",
      indiviso: "proindiviso",
      nuda_propiedad: "nuda propiedad",
      deuda_cesion: "cesión de deuda",
    };
    var s = sources[v];
    return s ? t(s) : null;
  }

  function availabilityLabel(value) {
    if (!value || typeof value !== "string") return null;
    var v = value.toLowerCase();
    var sources = {
      unknown: "desconocida",
      available: "disponible",
      seller_claim_available: "anunciada disponible por el vendedor",
      sold: "vendido",
      withdrawn: "retirada",
      blocked: "bloqueada",
      pending_review: "pendiente de revisar",
    };
    var s = sources[v];
    return s ? t(s) : null;
  }

  function humanValue(value, opts) {
    if (value === null || value === undefined) return null;
    var maxDepth = (opts && typeof opts.maxDepth === "number") ? opts.maxDepth : 3;
    var kind = (opts && opts.kind) || null;

    if (typeof value === "string") {
      var trimmed = value.trim();
      if (!trimmed) return null;
      if (/^\d{4}-\d{2}-\d{2}T/.test(trimmed)) {
        var formatted = formatDateTime(trimmed);
        if (formatted) return formatted;
      }
      return trimmed;
    }
    if (typeof value === "number") {
      if (!isFinite(value)) return null;
      return String(value);
    }
    if (typeof value === "boolean") {
      return value ? t("Sí") : t("No");
    }

    if (Array.isArray(value)) {
      var parts = [];
      for (var i = 0; i < value.length && parts.length < 6; i++) {
        var sub = humanValue(value[i], { maxDepth: maxDepth - 1 });
        if (sub) parts.push(sub);
      }
      if (!parts.length) return null;
      return parts.join(", ");
    }

    if (typeof value === "object") {
      if (maxDepth <= 0) return null;

      var keys = HUMAN_KEY_ORDER[kind] || HUMAN_KEY_ORDER.generic;
      var orderedKeys = [];
      var seenKeys = {};
      for (var k = 0; k < keys.length; k++) {
        if (Object.prototype.hasOwnProperty.call(value, keys[k]) && value[keys[k]] !== null && value[keys[k]] !== undefined && value[keys[k]] !== "") {
          orderedKeys.push(keys[k]);
          seenKeys[keys[k]] = true;
        }
      }
      var allKeys = Object.keys(value);
      for (var a = 0; a < allKeys.length; a++) {
        var ak = allKeys[a];
        if (seenKeys[ak]) continue;
        if (ak === "evidence" || ak === "raw" || ak === "source_url" || ak === "metadata") continue;
        if (value[ak] === null || value[ak] === undefined || value[ak] === "") continue;
        orderedKeys.push(ak);
      }

      var segments = [];
      var statusTranslated = false;
      for (var j = 0; j < orderedKeys.length && segments.length < 4; j++) {
        var key = orderedKeys[j];
        var raw = value[key];
        if (isHumanSensitive(key)) {
          // Never show the value; indicate presence discreetly.
          segments.push(humanLabel(key) + ": ••••");
          continue;
        }
        if (key === "status" && (kind === "possession" || kind === "availability")) {
          var translated = (kind === "possession" ? possessionLabel(raw) : availabilityLabel(raw));
          if (translated) { segments.push(translated); statusTranslated = true; continue; }
        }
        // For possession/availability, skip status if it has already been translated.
        // campos ruidosos verification/checked_at/documented_delivery_free.
        if (statusTranslated && kind === "availability") {
          if (key === "verification" || key === "checked_at") continue;
        }
        if (typeof raw === "boolean") {
          segments.push(humanLabel(key) + ": " + (raw ? t("Sí") : t("No")));
          continue;
        }
        var sub2 = humanValue(raw, { maxDepth: maxDepth - 1 });
        if (sub2) {
          if (kind==='location') { if(segments.indexOf(sub2)<0)segments.push(sub2); }
          else if (HUMAN_LABELS_ES[key]) segments.push(humanLabel(key) + ": " + sub2);
          else segments.push(sub2);
        }
      }
      if (!segments.length) return null;
      return segments.join(" · ");
    }

    return null;
  }

  function setText(node, value) {
    if (!node) return;
    if (value === null || value === undefined || value === "") {
      node.textContent = "";
    } else {
      node.textContent = String(value);
    }
  }

  function formatMoney(value, currency) {
    if (window.AuctionRadarUI && typeof window.AuctionRadarUI.formatMoney === "function") {
      return window.AuctionRadarUI.formatMoney(value, currency);
    }
    if (value === null || value === undefined || isNaN(value)) return null;
    var n = Number(value);
    if (!isFinite(n)) return null;
    try {
      return new Intl.NumberFormat("es-ES", {
        style: "currency",
        currency: currency || "EUR",
        maximumFractionDigits: 2,
      }).format(n);
    } catch (e) {
      var sign = currency || "EUR";
      return n.toLocaleString("es-ES", { maximumFractionDigits: 2 }) + " " + sign;
    }
  }

  function formatDateTime(value) {
    if (window.AuctionRadarUI && typeof window.AuctionRadarUI.formatDateTime === "function") {
      return window.AuctionRadarUI.formatDateTime(value);
    }
    if (!value) return null;
    var d = new Date(value);
    if (isNaN(d.getTime())) return null;
    try {
      return new Intl.DateTimeFormat("es-ES", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(d);
    } catch (e) {
      return d.toLocaleString("es-ES");
    }
  }

  function formatDate(value) {
    if (window.AuctionRadarUI && typeof window.AuctionRadarUI.formatDate === "function") {
      return window.AuctionRadarUI.formatDate(value);
    }
    if (!value) return null;
    var d = new Date(value);
    if (isNaN(d.getTime())) return null;
    try {
      return new Intl.DateTimeFormat("es-ES", { dateStyle: "long" }).format(d);
    } catch (e) {
      return d.toLocaleDateString("es-ES");
    }
  }

  function formatNumber(value, suffix) {
    if (window.AuctionRadarUI && typeof window.AuctionRadarUI.formatNumber === "function") {
      return window.AuctionRadarUI.formatNumber(value, suffix);
    }
    if (value === null || value === undefined || isNaN(value)) return null;
    var n = Number(value);
    if (!isFinite(n)) return null;
    try {
      var s = new Intl.NumberFormat("es-ES").format(n);
      return suffix ? s + " " + suffix : s;
    } catch (e) {
      return String(n);
    }
  }

  /** Human-readable label for an amount type (asking, reserve, etc.). */
  function priceTypeLabel(pt) {
    if (!pt) return t("Importe publicado");
    switch (String(pt).toLowerCase()) {
      case "asking": return t("Precio anunciado");
      case "reserve": return t("Reserva");
      case "starting": return t("Precio de salida");
      case "bid": return t("Puja");
      case "award": return t("Adjudicación");
      case "appraisal": return t("Tasación");
      case "auction_value": return t("Valor de subasta");
      default: return t("Importe publicado");
    }
  }

  /** Return the first reasonable amount from a prices object. */
  function pickBestPrice(prices) {
    if (!prices || typeof prices !== "object") return { value: null, type: null };
    var order = ["asking", "starting", "award", "bid", "reserve", "auction_value", "appraisal"];
    for (var i = 0; i < order.length; i++) {
      var k = order[i];
      if (prices[k] !== null && prices[k] !== undefined && !isNaN(prices[k])) {
        return { value: prices[k], type: k };
      }
    }
    return { value: null, type: null };
  }

  function statusLabel(s) {
    if (!s) return t("Pendiente");
    var v = String(s).toLowerCase();
    if (v === "ok") return "OK";
    if (v === "ko") return "KO";
    var labels = {
      pending: "Pendiente",
      pending_review: "Pendiente de comprobar",
      candidate: "Candidato",
      excluded: "Excluido por criterios",
      queued: "En cola",
      running: "En curso",
      partial: "Cobertura parcial",
      success: "Finalizada",
      error: "Error",
      cancelled: "Cancelada",
      blocked: "Bloqueada",
      disabled: "Deshabilitada",
      eligibility_unknown: "Elegibilidad sin acreditar",
      authentication_required: "Requiere cuenta",
      not_selected: "No seleccionada",
      asset_not_applicable: "No aplica al activo",
    };
    var source = labels[v];
    return source ? t(source) : String(s);
  }

  function assetLabel(a) {
    return window.AuctionRadarUI.assetLabel(a);
  }
  function aiLabel(ai) {
    if (!ai || ai.status==='not_requested') return '';
    if(ai.error_code==='invalid_input')return t('Datos insuficientes para IA');
    if (ai.status!=='evaluated') return t(ai.status==='pending'?'IA pendiente':ai.status==='unavailable'?'IA no disponible':'No se pudo evaluar');
    return t(ai.verdict==='keep'?'Recomendada por IA':ai.verdict==='reject'?'Descartada por IA':'Para revisar (IA)');
  }

  function showToast(message, kind) {
    var region = document.querySelector("[data-toast-region]");
    if (!region) return;
    region.hidden = false;
    var node = document.createElement("div");
    node.className = "toast-item alert " + (kind === "error" ? "alert-error" : kind === "info" ? "alert-info" : "alert-ok");
    node.setAttribute("role", kind === "error" ? "alert" : "status");
    node.textContent = message;
    region.appendChild(node);
    setTimeout(function () {
      if (node.parentNode) node.parentNode.removeChild(node);
    }, 4000);
  }

  /** Translation helper. Falls back to identity when AuctionRadarUI is unavailable. */
  function t(source, params) {
    if (window.AuctionRadarUI && typeof window.AuctionRadarUI.t === "function") {
      return window.AuctionRadarUI.t(source, params);
    }
    if (!params) return source;
    return String(source).replace(/\{(\w+)\}/g, function (_, k) {
      return Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : "{" + k + "}";
    });
  }

  // ----------------- Estado -----------------

  var state = {
    me: null,
    schedule: null,
    branding: null,
    aiConfig: null,
    lastUpdatedAt: null,
    filters: {
      asset: "all",
      category: "",
      status: "pending",
      q: "",
      source: "",
      location: "",
      price_min: "",
      price_max: "",
      has_photos: false,
      changed: false,
      sort: "updated",
      ai: "shortlist",
      search_id: "",
      page: 1,
    },
    sourcesCatalog: [], // fuente -> nombre humano
    categoriesCatalog: [], // categorías disponibles desde /api/categories
    pagination: { page: 1, pages: 1, total: 0 },
    feed: { generation:0, queryVersion:0, busy:false, initial:false, offset:0, hasMore:false, error:false, controller:null, promise:null, key:null, observer:null, reviewWrites:0 },
    lastRun: null,
    counts: { pending: 0, ok: 0, ko: 0, changed: 0 },
    settings: null,
    settingsSources: [],
    settingsRegions: [],
    users: [],
    runs: [],
    running: false,
    pollHandle: null,
    sourcesLoaded: false,
    carouselIndex: Object.create(null), // item.id -> índice de foto actual
  };

  // ----------------- Toasts -----------------

  // (Toast-region styles are in /static/app.css.)

  // ----------------- Navigation / sidebar -----------------

  var NAV_VIEWS = ["oportunidades", "suscripciones", "busquedas", "configuracion", "usuarios", "cuenta"];

  function applyAdminVisibility() {
    var nodes = document.querySelectorAll("[data-admin-only]");
    nodes.forEach(function (n) {
      if (isAdmin) {
        n.hidden = false;
      } else {
        n.hidden = true;
      }
    });
    document.querySelectorAll("[data-workspace-manager-only]").forEach(function (n) {
      n.hidden = !canManageWorkspace();
    });
  }

  function setActiveNav(view) {
    document.querySelectorAll(".nav-item").forEach(function (n) {
      if (n.getAttribute("data-nav") === view) {
        n.setAttribute("aria-current", "page");
        n.classList.add("active");
      } else {
        n.removeAttribute("aria-current");
        n.classList.remove("active");
      }
    });
  }

  function showView(view) {
    NAV_VIEWS.forEach(function (v) {
      var region = document.querySelector('[data-view="' + v + '"]');
      if (!region) return;
      if (v === view) {
        region.hidden = false;
      } else {
        region.hidden = true;
      }
    });
    setActiveNav(view);
    var titleNode = document.querySelector("[data-topbar-title]");
    if (titleNode) {
      var titles = {
        oportunidades: "Oportunidades",
        suscripciones: "Mis búsquedas",
        busquedas: "Búsquedas",
        configuracion: "Configuración",
        usuarios: "Usuarios",
        cuenta: "Cuenta",
      };
      var titleKey = titles[view] || "AuctionRadar";
      titleNode.textContent = t(titleKey);
    }
    // Lazy load
    if (view === "configuracion") loadSettings();
    if (view === "usuarios") loadUsers();
    if (view === "busquedas") loadRuns();
    if (view === "suscripciones") {
      // The saved-searches.js module loads its data in response to this event;
      // dispatch it only when a listener exists, avoiding silent failures.
      document.dispatchEvent(new CustomEvent("auctionradar:view-suscripciones", { bubbles: true }));
    }
  }

  function selectViewFromHash() {
    var hash = (window.location.hash || "").replace(/^#/, "");
    var view = NAV_VIEWS.indexOf(hash) !== -1 ? hash : "oportunidades";
    // Restrictions for non-admin users
    if ((!canManageWorkspace() && view === "configuracion") || (!isAdmin && view === "usuarios")) {
      view = "oportunidades";
    }
    showView(view);
  }

  // ----------------- Mobile sidebar -----------------

  function setupSidebarToggle() {
    var btn = document.querySelector("[data-menu-toggle]");
    var sb = document.getElementById("app-sidebar");
    if (!btn || !sb) return;
    btn.addEventListener("click", function () {
      var open = sb.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        var firstLink = sb.querySelector('.nav-item');
        if (firstLink) firstLink.focus();
      }
    });
    sb.addEventListener("click", function (e) {
      // Close when a link is clicked on mobile
      if (e.target && e.target.closest && e.target.closest(".nav-item")) {
        if (window.innerWidth <= 880) {
          sb.classList.remove("open");
          btn.setAttribute("aria-expanded", "false");
        }
      }
    });
  }

  // ----------------- Lista de oportunidades -----------------

  function buildFiltersPayload(options) {
    var f = state.filters;
    var params = [];
    params.push("asset=" + encodeURIComponent(f.asset || "all"));
    if (f.category) params.push("category=" + encodeURIComponent(f.category));
    params.push("status=" + encodeURIComponent(f.status || "all"));
    if (f.q) params.push("q=" + encodeURIComponent(f.q));
    if (f.changed) params.push("changed=1");
    if (f.source) params.push("source=" + encodeURIComponent(f.source));
    if (f.location) params.push("location=" + encodeURIComponent(f.location));
    if (f.has_photos) params.push("has_photos=1");
    if (f.sort && f.sort !== "updated") params.push("sort=" + encodeURIComponent(f.sort));
    if (f.ai && f.search_id) params.push("ai=" + encodeURIComponent(f.ai));
    if (f.search_id) params.push("search_id=" + encodeURIComponent(f.search_id));
    ['price_min','price_max'].forEach(function(key){if(f[key]!=='' && f[key]!==null && f[key]!==undefined)params.push(key+'='+encodeURIComponent(f[key]));});
    params.push("page=" + encodeURIComponent(f.page || 1));
    if(options && options.offset!==undefined)params.push('offset='+encodeURIComponent(options.offset));
    return "/api/items?" + params.join("&");
  }

  function ensureSourceSelect() {
    var sel = document.querySelector('[data-filter="source"]');
    if (!sel) return;
    // Keep only "All"
    sel.innerHTML = "";
    var opt = document.createElement("option");
    opt.value = "";
    opt.textContent = t("Todas las fuentes");
    sel.appendChild(opt);
    state.sourcesCatalog.forEach(function (s) {
      var o = document.createElement("option");
      o.value = s.id;
      o.textContent = s.name || s.id;
      sel.appendChild(o);
    });
    sel.value = state.filters.source || "";
    // Keep the "More filters" button in sync (the badge is updated by
    // renderActiveFilterChips, which already listens to state.filters).
    if (typeof renderActiveFilterChips === "function") renderActiveFilterChips();
  }

  function ensureCategorySelect() {
    var sel = document.querySelector('[data-filter="category"]');
    if (!sel) return;
    sel.innerHTML = "";
    var opt = document.createElement("option");
    opt.value = "";
    opt.textContent = t("Todas las categorías");
    sel.appendChild(opt);
    state.categoriesCatalog.forEach(function (c) {
      var o = document.createElement("option");
      o.value = c.id;
      o.textContent = window.AuctionRadarUI && window.AuctionRadarUI.language()==='en' ? (c.label_en || c.label_es || c.id) : (c.label_es || c.id);
      sel.appendChild(o);
    });
    sel.value = state.filters.category || "";
  }

  function updateCountsRow(payload) {
    if (!payload) return;
    var counts = payload.counts || {};
    var total = payload.total;
    var pendingNode = document.querySelector("[data-count-pending]");
    var okNode = document.querySelector("[data-count-ok]");
    var koNode = document.querySelector("[data-count-ko]");
    var lastNode = document.querySelector("[data-last-run]");

    function setVal(node, prefix, val) {
      if (!node) return;
      node.textContent = prefix + ": " + (val === null || val === undefined ? "—" : String(val));
    }

    setVal(pendingNode, t("Pendientes"), counts.pending);
    setVal(okNode, "OK", counts.ok);
    setVal(koNode, "KO", counts.ko);
    if (lastNode) {
      var lastRun = formatDateTime(payload.last_run);
      lastNode.textContent = t("Último recorrido: ") + (lastRun || "—");
    }
    var lastUpdWrap = document.querySelector("[data-lastupdated-wrap]");
    var lastUpdLbl = document.querySelector("[data-lastupdated-label]");
    var lastUpdatedAt = payload.last_updated_at || state.lastUpdatedAt;
    if (lastUpdLbl) {
      if (lastUpdatedAt) {
        var formatted = formatDateTime(lastUpdatedAt);
        lastUpdLbl.textContent = formatted || t("Sin datos disponibles");
        if (lastUpdWrap) lastUpdWrap.hidden = false;
      } else {
        lastUpdLbl.textContent = t("Sin datos disponibles");
        if (lastUpdWrap) lastUpdWrap.hidden = false;
      }
    }
  }

  function renderEmptyState(message, kind) {
    var region = document.querySelector("[data-list-region]");
    if (!region) return;
    region.innerHTML = "";
    var el = document.createElement("div");
    el.className = "state" + (kind === "error" ? " state-error" : "");
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    var tt = document.createElement("p");
    tt.className = "state-title";
    tt.textContent = message.title || t("Sin resultados");
    el.appendChild(tt);
    if (message.body) {
      var b = document.createElement("p");
      b.className = "text-small text-muted mt-sm";
      b.textContent = message.body;
      el.appendChild(b);
    }
    region.appendChild(el);
  }

  function renderLoadingState() {
    var region = document.querySelector("[data-list-region]");
    if (!region) return;
    region.innerHTML = "";
    var el = document.createElement("div");
    el.className = "state";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    var sp = document.createElement("div");
    sp.className = "spinner";
    el.appendChild(sp);
    var tt = document.createElement("p");
    tt.className = "state-title mb-0";
    tt.textContent = t("Cargando oportunidades…");
    el.appendChild(tt);
    region.appendChild(el);
  }

  function buildCard(item) {
    var card = document.createElement("article");
    card.className = "item-card";
    card.setAttribute("data-item-id", item.id || "");
    var statusName = (item.review && item.review.status) || "pending";
    card.setAttribute("data-review-status", statusName);
    if (item.changed_since_review) card.setAttribute("data-changed", "true");

    // Photo and navigation: the photo is a separate button that opens details.
    // Arrows and counter are siblings, so buttons are never nested.
    var mediaWrap = document.createElement("div");
    mediaWrap.className = "item-card-photo";
    var photos = Array.isArray(item.photos) ? item.photos : [];
    var photoUrls = [];
    for (var pi = 0; pi < photos.length; pi++) {
      var u = safeHttpUrl(photos[pi].url);
      if (u) photoUrls.push({ url: u, caption: photos[pi].caption || "" });
    }
    var photoBtn = document.createElement("button");
    photoBtn.type = "button";
    photoBtn.className = "item-card-photo-btn item-card-open";
    photoBtn.setAttribute("data-item-open", "");
    photoBtn.setAttribute("aria-label",
      t("Ver detalle de {title}", { title: (item.title || item.id || t("Oportunidad")) })
    );
    if (photoUrls.length > 0) {
      var currentIndex = state.carouselIndex[item.id] || 0;
      if (currentIndex >= photoUrls.length) currentIndex = 0;
      state.carouselIndex[item.id] = currentIndex;
      var imgEl = document.createElement("img");
      imgEl.className = "item-card-photo-img";
      imgEl.setAttribute("data-photo-img", "");
      imgEl.src = photoUrls[currentIndex].url;
      imgEl.alt = photoUrls[currentIndex].caption || item.title || t("Imagen del anuncio");
      imgEl.loading = "lazy";
      imgEl.decoding = "async";
      imgEl.referrerPolicy = "no-referrer";
      photoBtn.appendChild(imgEl);
    } else {
      var ph = document.createElement("div");
      ph.className = "item-card-photo-empty";
      var lbl = document.createElement("span");
      lbl.textContent = t(item.photo_notice&&item.photo_notice.code==='boe_login_required'?'Información adicional tras acceso al BOE':'Sin fotos disponibles');
      ph.appendChild(lbl);
      photoBtn.appendChild(ph);
    }

    // Badges sobre la foto (asset + estado + IA). pointer-events:none en CSS.
    var badges = document.createElement("div");
    badges.className = "item-card-badges";

    function addChip(text, modifier) {
      var c = document.createElement("span");
      c.className = "chip" + (modifier ? " " + modifier : "");
      c.textContent = text;
      badges.appendChild(c);
    }

    addChip(assetLabel(item), "chip-asset-" + (item.asset_type || "other"));
    addChip(statusLabel(statusName), "chip-status-" + statusName);
    if (item.selection && item.selection.status === "excluded") {
      addChip(t("Excluido por criterios"), "chip-status-ko");
    }
    if (item.changed_since_review) {
      addChip(t("Cambios"), "chip-status-changed");
    }
    if (item.ai && item.ai.verdict && item.ai.status!=='not_requested') {
      var v = String(item.ai.verdict).toLowerCase();
      var verdictLabel = aiLabel(item.ai);
      var mod = v === "keep" ? "chip-status-ok"
        : v === "needs_review" ? "chip-status-pending"
        : v === "reject" ? "chip-status-ko"
        : null;
      if (verdictLabel) addChip(verdictLabel, mod);
    }
    mediaWrap.appendChild(badges);
    mediaWrap.appendChild(photoBtn);

    // Carousel arrows (only when there is more than one photo). Siblings of photoBtn.
    if (photoUrls.length > 1) {
      var counter = document.createElement("span");
      counter.className = "item-card-photo-counter";
      counter.setAttribute("data-photo-counter", "");
      counter.textContent = t("Foto {current} de {total}", {
        current: (state.carouselIndex[item.id] || 0) + 1,
        total: photoUrls.length,
      });
      mediaWrap.appendChild(counter);

      var prevBtn = document.createElement("button");
      prevBtn.type = "button";
      prevBtn.className = "item-card-photo-prev";
      prevBtn.setAttribute("data-photo-prev", "");
      prevBtn.setAttribute("aria-label", t("Foto anterior"));
      prevBtn.textContent = "‹";
      mediaWrap.appendChild(prevBtn);

      var nextBtn = document.createElement("button");
      nextBtn.type = "button";
      nextBtn.className = "item-card-photo-next";
      nextBtn.setAttribute("data-photo-next", "");
      nextBtn.setAttribute("aria-label", t("Foto siguiente"));
      nextBtn.textContent = "›";
      mediaWrap.appendChild(nextBtn);

      // Click handlers: cycle through images without opening details or the lightbox.
      function showIndex(idx) {
        var total = photoUrls.length;
        var ni = ((idx % total) + total) % total;
        state.carouselIndex[item.id] = ni;
        var img2 = mediaWrap.querySelector("[data-photo-img]");
        if (img2) {
          img2.src = photoUrls[ni].url;
          img2.alt = photoUrls[ni].caption || item.title || t("Imagen del anuncio");
        }
        var cnt = mediaWrap.querySelector("[data-photo-counter]");
        if (cnt) cnt.textContent = t("Foto {current} de {total}", {
          current: ni + 1, total: total,
        });
      }
      prevBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        e.preventDefault();
        showIndex((state.carouselIndex[item.id] || 0) - 1);
      });
      nextBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        e.preventDefault();
        showIndex((state.carouselIndex[item.id] || 0) + 1);
      });
      mediaWrap.addEventListener('keydown',function(event){
        if(['ArrowLeft','ArrowRight','Home','End'].indexOf(event.key)<0)return;
        event.preventDefault();event.stopPropagation();
        showIndex(event.key==='Home'?0:event.key==='End'?photoUrls.length-1:(state.carouselIndex[item.id]||0)+(event.key==='ArrowLeft'?-1:1));
      });
    }
    // Clicking photoBtn opens details (with no nested buttons).
    photoBtn.addEventListener("click", function () {
      openDrawer(item.id, photoBtn);
    });

    card.appendChild(mediaWrap);

    var body = document.createElement("div");
    body.className = "item-card-body";

    var title = document.createElement("h3");
    title.className = "item-card-title";
    var titleLink=document.createElement('a');
    titleLink.href='/?item='+encodeURIComponent(item.id);
    titleLink.textContent=item.title || item.id || t('Oportunidad');
    titleLink.addEventListener('click',function(event){if(event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;event.preventDefault();openDrawer(item.id,titleLink);});
    title.appendChild(titleLink);
    body.appendChild(title);

    var meta = document.createElement("div");
    meta.className = "item-card-meta";
    var locValue = humanValue(itemField(item, "location"), { kind: "location" });
    if (locValue) {
      var loc = window.AuctionRadarUI.mapsLink(itemField(item,'location'),locValue) || document.createElement("span");
      if(!loc.hasAttribute('data-map-link'))loc.textContent = locValue;
      meta.appendChild(loc);
    }
    if (item.source_id) {
      var src = document.createElement("span");
      src.className = "text-faint text-small";
      src.textContent = t("Fuente: ") + (item.source_id);
      meta.appendChild(src);
    }
    if (item.category) {
      var catLabel = categoryLabel(item.category);
      if (catLabel) {
        var cat = document.createElement("span");
        cat.className = "text-faint text-small";
        cat.textContent = catLabel;
        meta.appendChild(cat);
      }
    }
    body.appendChild(meta);

    var priceRow = document.createElement("div");
    priceRow.className = "item-card-price";
    var displayPrice = (typeof item.display_price === "number") ? item.display_price : null;
    var displayPriceType = item.display_price_type || null;
    var pricesObj = item.prices || itemField(item, "prices") || {};
    var best = pickBestPrice(pricesObj);
    if ((best.value === null || best.value === undefined) && typeof item.exact_price === "number") {
      best = { value: item.exact_price, type: item.price_type };
    }
    // Si hay display_price (conocido o null) lo respetamos: siempre al final.
    var currency = item.currency || itemField(item, "currency") || "EUR";
    var money = null;
    if (displayPrice !== null && displayPrice !== undefined && !isNaN(displayPrice)) {
      money = formatMoney(displayPrice, currency);
      best = { value: displayPrice, type: displayPriceType || best.type };
    } else if (!Object.prototype.hasOwnProperty.call(item,'display_price') && best.value !== null && best.value !== undefined) {
      money = formatMoney(best.value, currency);
    }
    if (money) {
      priceRow.textContent = money;
      var pt = document.createElement("span");
      pt.className = "price-type";
      pt.textContent = priceTypeLabel(best.type || displayPriceType || item.price_type);
      priceRow.appendChild(pt);
    } else {
      priceRow.textContent = t("Importe no publicado");
      var pt2 = document.createElement("span");
      pt2.className = "price-type";
      pt2.textContent = t("Precio a confirmar");
      priceRow.appendChild(pt2);
    }
    body.appendChild(priceRow);

    card.appendChild(body);

    var footer = document.createElement("div");
    footer.className = "item-card-footer";
    var seen = formatDate(item.last_seen || item.observed_at || item.first_seen);
    var left = document.createElement("span");
    left.textContent = seen ? (t("Visto ") + seen) : t("Sin fecha");
    footer.appendChild(left);
    var right = document.createElement("span");
    var photosN = photoUrls.length;
    right.textContent = t(photosN === 1 ? "{n} foto" : "{n} fotos", { n: photosN });
    footer.appendChild(right);

    // Quick actions (OK / KO). These buttons are not nested inside the main button
    // because the article is not interactive and the buttons are siblings.
    var actions = document.createElement("div");
    actions.className = "item-card-actions";
    var okBtn = document.createElement("button");
    okBtn.type = "button";
    okBtn.className = "btn btn-review-ok review-action-ok";
    okBtn.setAttribute("data-review-ok", "");
    okBtn.setAttribute("aria-pressed", statusName === "ok" ? "true" : "false");
    okBtn.setAttribute("aria-label", t("Marcar OK"));
    okBtn.textContent = "✓ " + "OK";
    okBtn.disabled=!!(detailReviewSaves[item.id]&&detailReviewSaves[item.id].inFlight);
    okBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      quickReview(item.id, "ok", okBtn, koBtn);
    });
    actions.appendChild(okBtn);
    var koBtn = document.createElement("button");
    koBtn.type = "button";
    koBtn.className = "btn btn-review-ko review-action-ko";
    koBtn.setAttribute("data-review-ko", "");
    koBtn.setAttribute("aria-pressed", statusName === "ko" ? "true" : "false");
    koBtn.setAttribute("aria-label", t("Marcar KO"));
    koBtn.disabled=!!(detailReviewSaves[item.id]&&detailReviewSaves[item.id].inFlight);
    koBtn.textContent = "✕ " + "KO";
    koBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      quickReview(item.id, "ko", okBtn, koBtn);
    });
    actions.appendChild(koBtn);

    card.appendChild(actions);
    card.appendChild(footer);
    // Keep a reference to the item so quickReview can preserve
    // existing notes instead of overwriting them.
    card._auctionradarItem = item;
    if (item && item.id) {
      okBtn.setAttribute("data-card-id", String(item.id));
      koBtn.setAttribute("data-card-id", String(item.id));
    }
    return card;
  }

  function categoryLabel(id) {
    if (!id) return null;
    var list = state.categoriesCatalog || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].id === id) {
        return window.AuctionRadarUI && window.AuctionRadarUI.language()==='en' ? (list[i].label_en || list[i].label_es || id) : (list[i].label_es || id);
      }
    }
    return null;
  }

  function renderItems(payload) {
    state.lastItems = payload;
    // Rebuild the grid; preserve each item.id carousel index
    // so that changing language or re-rendering does not reset the photo.
    var region = document.querySelector("[data-list-region]");
    if (!region) return;
    region.innerHTML = "";
    if (!payload || !Array.isArray(payload.items) || payload.items.length === 0) {
      var isFilterEmpty = (state.filters.q || state.filters.source || state.filters.location || state.filters.has_photos || state.filters.changed || state.filters.status !== "all" || state.filters.asset !== "all" || state.filters.category || state.filters.search_id);
      renderEmptyState({
        title: t("Sin resultados"),
        body: isFilterEmpty
          ? t("No hay oportunidades que coincidan con los filtros activos. Prueba a limpiar o cambiar el estado.")
          : t("Todavía no se han registrado oportunidades. Lanza una búsqueda para empezar.")
      });
      return;
    }
    var grid = document.createElement("div");
    grid.className = "cards-grid";
    payload.items.forEach(function (it) {
      grid.appendChild(buildCard(it));
    });
    region.appendChild(grid);
  }

  function renderPagination(payload) {
    var nav = document.querySelector("[data-pagination]");
    var info = document.querySelector("[data-page-info]");
    var next = document.querySelector('[data-load-more]');
    if (!nav) return;
    var feed=state.feed;
    nav.hidden=!payload && !feed.busy && !feed.error;
    nav.setAttribute('aria-busy',String(feed.busy));
    if(info)info.textContent=feed.busy?t('Cargando oportunidades…'):feed.error?t('No se pudieron cargar las oportunidades'):t('Mostrando {loaded} de {total} resultados',{loaded:payload?payload.items.length:0,total:payload?payload.total:0});
    if(next){next.hidden=!feed.hasMore && !feed.error;next.disabled=feed.busy;next.textContent=t(feed.error?'Reintentar':'Cargar más');}
  }

  function cancelFeedRequest() {
    var feed=state.feed;
    feed.generation+=1;
    if(feed.controller)feed.controller.abort();
    feed.controller=null;feed.busy=false;feed.promise=null;
  }

  function feedNearViewport() {
    var sentinel=document.querySelector('[data-feed-sentinel]');
    var section=document.querySelector('[data-view="oportunidades"]');
    return sentinel && section && !section.hidden && sentinel.getBoundingClientRect().top<window.innerHeight+500;
  }

  function maybeLoadMore() {
    if(!state.feed.reviewWrites && !state.feed.busy && state.feed.hasMore && !state.feed.error && feedNearViewport())loadMoreItems();
  }

  function acceptFeedMetadata(data) {
    state.counts=data.counts || state.counts;
    state.lastRun=data.last_run || state.lastRun;
    state.lastUpdatedAt=data.last_updated_at || state.lastUpdatedAt;
    state.pagination={page:data.page,pages:data.pages,total:data.total};
    state.feed.offset=data.next_offset;
    state.feed.hasMore=!!data.has_more;
  }

  function loadItems() {
    cancelFeedRequest();
    var feed=state.feed,generation=feed.generation;
    feed.queryVersion+=1;
    state.filters.page=1;
    var key=buildFiltersPayload({offset:0}),resetPosition=feed.renderedKey!==key;
    feed.key=key;
    feed.busy=true;feed.initial=true;feed.hasMore=false;feed.error=false;
    feed.controller=new AbortController();
    if(!state.lastItems)renderLoadingState();
    renderPagination(state.lastItems);
    feed.promise=apiFetch(feed.key,{signal:feed.controller.signal}).then(function (data) {
      if(generation!==feed.generation)return;
      acceptFeedMetadata(data);
      feed.initial=false;
      feed.renderedKey=key;
      renderItems(data);
      if(resetPosition)window.scrollTo({top:0,behavior:'instant'});
      updateCountsRow(data);
    }).catch(function (err) {
      if(generation!==feed.generation)return;
      feed.error=true;
      if(!state.lastItems)renderEmptyState({
        title: t("No se pudieron cargar las oportunidades"),
        body: (err && err.error) ? err.error : t("Inténtalo de nuevo en unos segundos.")
      }, "error");
    }).then(function(){
      if(generation!==feed.generation)return;
      feed.busy=false;feed.controller=null;
      renderPagination(state.lastItems);
      window.requestAnimationFrame(maybeLoadMore);
    });
    return feed.promise;
  }

  function loadMoreItems() {
    var feed=state.feed;
    if(feed.reviewWrites)return Promise.resolve();
    if(feed.busy)return feed.promise || Promise.resolve();
    if(!feed.hasMore && !feed.error)return Promise.resolve();
    if(feed.initial || !state.lastItems)return loadItems();
    var generation=feed.generation,offset=feed.offset;
    feed.busy=true;feed.error=false;feed.controller=new AbortController();
    renderPagination(state.lastItems);
    feed.promise=apiFetch(buildFiltersPayload({offset:offset}),{signal:feed.controller.signal}).then(function(data){
      if(generation!==feed.generation)return;
      var grid=document.querySelector('[data-list-region] .cards-grid');
      var known=new Set(state.lastItems.items.map(function(item){return item.id;}));
      var added=data.items.filter(function(item){if(known.has(item.id))return false;known.add(item.id);return true;});
      state.lastItems.items=state.lastItems.items.concat(added);
      Object.keys(data).forEach(function(key){if(key!=='items')state.lastItems[key]=data[key];});
      acceptFeedMetadata(data);
      if(grid)added.forEach(function(item){grid.appendChild(buildCard(item));});
      // A server that makes no progress must not trigger a request loop.
      if(feed.offset<=offset)feed.hasMore=false;
      updateCountsRow(data);
    }).catch(function(){if(generation===feed.generation)feed.error=true;}).then(function(){
      if(generation!==feed.generation)return;
      feed.busy=false;feed.controller=null;renderPagination(state.lastItems);
      window.requestAnimationFrame(maybeLoadMore);
    });
    return feed.promise;
  }

  function setupInfiniteScroll() {
    var sentinel=document.querySelector('[data-feed-sentinel]');
    var more=document.querySelector('[data-load-more]');
    if(more)more.addEventListener('click',function(){if(state.feed.initial)loadItems();else loadMoreItems();});
    if(sentinel && 'IntersectionObserver' in window){
      state.feed.observer=new IntersectionObserver(function(entries){if(entries.some(function(entry){return entry.isIntersecting;}))maybeLoadMore();},{rootMargin:'500px 0px'});
      state.feed.observer.observe(sentinel);
    }else window.addEventListener('scroll',maybeLoadMore,{passive:true});
  }

  function gridAnchor(excludedId) {
    var cards=Array.from(document.querySelectorAll('.item-card'));
    var node=cards.find(function(card){var bounds=card.getBoundingClientRect();return card.getAttribute('data-item-id')!==excludedId && bounds.top>=0 && bounds.top<window.innerHeight;});
    return node?{node:node,top:node.getBoundingClientRect().top}:null;
  }

  function beginGridReview() {
    if(!state.feed.initial)cancelFeedRequest();
    state.feed.reviewWrites+=1;
  }

  function endGridReview() {
    state.feed.reviewWrites=Math.max(0,state.feed.reviewWrites-1);
    renderPagination(state.lastItems);
    window.requestAnimationFrame(maybeLoadMore);
  }

  function updateGridReview(item) {
    if(!item || !state.lastItems)return;
    // If the user changed filters while saving, the initial load must
    // volver a leer el nuevo filtro, no aplicar su resultado sobre la lista vieja.
    if(state.feed.initial){loadItems();return;}
    var index=state.lastItems.items.findIndex(function(previous){return previous.id===item.id;});
    if(index<0)return;
    var old=state.lastItems.items[index],previousStatus=(old.review || {}).status || 'pending';
    var status=(item.review || {}).status || 'pending';
    var anchor=gridAnchor(item.id);
    cancelFeedRequest(); // descartar páginas capturadas antes del cambio de estado
    state.feed.error=false;
    if(previousStatus!==status){state.counts[previousStatus]=Math.max(0,(state.counts[previousStatus] || 0)-1);state.counts[status]=(state.counts[status] || 0)+1;}
    if(old.changed_since_review && !item.changed_since_review)state.counts.changed=Math.max(0,(state.counts.changed || 0)-1);
    var card=document.querySelector('.item-card[data-item-id="'+cssEscape(item.id)+'"]');
    var keep=(state.filters.status==='all' || state.filters.status===status) && (!state.filters.changed || item.changed_since_review);
    if(keep){
      item=Object.assign({},old,item);
      state.lastItems.items[index]=item;
      if(card)card.replaceWith(buildCard(item));
    }else{
      state.lastItems.items.splice(index,1);
      state.lastItems.total=Math.max(0,state.lastItems.total-1);
      state.feed.offset=Math.max(0,state.feed.offset-1);
      if(card)card.remove();
    }
    state.lastItems.counts=state.counts;
    state.feed.hasMore=state.feed.offset<state.lastItems.total;
    if(anchor && anchor.node.isConnected)window.scrollBy(0,anchor.node.getBoundingClientRect().top-anchor.top);
    updateCountsRow(state.lastItems);renderPagination(state.lastItems);
    window.requestAnimationFrame(maybeLoadMore);
    return {removed:!keep,index:index};
  }

  // ----------------- Drawer de detalle -----------------

  var drawerState = { itemId: null, data: null, lastFocus: null, navigationVersion:0 };
  var drawerDrafts = Object.create(null);
  var detailReviewSaves = Object.create(null);

  function syncModalInert() {
    var shell = document.querySelector('.app-shell');
    var drawer = document.getElementById('item-drawer');
    var lightbox = document.querySelector('[data-lightbox]');
    var help = document.querySelector('[data-shortcuts-dialog]');
    var editor=document.getElementById('search-editor-drawer');
    var editorOpen=!!(editor && editor.classList.contains('open'));
    var drawerOpen = !!(drawer && drawer.classList.contains('open'));
    var photoOpen = !!(lightbox && !lightbox.hidden);
    var helpOpen = !!(help && !help.hidden);
    if (shell) shell.inert = drawerOpen || photoOpen || helpOpen || editorOpen;
    if (drawer) drawer.inert = !drawerOpen || photoOpen || helpOpen || editorOpen;
    if (editor) editor.inert=!editorOpen || drawerOpen || photoOpen || helpOpen;
    if (lightbox) lightbox.inert = !photoOpen || helpOpen;
    if (help) help.inert = !helpOpen;
    document.body.classList.toggle('body-locked', drawerOpen || photoOpen || helpOpen || editorOpen);
  }

  function rememberDrawerDraft() {
    if (!drawerState.itemId || !drawerState.data || !drawerState.data.item) return;
    var notes = document.getElementById("review-notes");
    if (!notes) return;
    var original = drawerState.data.item.review || {};
    if (notes.value !== (original.notes || "")) {
      drawerDrafts[drawerState.itemId] = { notes: notes.value };
    } else delete drawerDrafts[drawerState.itemId];
  }

  function restoreDrawerDraft() {
    var draft = drawerDrafts[drawerState.itemId];
    if (!draft) return;
    var notes = document.getElementById("review-notes");
    if (notes) notes.value = draft.notes;
    // Only notes are drafts. Restoring a view must never send
    // another decision to the server or overwrite a confirmed state.
  }

  function captureDetailReviewNavigation(itemId) {
    if(drawerState.itemId!==itemId || state.feed.initial)return null;
    return {version:drawerState.navigationVersion,queryVersion:state.feed.queryVersion};
  }

  function advanceReviewedDetail(itemId,outcome,context) {
    if(!context || !outcome || !outcome.removed)return Promise.resolve();
    function stillCurrent(){return drawerState.itemId===itemId && drawerState.navigationVersion===context.version && state.feed.queryVersion===context.queryVersion;}
    function advance(){
      if(!stillCurrent())return;
      // Unsaved notes belong to the previous listing, never the next one.
      rememberDrawerDraft();
      if(lightboxVisible())closeLightbox();
      var next=state.lastItems && state.lastItems.items[outcome.index];
      if(next){
        var trigger=document.querySelector('.item-card[data-item-id="'+cssEscape(next.id)+'"] .item-card-open');
        openDrawer(next.id,trigger);
      }else closeDrawer();
    }
    if(!stillCurrent())return Promise.resolve();
    if(!state.lastItems.items[outcome.index] && state.feed.hasMore){
      // Also cross a scroll block boundary without returning to page 1.
      return loadMoreItems().then(advance);
    }
    advance();return Promise.resolve();
  }

  function openDrawer(itemId, triggerEl) {
    if (drawerState.itemId && drawerState.itemId !== itemId) rememberDrawerDraft();
    drawerState.navigationVersion+=1;
    drawerState.itemId = itemId;
    drawerState.lastFocus = triggerEl || drawerState.lastFocus || document.activeElement;
    var drawer = document.getElementById("item-drawer");
    var backdrop = document.querySelector("[data-drawer-backdrop]");
    if (!drawer) return;
    drawer.setAttribute("aria-hidden", "false");
    drawer.classList.add("open");
    if (backdrop) {
      backdrop.hidden = false;
      backdrop.classList.add("open");
    }
    document.body.classList.add("body-locked");
    syncModalInert();
    loadDrawerDetail(itemId);
    // Move focus to the first focusable element in the drawer
    focusFirstInDrawer(drawer);
  }

  function closeDrawer() {
    var closingId = drawerState.itemId;
    drawerState.navigationVersion+=1;
    rememberDrawerDraft();
    if (lightboxVisible()) closeLightbox();
    var drawer = document.getElementById("item-drawer");
    var backdrop = document.querySelector("[data-drawer-backdrop]");
    if (!drawer) return;
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    if (backdrop) {
      backdrop.classList.remove("open");
      backdrop.hidden = true;
    }
    document.body.classList.remove("body-locked");
    syncModalInert();
    drawerState.itemId = null;
    drawerState.data = null;
    // Reset lightbox
    var lb = document.querySelector("[data-lightbox]");
    if (lb) lb.hidden = true;
    // Restaurar foco al disparador original
    if (drawerState.lastFocus && drawerState.lastFocus.isConnected && typeof drawerState.lastFocus.focus === "function") {
      try { drawerState.lastFocus.focus({preventScroll:true}); } catch (e) {}
    } else {
      var fallback = document.querySelector('.item-card[data-item-id="' + cssEscape(closingId) + '"] .item-card-open') || document.querySelector('.item-card-open');
      if (fallback) fallback.focus({preventScroll:true});
    }
    drawerState.lastFocus = null;
  }

  /** Return the visible focusable elements inside a node. */
  function focusableIn(root) {
    if (!root) return [];
    var sel = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    var nodes = root.querySelectorAll(sel);
    var out = [];
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      if (n.offsetParent !== null || n === document.activeElement) out.push(n);
    }
    return out;
  }

  function focusFirstInDrawer(drawer) {
    var list = focusableIn(drawer);
    if (list.length > 0) {
      try { list[0].focus(); } catch (e) {}
    }
  }

  function renderDrawerLoading() {
    var body = document.querySelector("[data-drawer-body]");
    if (!body) return;
    body.innerHTML = "";
    var st = document.createElement("div");
    st.className = "state";
    st.setAttribute("role", "status");
    st.setAttribute("aria-live", "polite");
    var sp = document.createElement("div");
    sp.className = "spinner";
    st.appendChild(sp);
    var tt = document.createElement("p");
    tt.className = "state-title mb-0";
    tt.textContent = t("Cargando detalle…");
    st.appendChild(tt);
    body.appendChild(st);
  }

  function renderDrawerError(message) {
    var body = document.querySelector("[data-drawer-body]");
    if (!body) return;
    body.innerHTML = "";
    var st = document.createElement("div");
    st.className = "state state-error";
    st.setAttribute("role", "alert");
    var tt = document.createElement("p");
    tt.className = "state-title mb-0";
    tt.textContent = message || t("No se pudo cargar el detalle.");
    st.appendChild(tt);
    body.appendChild(st);
  }

  function appendFact(parent, label, value, unknownText) {
    var fact = document.createElement("div");
    fact.className = "fact";
    var l = document.createElement("span");
    l.className = "fact-label";
    l.textContent = label;
    fact.appendChild(l);
    var v = document.createElement("span");
    v.className = "fact-value";
    if (value === null || value === undefined || value === "") {
      v.className = "fact-unknown";
      v.textContent = unknownText || t("Sin dato publicado");
    } else {
      v.textContent = String(value);
    }
    fact.appendChild(v);
    parent.appendChild(fact);
  }

  function renderPriceList(parent, prices, currency) {
    if (!prices || typeof prices !== "object") {
      appendFact(parent, t("Importes publicados"), null);
      return;
    }
    var labels = {
      asking: "Precio anunciado",
      reserve: "Reserva",
      starting: "Precio de salida",
      bid: "Puja actual",
      minimum_bid: "Puja mínima",
      auction_value: "Valor de subasta",
      award: "Adjudicación",
      appraisal: "Tasación",
      deposit: "Depósito",
    };
    var keys = Object.keys(labels);
    var hasAny = keys.some(function (k) {
      return prices[k] !== null && prices[k] !== undefined && !isNaN(prices[k]);
    });
    if (!hasAny) {
      appendFact(parent, t("Importes publicados"), null);
      return;
    }
    keys.forEach(function (k) {
      var v = prices[k];
      if (v === null || v === undefined || isNaN(v)) return;
      appendFact(parent, t(labels[k]), formatMoney(v, currency));
    });
  }

  function appendLocationFact(parent,location) {
    var label=humanValue(location,{kind:'location'});
    appendFact(parent,t('Ubicación'),label);
    var link=window.AuctionRadarUI.mapsLink(location,label);
    if(link)parent.lastElementChild.querySelector('.fact-value').replaceChildren(link);
  }

  function renderGallery(parent, photos) {
    parent.innerHTML = "";
    if (!Array.isArray(photos) || photos.length === 0) {
      var empty = document.createElement("div");
      empty.className = "gallery-empty";
      empty.textContent = t("No hay fotos del anuncio original.");
      parent.appendChild(empty);
      return;
    }
    var shown = 0;
    photos.forEach(function (p) {
      var u = safeHttpUrl(p && p.url);
      if (!u) return;
      shown += 1;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "gallery-thumb";
      var ti = document.createElement("img");
      ti.className = "gallery-thumb-img";
      ti.src = u;
      ti.alt = p && p.caption ? p.caption : t("Foto {n}", { n: shown });
      ti.loading = "lazy";
      ti.decoding = "async";
      ti.referrerPolicy = "no-referrer";
      btn.appendChild(ti);
      btn.setAttribute("aria-label", t("Ampliar foto {n}", { n: shown }));
      var photoIndex = shown - 1;
      btn.addEventListener("click", function () {
        openLightbox(photos, photoIndex);
      });
      parent.appendChild(btn);
    });
    if (shown === 0) {
      var empty2 = document.createElement("div");
      empty2.className = "gallery-empty";
      empty2.textContent = t("No hay fotos válidas del anuncio original.");
      parent.appendChild(empty2);
    }
  }

  function renderDocs(parent, docs) {
    parent.innerHTML = "";
    if (!Array.isArray(docs) || docs.length === 0) {
      parent.parentNode.hidden = true;
      return;
    }
    parent.parentNode.hidden = false;
    docs.forEach(function (d) {
      var u = safeHttpUrl(d && d.url);
      if (!u) return;
      var a = document.createElement("a");
      a.className = "doc-link";
      a.href = u;
      a.target = "_blank";
      a.rel = "nofollow noopener noreferrer";
      var icon = document.createElement("span");
      icon.className = "doc-link-icon";
      icon.textContent = (d && d.url && d.url.toLowerCase().indexOf(".pdf") !== -1) ? t("PDF") : t("DOC");
      a.appendChild(icon);
      var meta = document.createElement("span");
      meta.className = "doc-meta";
      var tt = document.createElement("span");
      tt.className = "doc-meta-title";
      tt.textContent = (d && d.title) || (d && d.url) || t("Documento");
      meta.appendChild(tt);
      var s = document.createElement("span");
      s.className = "doc-meta-status";
      var statusText = (d && d.read_status) || t("Enlace");
      s.textContent = t("Estado de lectura: {status}", { status: statusText });
      meta.appendChild(s);
      a.appendChild(meta);
      parent.appendChild(a);
    });
  }

  var lightboxState = {
    lastFocus: null,
    photos: [],
    index: 0,
  };

  function lightboxVisible() {
    var lb = document.querySelector("[data-lightbox]");
    return !!(lb && !lb.hidden);
  }

  function setLightboxImage(index) {
    var img = document.querySelector("[data-lightbox-img]");
    var counter = document.querySelector("[data-lightbox-counter]");
    var caption = document.querySelector("[data-lightbox-caption]");
    var prev = document.querySelector("[data-lightbox-prev]");
    var next = document.querySelector("[data-lightbox-next]");
    if (!img) return;
    if (!lightboxState.photos.length) return;
    var i = ((index % lightboxState.photos.length) + lightboxState.photos.length) % lightboxState.photos.length;
    lightboxState.index = i;
    var p = lightboxState.photos[i];
    img.src = p.url || "";
    img.alt = (p && p.caption) || t("Imagen ampliada");
    if (counter) {
      counter.textContent = t("Foto {current} de {total}", {
        current: i + 1,
        total: lightboxState.photos.length,
      });
    }
    if (caption) {
      caption.textContent = (p && p.caption) || "";
    }
    if (prev) prev.disabled = lightboxState.photos.length <= 1;
    if (next) next.disabled = lightboxState.photos.length <= 1;
  }

  function openLightbox(photos, startIndex) {
    var lb = document.querySelector("[data-lightbox]");
    var img = document.querySelector("[data-lightbox-img]");
    if (!lb || !img) return;
    // Validate: each entry must have a safe URL.
    var valid = [];
    var idx = Math.max(0, startIndex || 0);
    if (Array.isArray(photos)) {
      for (var i = 0; i < photos.length; i++) {
        var u = safeHttpUrl(photos[i] && photos[i].url);
        if (u) valid.push({ url: u, caption: (photos[i] && photos[i].caption) || "" });
      }
    } else if (typeof photos === "string") {
      // Legacy single-arg: keep working.
      var single = safeHttpUrl(photos);
      if (single) valid.push({ url: single, caption: typeof startIndex === "string" ? startIndex : "" });
      idx = 0;
    }
    if (!valid.length) return;
    lightboxState.photos = valid;
    lightboxState.lastFocus = document.activeElement;
    lb.hidden = false;
    lb.classList.add("open");
    syncModalInert();
    setLightboxImage(idx);
    var btn = lb.querySelector("[data-lightbox-close]");
    if (btn) btn.focus();
  }

  function closeLightbox() {
    var lb = document.querySelector("[data-lightbox]");
    var img = document.querySelector("[data-lightbox-img]");
    if (!lb) return;
    lb.classList.remove("open");
    lb.hidden = true;
    syncModalInert();
    if (img) img.src = "";
    lightboxState.photos = [];
    lightboxState.index = 0;
    if (lightboxState.lastFocus && lightboxState.lastFocus.isConnected && typeof lightboxState.lastFocus.focus === "function") {
      try { lightboxState.lastFocus.focus(); } catch (e) {}
    } else {
      var fallback = document.querySelector('#item-drawer .gallery-thumb');
      if (fallback) fallback.focus();
    }
    lightboxState.lastFocus = null;
  }

  function lightboxPrev() {
    if (!lightboxVisible()) return;
    setLightboxImage(lightboxState.index - 1);
  }
  function lightboxNext() {
    if (!lightboxVisible()) return;
    setLightboxImage(lightboxState.index + 1);
  }
  function lightboxFirst() {
    if (!lightboxVisible()) return;
    setLightboxImage(0);
  }
  function lightboxLast() {
    if (!lightboxVisible()) return;
    setLightboxImage(lightboxState.photos.length - 1);
  }

  function renderHistory(parent, history) {
    parent.innerHTML = "";
    if (!Array.isArray(history) || history.length === 0) {
      var empty = document.createElement("p");
      empty.className = "text-small text-faint mb-0";
      empty.textContent = t("Sin eventos de revisión registrados.");
      parent.appendChild(empty);
      return;
    }
    var list = document.createElement("ol");
    list.className = "history-list";
    history.forEach(function (h) {
      var li = document.createElement("li");
      var line = document.createElement("div");
      line.className = "text-small";
      var statusText = statusLabel(h.status) || t("Cambio");
      var when = formatDateTime(h.updated_at || h.observed_at) || t("fecha desconocida");
      line.textContent = statusText + " · " + when;
      li.appendChild(line);
      if (h.username) {
        var who = document.createElement("div");
        who.className = "text-small text-faint";
        who.textContent = t("por {who}", { who: h.username });
        li.appendChild(who);
      }
      if (h.notes) {
        var notes = document.createElement("div");
        notes.className = "text-small";
        notes.textContent = h.notes;
        li.appendChild(notes);
      }
      list.appendChild(li);
    });
    parent.appendChild(list);
  }

  function buildShareSection(item) {
    var sec = document.createElement("div");
    sec.className = "drawer-section";
    var h = document.createElement("h3");
    h.textContent = t("Compartir");
    sec.appendChild(h);

    var block = document.createElement("div");
    block.className = "share-block";

    var warn = document.createElement("div");
    warn.className = "share-warning";
    warn.textContent = t("El enlace con token funciona en tu LAN/VPN y NO incluye tus notas privadas.");
    block.appendChild(warn);

    var modes = document.createElement("div");
    modes.className = "share-modes";

    // Modo 1: URL original
    var m1 = document.createElement("div");
    m1.className = "share-mode";
    var m1h = document.createElement("h4"); m1h.textContent = t("Enlace al portal original"); m1.appendChild(m1h);
    var m1p = document.createElement("p"); m1p.textContent = t("URL del anuncio tal como la publica la fuente."); m1.appendChild(m1p);
    var m1a = document.createElement("div"); m1a.className = "share-mode-actions";
    var copy1 = document.createElement("button"); copy1.className = "btn"; copy1.type = "button"; copy1.textContent = t("Copiar");
    copy1.addEventListener("click", function () { copyText(item.url || "", copy1); });
    m1a.appendChild(copy1);
    if (item.url && /^https?:\/\//i.test(item.url)) {
      var wapp = document.createElement("a"); wapp.className = "btn btn-ghost"; wapp.href = "https://api.whatsapp.com/send?text=" + encodeURIComponent((item.title || t("Oportunidad")) + " " + item.url); wapp.target = "_blank"; wapp.rel = "nofollow noopener noreferrer"; wapp.textContent = t("WhatsApp");
      m1a.appendChild(wapp);
      var mail = document.createElement("a"); mail.className = "btn btn-ghost"; mail.href = "mailto:?subject=" + encodeURIComponent(item.title || t("Oportunidad")) + "&body=" + encodeURIComponent(item.url); mail.textContent = t("Correo");
      m1a.appendChild(mail);
    }
    m1.appendChild(m1a);
    modes.appendChild(m1);

    // Modo 2: enlace interno autenticado
    var m2 = document.createElement("div");
    m2.className = "share-mode";
    var m2h = document.createElement("h4"); m2h.textContent = t("Enlace interno autenticado"); m2.appendChild(m2h);
    var m2p = document.createElement("p"); m2p.textContent = t("Quien lo abra necesitará cuenta. Útil para coordinarte con otro revisor."); m2.appendChild(m2p);
    var internalUrl = window.location.origin + "/?item=" + encodeURIComponent(item.id);
    var inputInternal = document.createElement("input");
    inputInternal.className = "input text-mono";
    inputInternal.value = internalUrl;
    inputInternal.readOnly = true;
    m2.appendChild(inputInternal);
    var m2a = document.createElement("div"); m2a.className = "share-mode-actions";
    var copy2 = document.createElement("button"); copy2.className = "btn"; copy2.type = "button"; copy2.textContent = t("Copiar");
    copy2.addEventListener("click", function () { copyText(internalUrl, copy2); });
    m2a.appendChild(copy2);
    m2.appendChild(m2a);
    modes.appendChild(m2);

    // Mode 3: public token
    var m3 = document.createElement("div");
    m3.className = "share-mode";
    var m3h = document.createElement("h4"); m3h.textContent = t("Enlace sin login (solo lectura)"); m3.appendChild(m3h);
    var m3p = document.createElement("p"); m3p.textContent = t("No requiere cuenta. Muestra fotos, datos básicos y documentos del anuncio."); m3.appendChild(m3p);
    var m3a = document.createElement("div"); m3a.className = "share-mode-actions";
    var createBtn = document.createElement("button"); createBtn.className = "btn btn-primary"; createBtn.type = "button"; createBtn.textContent = t("Crear enlace");
    createBtn.addEventListener("click", function () { createShareToken(item.id, m3, item); });
    m3a.appendChild(createBtn);
    m3.appendChild(m3a);
    modes.appendChild(m3);

    block.appendChild(modes);

    // Tokens existentes
    if (Array.isArray(item.shares) && item.shares.length > 0) {
      var tokensTitle = document.createElement("p");
      tokensTitle.className = "text-small text-muted mb-0 share-tokens-title";
      tokensTitle.textContent = t("Enlaces públicos creados");
      block.appendChild(tokensTitle);
      var tokensList = document.createElement("div");
      tokensList.className = "tokens-list";
      item.shares.forEach(function (s) {
        var row = document.createElement("div");
        row.className = "token-row";
        var code = document.createElement("code");
        // For security, do not reconstruct a URL from a random hash.
        // Show it only if the backend returns it explicitly.
        code.textContent = s.url ? s.url : ("#" + s.id + " — no visible");
        row.appendChild(code);
        var date = document.createElement("span");
        date.className = "token-row-date";
        date.textContent = formatDate(s.created_at) || "—";
        row.appendChild(date);
        var status = document.createElement("span");
        status.className = "chip";
        status.textContent = s.revoked ? t("Revocado") : t("Activo");
        if (s.revoked) status.classList.add("chip-status-ko");
        row.appendChild(status);
        if (s.url) {
          var copyT = document.createElement("button"); copyT.className = "btn btn-ghost"; copyT.type = "button"; copyT.textContent = t("Copiar");
          copyT.addEventListener("click", function () { copyText(s.url, copyT); });
          row.appendChild(copyT);
        } else if (!s.revoked) {
          var note = document.createElement("span");
          note.className = "token-row-note";
          note.textContent = t("URL sólo visible al crear");
          row.appendChild(note);
        }
        if (!s.revoked) {
          var rev = document.createElement("button"); rev.className = "btn btn-danger"; rev.type = "button"; rev.textContent = t("Revocar");
          rev.addEventListener("click", function () { revokeShareToken(s.id, row); });
          row.appendChild(rev);
        }
        tokensList.appendChild(row);
      });
      block.appendChild(tokensList);
    }

    sec.appendChild(block);
    return sec;
  }

  function copyText(value, btn) {
    if (!value) {
      showToast(t("No hay valor para copiar."), "info");
      return;
    }
    var done = function () { showToast(t("Copiado al portapapeles."), "ok"); };
    var fail = function () { showToast(t("No se pudo copiar."), "error"); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done, function () {
        legacyCopy(value) ? done() : fail();
      });
    } else if (legacyCopy(value)) {
      done();
    } else {
      fail();
    }
    if (btn && btn.tagName === "BUTTON") {
      var prev = btn.textContent;
      btn.textContent = t("¡Copiado!");
      setTimeout(function () { btn.textContent = prev; }, 1500);
    }
  }

  function legacyCopy(value) {
    try {
      var ta = document.createElement("textarea");
      ta.value = value;
      ta.setAttribute("readonly", "");
      ta.className = "clipboard-helper";
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch (e) { return false; }
  }

  function createShareToken(itemId, container, item) {
    apiFetch("/api/items/" + encodeURIComponent(itemId) + "/share", { method: "POST", body: {} })
      .then(function (resp) {
        if (!resp || !resp.url) {
          showToast(t("No se pudo crear el enlace."), "error");
          return;
        }
        var row = document.createElement("div");
        row.className = "token-row";
        var code = document.createElement("code");
        code.textContent = resp.url;
        row.appendChild(code);
        var date = document.createElement("span");
        date.className = "token-row-date";
        date.textContent = t("Recién creado");
        row.appendChild(date);
        var status = document.createElement("span");
        status.className = "chip chip-status-ok";
        status.textContent = t("Activo");
        row.appendChild(status);
        var copyT = document.createElement("button"); copyT.className = "btn btn-ghost"; copyT.type = "button"; copyT.textContent = t("Copiar");
        copyT.addEventListener("click", function () { copyText(resp.url, copyT); });
        row.appendChild(copyT);
        var rev = document.createElement("button"); rev.className = "btn btn-danger"; rev.type = "button"; rev.textContent = t("Revocar");
        rev.addEventListener("click", function () { revokeShareToken(resp.id, row); });
        row.appendChild(rev);

        var existing = container.parentNode.querySelector(".tokens-list");
        if (!existing) {
          var title = document.createElement("p");
          title.className = "text-small text-muted mb-0 share-tokens-title";
          title.textContent = t("Enlaces públicos creados");
          container.parentNode.appendChild(title);
          var list = document.createElement("div");
          list.className = "tokens-list";
          list.appendChild(row);
          container.parentNode.appendChild(list);
        } else {
          existing.appendChild(row);
        }
        // Also refresh the current item
        if (item) {
          if (!Array.isArray(item.shares)) item.shares = [];
          item.shares.unshift({ id: resp.id, url: resp.url, revoked: false, created_at: new Date().toISOString() });
        }
        showToast(t("Enlace de solo lectura creado para LAN/VPN."), "ok");
      }).catch(function (err) {
        showToast((err && err.error) || t("No se pudo crear el enlace."), "error");
      });
  }

  function revokeShareToken(shareId, row) {
    if (!shareId) return;
    apiFetch("/api/shares/" + encodeURIComponent(shareId) + "/revoke", { method: "POST", body: {} })
      .then(function () {
        showToast(t("Enlace revocado."), "ok");
        if (row && row.parentNode) row.parentNode.removeChild(row);
      }).catch(function (err) {
        showToast((err && err.error) || t("No se pudo revocar."), "error");
      });
  }

  function buildReviewSection(item) {
    var sec = document.createElement("div");
    sec.className = "drawer-section";
    var h = document.createElement("h3");
    h.textContent = t("Decisión de revisión");
    sec.appendChild(h);

    var block = document.createElement("div");
    block.className = "review-block";

    var row = document.createElement("div");
    row.className = "review-status-row";
    row.setAttribute('role','group');row.setAttribute('aria-label',t('Estado de revisión'));
    var opts = [
      { v: "pending", label: "Pendiente" },
      { v: "ok", label: "Marcar como OK" },
      { v: "ko", label: "Marcar como KO" },
    ];
    var btns = [];
    var persistedStatus = (item.review && item.review.status) || "pending";
    var reviewSave=detailReviewSaves[item.id];
    if(!reviewSave)reviewSave=detailReviewSaves[item.id]={inFlight:null,persistedStatus:persistedStatus,desiredStatus:persistedStatus};
    if(!reviewSave.inFlight){reviewSave.persistedStatus=persistedStatus;reviewSave.desiredStatus=persistedStatus;}
    var reviewState = { status: reviewSave.desiredStatus };
    var setActiveStatus = function (newStatus, btnEl) {
      reviewState.status = newStatus;
      btns.forEach(function (x) {
        x.classList.remove("btn-primary");
        x.setAttribute("aria-pressed", "false");
      });
      if (btnEl) {
        btnEl.classList.add("btn-primary");
        btnEl.setAttribute("aria-pressed", "true");
      }
    };
    block.classList.add("review-block-form");
    block.setAttribute("data-review-form", "");
    block.setAttribute('data-review-item-id',item.id);
    function visibleForm(){
      if(drawerState.itemId!==item.id)return null;
      var form=document.querySelector('[data-review-form]');
      return form&&form.getAttribute('data-review-item-id')===item.id?form:null;
    }
    function syncReviewControls(){
      document.querySelectorAll('.item-card').forEach(function(card){
        if(card.getAttribute('data-item-id')!==item.id)return;
        card.querySelectorAll('[data-review-ok],[data-review-ko]').forEach(function(button){button.disabled=!!reviewSave.inFlight;});
      });
      var form=visibleForm();if(!form)return;
      var chosen=reviewSave.inFlight?reviewSave.desiredStatus:reviewSave.persistedStatus;
      form.querySelectorAll('[data-review-status]').forEach(function(button){
        var active=button.getAttribute('data-review-status')===chosen;
        button.setAttribute('aria-pressed',String(active));button.classList.toggle('btn-primary',active);button.disabled=!!reviewSave.inFlight;
      });
      var save=form.querySelector('[data-save-review]');
      if(save){save.disabled=!!reviewSave.inFlight;save.textContent=t(reviewSave.inFlight?'Guardando…':'Guardar notas');}
    }
    function saveDetailReview(target,explicitNotes){
      if(target===reviewSave.persistedStatus&&!reviewSave.inFlight&&explicitNotes===undefined){syncReviewControls();return;}
      reviewSave.desiredStatus=target;
      var previous=reviewSave.inFlight||Promise.resolve();
      var notesBefore,originalNotes,acknowledged=false;
      var navigation=captureDetailReviewNavigation(item.id),gridOutcome;
      var operation=previous.then(function(){
        var form=visibleForm();var notes=form&&form.querySelector('#review-notes');
        notesBefore=notes?notes.value:undefined;
        originalNotes=drawerState.itemId===item.id&&drawerState.data&&drawerState.data.item?(drawerState.data.item.review||{}).notes||'':(item.review||{}).notes||'';
        var body=explicitNotes===undefined?{status:target,preserve_notes:true}:{status:target,notes:explicitNotes};
        beginGridReview();
        return apiFetch('/api/items/'+encodeURIComponent(item.id)+'/review',{method:'POST',body:body});
      }).then(function(resp){
        if(!resp||!resp.item||resp.item.id!==item.id||!resp.item.review)throw {error:t('No se pudo guardar.')};
        acknowledged=true;reviewSave.persistedStatus=resp.item.review.status;
        var form=visibleForm();
        if(form&&drawerState.data){
          var notes=form.querySelector('#review-notes');var serverNotes=resp.item.review.notes||'';
          // If there were no local edits, reflect the latest notes
          // preserved by preserve_notes. Never turn an old copy into a draft.
          if(notes&&explicitNotes===undefined&&notesBefore===originalNotes&&notes.value===notesBefore)notes.value=serverNotes;
          drawerState.data.item=resp.item;
          if(Array.isArray(resp.history))drawerState.data.history=resp.history;
          if(notes&&notes.value!==serverNotes)drawerDrafts[item.id]={notes:notes.value};else delete drawerDrafts[item.id];
          var badge=document.querySelector('[data-drawer-status-chip]');
          if(badge){badge.textContent=statusLabel(reviewSave.persistedStatus);badge.className='chip chip-status-'+reviewSave.persistedStatus;}
          var author=form.querySelector('[data-review-author]');
          if(author){var review=resp.item.review;author.textContent=t('Última edición {when} {who}',{when:formatDateTime(review.updated_at)||'',who:review.username?t('por {who}',{who:review.username}):t('Sin autor registrado')});}
          var history=document.querySelector('[data-review-history]');
          if(history&&Array.isArray(resp.history))renderHistory(history,resp.history);
        }
        gridOutcome=updateGridReview(resp.item);showToast(t('Decisión guardada.'),'ok');
      }).catch(function(error){
        if(reviewSave.inFlight===operation&&!acknowledged)reviewSave.desiredStatus=reviewSave.persistedStatus;
        showToast((error&&error.error)||t('No se pudo guardar.'),'error');
      }).then(function(){
        var latest=reviewSave.inFlight===operation;
        if(latest){reviewSave.inFlight=null;reviewSave.desiredStatus=reviewSave.persistedStatus;}
        syncReviewControls();
        endGridReview();
        if(latest && acknowledged)return advanceReviewedDetail(item.id,gridOutcome,navigation);
      });
      reviewSave.inFlight=operation;syncReviewControls();
    }
    opts.forEach(function (o) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-review-" + o.v;
      b.setAttribute("data-review-status", o.v);
      b.textContent = t(o.label);
      b.setAttribute("aria-pressed", reviewState.status === o.v ? "true" : "false");
      if (reviewState.status === o.v) b.classList.add("btn-primary");
      b.disabled=!!reviewSave.inFlight;
      b.addEventListener("click", function () {
        setActiveStatus(o.v,b);saveDetailReview(o.v);
      });
      btns.push(b);
      row.appendChild(b);
    });
    block.appendChild(row);
    var hint=document.createElement('p');hint.className='text-small text-muted';hint.textContent=t('El estado se guarda al instante. Guarda las notas con el botón.');block.appendChild(hint);

    var ta = document.createElement("textarea");
    var notesLabel = document.createElement("label");
    notesLabel.className = "field-label";
    notesLabel.htmlFor = "review-notes";
    notesLabel.textContent = t(privateWorkspace ? "Mis notas privadas" : "Notas del equipo");
    block.appendChild(notesLabel);
    ta.id = "review-notes";
    ta.className = "textarea";
    ta.maxLength = 4000;
    ta.placeholder = t("Visibles para los usuarios; no se incluyen en el enlace de solo lectura");
    ta.value = (item.review && item.review.notes) || "";
    block.appendChild(ta);

    var saveRow = document.createElement("div");
    saveRow.className = "save-row";
    var saveBtn = document.createElement("button");
    saveBtn.className = "btn btn-primary";
    saveBtn.type = "button";
    saveBtn.setAttribute("data-save-review", "");
    saveBtn.textContent = t(reviewSave.inFlight?'Guardando…':'Guardar notas');
    saveBtn.disabled=!!reviewSave.inFlight;
    var fb = document.createElement("span");
    fb.className = "save-feedback";
    saveBtn.addEventListener("click", function () {
      saveDetailReview(reviewSave.desiredStatus,ta.value||'');
    });
    saveRow.appendChild(saveBtn);
    saveRow.appendChild(fb);
    block.appendChild(saveRow);

    var author = document.createElement("div");
    author.className = "review-author";
    author.setAttribute('data-review-author','');
    if (item.review && (item.review.username || item.review.updated_at)) {
      var who = item.review.username ? t("por {who}", { who: item.review.username }) : t("Sin autor registrado");
      var when = item.review.updated_at ? (" · " + (formatDateTime(item.review.updated_at) || "")) : "";
      author.textContent = t("Última edición {when} {who}", { when: when, who: who });
    } else {
      author.textContent = t("Sin revisión previa.");
    }
    block.appendChild(author);

    sec.appendChild(block);
    return sec;
  }

  function buildDetailBody(item) {
    var body = document.createElement("div");

    // Cabecera con badges
    var head = document.createElement("div");
    head.className = "row-flex";
    function chip(tt, mod) {
      var c = document.createElement("span");
      c.className = "chip" + (mod ? " " + mod : "");
      c.textContent = tt;
      head.appendChild(c);
    }
    chip(assetLabel(item), "chip-asset-" + (item.asset_type || "other"));
    var sName = (item.review && item.review.status) || "pending";
    chip(statusLabel(sName), "chip-status-" + sName);
    head.lastElementChild.setAttribute('data-drawer-status-chip','');
    if (item.changed_since_review) chip(t("Cambios desde revisión"), "chip-status-changed");
    if (item.source_id) chip(t("Fuente: ") + item.source_id);
    body.appendChild(head);

    // Resumen
    var summary = document.createElement("div");
    summary.className = "card mt-md";
    var p = document.createElement("p");
    p.className = "mb-0";
    var locSummary = humanValue(itemField(item, "location"), { kind: "location" });
    p.textContent = item.title || item.id || '—';
    if(locSummary){p.appendChild(document.createTextNode(' — '));p.appendChild(window.AuctionRadarUI.mapsLink(itemField(item,'location'),locSummary)||document.createTextNode(locSummary));}
    summary.appendChild(p);
    body.appendChild(summary);

    // Gallery
    var galSec = document.createElement("div");
    galSec.className = "drawer-section";
    var galTitle = document.createElement("h3"); galTitle.textContent = t("Galería"); galSec.appendChild(galTitle);
    var gal = document.createElement("div"); gal.className = "gallery";
    galSec.appendChild(gal);
    renderGallery(gal, item.photos || []);
    window.AuctionRadarUI.renderPhotoNotice(gal,item.photo_notice);
    body.appendChild(galSec);

    // Basic information
    var factsSec = document.createElement("div");
    factsSec.className = "drawer-section";
    var factsTitle = document.createElement("h3"); factsTitle.textContent = t("Datos publicados"); factsSec.appendChild(factsTitle);
    var facts = document.createElement("div"); facts.className = "facts-grid";
    factsSec.appendChild(facts);

    if (item.asset_type === "vehicles") {
      appendLocationFact(facts,itemField(item,'location'));
      appendFact(facts, t("Marca"), itemField(item, "make"));
      appendFact(facts, t("Modelo"), itemField(item, "model"));
      appendFact(facts, t("Año"), formatNumber(itemField(item, "year")));
      appendFact(facts, t("Kilometraje"), formatNumber(itemField(item, "mileage_km"), "km"));
      appendFact(facts, t("Combustible"), itemField(item, "fuel"));
      appendFact(facts, t("Tipo de cambio"), itemField(item, "transmission"));
      appendFact(facts, t("Documentación"), itemField(item, "documentation"));
      appendFact(facts, t("Estado mecánico"), itemField(item, "mechanical_condition"));
      appendFact(facts, t("ITV"), itemField(item, "itv"));
      appendFact(facts, t("Cargas"), itemField(item, "charges"));
      appendFact(facts, t("Cuota IVA"), itemField(item, "vat_status"));
      appendFact(facts, t("Comisión"), itemField(item, "fees"));
    } else if (['housing','commercial','land'].indexOf(item.asset_type)>=0) {
      appendLocationFact(facts,itemField(item,'location'));
      appendFact(facts, t("Superficie construida"), formatNumber(itemField(item, "built_area_m2"), "m²"));
      appendFact(facts, t("Superficie útil"), formatNumber(itemField(item, "usable_area_m2"), "m²"));
      appendFact(facts, t("Habitaciones"), formatNumber(itemField(item, "bedrooms")));
      appendFact(facts, t("Baños"), formatNumber(itemField(item, "bathrooms")));
      appendFact(facts, t("Planta"), itemField(item, "floor"));
      appendFact(facts, t("Ascensor"), itemField(item, "elevator"));
      appendFact(facts, t("Año construcción"), formatNumber(itemField(item, "construction_year")));
      appendFact(facts, t("Estado físico"), itemField(item, "physical_condition"));
      appendFact(facts, t("Reforma necesaria"), itemField(item, "renovation_scope"));
      appendFact(facts, t("Posesión"), humanValue(itemField(item, "possession"), { kind: "possession" }));
      appendFact(facts, t("Disponibilidad"), humanValue(itemField(item, "availability"), { kind: "availability" }));
    } else {
      appendFact(facts,t('Categoría'),assetLabel(item));
      appendLocationFact(facts,itemField(item,'location'));
      appendFact(facts,t('Marca'),itemField(item,'make'));
      appendFact(facts,t('Modelo'),itemField(item,'model'));
      appendFact(facts,t('Año'),formatNumber(itemField(item,'year')));
      if (item.asset_type==='boats') {
        appendFact(facts,t('Eslora'),formatNumber(itemField(item,'length_m'),'m'));
        appendFact(facts,t('Manga'),formatNumber(itemField(item,'beam_m'),'m'));
        appendFact(facts,t('Horas de motor'),formatNumber(itemField(item,'engine_hours'),'h'));
      }
      appendFact(facts,t('Estado físico'),itemField(item,'physical_condition'));
      appendFact(facts,t('Documentación'),itemField(item,'documentation'));
      appendFact(facts,t('Disponibilidad'),humanValue(itemField(item,'availability'),{kind:'availability'}));
    }

    // Detection dates
    appendFact(facts, t("Primera detección"), formatDate(item.first_seen));
    appendFact(facts, t("Última detección"), formatDate(item.last_seen || item.observed_at));

    // Importes diferenciados
    var pricesTitle = document.createElement("h3");
    pricesTitle.className = "section-spacer-top";
    pricesTitle.textContent = t("Importes publicados");
    factsSec.appendChild(pricesTitle);
    var pricesFacts = document.createElement("div");
    pricesFacts.className = "facts-grid";
    factsSec.appendChild(pricesFacts);
    renderPriceList(pricesFacts, item.prices || itemField(item, "prices") || {}, item.currency || itemField(item, "currency") || "EUR");
    appendFact(pricesFacts, t("Importe principal"), item.exact_price == null ? null : formatMoney(item.exact_price, item.currency || itemField(item, "currency") || "EUR"));
    appendFact(pricesFacts, t("Tipo de importe"), (item.price_type && priceTypeLabel(item.price_type)));
    appendFact(pricesFacts, t("Estimación coste total"), itemField(item, "all_in_cost_estimate") == null ? null : formatMoney(itemField(item, "all_in_cost_estimate"), item.currency || itemField(item, "currency") || "EUR"));

    // Exclusion reason
    if (item.selection && Array.isArray(item.selection.reasons) && item.selection.reasons.length > 0) {
      var reasonsTitle = document.createElement("h3");
      reasonsTitle.className = "reason-title-spacing";
      reasonsTitle.textContent = t("Razones registradas");
      factsSec.appendChild(reasonsTitle);
      var reasonsList = document.createElement("ul");
      reasonsList.className = "reasons-list";
      item.selection.reasons.forEach(function (r) {
        var li = document.createElement("li");
        li.className = "text-small";
        li.textContent = r;
        reasonsList.appendChild(li);
      });
      factsSec.appendChild(reasonsList);
    }

    body.appendChild(factsSec);

    // Documentos
    var docsSec = document.createElement("div");
    docsSec.className = "drawer-section";
    var docsTitle = document.createElement("h3"); docsTitle.textContent = t("Documentos"); docsSec.appendChild(docsTitle);
    var docs = document.createElement("div"); docs.className = "docs-list";
    docsSec.appendChild(docs);
    renderDocs(docs, item.documents || []);
    body.appendChild(docsSec);

    // Original source (external link, when available)
    if (item.url && safeHttpUrl(item.url)) {
      var srcSec = document.createElement("div");
      srcSec.className = "drawer-section";
      var srcTitle = document.createElement("h3"); srcTitle.textContent = t("Fuente original"); srcSec.appendChild(srcTitle);
      var link = document.createElement("a");
      link.href = safeHttpUrl(item.url);
      link.target = "_blank";
      link.rel = "nofollow noopener noreferrer";
      link.textContent = t("Abrir anuncio en el portal de origen");
      srcSec.appendChild(link);
      body.appendChild(srcSec);
    }

    // Historial
    var histSec = document.createElement("div");
    histSec.className = "drawer-section";
    var histTitle = document.createElement("h3"); histTitle.textContent = t("Historial de revisión"); histSec.appendChild(histTitle);
    var hist = document.createElement("div");
    hist.setAttribute('data-review-history','');
    histSec.appendChild(hist);
    renderHistory(hist, drawerState.data && drawerState.data.history ? drawerState.data.history : []);
    body.appendChild(histSec);

    // AI evaluation (only when the backend returns AI data for the item)
    if (item.ai && typeof item.ai === "object" && item.ai.status!=='not_requested') {
      var aiSec = document.createElement("div");
      aiSec.className = "drawer-section drawer-section-ai";
      var aiTitle = document.createElement("h3"); aiTitle.textContent = t("Evaluación de IA"); aiSec.appendChild(aiTitle);
      var aiBlock = document.createElement("div");
      aiBlock.className = "ai-block";
      var verdictRaw = String(item.ai.verdict || "").toLowerCase();
      var verdictLabel = aiLabel(item.ai);
      var verdictEl = document.createElement("div");
      verdictEl.className = "ai-verdict ai-verdict-" + verdictRaw;
      verdictEl.textContent = t("Veredicto: {verdict}", { verdict: verdictLabel });
      aiBlock.appendChild(verdictEl);
      if (typeof item.ai.score === "number") {
        var scoreEl = document.createElement("div");
        scoreEl.className = "ai-score";
        scoreEl.textContent = t("Puntuación: {score}", { score: Math.round(item.ai.score)+'/100' });
        aiBlock.appendChild(scoreEl);
      }
      if (item.ai.reason) {
        var reasonEl = document.createElement("p");
        reasonEl.className = "ai-reason";
        var problems={invalid_input:'La fuente no proporciona una ficha individual verificable.',invalid_evidence:'La IA no ha citado datos publicados válidos para justificar su decisión.',transport_error:'El servicio de IA no está disponible temporalmente.',timeout:'La evaluación de IA ha superado el tiempo disponible.',provider_refusal:'La IA no ha podido valorar esta ficha.',unconfigured:'IA pendiente de configurar',disabled:'IA desactivada'};
        var explanation=item.ai.error_code&&problems[item.ai.error_code]?t(problems[item.ai.error_code]):item.ai.reason;
        reasonEl.textContent = t('Motivo: {reason}',{reason:explanation});
        aiBlock.appendChild(reasonEl);
      }
      var missing = Array.isArray(item.ai.missing_fields) ? item.ai.missing_fields : [];
      if (missing.length) {
        var missEl = document.createElement("p");
        missEl.className = "ai-missing";
        missEl.textContent = t("Campos que faltan: {fields}", { fields: missing.join(", ") });
        aiBlock.appendChild(missEl);
      } else if (item.ai.status==='evaluated') {
        var missEl2 = document.createElement("p");
        missEl2.className = "ai-missing text-faint";
        missEl2.textContent = t("El modelo no ha señalado campos pendientes.");
        aiBlock.appendChild(missEl2);
      }
      var statusAi = item.ai.status || "";
      if (statusAi) {
        var stEl = document.createElement("p");
        stEl.className = "ai-status text-faint";
        stEl.textContent = [item.ai.provider,item.ai.model,item.ai.evaluated_at ? formatDateTime(item.ai.evaluated_at) : ''].filter(Boolean).join(' · ');
        aiBlock.appendChild(stEl);
      }
      if (Array.isArray(item.ai.evidence) && item.ai.evidence.length) {
        var evidence=document.createElement('details');
        var evidenceTitle=document.createElement('summary');evidenceTitle.textContent=t('Datos que respaldan la evaluación');evidence.appendChild(evidenceTitle);
        item.ai.evidence.forEach(function(entry){var line=document.createElement('p');line.className='text-small';line.textContent=(entry.field || '')+': '+(humanValue(entry.value) || t('Sin dato publicado'));evidence.appendChild(line);});
        aiBlock.appendChild(evidence);
      }
      var aiNotice=document.createElement('p');aiNotice.className='text-small text-faint';
      aiNotice.textContent=t('La IA analiza datos publicados; no verifica el bien ni interpreta las fotos.');
      aiBlock.appendChild(aiNotice);
      aiSec.appendChild(aiBlock);
      body.appendChild(aiSec);
    }

    // Review
    body.appendChild(buildReviewSection(item));

    // Compartir
    body.appendChild(buildShareSection(item));

    return body;
  }

  function loadDrawerDetail(itemId) {
    var navigationVersion=drawerState.navigationVersion;
    renderDrawerLoading();
    var title = document.querySelector("[data-drawer-title]");
    if (title) title.textContent = t("Cargando…");
    apiFetch("/api/items/" + encodeURIComponent(itemId) + (state.filters.search_id ? '?search_id='+encodeURIComponent(state.filters.search_id) : ''))
      .then(function (resp) {
        if (drawerState.itemId !== itemId || drawerState.navigationVersion!==navigationVersion) return;
        if (!resp || !resp.item) {
          renderDrawerError(t("No se encontró la oportunidad."));
          return;
        }
        drawerState.data = resp;
        var item = resp.item;
        if (title) title.textContent = item.title || item.id || t("Detalle");
        var body = document.querySelector("[data-drawer-body]");
        if (body) {
          body.innerHTML = "";
          body.appendChild(buildDetailBody(item));
          restoreDrawerDraft();
        }
      }).catch(function (err) {
        if(drawerState.itemId!==itemId || drawerState.navigationVersion!==navigationVersion)return;
        renderDrawerError((err && err.error) || t("No se pudo cargar el detalle."));
      });
  }

  // ----------------- Settings (admin) -----------------

  function loadSettings() {
    var loading = document.querySelector("[data-settings-state='loading']");
    var error = document.querySelector("[data-settings-state='error']");
    var form = document.querySelector("[data-settings-form]");
    if (loading) loading.hidden = false;
    if (error) error.hidden = true;
    if (form) form.hidden = true;

    apiFetch("/api/settings").then(function (resp) {
      state.settings = (resp && resp.settings) || {};
      state.settingsSources = (resp && resp.sources) || [];
      state.settingsRegions = (resp && resp.regions_catalog) || [];
      renderSettingsForm();
      if (loading) loading.hidden = true;
      if (form) form.hidden = false;
    }).catch(function (err) {
      if (loading) loading.hidden = true;
      if (error) {
        error.hidden = false;
        var msg = document.querySelector("[data-settings-error-msg]");
        if (msg) msg.textContent = (err && err.error) || t("Error al cargar la configuración.");
      }
    });
  }

  function renderSettingsForm() {
    var s = state.settings || {};
    // Vivienda
    setNumericField("housing_budget", s.housing_budget);
    setNumericField("vehicle_budget", s.vehicle_budget);
    setNumericField("vehicle_max_mileage", s.vehicle_max_mileage);
    setNumericField("timeout", s.timeout);
    setNumericField("max_pages", s.max_pages);
    setNumericField("max_details", s.max_details);
    setNumericField("max_documents", s.max_documents);
    setNumericField("workers", s.workers);

    // Preferred regions
    var prefWrap = document.querySelector("[data-regions-preferred]");
    if (prefWrap) {
      prefWrap.innerHTML = "";
      var prefList = Array.isArray(s.preferred_regions) ? s.preferred_regions : [];
      var prefNames = prefList.map(function (r) { return (r && r.name) || r; });
      var regionNames = (state.settingsRegions || []).map(function (r) { return r.name || r.code; });
      prefNames.forEach(function (name) { if (regionNames.indexOf(name) < 0) regionNames.push(name); });
      regionNames.forEach(function (name) {
        var row = document.createElement("label");
        row.className = "checkbox-row";
        var input = document.createElement("input");
        input.type = "checkbox";
        input.value = name;
        input.checked = prefNames.indexOf(name) >= 0;
        var lbl = document.createElement("span");
        lbl.textContent = name;
        row.appendChild(input);
        row.appendChild(lbl);
        prefWrap.appendChild(row);
      });
    }

    // Strict regions
    var strictSel = document.querySelector('[data-field="regions"]');
    if (strictSel) {
      strictSel.innerHTML = "";
      var opts = (state.settingsRegions || []).map(function (r) { return r.name || r.code; });
      (s.regions || []).forEach(function (name) { if (opts.indexOf(name) < 0) opts.push(name); });
      opts.forEach(function (r) {
        var o = document.createElement("option");
        o.value = r;
        o.textContent = r;
        strictSel.appendChild(o);
      });
      var cur = Array.isArray(s.regions) ? s.regions : [];
      Array.from(strictSel.options).forEach(function (o) {
        o.selected = cur.indexOf(o.value) !== -1;
      });
    }

    // Sources
    var sourcesWrap = document.querySelector("[data-sources-list]");
    if (sourcesWrap) {
      sourcesWrap.innerHTML = "";
      // s.sources may be a {sid:{enabled}} map (new contract) or an array (legacy).
      var sourcesMap = {};
      if (s.sources && typeof s.sources === "object" && !Array.isArray(s.sources)) {
        Object.keys(s.sources).forEach(function (sid) {
          var v = s.sources[sid];
          sourcesMap[sid] = (v && typeof v === "object") ? !!v.enabled : !!v;
        });
      } else if (Array.isArray(s.sources)) {
        s.sources.forEach(function (id) { sourcesMap[id] = true; });
      }
      if (state.settingsSources.length === 0) {
        var np = document.createElement("p");
        np.className = "text-small text-faint mb-0";
        np.textContent = t("Catálogo de fuentes no disponible.");
        sourcesWrap.appendChild(np);
      } else {
        state.settingsSources.forEach(function (src) {
          var row = document.createElement("label");
          row.className = "checkbox-row";
          var input = document.createElement("input");
          input.type = "checkbox";
          input.value = src.id;
          // If the source is in the map, honor its enabled value;
          // otherwise use the catalog enabled value (default true).
          input.checked = sourcesMap.hasOwnProperty(src.id) ? sourcesMap[src.id] : !!src.enabled;
          input.disabled = !src.enabled;
          var lbl = document.createElement("span");
          var nameSpan = document.createElement("span");
          nameSpan.textContent = src.name || src.id;
          lbl.appendChild(nameSpan);
          var meta = document.createElement("span");
          meta.className = "source-tag";
          meta.textContent = src.enabled ? "" : t("(deshabilitada)");
          lbl.appendChild(meta);
          row.appendChild(input);
          row.appendChild(lbl);
          sourcesWrap.appendChild(row);
        });
      }
    }
  }

  function setNumericField(name, value) {
    var node = document.querySelector('[data-field="' + name + '"]');
    if (!node) return;
    if (value === null || value === undefined || isNaN(value)) {
      node.value = "";
    } else {
      node.value = String(value);
    }
  }

  function collectSettings() {
    var out = {};
    var nodes = document.querySelectorAll("[data-settings-form] [data-field]");
    nodes.forEach(function (n) {
      var k = n.getAttribute("data-field");
      if (!k) return;
      if (k === "regions") {
        // select only the regions chosen by the user
        out[k] = Array.from((n.selectedOptions || [])).map(function (o) { return o.value; });
      } else if (n.tagName === "SELECT" && n.multiple) {
        out[k] = Array.from(n.selectedOptions || []).map(function (o) { return o.value; });
      } else if (n.type === "number") {
        var v = n.value;
        out[k] = (v === "" || v === null) ? null : Number(v);
      } else {
        out[k] = n.value;
      }
    });
    out.preferred_regions = Array.from(document.querySelectorAll('[data-regions-preferred] input:checked'))
      .map(function (input) { return input.value; });
    // Sources: el backend espera mapa {sid:{enabled:bool}}
    var sourcesMap = {};
    document.querySelectorAll("[data-sources-list] input[type='checkbox']").forEach(function (cb) {
      if (cb.disabled) return;
      sourcesMap[cb.value] = { enabled: !!cb.checked };
    });
    out.sources = sourcesMap;
    return out;
  }

  function setupSettingsForm() {
    var sourceSearch=document.querySelector('[data-settings-source-search]');
    if(sourceSearch)sourceSearch.addEventListener('input',function(){var query=sourceSearch.value.toLocaleLowerCase();document.querySelectorAll('[data-sources-list] .checkbox-row').forEach(function(row){row.hidden=row.textContent.toLocaleLowerCase().indexOf(query)<0;});});
    var form = document.querySelector("[data-settings-form]");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var fb = document.querySelector("[data-save-feedback]");
      if (fb) { fb.textContent = t("Guardando…"); fb.removeAttribute("data-state"); }
      var payload = { settings: collectSettings() };
      apiFetch("/api/settings", { method: "PUT", body: payload })
        .then(function () {
          if (fb) {
            fb.textContent = t("Guardado para la próxima búsqueda. El historial se conserva.");
            fb.setAttribute("data-state", "ok");
          }
          showToast(t("Configuración guardada."), "ok");
          // Refrescar settings
          loadSettings();
        }).catch(function (err) {
          if (fb) {
            fb.textContent = (err && err.error) || t("No se pudo guardar.");
            fb.setAttribute("data-state", "error");
          }
          showToast((err && err.error) || t("No se pudo guardar."), "error");
        });
    });
  }

  // ----------------- Usuarios (admin) -----------------

  function loadUsers() {
    var loading = document.querySelector("[data-users-state='loading']");
    var error = document.querySelector("[data-users-state='error']");
    var list = document.querySelector("[data-users-list]");
    if (loading) loading.hidden = false;
    if (error) error.hidden = true;
    if (list) list.innerHTML = "";

    apiFetch("/api/users").then(function (resp) {
      state.users = (resp && resp.users) || [];
      renderUsers();
      if (loading) loading.hidden = true;
    }).catch(function (err) {
      if (loading) loading.hidden = true;
      if (error) {
        error.hidden = false;
        var msg = document.querySelector("[data-users-error-msg]");
        if (msg) msg.textContent = (err && err.error) || t("Error al cargar usuarios.");
      }
    });
  }

  function renderUsers() {
    var list = document.querySelector("[data-users-list]");
    if (!list) return;
    list.innerHTML = "";
    if (!state.users.length) {
      var empty = document.createElement("div");
      empty.className = "state";
      empty.textContent = t("Sin usuarios registrados.");
      list.appendChild(empty);
      return;
    }
    state.users.forEach(function (u) {
      var row = document.createElement("div");
      row.className = "user-row";
      row.setAttribute("data-user-id", String(u.id));
      row.setAttribute("data-active", u.active === false ? "false" : "true");

      var meta = document.createElement("div");
      meta.className = "user-meta";
      var name = document.createElement("span");
      name.className = "user-username";
      name.textContent = u.username;
      meta.appendChild(name);
      var tags = document.createElement("div");
      tags.className = "user-tags";
      var adminTag = document.createElement("span");
      adminTag.className = "chip";
      adminTag.textContent = u.is_admin ? t("Administrador") : t("Revisor");
      tags.appendChild(adminTag);
      var activeTag = document.createElement("span");
      activeTag.className = "chip";
      activeTag.textContent = u.active === false ? t("Desactivado") : t("Activo");
      if (u.active === false) activeTag.classList.add("chip-status-ko");
      tags.appendChild(activeTag);
      meta.appendChild(tags);
      row.appendChild(meta);

      // Spacer
      var spacer = document.createElement("div");
      row.appendChild(spacer);

      var actions = document.createElement("div");
      var toggleBtn = document.createElement("button");
      toggleBtn.className = "btn btn-ghost";
      toggleBtn.type = "button";
      toggleBtn.textContent = u.active === false ? t("Reactivar") : t("Desactivar");
      toggleBtn.addEventListener("click", function () {
        var next = { active: u.active === false ? true : false };
        apiFetch("/api/users/" + encodeURIComponent(u.id), { method: "PATCH", body: next })
          .then(function () {
            showToast(t("Usuario actualizado."), "ok");
            loadUsers();
          }).catch(function (err) {
            showToast((err && err.error) || t("No se pudo actualizar."), "error");
          });
      });
      actions.appendChild(toggleBtn);
      row.appendChild(actions);
      list.appendChild(row);
    });
  }

  function setupUserForm() {
    var form = document.querySelector("[data-form-new-user]");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var usernameNode = document.querySelector("[data-new-username]");
      var pwdNode = document.querySelector("[data-new-password]");
      var errBox = document.querySelector("[data-new-user-error]");
      if (errBox) { errBox.hidden = true; errBox.textContent = ""; }
      var username = usernameNode && usernameNode.value ? usernameNode.value.trim() : "";
      var password = pwdNode && pwdNode.value ? pwdNode.value : "";
      if (!username || !password) {
        if (errBox) { errBox.hidden = false; errBox.textContent = t("Usuario y contraseña son obligatorios."); }
        return;
      }
      apiFetch("/api/users", { method: "POST", body: { username: username, password: password, is_admin: false } })
        .then(function () {
          if (usernameNode) usernameNode.value = "";
          if (pwdNode) pwdNode.value = "";
          showToast(t("Usuario creado."), "ok");
          loadUsers();
        }).catch(function (err) {
          if (errBox) { errBox.hidden = false; errBox.textContent = (err && err.error) || t("No se pudo crear el usuario."); }
        });
    });
  }

  // ----------------- Cuenta -----------------

  function setupPasswordForm() {
    var form = document.querySelector("[data-form-password]");
    if (!form) return;
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var cur = document.querySelector("[data-pw-current]");
      var next = document.querySelector("[data-pw-new]");
      var conf = document.querySelector("[data-pw-confirm]");
      var fb = document.querySelector("[data-pw-feedback]");
      var btn = form.querySelector("button[type='submit']");
      var cu = cur && cur.value || "";
      var ne = next && next.value || "";
      var co = conf && conf.value || "";
      if (!cu || !ne || !co) {
        if (fb) { fb.textContent = t("Completa los tres campos."); fb.setAttribute("data-state", "error"); }
        return;
      }
      if (ne !== co) {
        if (fb) { fb.textContent = t("La nueva contraseña no coincide."); fb.setAttribute("data-state", "error"); }
        return;
      }
      if (ne.length < 12) {
        if (fb) { fb.textContent = t("La nueva contraseña debe tener al menos 12 caracteres."); fb.setAttribute("data-state", "error"); }
        return;
      }
      if (btn) { btn.disabled = true; btn.textContent = t("Guardando…"); }
      apiFetch("/api/password", { method: "POST", body: { current_password: cu, new_password: ne } })
        .then(function () {
          // On success, invalidate the session (password change) and redirect to /login.
          showToast(t("Contraseña actualizada. Vuelve a iniciar sesión."), "ok");
          if (cur) cur.value = "";
          if (next) next.value = "";
          if (conf) conf.value = "";
          if (fb) { fb.textContent = t("Sesión cerrada. Redirigiendo…"); fb.setAttribute("data-state", "ok"); }
          // Call the logout endpoint to invalidate the server-side session;
          // redirect regardless of the outcome.
          apiFetch("/logout", { method: "POST", body: {} })
            .catch(function () { /* ignore */ })
            .then(function () { window.location.href = "/login"; });
        }).catch(function (err) {
          if (fb) { fb.textContent = (err && err.error) || t("No se pudo actualizar."); fb.setAttribute("data-state", "error"); }
        }).then(function () {
          if (btn) { btn.disabled = false; btn.textContent = t("Actualizar contraseña"); }
        });
    });
  }

  // ----------------- Runs (searches) -----------------

  function loadRuns() {
    var loading = document.querySelector("[data-runs-state='loading']");
    var error = document.querySelector("[data-runs-state='error']");
    var list = document.querySelector("[data-runs-list]");
    if (loading) loading.hidden = false;
    if (error) error.hidden = true;
    if (list) list.innerHTML = "";

    apiFetch("/api/runs").then(function (resp) {
      state.runs = (resp && resp.runs) || [];
      state.running = !!(resp && resp.running);
      renderRuns();
      if (loading) loading.hidden = true;
      maybeStartPolling();
    }).catch(function (err) {
      if (loading) loading.hidden = true;
      if (error) {
        error.hidden = false;
        var msg = document.querySelector("[data-runs-error-msg]");
        if (msg) msg.textContent = (err && err.error) || t("Error al cargar el historial.");
      }
    });
  }

  function extractRunCounters(summary) {
    if (!summary || typeof summary !== "object") {
      return { ingested: 0, new: 0, updated: 0, partial: false };
    }
    var ingested = summary.ingested;
    var neu = summary.new;
    var updated = summary.updated;
    function num(v) {
      if (typeof v === "number" && isFinite(v)) return v;
      if (typeof v === "string" && v !== "" && !isNaN(Number(v))) return Number(v);
      return 0;
    }
    return {
      ingested: num(ingested),
      new: num(neu),
      updated: num(updated),
      partial: !!summary.partial,
    };
  }

  function sanitizeLimitations(arr) {
    if (!Array.isArray(arr)) return [];
    var out = [];
    for (var i = 0; i < arr.length; i++) {
      var v = arr[i];
      if (v === null || v === undefined) continue;
      if (typeof v === "string") {
        out.push(v);
      } else if (typeof v === "object") {
        // Best effort: prefer known string fields, otherwise a short hint.
        if (typeof v.text === "string") out.push(v.text);
        else if (typeof v.message === "string") out.push(v.message);
        else if (typeof v.code === "string") out.push("(" + v.code + ")");
      } else {
        out.push(String(v));
      }
    }
    return out;
  }

  function renderRuns() {
    var list = document.querySelector("[data-runs-list]");
    if (!list) return;
    list.innerHTML = "";
    if (!state.runs.length) {
      var empty = document.createElement("div");
      empty.className = "state";
      empty.textContent = t("No hay búsquedas registradas todavía.");
      list.appendChild(empty);
      return;
    }
    state.runs.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "run-row";
      row.setAttribute("data-run-id", String(r.id || r.run_id || ""));
      row.setAttribute("data-status", r.status || "unknown");

      var info = document.createElement("div");
      var title = document.createElement("h3");
      title.className = "run-title";
      title.textContent = (r.id || r.run_id) ? t("Recorrido {id}", { id: (r.id || r.run_id) }) : t("Recorrido sin ID");
      info.appendChild(title);
      var meta = document.createElement("div");
      meta.className = "run-meta";
      var status = document.createElement("span");
      var statusLbl = statusLabel(r.status);
      status.textContent = r.status === "running"
        ? t("Estado: {status} (en curso)", { status: statusLbl })
        : t("Estado: {status}", { status: statusLbl });
      meta.appendChild(status);
      if (r.started_at || r.observed_at) {
        var started = document.createElement("span");
        started.textContent = t("Inicio: {when}", { when: formatDateTime(r.started_at || r.observed_at) || "—" });
        meta.appendChild(started);
      }
      if (r.finished_at) {
        var finished = document.createElement("span");
        finished.textContent = t("Fin: {when}", { when: formatDateTime(r.finished_at) || "—" });
        meta.appendChild(finished);
      }
      var counters = extractRunCounters(r.summary);
      if (counters.ingested || counters.new || counters.updated || counters.partial) {
        var sum = document.createElement("span");
        sum.textContent = t("Activos observados: {ingested} · Nuevos: {new} · Actualizados: {updated}", {
          ingested: counters.ingested,
          new: counters.new,
          updated: counters.updated,
        });
        meta.appendChild(sum);
      }
      if (counters.partial) {
        var warn = document.createElement("span");
        warn.className = "text-small text-faint";
        warn.textContent = t("Cobertura parcial de fuentes.");
        meta.appendChild(warn);
      }
      if (r.error) {
        var er = document.createElement("span");
        er.className = "text-small color-ko";
        er.textContent = t("Error: {msg}", { msg: r.error });
        meta.appendChild(er);
      }
      info.appendChild(meta);

      // Resumen compacto de cobertura
      var health = r.summary && r.summary.health;
      if (health && typeof health === "object") {
        var coverage = document.createElement("div");
        coverage.className = "run-coverage";
        var total = health.total || 0;
        var withResults = health.with_results || 0;
        var issues = (health.blocked || 0) + (health.error || 0) + (health.access_only || 0);
        coverage.textContent = t("Cobertura del recorrido") + ": " + withResults + "/" + total + " " +
          t("Fuentes con resultados") + " · " + issues + " " + t("Fuentes con problemas");
        info.appendChild(coverage);
      }

      if (Array.isArray(r.sources) && r.sources.length > 0) {
        // Group sources into: with results / with issues / not checked / disabled.
        var grouped = groupSources(r.sources);
        var sum = document.createElement("details");
        sum.className = "source-summary";
        var sumT = document.createElement("summary");
        sumT.textContent = t("Ver cobertura y limitaciones de {n} fuentes", { n: r.sources.length });
        sum.appendChild(sumT);

        var sharedLimit = r.common_limitations || (r.summary && r.summary.common_limitations) || [];
        if (!sharedLimit.length) sharedLimit=computeSharedLimitations(r.sources);
        if (sharedLimit.length) {
          var sharedP = document.createElement("p");
          sharedP.className = "source-shared-limitations";
          sharedP.textContent = t("Limitaciones compartidas") + ": " + sharedLimit.join(" · ");
          sum.appendChild(sharedP);
        }

        appendSourceGroup(sum, grouped.results, t("Fuentes con resultados"));
        appendSourceGroup(sum, grouped.issues, t("Fuentes con problemas"));
        appendSourceGroup(sum, grouped.limited, t("Fuentes no exploradas en profundidad"));
        appendSourceGroup(sum, grouped.disabled, t("Fuentes deshabilitadas"));
        info.appendChild(sum);
      }
      row.appendChild(info);

      var actions = document.createElement("div");
      actions.className = "run-actions";
      if (r.status === "running" || state.running) {
        var sp = document.createElement("span");
        sp.className = "text-small text-muted";
        sp.textContent = t("Actualizando…");
        actions.appendChild(sp);
      }
      row.appendChild(actions);

      list.appendChild(row);
    });
  }

  function appendSourceGroup(parent, group, titleLabel) {
    if (!group || !group.length) return;
    var groupEl = document.createElement("div");
    groupEl.className = "source-group";
    var head = document.createElement("h4");
    head.className = "source-group-title";
    head.textContent = titleLabel + " (" + group.length + ")";
    groupEl.appendChild(head);
    group.forEach(function (s) {
      var line = document.createElement("div");
      line.className = "source-line";
      var name = document.createElement("span");
      name.className = "source-name";
      name.textContent = s.name || s.source_id || s.id || "fuente";
      var state2 = document.createElement("span");
      state2.className = "source-state";
      state2.textContent = (s.status ? statusLabel(s.status) : t("En cola")) +
        (s.items_count ? (" · " + t("Activos encontrados: {n}", { n: s.items_count })) : "");
      line.appendChild(name);
      line.appendChild(state2);
      // Keep compact details (reason, time) inside a <details> element; do not dump 65 paragraphs.
      var lims = sanitizeLimitations(s.limitations);
      var detailHasReason = (s.reason && typeof s.reason === "string") || s.finished_at || lims.length;
      if (detailHasReason) {
        var d = document.createElement("details");
        d.className = "source-line-detail";
        var ds = document.createElement("summary");
        ds.textContent = t("Ver detalles de {name}", { name: (s.name || s.source_id || "fuente") });
        d.appendChild(ds);
        var body = document.createElement("div");
        body.className = "source-line-detail-body";
        if (s.reason) {
          var r = document.createElement("p");
          r.className = "text-small";
          r.textContent = t("Motivo: {reason}", { reason: s.reason });
          body.appendChild(r);
        }
        if (s.finished_at) {
          var when = document.createElement("p");
          when.className = "text-small text-faint";
          when.textContent = t("Hora: {when}", { when: formatDateTime(s.finished_at) || "—" });
          body.appendChild(when);
        }
        if (lims.length) {
          var lim = document.createElement("p");
          lim.className = "source-limitations text-small";
          lim.textContent = lims.join(" · ");
          body.appendChild(lim);
        }
        d.appendChild(body);
        line.appendChild(d);
      }
      groupEl.appendChild(line);
    });
    parent.appendChild(groupEl);
  }

  function groupSources(sources) {
    var groups = { results: [], issues: [], limited: [], disabled: [] };
    if (!Array.isArray(sources)) return groups;
    sources.forEach(function (s) {
      if (!s || typeof s !== "object") return;
      var status = (s.status || "").toLowerCase();
      if (status === "disabled") { groups.disabled.push(s); return; }
      if (status === "blocked" || status === "error" || status === "access_only") {
        groups.issues.push(s); return;
      }
      var lims = sanitizeLimitations(s.limitations);
      var hasResults = (s.items_count && s.items_count > 0);
      if (hasResults && !lims.length) { groups.results.push(s); return; }
      if (hasResults && lims.length) { groups.results.push(s); return; }
      if (!hasResults && (status === "limited" || status === "partial" || lims.length)) {
        groups.limited.push(s); return;
      }
      groups.limited.push(s);
    });
    return groups;
  }

  function computeSharedLimitations(sources) {
    if (!Array.isArray(sources)) return [];
    var map = Object.create(null);
    sources.forEach(function (s) {
      var lims = sanitizeLimitations(s && s.limitations);
      lims.forEach(function (l) {
        if (!l) return;
        map[l] = (map[l] || 0) + 1;
      });
    });
    var shared = [];
    Object.keys(map).forEach(function (k) {
      if (map[k] > 1) shared.push(k);
    });
    return shared;
  }

  function queueRun() {
    if (!canManageWorkspace()) return;
    apiFetch("/api/runs", { method: "POST", body: {} })
      .then(function () {
        showToast(t("Búsqueda encolada."), "ok");
        loadRuns();
      }).catch(function (err) {
        showToast((err && err.error) || t("No se pudo encolar la búsqueda."), "error");
      });
  }

  function maybeStartPolling() {
    if (state.pollHandle) {
      clearTimeout(state.pollHandle);
      state.pollHandle = null;
    }
    if (state.running) {
      state.pollHandle = setTimeout(function () {
        if (document.querySelector('[data-view="busquedas"]:not([hidden])')) {
          loadRuns();
        } else {
          state.running = false;
        }
      }, 5000);
    }
  }

  // ----------------- Filtros / Toolbar -----------------

  // ----------------- Generic popover (sort / saved search / AI / more categories) -----------------
  // Minimal implementation based on `role="dialog"` + `aria-haspopup`:
  //  - open on click of `[data-popover-trigger]`
  //  - close on outside click, Escape, or selection
  //  - keyboard: Tab keeps focus inside; arrows move between options; Enter/Space selects
  //  - only one popover open at a time; respect the stack order (topmost)
  var popoverStack = [];
  var popoverCloseTimer = null;

  function positionPopover(popover, trigger) {
    if (!popover || !trigger) return;
    var rect = trigger.getBoundingClientRect();
    var pRect = popover.getBoundingClientRect();
    var vw = window.innerWidth || document.documentElement.clientWidth;
    var vh = window.innerHeight || document.documentElement.clientHeight;
    var margin = 8;
    // Default: below the trigger, left-aligned.
    var top = rect.bottom + window.scrollY + 4;
    var left = rect.left + window.scrollX;
    // Si no cabe, lo colocamos arriba.
    if (top + pRect.height + margin > vh + window.scrollY) {
      top = rect.top + window.scrollY - pRect.height - 4;
    }
    // If it does not fit on the left, align it with the trigger's right edge.
    if (left + pRect.width + margin > vw + window.scrollX) {
      left = (rect.right + window.scrollX) - pRect.width;
    }
    if (left < margin + window.scrollX) left = margin + window.scrollX;
    if (top < margin + window.scrollY) top = margin + window.scrollY;
    popover.style.top = top + "px";
    popover.style.left = left + "px";
  }

  function closeAllPopovers() {
    popoverStack.forEach(function (entry) { closePopover(entry); });
    popoverStack = [];
  }

  function closePopover(entry) {
    if (!entry || !entry.popover) return;
    entry.popover.removeAttribute("data-open");
    entry.popover.hidden = true;
    if (entry.trigger) entry.trigger.setAttribute("aria-expanded", "false");
    if (entry.onClose) try { entry.onClose(); } catch (e) {}
  }

  function closeTopmostPopover() {
    if (!popoverStack.length) return false;
    var top = popoverStack.pop();
    closePopover(top);
    // Restaurar foco al trigger
    if (top.trigger && typeof top.trigger.focus === "function") {
      try { top.trigger.focus(); } catch (e) {}
    }
    return true;
  }

  function openPopover(trigger) {
    if (!trigger) return;
    var key = trigger.getAttribute("data-popover-trigger");
    var popover = document.querySelector('[data-popover="' + key + '"]');
    if (!popover) return;
    // Close other popovers before opening this one
    popoverStack.slice().forEach(function (entry) { closePopover(entry); });
    popoverStack = [];
    // Position before showing it to avoid visible reflow
    popover.hidden = false;
    popover.setAttribute("data-open", "true");
    trigger.setAttribute("aria-expanded", "true");
    positionPopover(popover, trigger);
    // Foco al primer focuseable
    var focusable = popover.querySelector('button, [tabindex]:not([tabindex="-1"])');
    if (focusable) { try { focusable.focus({ preventScroll: true }); } catch (e) {} }
    popoverStack.push({ popover: popover, trigger: trigger });
  }

  function setupPopoverTrigger(trigger) {
    if (!trigger || trigger.dataset.popoverBound) return;
    trigger.dataset.popoverBound = "true";
    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      var expanded = trigger.getAttribute("aria-expanded") === "true";
      if (expanded) {
        // Close only this one
        for (var i = popoverStack.length - 1; i >= 0; i--) {
          if (popoverStack[i].trigger === trigger) { closePopover(popoverStack.splice(i, 1)[0]); break; }
        }
      } else {
        openPopover(trigger);
      }
    });
  }

  function focusPopoverChoice(popover) {
    if(!popover||popover.hidden)return;
    var trigger=document.querySelector('[data-popover-trigger="'+popover.getAttribute('data-popover')+'"]');
    if(trigger)positionPopover(popover,trigger);
    var choice=popover.querySelector('[aria-pressed="true"]')||popover.querySelector('button');
    if(choice)choice.focus({preventScroll:true});
  }

  function bindPopoverItemEvents(popover, onSelect) {
    if (!popover || popover.dataset.itemsBound) return;
    popover.dataset.itemsBound = "true";
    popover.addEventListener('keydown',function(event){
      if(['ArrowDown','ArrowUp','Home','End'].indexOf(event.key)<0)return;
      var buttons=Array.from(popover.querySelectorAll('button:not([disabled])'));if(!buttons.length)return;
      var current=buttons.indexOf(document.activeElement),next=event.key==='Home'?0:event.key==='End'?buttons.length-1:(current+(event.key==='ArrowUp'?-1:1)+buttons.length)%buttons.length;
      event.preventDefault();event.stopPropagation();buttons[next].focus();
    });
    popover.addEventListener("click", function (e) {
      var btn = e.target && e.target.closest && e.target.closest("[data-popover-value]");
      if (!btn) return;
      e.preventDefault();
      var value = btn.getAttribute("data-popover-value");
      var trigger = document.querySelector('[data-popover-trigger="' + popover.getAttribute("data-popover") + '"]');
      if (onSelect) onSelect(value, btn, trigger);
      // Cerrar el popover tras seleccionar.
      for (var i = popoverStack.length - 1; i >= 0; i--) {
        if (popoverStack[i].popover === popover) { closePopover(popoverStack.splice(i, 1)[0]); break; }
      }
      if (trigger && typeof trigger.focus === "function") {
        try { trigger.focus({ preventScroll: true }); } catch (e) {}
      }
    });
  }

  function closePopoverIfClickingOutside(e) {
    if (!popoverStack.length) return;
    // Is the click inside a popover?
    for (var i = 0; i < popoverStack.length; i++) {
      if (popoverStack[i].popover.contains(e.target)) return;
    }
    // Is it on the trigger?
    for (var j = 0; j < popoverStack.length; j++) {
      if (popoverStack[j].trigger && popoverStack[j].trigger.contains(e.target)) return;
    }
    closeAllPopovers();
  }

  // ----------------- Popover rendering helpers -----------------
  function renderSelectOptionsIntoPopover(popover, selectEl, currentValue, formatFn) {
    if (!popover || !selectEl) return;
    popover.innerHTML = "";
    var title = document.createElement("div");
    title.className = "popover-title";
    title.textContent = popover.getAttribute("aria-label") || "";
    popover.appendChild(title);
    var list = document.createElement("div");
    list.className = "popover-list";
    popover.appendChild(list);
    var opts = Array.from(selectEl.options || []);
    if (!opts.length) {
      var empty = document.createElement("div");
      empty.className = "popover-empty";
      empty.textContent = t("Sin resultados");
      popover.appendChild(empty);
      return;
    }
    opts.forEach(function (o) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "popover-item";
      b.setAttribute("data-popover-value", o.value);
      b.setAttribute("aria-pressed", String(o.value === currentValue));
      var lbl = formatFn ? formatFn(o) : (o.textContent || "");
      var lblSpan = document.createElement("span");
      lblSpan.textContent = lbl;
      b.appendChild(lblSpan);
      if (o.value === currentValue) {
        var ck = document.createElement("span");
        ck.className = "popover-item-meta";
        ck.textContent = "✓";
        ck.setAttribute("aria-hidden", "true");
        b.appendChild(ck);
      }
      list.appendChild(b);
    });
    focusPopoverChoice(popover);
  }

  function updatePopoverLabel(popover, trigger, label) {
    if (!trigger || !label) return;
    var span = trigger.querySelector("[data-i18n]") || trigger.querySelector(".btn-icon-label");
    // Find a span with the key that already has data-i18n; otherwise,
    // preserve the icon and update only the label span.
    if (!span) return;
    span.textContent = label;
  }

  // ----------------- Active filter chips (filtros removibles) -----------------
  function getActiveFilterDescriptors() {
    var f = state.filters;
    var out = [];
    // category (incluye via chips)
    if (f.category) {
      out.push({
        key: "category",
        label: t("Categoría") + ": " + (categoryLabel(f.category) || f.category),
        clear: function () { clearFilter("category", ""); },
      });
    }
    if (f.search_id) {
      var sel = document.querySelector('[data-search-id-select]');
      var opt = sel && sel.querySelector('option[value="' + cssEscape(f.search_id) + '"]');
      var name = opt ? opt.textContent : ("#" + f.search_id);
      out.push({
        key: "search_id",
        label: t("Búsqueda") + ": " + name,
        clear: function () { clearFilter("search_id", ""); },
      });
    }
    if (f.q) out.push({ key: "q", label: t("Buscar") + ": " + f.q, clear: function () { clearFilter("q", ""); } });
    if (f.location) out.push({ key: "location", label: t("Ubicación") + ": " + f.location, clear: function () { clearFilter("location", ""); } });
    if (f.source) {
      var sel2 = document.querySelector('[data-filter="source"]');
      var opt2 = sel2 && sel2.querySelector('option[value="' + cssEscape(f.source) + '"]');
      out.push({
        key: "source",
        label: t("Fuente") + ": " + (opt2 ? opt2.textContent : f.source),
        clear: function () { clearFilter("source", ""); },
      });
    }
    if (f.asset && f.asset !== "all") {
      out.push({
        key: "asset",
        label: t("Familia") + ": " + t(assetFamilyLabel(f.asset)),
        clear: function () { clearFilter("asset", "all"); },
      });
    }
    if (f.price_min) out.push({ key: "price_min", label: t("Importe mín.") + ": " + formatMoney(f.price_min), clear: function () { clearFilter("price_min", ""); } });
    if (f.price_max) out.push({ key: "price_max", label: t("Importe máx.") + ": " + formatMoney(f.price_max), clear: function () { clearFilter("price_max", ""); } });
    if (f.has_photos) out.push({ key: "has_photos", label: t("Con fotos"), clear: function () { clearFilter("has_photos", false); } });
    if (f.changed) out.push({ key: "changed", label: t("Con cambios desde revisión"), clear: function () { clearFilter("changed", false); } });
    if (f.ai && f.ai !== "shortlist") {
      var aiSel = document.querySelector('[data-filter="ai"]');
      var aiOpt = aiSel && aiSel.querySelector('option[value="' + cssEscape(f.ai) + '"]');
      out.push({
        key: "ai",
        label: t("IA") + ": " + (aiOpt ? aiOpt.textContent : f.ai),
        clear: function () { clearFilter("ai", "shortlist"); },
      });
    }
    return out;
  }

  function assetFamilyLabel(code) {
    var map = {
      housing: "Vivienda",
      vehicles: "Vehículos",
      commercial: "Local comercial",
      land: "Terreno",
      boats: "Embarcaciones",
      machinery: "Maquinaria",
      electronics: "Electrónica",
      furniture: "Mobiliario",
      collectibles: "Objetos de colección",
      other: "Otros",
      unknown: "Sin clasificar",
    };
    return map[code] || code;
  }

  function clearFilter(key, defaultValue) {
    state.filters[key] = defaultValue;
    // Update the corresponding control type.
    var control = document.querySelector('[data-filter="' + key + '"]');
    if (control) {
      if (control.type === "checkbox") control.checked = !!defaultValue;
      else control.value = defaultValue;
    }
    // Status segmented control si la key es status
    if (key === "status") {
      var sg = document.querySelector('[data-filter="status"]');
      if (sg) sg.querySelectorAll('[data-status-value]').forEach(function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-status-value") === defaultValue ? "true" : "false");
      });
    }
    if(key==='search_id'){var aiWrap=document.querySelector('[data-ai-toolbar]');if(aiWrap)aiWrap.hidden=!defaultValue;}
    state.filters.page=1;
    refreshCompactFilterLabels();
    loadItems();
  }

  function renderActiveFilterChips() {
    document.querySelectorAll('[data-category-chip]').forEach(function(button){var value=button.getAttribute('data-category-chip');var selected=value==='vehicles'?state.filters.asset==='vehicles'&&!state.filters.category:state.filters.category===value && state.filters.asset==='all';button.setAttribute('aria-pressed',String(selected));});
    var wrap = document.querySelector("[data-active-filters]");
    var list = document.querySelector("[data-active-filters-list]");
    var badge = document.querySelector("[data-active-filter-count]");
    if (!wrap || !list) return;
    var descriptors = getActiveFilterDescriptors();
    list.innerHTML = "";
    if (!descriptors.length) {
      wrap.hidden = true;
      if (badge) badge.hidden = true;
      return;
    }
    wrap.hidden = false;
    if (badge) {
      badge.textContent = String(descriptors.length);
      badge.hidden = false;
    }
    descriptors.forEach(function (d) {
      var chip = document.createElement("span");
      chip.className = "active-filter-chip";
      var lbl = document.createElement("span");
      lbl.textContent = d.label;
      chip.appendChild(lbl);
      var clear = document.createElement("button");
      clear.type = "button";
      clear.className = "active-filter-clear";
      clear.setAttribute("aria-label", t("Quitar") + " " + d.label);
      clear.setAttribute("title", t("Quitar") + " " + d.label);
      clear.textContent = "×";
      clear.addEventListener("click", function () { d.clear(); });
      chip.appendChild(clear);
      list.appendChild(chip);
    });
  }

  // ----------------- Popover-to-select delegation helpers -----------------
  function triggerSelectChange(selectEl, value) {
    if (!selectEl) return;
    if (selectEl.value === value) return;
    selectEl.value = value;
    selectEl.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function triggerInputChange(inputEl, value) {
    if (!inputEl) return;
    inputEl.value = value;
    inputEl.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function triggerCheckboxChange(inputEl, checked) {
    if (!inputEl) return;
    inputEl.checked = !!checked;
    inputEl.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setupFilters() {
    document.querySelectorAll('.sv-only select').forEach(function(select){select.tabIndex=-1;});
    ['price_min','price_max'].forEach(function(key){var input=document.querySelector('[data-filter="'+key+'"]');if(input)input.addEventListener('change',function(){state.filters[key]=input.value;state.filters.page=1;loadItems();});});
    var selAsset = document.querySelector('[data-filter="asset"]');
    if (selAsset) {
      selAsset.addEventListener("change", function () {
        state.filters.asset = selAsset.value;
        var entry=(state.categoriesCatalog || []).find(function(c){return c.id===state.filters.category;});
        if (entry && state.filters.asset!=='all' && entry.asset_type!==state.filters.asset) {state.filters.category='';var categoryControl=document.querySelector('[data-filter="category"]');if(categoryControl)categoryControl.value='';}
        state.filters.page = 1;
        loadItems();
      });
    }
    var selCategory = document.querySelector('[data-filter="category"]');
    if (selCategory) {
      selCategory.addEventListener("change", function () {
        state.filters.category = selCategory.value;
        var entry=(state.categoriesCatalog || []).find(function(c){return c.id===state.filters.category;});
        if (entry && state.filters.asset!=='all' && entry.asset_type!==state.filters.asset) {state.filters.asset='all';if(selAsset)selAsset.value='all';}
        state.filters.page = 1;
        loadItems();
      });
    }
    var selSort = document.querySelector('[data-filter="sort"]');
    if (selSort) {
      selSort.addEventListener("change", function () {
        state.filters.sort = selSort.value;
        state.filters.page = 1;
        loadItems();
      });
    }
    var selAi = document.querySelector('[data-filter="ai"]');
    if (selAi) {
      selAi.addEventListener("change", function () {
        state.filters.ai = selAi.value;
        state.filters.page = 1;
        loadItems();
      });
    }
    var selSearchId = document.querySelector('[data-search-id-select]');
    if (selSearchId) {
      selSearchId.addEventListener("change", function () {
        onSelectSearch({detail:{id:selSearchId.value}});
      });
    }
    var statusGroup = document.querySelector('[data-filter="status"]');
    if (statusGroup) {
      statusGroup.querySelectorAll("[data-status-value]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          state.filters.status = btn.getAttribute("data-status-value");
          state.filters.page = 1;
          statusGroup.querySelectorAll("[data-status-value]").forEach(function (b) {
            b.setAttribute("aria-pressed", b === btn ? "true" : "false");
          });
          loadItems();
        });
      });
    }
    var inputQ = document.querySelector('[data-filter="q"]');
    if (inputQ) {
      var timer = null;
      inputQ.addEventListener("input", function () {
        if (timer) clearTimeout(timer);
        timer = setTimeout(function () {
          state.filters.q = inputQ.value;
          state.filters.page = 1;
          loadItems();
        }, 300);
      });
    }
    var selSource = document.querySelector('[data-filter="source"]');
    if (selSource) {
      selSource.addEventListener("change", function () {
        state.filters.source = selSource.value;
        state.filters.page = 1;
        loadItems();
      });
    }
    var inputLocation = document.querySelector('[data-filter="location"]');
    if (inputLocation) {
      var locTimer = null;
      inputLocation.addEventListener("input", function () {
        if (locTimer) clearTimeout(locTimer);
        locTimer = setTimeout(function () {
          state.filters.location = inputLocation.value;
          state.filters.page = 1;
          loadItems();
        }, 300);
      });
    }
    var chkPhotos = document.querySelector('[data-filter="has_photos"]');
    if (chkPhotos) {
      chkPhotos.addEventListener("change", function () {
        state.filters.has_photos = chkPhotos.checked;
        state.filters.page = 1;
        loadItems();
      });
    }
    var chkChanged = document.querySelector('[data-filter="changed"]');
    if (chkChanged) {
      chkChanged.addEventListener("change", function () {
        state.filters.changed = chkChanged.checked;
        if (chkChanged.checked) {
          // A KO with changes must be able to reappear as a warning without losing its decision.
          state.filters.status = "all";
          if (statusGroup) statusGroup.querySelectorAll('[data-status-value]').forEach(function (button) {
            button.setAttribute('aria-pressed', button.getAttribute('data-status-value') === 'all' ? 'true' : 'false');
          });
        }
        state.filters.page = 1;
        loadItems();
      });
    }

    var resetBtn = document.querySelector('[data-action="reset-filters"]');
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        state.filters.asset = "all";
        state.filters.category = "";
        state.filters.status = "pending";
        state.filters.q = "";
        state.filters.source = "";
        state.filters.location = "";
        state.filters.has_photos = false;
        state.filters.changed = false;
        state.filters.sort = "updated";
        state.filters.ai = "shortlist";
        state.filters.price_min='';state.filters.price_max='';
        document.querySelectorAll('[data-filter="price_min"],[data-filter="price_max"]').forEach(function(input){input.value='';});
        state.filters.search_id = "";
        state.filters.page = 1;
        if (selAsset) selAsset.value = "all";
        if (selCategory) selCategory.value = "";
        if (selSort) selSort.value = "updated";
        if (selAi) selAi.value = "keep";
        if (selSearchId) selSearchId.value = "";
        if (statusGroup) {
          statusGroup.querySelectorAll("[data-status-value]").forEach(function (b) {
            b.setAttribute("aria-pressed", b.getAttribute("data-status-value") === "pending" ? "true" : "false");
          });
        }
        if (inputQ) inputQ.value = "";
        if (inputLocation) inputLocation.value = "";
        if (selSource) selSource.value = "";
        if (chkChanged) chkChanged.checked = false;
        if (chkPhotos) chkPhotos.checked = false;
        var aiWrap = document.querySelector('[data-ai-toolbar]');
        if (aiWrap) aiWrap.hidden = true;
        // Reset chips: "Todas" activo.
        var chipGroup = document.querySelector("[data-category-chips]");
        if (chipGroup) {
          chipGroup.querySelectorAll("[data-category-chip]").forEach(function (b) {
            b.setAttribute("aria-pressed", (b.getAttribute("data-category-chip") || "") === "" ? "true" : "false");
          });
        }
        // Close the advanced panel if it is open.
        var adv = document.querySelector("[data-advanced-filters]");
        if (adv) adv.hidden = true;
        var filtBtn = document.querySelector("[data-filters-trigger]");
        if (filtBtn) filtBtn.setAttribute("aria-expanded", "false");
        // Refrescar etiquetas visibles.
        refreshCompactFilterLabels();
        loadItems();
      });
    }
    var refreshBtn = document.querySelector('[data-action="refresh"]');
    if (refreshBtn) {
      refreshBtn.addEventListener("click", function () { loadItems(); });
    }

    setupInfiniteScroll();

    // ---- Category chips and sort/search/AI/more-category popovers ----

    // Category chips map to a family value, or an empty value (All).
    var chipGroup = document.querySelector("[data-category-chips]");
    if (chipGroup) {
      chipGroup.querySelectorAll("[data-category-chip]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var value = btn.getAttribute("data-category-chip") || "";
          // Reset the asset filter if the selected category belongs to another family.
          var assetVal = value==='vehicles'?'vehicles':'all';
          state.filters.category = value==='vehicles'?'':value;
          state.filters.asset = assetVal;
          if (selAsset) selAsset.value = assetVal;
          if (selCategory) selCategory.value = state.filters.category;
          state.filters.page = 1;
          // Marcar chip activo
          chipGroup.querySelectorAll("[data-category-chip]").forEach(function (b) {
            b.setAttribute("aria-pressed", b === btn ? "true" : "false");
          });
          // Disparar loadItems directamente (no propagamos change a selectos
          // to avoid loops with the existing listener while preserving its contract.
          renderActiveFilterChips();
          loadItems();
        });
      });
    }

    // "More filters" button: toggle <details> and synchronize the badge.
    var filtersTrigger = document.querySelector("[data-filters-trigger]");
    var advancedDetails = document.querySelector("[data-advanced-filters]");
    if (filtersTrigger && advancedDetails) {
      filtersTrigger.addEventListener("click", function (e) {
        // If a popover is open, close it; otherwise open
        // the advanced panel (rather than the generic popover, to preserve
        // accesible un <details> con sus controles nativos).
        e.preventDefault();
        e.stopPropagation();
        // Close any popovers
        closeAllPopovers();
        var wasHidden = !!advancedDetails.hidden;
        // The <details> element has its own `open` state; keep it synchronized
        // so the user can toggle it from either the summary or the button.
        if (wasHidden) {
          advancedDetails.hidden = false;
          advancedDetails.open = true;
        } else {
          advancedDetails.hidden = true;
          advancedDetails.open = false;
        }
        filtersTrigger.setAttribute("aria-expanded", wasHidden ? "true" : "false");
        if (wasHidden) {
          // Focus the panel's first input for faster entry.
          var first = advancedDetails.querySelector("input, select, button");
          if (first) try { first.focus({ preventScroll: true }); } catch (e2) {}
        }
      });
      // Keep aria-expanded synchronized with <details> when the user
      // abre/cierra con teclado/resumen.
      advancedDetails.addEventListener("toggle", function () {
        // If the user activates the summary, synchronize hidden so the button
        // externo siga reflejando el estado real.
        advancedDetails.hidden = !advancedDetails.open;
        filtersTrigger.setAttribute("aria-expanded", advancedDetails.open ? "true" : "false");
      });
    }

    // Sort popover: delegate changes to <select data-filter="sort">.
    var sortTrigger = document.querySelector("[data-sort-trigger]");
    var sortPopover = document.querySelector('[data-popover="sort"]');
    var sortSelect = selSort;
    if (sortTrigger && sortPopover && sortSelect) {
      setupPopoverTrigger(sortTrigger);
      var renderSortPopover = function () {
        var current = state.filters.sort || sortSelect.value || "updated";
        renderSelectOptionsIntoPopover(sortPopover, sortSelect, current, function (o) { return o.textContent || o.value; });
        refreshCompactFilterLabels();
      };
      bindPopoverItemEvents(sortPopover, function (value) {
        triggerSelectChange(sortSelect, value);
        renderSortPopover();
      });
      sortTrigger.addEventListener("click", function () {
        // Si el popover estaba cerrado, lo reabrimos con el contenido actualizado.
        if (!sortPopover.hidden) renderSortPopover();
      });
      renderSortPopover();
    }

    // Saved-search popover: list the user's searches and "All".
    var savedSearchTrigger = document.querySelector("[data-saved-search-trigger]");
    var savedSearchPopover = document.querySelector('[data-popover="saved-search"]');
    var savedSearchSelect = selSearchId;
    function renderSavedSearchPopover() {
      if (!savedSearchPopover || !savedSearchSelect) return;
      savedSearchPopover.innerHTML = "";
      var title = document.createElement("div");
      title.className = "popover-title";
      title.textContent = t("Búsquedas guardadas");
      savedSearchPopover.appendChild(title);
      var list = document.createElement("div");
      list.className = "popover-list";
      savedSearchPopover.appendChild(list);
      var opts = Array.from(savedSearchSelect.options || []);
      var empty = !opts.some(function (o) { return o.value !== ""; });
      if (empty) {
        var e = document.createElement("div");
        e.className = "popover-empty";
        e.textContent = t("No tienes búsquedas guardadas todavía.");
        savedSearchPopover.appendChild(e);
      }
      opts.forEach(function (o) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "popover-item";
        b.setAttribute("data-popover-value", o.value);
        b.setAttribute("aria-pressed", String(o.value === (state.filters.search_id || "")));
        var lbl = document.createElement("span");
        lbl.textContent = o.textContent || o.value;
        b.appendChild(lbl);
        list.appendChild(b);
      });
      var labelNode = savedSearchTrigger && savedSearchTrigger.querySelector(".btn-icon-label");
      if (labelNode) {
        var cur = savedSearchSelect.querySelector('option[value="' + cssEscape(state.filters.search_id || "") + '"]');
        labelNode.textContent = cur ? cur.textContent : t("Búsqueda guardada");
      }
      focusPopoverChoice(savedSearchPopover);
    }
    if (savedSearchTrigger && savedSearchPopover && savedSearchSelect) {
      setupPopoverTrigger(savedSearchTrigger);
      bindPopoverItemEvents(savedSearchPopover, function (value) {
        triggerSelectChange(savedSearchSelect, value);
        renderSavedSearchPopover();
        renderActiveFilterChips();
      });
      savedSearchTrigger.addEventListener("click", function () {
        if (!savedSearchPopover.hidden) renderSavedSearchPopover();
      });
    }

    // Popover de veredicto IA.
    var aiTrigger = document.querySelector("[data-ai-trigger]");
    var aiPopover = document.querySelector('[data-popover="ai"]');
    var aiSelect = selAi;
    if (aiTrigger && aiPopover && aiSelect) {
      setupPopoverTrigger(aiTrigger);
      var renderAiPopover = function () {
        renderSelectOptionsIntoPopover(aiPopover, aiSelect, state.filters.ai || aiSelect.value || "shortlist", function (o) { return o.textContent || o.value; });
        var opt = aiSelect.querySelector('option[value="' + (state.filters.ai || aiSelect.value) + '"]');
        var labelNode = aiTrigger.querySelector(".btn-icon-label");
        if (labelNode && opt) {
          labelNode.textContent = t("IA:") + " " + (opt.textContent || opt.value);
        }
      };
      bindPopoverItemEvents(aiPopover, function (value) {
        triggerSelectChange(aiSelect, value);
        renderAiPopover();
        renderActiveFilterChips();
      });
      aiTrigger.addEventListener("click", function () {
        if (!aiPopover.hidden) renderAiPopover();
      });
      renderAiPopover();
    }

    // "More categories" popover: all catalog categories, grouped
    // by family for quick scanning.
    var moreTrigger = document.querySelector("[data-more-categories-trigger]");
    var morePopover = document.querySelector('[data-popover="more-categories"]');
    if (moreTrigger && morePopover) {
      setupPopoverTrigger(moreTrigger);
      bindPopoverItemEvents(morePopover, function (value) {
        var sel = document.querySelector('[data-filter="category"]');
        triggerSelectChange(sel, value);
        renderActiveFilterChips();
      });
      var renderMoreCategories = function () {
        morePopover.innerHTML = "";
        var title = document.createElement("div");
        title.className = "popover-title";
        title.textContent = t("Todas las categorías");
        morePopover.appendChild(title);
        var list = document.createElement("div");
        list.className = "popover-list";
        morePopover.appendChild(list);
        // "All" option to clear the selection
        var allBtn = document.createElement("button");
        allBtn.type = "button";
        allBtn.className = "popover-item";
        allBtn.setAttribute("data-popover-value", "");
        allBtn.setAttribute("aria-pressed", String(!state.filters.category));
        var allLbl = document.createElement("span");
        allLbl.textContent = t("Todas las categorías");
        allBtn.appendChild(allLbl);
        list.appendChild(allBtn);
        var cats = state.categoriesCatalog || [];
        cats.forEach(function (c) {
          var b = document.createElement("button");
          b.type = "button";
          b.className = "popover-item";
          b.setAttribute("data-popover-value", c.id);
          b.setAttribute("aria-pressed", String(c.id === state.filters.category));
          var lbl = document.createElement("span");
          lbl.textContent = (window.AuctionRadarUI && window.AuctionRadarUI.language() === "en") ? (c.label_en || c.label_es || c.id) : (c.label_es || c.id);
          b.appendChild(lbl);
          if (c.id === state.filters.category) {
            var ck = document.createElement("span");
            ck.className = "popover-item-meta";
            ck.textContent = "✓";
            ck.setAttribute("aria-hidden", "true");
            b.appendChild(ck);
          }
          list.appendChild(b);
        });
        focusPopoverChoice(morePopover);
      };
      moreTrigger.addEventListener("click", function () {
        if (!morePopover.hidden) renderMoreCategories();
      });
    }

    // Escape: close the topmost popover before any modal layer.
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && popoverStack.length && !drawerState.itemId && !lightboxVisible() && !shortcutsDialogVisible() && !document.querySelector('[data-search-editor-drawer].open')) {
        e.preventDefault();
        e.stopPropagation();
        closeTopmostPopover();
      }
    }, true);

    // Outside click: close popovers. Use capture to avoid conflicts with handlers
    // that stop propagation on their own buttons.
    document.addEventListener("click", function (e) {
      if (!popoverStack.length) return;
      // Si el click es en un trigger de popover, ya lo gestionamos arriba.
      closePopoverIfClickingOutside(e);
    }, true);
    document.addEventListener('focusin',function(event){if(popoverStack.length&&!popoverStack.some(function(entry){return entry.popover.contains(event.target)||entry.trigger.contains(event.target);}))closeAllPopovers();});

    // Resize / scroll: reposition the topmost popover if it is open.
    var repositionTimer = null;
    function repositionOpenPopover() {
      if (!popoverStack.length) return;
      var top = popoverStack[popoverStack.length - 1];
      if (top) positionPopover(top.popover, top.trigger);
    }
    window.addEventListener("resize", function () {
      if (repositionTimer) clearTimeout(repositionTimer);
      repositionTimer = setTimeout(repositionOpenPopover, 80);
    });
    window.addEventListener("scroll", repositionOpenPopover, true);

    renderActiveFilterChips();
  }

  // ----------------- Nav links -----------------

  function setupNav() {
    document.querySelectorAll(".nav-item").forEach(function (n) {
      n.addEventListener("click", function (e) {
        var v = n.getAttribute("data-nav");
        if (!v) return;
        e.preventDefault();
        window.location.hash = v;
        showView(v);
      });
    });
    window.addEventListener("hashchange", selectViewFromHash);
  }

  // ----------------- Drawer / Lightbox cierre -----------------

  function setupDrawerClose() {
    var closeBtn = document.querySelector("[data-drawer-close]");
    if (closeBtn) closeBtn.addEventListener("click", closeDrawer);
    var backdrop = document.querySelector("[data-drawer-backdrop]");
    if (backdrop) backdrop.addEventListener("click", closeDrawer);

    var lbClose = document.querySelector("[data-lightbox-close]");
    if (lbClose) lbClose.addEventListener("click", closeLightbox);
    var lbPrev = document.querySelector("[data-lightbox-prev]");
    if (lbPrev) lbPrev.addEventListener("click", function () { lightboxPrev(); });
    var lbNext = document.querySelector("[data-lightbox-next]");
    if (lbNext) lbNext.addEventListener("click", function () { lightboxNext(); });
    var lb = document.querySelector("[data-lightbox]");
    if (lb) lb.addEventListener("click", function (e) {
      if (e.target === lb) closeLightbox();
    });
  }

  // ----------------- Atajos de teclado (capa-aware) -----------------

  function shortcutsDialogVisible() {
    var d = document.querySelector("[data-shortcuts-dialog]");
    return !!(d && !d.hidden);
  }

  function openShortcutsDialog() {
    var d = document.querySelector("[data-shortcuts-dialog]");
    if (!d) return;
    var body = d.querySelector("[data-shortcuts-body]");
    if (body) {
      body.innerHTML = "";
      var rows = [
        { key: "?", action: "Abrir esta ayuda" },
        { key: "Esc", action: "Cerrar la capa superior" },
        { key: "←", action: "Foto anterior (lightbox)" },
        { key: "→", action: "Siguiente foto (lightbox)" },
        { key: "Home", action: "Primera foto" },
        { key: "End", action: "Última foto" },
        { key: "J", action: "Siguiente tarjeta" },
        { key: "K", action: "Tarjeta anterior" },
        { key: "O", action: "Marcar OK (detalle abierto)" },
        { key: "X", action: "Marcar KO (detalle abierto)" },
        { key: "Ctrl/⌘ + Enter", action: "Guardar notas" },
        { key: "/", action: "Buscar (no en editor)" },
      ];
      rows.forEach(function (r) {
        var row = document.createElement("div");
        row.className = "shortcut-row";
        var kbd = document.createElement("kbd");
        kbd.className = "shortcut-key";
        kbd.textContent = r.key;
        var lbl = document.createElement("span");
        lbl.className = "shortcut-action";
        lbl.textContent = t(r.action);
        row.appendChild(kbd);
        row.appendChild(lbl);
        body.appendChild(row);
      });
    }
    shortcutsDialogState.lastFocus = document.activeElement;
    d.hidden = false;
    d.classList.add("open");
    syncModalInert();
    var closeBtn = d.querySelector("[data-shortcuts-close]");
    if (closeBtn) closeBtn.focus();
  }

  function closeShortcutsDialog() {
    var d = document.querySelector("[data-shortcuts-dialog]");
    if (!d) return;
    d.classList.remove("open");
    d.hidden = true;
    syncModalInert();
    if (shortcutsDialogState.lastFocus && typeof shortcutsDialogState.lastFocus.focus === "function") {
      try { shortcutsDialogState.lastFocus.focus(); } catch (e) {}
    }
    shortcutsDialogState.lastFocus = null;
  }

  var shortcutsDialogState = { lastFocus: null };

  function setupShortcutsDialog() {
    var trigger = document.querySelector("[data-shortcuts-trigger]");
    if (trigger) trigger.addEventListener("click", openShortcutsDialog);
    var closeBtn = document.querySelector("[data-shortcuts-close]");
    if (closeBtn) closeBtn.addEventListener("click", closeShortcutsDialog);
    var dlg = document.querySelector("[data-shortcuts-dialog]");
    if (dlg) dlg.addEventListener("click", function (e) {
      if (e.target === dlg) closeShortcutsDialog();
    });
  }

  function findCards() {
    return Array.prototype.slice.call(
      document.querySelectorAll("[data-view='oportunidades']:not([hidden]) .cards-grid .item-card")
    );
  }

  function moveCardFocus(delta) {
    var cards = findCards();
    if (!cards.length) return false;
    var active = document.activeElement;
    var idx = -1;
    for (var i = 0; i < cards.length; i++) {
      var open = cards[i].querySelector(".item-card-open");
      if (open && (open === active || cards[i] === active)) { idx = i; break; }
    }
    var next = idx < 0 ? (delta > 0 ? 0 : cards.length - 1) : ((idx + delta + cards.length) % cards.length);
    var btn = cards[next].querySelector(".item-card-open");
    if (btn) btn.focus();
    return true;
  }

  function focusSearch() {
    var input = document.querySelector('[data-filter="q"]');
    if (!input || input.disabled) return false;
    input.focus();
    if (typeof input.select === "function") input.select();
    return true;
  }

  function isEditor(node) {
    if (!node) return false;
    if (node.isContentEditable) return true;
    var tag = node.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    return false;
  }

  function cssEscape(value) {
    if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(String(value));
    return String(value).replace(/['"\\]/g, "\\$&");
  }

  function quickReviewByKey(status) {
    if (!drawerState.itemId) return false;
    var option = document.querySelector('.review-block [data-review-status="' + status + '"]');
    if (!option) return false;
    option.click();
    return true;
  }

  function handleGlobalKeydown(e) {
    if(popoverStack.length)return;
    var searchEditor=document.getElementById('search-editor-drawer');
    if (searchEditor && searchEditor.classList.contains('open') && !shortcutsDialogVisible()) return;
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && drawerState.itemId && !lightboxVisible() && !shortcutsDialogVisible()) {
      var save = document.querySelector('[data-save-review]');
      if (save && !save.disabled) { e.preventDefault(); save.click(); }
      return;
    }
    // Modifier keys change the behavior; handle only events without Ctrl/Meta/Alt.
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    // Escape: cierra la capa superior.
    if (e.key === "Escape") {
      if (shortcutsDialogVisible()) { closeShortcutsDialog(); e.preventDefault(); return; }
      var dlg = document.querySelector("[data-shortcuts-dialog]");
      if (dlg && !dlg.hidden) { closeShortcutsDialog(); e.preventDefault(); return; }
      var lb = document.querySelector("[data-lightbox]");
      if (lb && !lb.hidden) { closeLightbox(); e.preventDefault(); return; }
      var drawer = document.getElementById("item-drawer");
      if (drawer && drawer.classList.contains("open")) { closeDrawer(); e.preventDefault(); return; }
      var sidebar = document.getElementById("app-sidebar");
      if (sidebar && sidebar.classList.contains("open")) {
        sidebar.classList.remove("open");
        var menu = document.querySelector('[data-menu-toggle]');
        if (menu) { menu.setAttribute('aria-expanded', 'false'); menu.focus(); }
        e.preventDefault();
      }
      return;
    }

    // Lightbox: Arrow and Home/End keys while it is open.
    if (lightboxVisible()) {
      if (e.key === "ArrowLeft") { lightboxPrev(); e.preventDefault(); return; }
      if (e.key === "ArrowRight") { lightboxNext(); e.preventDefault(); return; }
      if (e.key === "Home") { lightboxFirst(); e.preventDefault(); return; }
      if (e.key === "End") { lightboxLast(); e.preventDefault(); return; }
      if (e.key !== "Tab") return;
    }

    // Tab focus trap: keep focus inside open drawers/lightboxes.
    if (e.key === "Tab") {
      var lb2 = document.querySelector("[data-lightbox]");
      if (lb2 && !lb2.hidden) {
        var lbFocusables = focusableIn(lb2);
        if (lbFocusables.length === 0) { e.preventDefault(); return; }
        var first = lbFocusables[0];
        var last = lbFocusables[lbFocusables.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        return;
      }
      var dlg2 = document.querySelector("[data-shortcuts-dialog]");
      if (dlg2 && !dlg2.hidden) {
        var dFocusables2 = focusableIn(dlg2);
        if (dFocusables2.length === 0) { e.preventDefault(); return; }
        var dFirst2 = dFocusables2[0];
        var dLast2 = dFocusables2[dFocusables2.length - 1];
        if (e.shiftKey && document.activeElement === dFirst2) { e.preventDefault(); dLast2.focus(); }
        else if (!e.shiftKey && document.activeElement === dLast2) { e.preventDefault(); dFirst2.focus(); }
        return;
      }
      var drawer2 = document.getElementById("item-drawer");
      if (drawer2 && drawer2.classList.contains("open")) {
        var dFocusables = focusableIn(drawer2);
        if (dFocusables.length === 0) { e.preventDefault(); return; }
        var dFirst = dFocusables[0];
        var dLast = dFocusables[dFocusables.length - 1];
        if (e.shiftKey && document.activeElement === dFirst) { e.preventDefault(); dLast.focus(); }
        else if (!e.shiftKey && document.activeElement === dLast) { e.preventDefault(); dFirst.focus(); }
        return;
      }
    }

    if (shortcutsDialogVisible()) return;
    // Shortcuts work only when focus is NOT in an editor.
    if (isEditor(document.activeElement)) return;

    // ?: ayuda (Shift+/ produce '?')
    if (e.key === "?" || (e.shiftKey && e.key === "/")) {
      openShortcutsDialog();
      e.preventDefault();
      return;
    }
    // The detail view is modal: O/X immediately persist the status, just like
    // the grid quick action. Notes still require explicit saving.
    // isEditor already blocks typing, so shortcuts fire only when focus is outside the textarea.
    if (drawerState.itemId) {
      if (e.key === 'o' || e.key === 'O') { if (quickReviewByKey('ok')) e.preventDefault(); }
      if (e.key === 'x' || e.key === 'X') { if (quickReviewByKey('ko')) e.preventDefault(); }
      return;
    }
    // /: focus the search input
    if (e.key === "/") {
      if (focusSearch()) { e.preventDefault(); return; }
    }
    // j/k: siguiente / anterior tarjeta
    if (e.key === "j" || e.key === "J") { if (moveCardFocus(1)) e.preventDefault(); return; }
    if (e.key === "k" || e.key === "K") { if (moveCardFocus(-1)) e.preventDefault(); return; }
  }

  // ----------------- Quick review (card actions) -----------------

  function quickReview(itemId, status, okBtn, koBtn) {
    if (!itemId) return;
    var coordinator=detailReviewSaves[itemId];
    if(coordinator && coordinator.inFlight)return;
    if (okBtn) okBtn.disabled = true;
    if (koBtn) koBtn.disabled = true;
    var card = okBtn && okBtn.closest ? okBtn.closest(".item-card") : null;
    var index = card ? Array.prototype.indexOf.call(document.querySelectorAll('.item-card'), card) : 0;
    var oldStatus=card && card._auctionradarItem && card._auctionradarItem.review?card._auctionradarItem.review.status:'pending';
    if(!coordinator)coordinator=detailReviewSaves[itemId]={persistedStatus:oldStatus,desiredStatus:status,inFlight:null};
    coordinator.desiredStatus=status;
    beginGridReview();
    // Read the latest notes atomically from the server; do not overwrite them with a cache.
    var body = { status: status, preserve_notes: true };

    var operation=apiFetch("/api/items/" + encodeURIComponent(itemId) + "/review", {
      method: "POST",
      body: body
    }).then(function (resp) {
      if(!resp || !resp.item || resp.item.id!==itemId || !resp.item.review)throw {error:t('No se pudo guardar.')};
      coordinator.persistedStatus=resp.item.review.status;
      showToast(t("Decisión guardada."), "ok");
      if (drawerState && drawerState.itemId === itemId && resp && resp.item) {
        rememberDrawerDraft();
        drawerState.data.item = resp.item;
      }
      updateGridReview(resp.item);
        var updated = document.querySelector('.item-card[data-item-id="' + cssEscape(itemId) + '"]');
        var action = updated && updated.querySelector(status === 'ok' ? '[data-review-ok]' : '[data-review-ko]');
        var cards = document.querySelectorAll('.item-card-open');
        var target = action || cards[Math.min(Math.max(0, index), cards.length - 1)];
        if (target && !drawerState.itemId) target.focus({preventScroll: true});
    }).catch(function (err) {
      showToast((err && err.error) || t("No se pudo guardar."), "error");
    }).then(function () {
      if(coordinator.inFlight===operation){coordinator.inFlight=null;coordinator.desiredStatus=coordinator.persistedStatus;}
      if (okBtn) okBtn.disabled = false;
      if (koBtn) koBtn.disabled = false;
      document.querySelectorAll('.item-card[data-item-id="'+cssEscape(itemId)+'"] [data-review-ok],.item-card[data-item-id="'+cssEscape(itemId)+'"] [data-review-ko]').forEach(function(button){button.disabled=!!coordinator.inFlight;});
      endGridReview();
    });
    coordinator.inFlight=operation;
  }

  // ----------------- Re-render al cambiar idioma -----------------

  function onLanguageChange() {
    var active = document.activeElement;
    var activeId = active && active.id;
    var selection = active && typeof active.selectionStart === "number" ? [active.selectionStart, active.selectionEnd] : null;
    rememberDrawerDraft();
    // Changing the language is local: do not reload forms or notes.
    var nav = document.querySelector('.nav-item[aria-current="page"] [data-i18n]');
    var topTitle = document.querySelector('[data-topbar-title]');
    if (nav && topTitle) topTitle.textContent = t(nav.getAttribute('data-i18n'));
    if (state.lastItems) {
      renderItems(state.lastItems);
      renderPagination(state.lastItems);
      updateCountsRow(state.lastItems);
    }
    ensureSourceSelect();
    ensureCategorySelect();
    // Refresh compact button labels (sort/AI/saved search)
    // and the visible content of chips/popovers.
    refreshCompactFilterLabels();
    if (state.users.length) renderUsers();
    if (state.runs.length) renderRuns();
    if (state.me) {
      var role = document.querySelector('[data-user-role]');
      if (role) role.textContent = t(state.me.is_admin ? 'Administrador' : 'Usuario');
    }
    if (state.schedule) {
      var staticSched = document.querySelector('[data-schedule-static-label]');
      if (staticSched) staticSched.textContent = t(state.schedule.description);
    }
    if (state.aiConfig) renderAiStatusStatic(state.aiConfig);
    if (drawerState.itemId && drawerState.data && drawerState.data.item) {
      var body = document.querySelector('[data-drawer-body]');
      body.replaceChildren(buildDetailBody(drawerState.data.item));
      restoreDrawerDraft();
      if (lightboxVisible()) {
        lightboxState.lastFocus = body.querySelectorAll('.gallery-thumb')[lightboxState.index] || body.querySelector('.gallery-thumb');
        setLightboxImage(lightboxState.index);
      }
    }
    if (shortcutsDialogVisible()) {
      var previous = shortcutsDialogState.lastFocus;
      openShortcutsDialog();
      shortcutsDialogState.lastFocus = previous;
    }
    // ui.js owns the languagechange event and has already notified external modules.
    // This handler only refreshes this app's rendered content; redispatching here
    // would synchronously call this same listener again.
    var focus = activeId ? document.getElementById(activeId) : (active && active.isConnected ? active : null);
    if (focus) {
      focus.focus({preventScroll: true});
      if (selection && focus.setSelectionRange) focus.setSelectionRange(selection[0], selection[1]);
    }
  }

  // Refresh visible labels on the compact toolbar buttons.
  function refreshCompactFilterLabels() {
    var sortTrigger = document.querySelector("[data-sort-trigger]");
    var sortSelect = document.querySelector('[data-filter="sort"]');
    if (sortTrigger && sortSelect) {
      var labelNode = sortTrigger.querySelector(".btn-icon-label");
      var opt = sortSelect.querySelector('option[value="' + cssEscape(state.filters.sort || sortSelect.value || "updated") + '"]');
      if (labelNode && opt) {
        var shortSort={updated:'Recientes',newest:'Más nuevas',oldest:'Más antiguas',price_asc:'Precio ↑',price_desc:'Precio ↓',ai_score:'Puntuación IA'};
        labelNode.textContent=t(shortSort[opt.value] || opt.textContent);
        sortTrigger.setAttribute('aria-label',t('Ordenar por')+': '+opt.textContent);
        sortTrigger.title=t('Ordenar por')+': '+opt.textContent;
      }
    }
    var aiTrigger = document.querySelector("[data-ai-trigger]");
    var aiSelect = document.querySelector('[data-filter="ai"]');
    if (aiTrigger && aiSelect) {
      var aLbl = aiTrigger.querySelector(".btn-icon-label");
      var aiOpt = aiSelect.querySelector('option[value="' + cssEscape(state.filters.ai || aiSelect.value || "shortlist") + '"]');
      if (aLbl && aiOpt) aLbl.textContent = t("IA:") + " " + (aiOpt.textContent || aiOpt.value);
    }
    var ssTrigger = document.querySelector("[data-saved-search-trigger]");
    var ssSelect = document.querySelector('[data-search-id-select]');
    if (ssTrigger && ssSelect) {
      var sLbl = ssTrigger.querySelector(".btn-icon-label");
      var ssOpt = ssSelect.querySelector('option[value="' + cssEscape(state.filters.search_id || "") + '"]');
      if (ssOpt && !ssOpt.value) ssOpt.textContent=t('Todas las búsquedas');
      if (sLbl) sLbl.textContent = ssOpt ? ssOpt.textContent : t("Búsqueda guardada");
    }
    renderActiveFilterChips();
  }

  // When another module (saved-searches.js) activates a saved search
  // desde el grid principal, escuchamos el evento y reconfiguramos filtros.
  function onSelectSearch(evt) {
    var detail = (evt && evt.detail) || {};
    var id = detail.id || "";
    var name = detail.name || "";
    state.filters.search_id = id;
    state.filters.ai = "shortlist";
    Object.assign(state.filters,{asset:'all',category:'',status:'all',q:'',source:'',location:'',has_photos:false,changed:false,price_min:'',price_max:''});
    document.querySelectorAll('[data-filter]').forEach(function(control){var key=control.getAttribute('data-filter');if(!(key in state.filters))return;if(key==='status'){control.querySelectorAll('[data-status-value]').forEach(function(button){button.setAttribute('aria-pressed',button.getAttribute('data-status-value')==='all'?'true':'false');});}else if(control.type==='checkbox'){control.checked=!!state.filters[key];}else if(control.tagName==='INPUT'||control.tagName==='SELECT'){control.value=state.filters[key];}});
    state.filters.page = 1;
    var sel = document.querySelector('[data-search-id-select]');
    if (sel) sel.value = id;
    var aiWrap = document.querySelector('[data-ai-toolbar]');
    if (aiWrap) aiWrap.hidden = !id;
    // Return to Opportunities to view the filtered grid.
    showView("oportunidades");
    window.location.hash = "oportunidades";
    loadItems().then(function(){if(detail.item_id)openDrawer(detail.item_id);});
    if (name) showToast(name, "info");
  }
  document.addEventListener("auctionradar:select-search", onSelectSearch);

  // ----------------- /me schedule -----------------

  function loadMe() {
    apiFetch("/api/me").then(function (resp) {
      if (resp && resp.user) {
        state.me = resp.user;
        // Si cambia is_admin respecto a la meta, refrescar visibilidad
        if (!!resp.user.is_admin !== isAdmin) {
          isAdmin = !!resp.user.is_admin;
          applyAdminVisibility();
        }
        var nameNode = document.querySelector("[data-user-name]");
        if (nameNode) nameNode.textContent = resp.user.username || "";
        var roleNode = document.querySelector("[data-user-role]");
        if (roleNode) roleNode.textContent = resp.user.is_admin ? t("Administrador") : t("Usuario");
      }
      if (resp && resp.schedule) {
        state.schedule = resp.schedule;
        // Show the schedule only in Settings (not in the top bar).
        var staticSched = document.querySelector("[data-schedule-static-label]");
        if (staticSched && resp.schedule && resp.schedule.description) {
          staticSched.textContent = t(resp.schedule.description);
        }
      }
      if (resp && resp.branding) {
        state.branding = resp.branding;
      }
      if (resp && resp.ai) {
        state.aiConfig = resp.ai;
        renderAiStatusStatic(resp.ai);
      }
      // Top-level last_updated_at: present when returned by /api/me.
      if (resp && resp.last_updated_at) {
        state.lastUpdatedAt = resp.last_updated_at;
        var wrap = document.querySelector("[data-lastupdated-wrap]");
        var lbl = document.querySelector("[data-lastupdated-label]");
        if (wrap) wrap.hidden = false;
        if (lbl) {
          var formatted = formatDateTime(resp.last_updated_at);
          lbl.textContent = formatted || t("Sin datos disponibles");
        }
      } else {
        // No tenemos dato: mantenemos visible el bloque y mostramos mensaje honesto.
        var wrap2 = document.querySelector("[data-lastupdated-wrap]");
        var lbl2 = document.querySelector("[data-lastupdated-label]");
        if (wrap2) wrap2.hidden = false;
        if (lbl2) lbl2.textContent = t("Sin datos disponibles");
      }
    }).catch(function () {
      // Si /api/me falla con 401/403, apiFetch ya redirige
    });
  }

  function renderAiStatusStatic(ai) {
    if (!ai || typeof ai !== "object") return;
    var chip = document.querySelector("[data-ai-status-chip]");
    var providerLine = document.querySelector("[data-ai-provider-line]");
    if (chip) {
      chip.textContent = ai.configured ? t("IA configurada") : t("IA pendiente de configurar");
      chip.classList.toggle("chip-status-ok", !!ai.configured);
      chip.classList.toggle("chip-status-pending", !ai.configured);
    }
    if (providerLine) {
      var prov = ai.provider || "—";
      var model = ai.model || "—";
      var enabledTxt = ai.enabled ? t("habilitada") : t("deshabilitada");
      providerLine.textContent = prov + " · " + model + " · " + enabledTxt;
    }
  }

  // ----------------- Load the source catalog for the select -----------------

  function loadSourceCatalog() {
    apiFetch("/api/sources").then(function (resp) {
      state.sourcesCatalog = (resp && resp.sources) || [];
      ensureSourceSelect();
    }).catch(function () { /* Search remains available without a source filter. */ });
  }

  function loadCategories() {
    apiFetch("/api/categories").then(function (resp) {
      var cats = (resp && resp.categories) || [];
      state.categoriesCatalog = cats;
      ensureCategorySelect();
      // Also populate the select in saved-searches.js if it is ready.
      document.dispatchEvent(new CustomEvent("auctionradar:categories-loaded", {
        detail: { categories: cats }, bubbles: true
      }));
    }).catch(function () { /* If there are no categories, keep using the family filter. */ });
  }

  // Synchronize the toolbar's saved-search selector with the list
  // returned by /api/searches. When saved-searches.js finishes loading the
  // list (including the user's searches), it broadcasts it through this event.
  function onSearchesLoaded(evt) {
    var detail = (evt && evt.detail) || {};
    var searches = Array.isArray(detail.searches) ? detail.searches : [];
    var sel = document.querySelector('[data-search-id-select]');
    if (!sel) return;
    // Keep "All searches" as the first option.
    sel.innerHTML = "";
    var opt = document.createElement("option");
    opt.value = "";
    opt.setAttribute('data-i18n','Todas las búsquedas');
    opt.textContent = t("Todas las búsquedas");
    sel.appendChild(opt);
    searches.forEach(function (s) {
      if (!s || !s.id) return;
      var o = document.createElement("option");
      o.value = String(s.id);
      o.textContent = s.name || ("#" + s.id);
      if (!s.active) {
        o.textContent += " (" + t("Archivada") + ")";
      }
      sel.appendChild(o);
    });
    sel.value = state.filters.search_id || "";
    // Synchronize the visibility of the AI selector.
    var aiWrap = document.querySelector('[data-ai-toolbar]');
    if (aiWrap) aiWrap.hidden = !state.filters.search_id;
    // Refresh the saved-search popover (text + options) after i18n.
    var ssTrigger = document.querySelector("[data-saved-search-trigger]");
    var ssPopover = document.querySelector('[data-popover="saved-search"]');
    if (ssTrigger && ssPopover && !ssPopover.hidden) {
      // Basic re-render: update the trigger label.
      var labelNode = ssTrigger.querySelector(".btn-icon-label");
      if (labelNode) {
        var cur = sel.querySelector('option[value="' + cssEscape(state.filters.search_id || "") + '"]');
        labelNode.textContent = cur ? cur.textContent : t("Búsqueda guardada");
      }
    }
    if (typeof renderActiveFilterChips === "function") renderActiveFilterChips();
  }
  document.addEventListener("auctionradar:searches-loaded", onSearchesLoaded);

  // ----------------- Initialization -----------------

  function doLogout() {
    // POST al endpoint con CSRF; redirigimos siempre a /login.
    apiFetch("/logout", { method: "POST", body: {} })
      .catch(function () { /* The backend may return a redirect; ignore it. */ })
      .then(function () { window.location.href = "/login"; });
  }

  function init() {
    applyAdminVisibility();
    setupSidebarToggle();
    setupNav();
    setupFilters();
    setupDrawerClose();
    setupShortcutsDialog();
    document.addEventListener('auctionradar:modal-change',syncModalInert);
    document.addEventListener('auctionradar:close-item',function(){if(drawerState.itemId) closeDrawer();});
    syncModalInert();
    setupSettingsForm();
    setupUserForm();
    setupPasswordForm();

    // i18n / theme picker (AuctionRadarUI expone helpers idempotentes).
    if (window.AuctionRadarUI) {
      window.AuctionRadarUI.setupLanguagePicker();
      window.AuctionRadarUI.setupThemeToggle();
    }
    document.addEventListener("auctionradar:languagechange", onLanguageChange);

    // Single global shortcut handler: respect editor focus and modal layers.
    document.addEventListener("keydown", handleGlobalKeydown);

    // Acciones adicionales
    var queueBtn = document.querySelector('[data-action="queue-run"]');
    if (queueBtn) queueBtn.addEventListener("click", queueRun);

    var logoutBtn = document.querySelector('[data-action="logout"]');
    if (logoutBtn) logoutBtn.addEventListener("click", doLogout);

    // Abrir detalle si llega con ?item=
    var params = new URLSearchParams(window.location.search);
    var itemParam = params.get("item");
    if (itemParam) {
      // Mostrar vista oportunidades y abrir drawer
      showView("oportunidades");
      setTimeout(function () { openDrawer(itemParam, null); }, 0);
    } else {
      selectViewFromHash();
    }

    loadMe();
    loadSourceCatalog();
    loadCategories();
    loadItems();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
