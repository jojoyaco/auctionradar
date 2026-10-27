/* AuctionRadar · share.js
 * Public view of an asset shared using a read-only token.
 * Read the token from the URL and request GET /api/public/<token>.
 * i18n and theme handling are delegated to /static/ui.js (loaded earlier in <head>).
 */
(function () {
  "use strict";

  function t(source, params) {
    if (window.AuctionRadarUI && typeof window.AuctionRadarUI.t === "function") {
      return window.AuctionRadarUI.t(source, params);
    }
    if (!params) return source;
    return String(source).replace(/\{(\w+)\}/g, function (_, k) {
      return Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : "{" + k + "}";
    });
  }

  function safeHttpUrl(value) {
    if (typeof value !== "string") return null;
    var trimmed = value.trim();
    if (!trimmed) return null;
    if (/^javascript:/i.test(trimmed)) return null;
    if (/^data:/i.test(trimmed)) return null;
    try {
      var u = new URL(trimmed);
      if (u.protocol !== "http:" && u.protocol !== "https:") return null;
      return u.toString();
    } catch (e) {
      return null;
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
      return n.toLocaleString("es-ES", { maximumFractionDigits: 2 }) + " " + (currency || "EUR");
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

  /** Read a field from the item at the top level or, as a fallback, from item.data. */
  function itemField(item, name) {
    if (!item) return undefined;
    if (item[name] !== undefined && item[name] !== null) return item[name];
    if (item.data && typeof item.data === "object" && item.data[name] !== undefined && item.data[name] !== null) {
      return item.data[name];
    }
    return undefined;
  }

  /** Dictionary of human-readable labels for object keys. */
  var SHARE_HUMAN_LABELS = {
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

  var SHARE_KEY_ORDER = {
    location: ["address", "postal_code", "locality", "city", "municipality", "province", "region", "country"],
    possession: ["status", "documented_delivery_free"],
    availability: ["status", "verification", "checked_at"],
    generic: ["label", "name", "title", "value", "text", "status", "type", "code"],
  };

  function sharePossessionLabel(value) {
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

  function shareAvailabilityLabel(value) {
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

  /**
   * Convert an arbitrary value to a human-readable string, avoiding
   * "[object Object]". Follows the same approach as humanValue in app.js.
   */
  function shareHumanValue(value, opts) {
    if (value === null || value === undefined) return null;
    var maxDepth = (opts && typeof opts.maxDepth === "number") ? opts.maxDepth : 3;
    var kind = (opts && opts.kind) || null;

    if (typeof value === "string") {
      var trimmed = value.trim();
      if (!trimmed) return null;
      if (/^\d{4}-\d{2}-\d{2}T/.test(trimmed)) {
        var d = new Date(trimmed);
        if (!isNaN(d.getTime())) {
          if (window.AuctionRadarUI && typeof window.AuctionRadarUI.formatDateTime === "function") {
            return window.AuctionRadarUI.formatDateTime(trimmed);
          }
          try {
            return new Intl.DateTimeFormat("es-ES", { dateStyle: "long", timeStyle: "short" }).format(d);
          } catch (e) { return d.toLocaleString("es-ES"); }
        }
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
        var sub = shareHumanValue(value[i], { maxDepth: maxDepth - 1 });
        if (sub) parts.push(sub);
      }
      if (!parts.length) return null;
      return parts.join(", ");
    }

    if (typeof value === "object") {
      if (maxDepth <= 0) return null;
      var keys = SHARE_KEY_ORDER[kind] || SHARE_KEY_ORDER.generic;
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
        if (ak === "evidence" || ak === "raw" || ak === "source_url" || ak === "metadata"
          || ak === "path" || ak === "verification" || ak === "checked_at" || ak === "documented_delivery_free") continue;
        if (value[ak] === null || value[ak] === undefined || value[ak] === "") continue;
        orderedKeys.push(ak);
      }

      var segments = [];
      var statusTranslated = false;
      for (var j = 0; j < orderedKeys.length && segments.length < 4; j++) {
        var key = orderedKeys[j];
        var raw = value[key];
        if (key === "status" && (kind === "possession" || kind === "availability")) {
          var translated = (kind === "possession" ? sharePossessionLabel(raw) : shareAvailabilityLabel(raw));
          if (translated) { segments.push(translated); statusTranslated = true; continue; }
        }
        if (statusTranslated && kind === "availability") {
          if (key === "verification" || key === "checked_at") continue;
        }
        if (typeof raw === "boolean") {
          var lblKey = SHARE_HUMAN_LABELS[key] || key;
          segments.push(t(lblKey) + ": " + (raw ? t("Sí") : t("No")));
          continue;
        }
        var sub2 = shareHumanValue(raw, { maxDepth: maxDepth - 1 });
        if (sub2) {
          if(kind==='location'){if(segments.indexOf(sub2)<0)segments.push(sub2);}
          else if (SHARE_HUMAN_LABELS[key]) segments.push(t(SHARE_HUMAN_LABELS[key]) + ": " + sub2);
          else segments.push(sub2);
        }
      }
      if (!segments.length) return null;
      return segments.join(" · ");
    }

    return null;
  }

  function assetLabel(a) {
    return window.AuctionRadarUI.assetLabel(a);
  }

  function getTokenFromPath() {
    var path = window.location.pathname || "";
    var m = path.match(/^\/share\/([A-Za-z0-9_\-\.]+)/);
    if (m) return m[1];
    var qs = new URLSearchParams(window.location.search);
    return qs.get("token") || "";
  }

  function appendFact(parent, label, value) {
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
      v.textContent = t("Sin dato publicado");
    } else {
      v.textContent = String(value);
    }
    fact.appendChild(v);
    parent.appendChild(fact);
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
      var e2 = document.createElement("div");
      e2.className = "gallery-empty";
      e2.textContent = t("No hay fotos válidas del anuncio original.");
      parent.appendChild(e2);
    }
  }

  function appendLocationFact(parent,location) {
    var label=shareHumanValue(location,{kind:'location'});
    appendFact(parent,t('Ubicación'),label);
    var link=window.AuctionRadarUI.mapsLink(location,label);
    if(link)parent.lastElementChild.querySelector('.fact-value').replaceChildren(link);
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
    var valid = [];
    var idx = Math.max(0, startIndex || 0);
    if (Array.isArray(photos)) {
      for (var i = 0; i < photos.length; i++) {
        var u = safeHttpUrl(photos[i] && photos[i].url);
        if (u) valid.push({ url: u, caption: (photos[i] && photos[i].caption) || "" });
      }
    }
    if (!valid.length) return;
    lightboxState.photos = valid;
    lightboxState.lastFocus = document.activeElement;
    lb.hidden = false;
    lb.classList.add("open");
    var shell = document.querySelector('.share-shell');
    if (shell) shell.inert = true;
    document.body.classList.add('body-locked');
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
    var shell = document.querySelector('.share-shell');
    if (shell) shell.inert = false;
    document.body.classList.remove('body-locked');
    if (img) img.src = "";
    lightboxState.photos = [];
    lightboxState.index = 0;
    if (lightboxState.lastFocus && typeof lightboxState.lastFocus.focus === "function") {
      try { lightboxState.lastFocus.focus(); } catch (e) {}
    }
    lightboxState.lastFocus = null;
  }

  function lightboxPrev() { if (lightboxVisible()) setLightboxImage(lightboxState.index - 1); }
  function lightboxNext() { if (lightboxVisible()) setLightboxImage(lightboxState.index + 1); }
  function lightboxFirst() { if (lightboxVisible()) setLightboxImage(0); }
  function lightboxLast() { if (lightboxVisible()) setLightboxImage(lightboxState.photos.length - 1); }

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

  function isEditor(node) {
    if (!node) return false;
    if (node.isContentEditable) return true;
    var tag = node.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    return false;
  }

  function handleShareKeydown(e) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "Escape") {
      if (lightboxVisible()) { closeLightbox(); e.preventDefault(); return; }
    }
    if (!lightboxVisible()) return;
    if (e.key === "ArrowLeft") { lightboxPrev(); e.preventDefault(); return; }
    if (e.key === "ArrowRight") { lightboxNext(); e.preventDefault(); return; }
    if (e.key === "Home") { lightboxFirst(); e.preventDefault(); return; }
    if (e.key === "End") { lightboxLast(); e.preventDefault(); return; }
  }

  var lastItem = null;

  function renderItem(item) {
    lastItem = item;
    var title = document.querySelector("[data-share-title]");
    if (title) title.textContent = item.title || item.id || t("Oportunidad compartida");

    var badges = document.querySelector("[data-share-badges]");
    if (badges) {
      badges.innerHTML = "";
      function chip(tt) {
        var c = document.createElement("span");
        c.className = "chip";
        c.textContent = tt;
        badges.appendChild(c);
      }
      chip(assetLabel(item));
      if (item.source_id) chip(t("Fuente: ") + item.source_id);
      var locShare = shareHumanValue(itemField(item, "location"), { kind: "location" });
      if (locShare) {var locationLink=window.AuctionRadarUI.mapsLink(itemField(item,'location'),locShare);if(locationLink)badges.appendChild(locationLink);else chip(locShare);}
    }

    renderGallery(document.querySelector("[data-share-gallery]"), item.photos || []);
    window.AuctionRadarUI.renderPhotoNotice(document.querySelector('[data-share-gallery]'),item.photo_notice);

    var facts = document.querySelector("[data-share-facts]");
    if (facts) {
      facts.innerHTML = "";
      if (item.asset_type === "vehicles") {
        appendLocationFact(facts,itemField(item,'location'));
        appendFact(facts, t("Marca"), itemField(item, "make"));
        appendFact(facts, t("Modelo"), itemField(item, "model"));
        appendFact(facts, t("Año"), formatNumber(itemField(item, "year")));
        appendFact(facts, t("Kilometraje"), formatNumber(itemField(item, "mileage_km"), "km"));
        appendFact(facts, t("Combustible"), itemField(item, "fuel"));
      } else if (['housing','commercial','land'].indexOf(item.asset_type)>=0) {
        appendLocationFact(facts,itemField(item,'location'));
        appendFact(facts, t("Superficie construida"), formatNumber(itemField(item, "built_area_m2"), "m²"));
        appendFact(facts, t("Habitaciones"), formatNumber(itemField(item, "bedrooms")));
        appendFact(facts, t("Baños"), formatNumber(itemField(item, "bathrooms")));
        appendFact(facts, t("Año construcción"), formatNumber(itemField(item, "construction_year")));
        appendFact(facts, t("Planta"), itemField(item, "floor"));
        appendFact(facts, t("Ascensor"), itemField(item, "elevator"));
      } else {
        appendFact(facts,t('Categoría'),assetLabel(item));
        appendLocationFact(facts,itemField(item,'location'));
        appendFact(facts,t('Marca'),itemField(item,'make'));
        appendFact(facts,t('Modelo'),itemField(item,'model'));
        if(item.asset_type==='boats') {
          appendFact(facts,t('Eslora'),formatNumber(itemField(item,'length_m'),'m'));
          appendFact(facts,t('Manga'),formatNumber(itemField(item,'beam_m'),'m'));
          appendFact(facts,t('Horas de motor'),formatNumber(itemField(item,'engine_hours'),'h'));
        }
      }
      appendFact(facts, t("Tipo de importe"), priceTypeLabel(item.price_type));
      var prices = item.prices || itemField(item, "prices") || {};
      var best = null;
      ["asking", "starting", "auction_value", "bid", "reserve", "award", "appraisal"].forEach(function (k) {
        if (best === null && prices[k] !== null && prices[k] !== undefined && !isNaN(prices[k])) best = prices[k];
      });
      var currency = item.currency || itemField(item, "currency") || "EUR";
      appendFact(facts, t("Importe publicado"), best === null ? null : formatMoney(best, currency));
      appendFact(facts, t("Estado de posesión"), shareHumanValue(itemField(item, "possession"), { kind: "possession" }));
    }

    renderDocs(document.querySelector("[data-share-docs]"), item.documents || []);

    var linkRegion = document.querySelector("[data-share-source-region]");
    var link = document.querySelector("[data-share-source-link]");
    if (linkRegion && link) {
      var safe = safeHttpUrl(item.url);
      if (safe) {
        link.href = safe;
        link.textContent = t("Abrir ficha original");
        linkRegion.hidden = false;
      } else {
        linkRegion.hidden = true;
      }
    }

    var tokenLabel = document.querySelector("[data-share-token-label]");
    if (tokenLabel) {
      tokenLabel.textContent = t("Enlace de solo lectura");
    }

    var content = document.querySelector("[data-share-content]");
    if (content) content.hidden = false;
    var loading = document.querySelector('[data-share-state="loading"]');
    if (loading) loading.hidden = true;
    var error = document.querySelector('[data-share-state="error"]');
    if (error) error.hidden = true;
  }

  function onLanguageChange() {
    if (window.AuctionRadarUI && typeof window.AuctionRadarUI.applyI18n === "function") {
      window.AuctionRadarUI.applyI18n(document);
    }
    if (lastItem) {
      renderItem(lastItem);
      if (lightboxVisible()) {
        lightboxState.lastFocus = document.querySelectorAll('[data-share-gallery] .gallery-thumb')[lightboxState.index] || document.querySelector('[data-share-gallery] .gallery-thumb');
        setLightboxImage(lightboxState.index);
      }
    } else {
      var title = document.querySelector("[data-share-title]");
      if (title && title.textContent) title.textContent = t("Cargando oportunidad…");
    }
  }

  function showError(message) {
    var loading = document.querySelector('[data-share-state="loading"]');
    if (loading) loading.hidden = true;
    var error = document.querySelector('[data-share-state="error"]');
    if (error) error.hidden = false;
    var msg = document.querySelector("[data-share-error-msg]");
    if (msg) msg.textContent = message || t("No se pudo cargar el enlace.");
    var title = document.querySelector("[data-share-title]");
    if (title) title.textContent = t("Enlace no disponible");
  }

  function loadPublicItem() {
    var token = getTokenFromPath();
    if (!token) {
      showError(t("Falta el identificador del enlace."));
      return;
    }
    fetch("/api/public/" + encodeURIComponent(token), {
      method: "GET",
      credentials: "omit",
      headers: { Accept: "application/json" },
    }).then(function (resp) {
      if (resp.status === 401 || resp.status === 403 || resp.status === 404 || resp.status === 410) {
        showError(t("El enlace ha caducado, fue revocado o no existe."));
        return;
      }
      if (!resp.ok) {
        showError(t("No se pudo cargar el enlace (HTTP {status}).", { status: resp.status }));
        return;
      }
      var contentType = resp.headers.get("content-type") || "";
      if (contentType.indexOf("application/json") === -1) {
        showError(t("La respuesta del servidor no es válida."));
        return;
      }
      return resp.json();
    }).then(function (data) {
      if (!data) return;
      if (data && data.item) {
        renderItem(data.item);
      } else {
        showError(t("El enlace no contiene datos."));
      }
    }).catch(function () {
      showError(t("No se pudo conectar con el servidor."));
    });
  }

  function init() {
    // Picker + theme
    if (window.AuctionRadarUI) {
      window.AuctionRadarUI.setupLanguagePicker();
      window.AuctionRadarUI.setupThemeToggle();
    }
    document.addEventListener("auctionradar:languagechange", onLanguageChange);

    var lb = document.querySelector("[data-lightbox]");
    if (lb) {
      var btn = lb.querySelector("[data-lightbox-close]");
      if (btn) btn.addEventListener("click", closeLightbox);
      var prev = lb.querySelector("[data-lightbox-prev]");
      if (prev) prev.addEventListener("click", function () { lightboxPrev(); });
      var next = lb.querySelector("[data-lightbox-next]");
      if (next) next.addEventListener("click", function () { lightboxNext(); });
      lb.addEventListener("click", function (e) { if (e.target === lb) closeLightbox(); });
    }
    document.addEventListener("keydown", handleShareKeydown);
    loadPublicItem();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
