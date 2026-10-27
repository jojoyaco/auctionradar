/* AuctionRadar · saved-searches.js
 * Standalone module for the "My searches" view (#suscripciones).
 *
 * Responsibilities:
 *  - List, create, edit, archive, and evaluate the user's saved searches.
 *  - Show recent alerts with paginated web polling (every 60s while visible).
 *  - Emit `auctionradar:select-search` when the user wants to filter the grid.
 *  - Re-render after `auctionradar:languagechange` or `auctionradar:categories-loaded`.
 *
 * It does not replace app.js: app.js remains the main module. This module
 * mounts in the template's empty container and registers its own
 * listeners. No external dependencies; vanilla JS.
 */
(function () {
  "use strict";

  // ----------------- Module state -----------------

  var moduleState = {
    searchPollHandle:null,
    searches: [],
    presets: [],
    aiConfig: null,
    loading: false,
    editing: null, // {mode:"create"|"edit", search:{}, error}
    notifications: [],
    notificationsPollHandle: null,
    notificationsLastFetch: 0,
    categoriesCatalog: [],
    badgeCount: 0,
  };

  function t(source, params) {
    if (window.AuctionRadarUI && typeof window.AuctionRadarUI.t === "function") {
      return window.AuctionRadarUI.t(source, params);
    }
    if (!params) return source;
    return String(source).replace(/\{(\w+)\}/g, function (_, k) {
      return Object.prototype.hasOwnProperty.call(params, k) ? String(params[k]) : "{" + k + "}";
    });
  }

  function setText(node, value) {
    if (!node) return;
    node.textContent = (value === null || value === undefined) ? "" : String(value);
  }

  // ----------------- DOM helpers -----------------

  function region() { return document.querySelector('[data-view="suscripciones"]'); }
  function listEl() { return document.querySelector('[data-searches-list]'); }
  function loadingEl() { return document.querySelector('[data-searches-state="loading"]'); }
  function errorEl() { return document.querySelector('[data-searches-state="error"]'); }
  function errorMsgEl() { return document.querySelector('[data-searches-error-msg]'); }
  function notifCard() { return document.querySelector('[data-searches-notifications-card]'); }
  function notifList() { return document.querySelector('[data-notifications-list]'); }
  function editorDrawer() { return document.getElementById("search-editor-drawer"); }
  function editorBackdrop() { return document.querySelector('[data-search-editor-backdrop]'); }
  function editorBody() { return document.querySelector('[data-search-editor-body]'); }
  function editorTitle() { return document.querySelector('[data-search-editor-title]'); }

  function getCsrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? (m.getAttribute("content") || "") : "";
  }

  function apiFetch(path, options) {
    var opts = options || {};
    var method = (opts.method || "GET").toUpperCase();
    var init = { method: method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (method !== "GET" && method !== "HEAD") {
      init.headers["Content-Type"] = "application/json";
      init.headers["X-CSRFToken"] = getCsrf();
    }
    if (opts.body !== undefined && opts.body !== null) {
      init.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
    }
    return fetch(path, init).then(function (resp) {
      var ct = resp.headers.get("content-type") || "";
      var isJson = ct.indexOf("application/json") !== -1;
      if (resp.status === 401) {
        return (isJson ? resp.json() : Promise.resolve({})).then(function (body) {
          if (window.location.pathname !== "/login") window.location.href = "/login";
          return Promise.reject({ status: 401, error: window.AuctionRadarUI.errorMessage("auth_required") });
        });
      }
      if (!resp.ok) {
        return (isJson ? resp.json() : Promise.resolve({})).then(function (body) {
          return Promise.reject({ status: resp.status, error: window.AuctionRadarUI.errorMessage((body && body.error) || ("HTTP " + resp.status)) });
        });
      }
      if (isJson) return resp.json();
      return resp.text();
    });
  }

  // ----------------- Renderizado -----------------

  function showLoading() {
    if (loadingEl()) loadingEl().hidden = false;
    if (errorEl()) errorEl().hidden = true;
  }
  function showError(msg) {
    if (loadingEl()) loadingEl().hidden = true;
    if (errorEl()) errorEl().hidden = false;
    if (errorMsgEl()) errorMsgEl().textContent = msg || t("No se pudieron cargar las búsquedas guardadas.");
  }
  function showContent() {
    if (loadingEl()) loadingEl().hidden = true;
    if (errorEl()) errorEl().hidden = true;
  }

  function renderSearches() {
    var templates=document.querySelector('[data-search-presets]');
    if (templates) {
      templates.replaceChildren();
      moduleState.presets.forEach(function(preset){var button=document.createElement('button');button.type='button';button.className='btn btn-ghost';button.textContent=window.AuctionRadarUI.language()==='en'?(preset.label_en || preset.name):(preset.label_es || preset.name);button.addEventListener('click',function(){openEditor({mode:'create',preset:preset});});templates.appendChild(button);});
    }
    var list = listEl();
    if (!list) return;
    list.innerHTML = "";
    if (!moduleState.searches.length) {
      var empty = document.createElement("div");
      empty.className = "state";
      var p = document.createElement("p");
      p.className = "state-title";
      p.textContent = t("No tienes búsquedas guardadas todavía.");
      empty.appendChild(p);
      var create = document.createElement("button");
      create.type = "button";
      create.className = "btn btn-primary mt-md";
      create.textContent = t("Crear primera búsqueda");
      create.addEventListener("click", function () { openEditor({ mode: "create" }); });
      empty.appendChild(create);
      list.appendChild(empty);
      return;
    }
    moduleState.searches.forEach(function (s) {
      list.appendChild(buildSearchRow(s));
    });
  }

  function buildSearchRow(search) {
    var row = document.createElement("article");
    row.className = "search-row";
    row.setAttribute("data-search-id", String(search.id));
    row.setAttribute("data-search-active", search.active ? "true" : "false");

    var head = document.createElement("header");
    head.className = "search-row-head";
    var title = document.createElement("h3");
    title.className = "search-row-title";
    title.textContent = search.name || t("Búsqueda sin nombre");
    head.appendChild(title);
    var tags = document.createElement("div");
    tags.className = "search-row-tags";
    if (search.active) {
      tags.appendChild(chip(t("Activa"), "chip-status-ok"));
    } else {
      tags.appendChild(chip(t("Inactiva"), "chip-status-pending"));
    }
    if (search.notifications_enabled) {
      tags.appendChild(chip(t("Avisos activos"), "chip-status-info"));
    } else {
      tags.appendChild(chip(t("Avisos desactivados"), "chip-status-pending"));
    }
    if (search.ai_enabled) {
      var aiTxt = moduleState.aiConfig && moduleState.aiConfig.configured
        ? t("IA activada")
        : t("Pendiente de configurar");
      tags.appendChild(chip(aiTxt, "chip-status-info"));
    }
    head.appendChild(tags);
    row.appendChild(head);

    var summary = document.createElement("p");
    summary.className = "search-row-summary text-small";
    var parts = [];
    var crit = search.criteria || {};
    if (Array.isArray(crit.categories) && crit.categories.length) {
      parts.push(crit.categories.length + " " + t("Categorías"));
    }
    if (Array.isArray(crit.regions) && crit.regions.length) {
      parts.push(crit.regions.length + " " + t("Ubicaciones estrictas (opcional)"));
    } else if (Array.isArray(crit.preferred_regions) && crit.preferred_regions.length) {
      parts.push(crit.preferred_regions.length + " " + t("Ubicaciones preferidas"));
    }
    // Maximum published amount: use the compact text "Total máx. 130.000€"
    // without repeating the long "(EUR, opcional)" suffix in every row.
    if (typeof crit.all_in_max === "number") {
      parts.push(t("Coste total máx.") + " " + formatCompactEur(crit.all_in_max));
    } else if (typeof crit.price_max === "number") {
      parts.push(t("Total máx.") + " " + formatCompactEur(crit.price_max));
    }
    if (typeof search.match_count === "number") {
      parts.push(t("Resultados coincidentes: {n}", { n: search.match_count }));
    }
    if (search.last_checked_at) {
      parts.push(t("Última evaluación: {when}", { when: formatDateTime(search.last_checked_at) || "—" }));
    } else if (search.ai_enabled) {
      parts.push(t("Sin evaluar"));
    }
    summary.textContent = parts.length ? parts.join(" · ") : t("Valores iniciales");
    if(search.ai_enabled){var states={queued:'En cola',running:'Evaluación en curso',ready:'Evaluación completada',partial:'Evaluación parcial',pending:'Pendiente de revisión',unavailable:'IA no disponible',error:'No se pudo evaluar'};summary.textContent+=' · '+t(states[search.ai_status]||'Sin evaluar');}
    if(search.ai_enabled && search.evaluation_counts && search.evaluation_counts.pending){summary.textContent+=' · '+t('{n} evaluaciones pendientes; continúa ahora o en la próxima búsqueda.',{n:search.evaluation_counts.pending});}
    row.appendChild(summary);

    var actions = document.createElement("div");
    actions.className = "search-row-actions";

    // Actions have an explicit hierarchy. The primary action ("Ver resultados") takes
    // most of the row; Archivar is a destructive secondary action and should be less prominent.
    var selectBtn = document.createElement("button");
    selectBtn.type = "button";
    selectBtn.className = "btn btn-primary";
    selectBtn.textContent = t("Ver resultados");
    selectBtn.addEventListener("click", function () {
      document.dispatchEvent(new CustomEvent("auctionradar:select-search", {
        detail: { id: search.id, name: search.name }, bubbles: true
      }));
      apiFetch('/api/searches/'+encodeURIComponent(search.id)+'/read',{method:'POST',body:{}}).then(function(){loadNotifications(true);}).catch(function(){});
    });
    actions.appendChild(selectBtn);

    var evalBtn = document.createElement("button");
    evalBtn.type = "button";
    evalBtn.className = "btn";
    evalBtn.textContent = t("Reevaluar");
    evalBtn.addEventListener("click", function () { evaluateSearch(search.id); });
    actions.appendChild(evalBtn);

    var editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "btn btn-ghost btn-icon-only";
    editBtn.setAttribute("aria-label", t("Editar"));
    editBtn.setAttribute("title", t("Editar"));
    var editIcon = document.createElement("span");
    editIcon.className = "icon-svg";
    editIcon.setAttribute("aria-hidden", "true");
    editIcon.innerHTML = '<svg viewBox="0 0 16 16" focusable="false"><path d="M11.5 1.5l3 3-8.5 8.5H3v-3l8.5-8.5zm.7 1.2L10.4 4.5l1.1 1.1 1.8-1.8-1.1-1.1zM4 13h2l6-6-1.1-1.1L4 11.9V13z" fill="currentColor"/></svg>';
    editBtn.appendChild(editIcon);
    editBtn.addEventListener("click", function () { openEditor({ mode: "edit", search: search }); });
    actions.appendChild(editBtn);

    var archiveBtn = document.createElement("button");
    archiveBtn.type = "button";
    archiveBtn.className = "btn btn-danger btn-icon-only";
    archiveBtn.setAttribute("aria-label", t("Archivar"));
    archiveBtn.setAttribute("title", t("Archivar"));
    var archIcon = document.createElement("span");
    archIcon.className = "icon-svg";
    archIcon.setAttribute("aria-hidden", "true");
    archIcon.innerHTML = '<svg viewBox="0 0 16 16" focusable="false"><path d="M3 3h10v1H3V3zm1.5 2.5h7L11 14H5l-.5-8.5zM6 6v6h1V6H6zm3 0v6h1V6H9z" fill="currentColor"/></svg>';
    archiveBtn.appendChild(archIcon);
    archiveBtn.addEventListener("click", function () { archiveSearch(search.id); });
    actions.appendChild(archiveBtn);

    row.appendChild(actions);
    return row;
  }

  // Compact amount format: "130.000 €" (es-ES separator, no
  // decimals for whole numbers; trim the unit so the currency symbol
  // aligns with other amounts in the list).
  function formatCompactEur(value) {
    var n = Number(value);
    if (!isFinite(n)) return String(value);
    try {
      return new Intl.NumberFormat((window.AuctionRadarUI && window.AuctionRadarUI.locale) ? window.AuctionRadarUI.locale() : "es-ES", {
        style: "currency",
        currency: "EUR",
        maximumFractionDigits: 0,
      }).format(n);
    } catch (e) {
      return n.toLocaleString("es-ES", { maximumFractionDigits: 0 }) + " €";
    }
  }

  function chip(text, modifier) {
    var c = document.createElement("span");
    c.className = "chip" + (modifier ? " " + modifier : "");
    c.textContent = text;
    return c;
  }

  function formatDateTime(value) {
    if (!value) return null;
    var d = new Date(value);
    if (isNaN(d.getTime())) return null;
    try {
      return new Intl.DateTimeFormat((window.AuctionRadarUI && window.AuctionRadarUI.locale) ? window.AuctionRadarUI.locale() : "es-ES", {
        dateStyle: "medium", timeStyle: "short",
      }).format(d);
    } catch (e) {
      return d.toLocaleString();
    }
  }

  function setBadge(count) {
    moduleState.badgeCount = count;
    var node = document.querySelector('[data-nav-badge="suscripciones"]');
    if (!node) return;
    if (count && count > 0) {
      node.textContent = count > 99 ? "99+" : String(count);
      node.hidden = false;
    } else {
      node.hidden = true;
    }
  }

  // ----------------- Carga principal -----------------

  function loadSearches() {
    showLoading();
    apiFetch("/api/searches").then(function (resp) {
      moduleState.searches = (resp && resp.searches) || [];
      if (moduleState.searchPollHandle) clearTimeout(moduleState.searchPollHandle);
      if (moduleState.searches.some(function(s){return s.ai_status==='queued'||s.ai_status==='running';})) {
        moduleState.searchPollHandle=setTimeout(function(){if(document.visibilityState==='visible')loadSearches();},5000);
      }
      moduleState.presets = (resp && resp.presets) || [];
      moduleState.aiConfig = (resp && resp.ai) || moduleState.aiConfig;
      var unread = moduleState.searches.reduce(function (acc, s) {
        return acc + (typeof s.unread_count === "number" ? s.unread_count : 0);
      }, 0);
      setBadge(unread);
      renderSearches();
      showContent();
      // Notify app.js so it can synchronize the "Búsqueda guardada" selector.
      document.dispatchEvent(new CustomEvent("auctionradar:searches-loaded", {
        detail: { searches: moduleState.searches, ai: moduleState.aiConfig }, bubbles: true
      }));
      // Load alerts immediately if the view is visible.
      if (region() && !region().hidden) {
        loadNotifications(true);
      }
    }).catch(function (err) {
      showError(t((err && err.error) === "not_found" ? "Búsqueda no encontrada." : "No se pudieron cargar las búsquedas guardadas."));
    });
  }

  function loadNotifications(force) {
    if (!notifList()) return;
    var now = Date.now();
    if (!force && moduleState.notificationsLastFetch && (now - moduleState.notificationsLastFetch) < 30000) {
      return;
    }
    moduleState.notificationsLastFetch = now;
    apiFetch("/api/notifications").then(function (resp) {
      moduleState.notifications = (resp && resp.notifications) || [];
      var unread = (resp && typeof resp.unread_count === "number") ? resp.unread_count : moduleState.notifications.length;
      setBadge(unread);
      renderNotifications();
      if (notifCard()) notifCard().hidden = false;
    }).catch(function () {
      // No mostramos error de avisos: la vista principal sigue funcionando.
    });
  }

  function renderNotifications() {
    if (!notifList()) return;
    notifList().innerHTML = "";
    if (!moduleState.notifications.length) {
      var empty = document.createElement("p");
      empty.className = "text-small text-faint mb-0";
      empty.textContent = t("Sin avisos nuevos.");
      notifList().appendChild(empty);
      return;
    }
    var wrap = document.createElement("ul");
    wrap.className = "notifications-list-ul";
    moduleState.notifications.forEach(function (n) {
      var li = document.createElement("li");
      li.className = "notification-row";
      li.setAttribute("data-notification-id", String(n.id));
      if (!n.read_at) li.setAttribute("data-unread", "true");
      var title = document.createElement("strong");
      title.className = "text-small";
      title.textContent = n.item_title || t("Aviso: {title}", { title: n.search_name || "—" });
      li.appendChild(title);
      var meta = document.createElement("div");
      meta.className = "text-small text-faint";
      meta.textContent = (n.search_name || "—") + " · " + (formatDateTime(n.created_at) || "—");
      li.appendChild(meta);
      if (n.read_at) {
        var read = document.createElement("span");
        read.className = "chip chip-status-ok";
        read.textContent = t("Leído");
        li.appendChild(read);
      }
      // Mark as read on click.
      li.addEventListener("click", function () { markRead([n.id]);document.dispatchEvent(new CustomEvent('auctionradar:select-search',{detail:{id:n.search_id,name:n.search_name,item_id:n.item_id},bubbles:true})); });
      wrap.appendChild(li);
    });
    notifList().appendChild(wrap);
  }

  function markRead(ids) {
    if (!ids || !ids.length) return;
    apiFetch("/api/notifications/read", { method: "POST", body: { ids: ids } })
      .then(function () {
        // Refresh to update the UI and badge.
        moduleState.notificationsLastFetch = 0;
        loadNotifications(true);
      }).catch(function () { /* No optimistic UI update is applied. */ });
  }

  function markAllRead() {
    apiFetch("/api/notifications/read", { method: "POST", body: { all: true } })
      .then(function () {
        moduleState.notificationsLastFetch = 0;
        loadNotifications(true);
      }).catch(function () { /* swallow */ });
  }

  // ----------------- Search editor -----------------

  var editorState = {
    mode: "create",
    draft: null,
    backdrop: null,
    lastFocus: null,
  };

  function openEditor(opts) {
    opts = opts || {};
    var drawer = editorDrawer();
    var backdrop = editorBackdrop();
    if (!drawer || !backdrop) return;
    document.dispatchEvent(new CustomEvent('auctionradar:close-item'));
    editorState.mode = opts.mode || "create";
    editorState.lastFocus = document.activeElement;
    if (editorState.mode === "edit" && opts.search) {
      editorState.draft = JSON.parse(JSON.stringify(opts.search));
    } else {
      editorState.draft = freshDraft();
      if (opts.preset) {
        editorState.draft.name=window.AuctionRadarUI.language()==='en'?(opts.preset.label_en || opts.preset.name):(opts.preset.label_es || opts.preset.name);
        editorState.draft.criteria=JSON.parse(JSON.stringify(opts.preset.criteria));
        editorState.draft.prompt=(window.AuctionRadarUI.language()==='es' && opts.preset.prompt_es) || opts.preset.prompt || '';
        editorState.draft.ai_enabled=!!(moduleState.aiConfig && moduleState.aiConfig.configured);
      }
    }
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    backdrop.hidden = false;
    backdrop.classList.add("open");
    document.body.classList.add("body-locked");
    syncEditorInert();
    renderEditorBody();
    if (editorTitle()) {
      editorTitle().textContent = editorState.mode === "create" ? t("Nueva búsqueda") : t("Editar búsqueda");
    }
    // Mover foco al primer input focuseable.
    var first = drawer.querySelector("input, select, textarea, button:not([data-search-editor-close])");
    if (first) try { first.focus(); } catch (e) {}
  }

  function closeEditor() {
    var drawer = editorDrawer();
    var backdrop = editorBackdrop();
    if (!drawer || !backdrop) return;
    drawer.classList.remove("open");
    backdrop.classList.remove("open");
    backdrop.hidden = true;
    document.body.classList.remove("body-locked");
    syncEditorInert();
    editorState.draft = null;
    var restore=editorState.lastFocus&&editorState.lastFocus.isConnected?editorState.lastFocus:document.querySelector('[data-action="new-saved-search"]');
    if(restore&&restore.focus)try{restore.focus();}catch(e){}
    drawer.setAttribute("aria-hidden", "true");
    editorState.lastFocus = null;
  }

  function syncEditorInert() {
    document.dispatchEvent(new CustomEvent('auctionradar:modal-change'));
  }

  function freshDraft() {
    return {
      name: "",
      criteria: {
        categories: [],
        query: "",
        regions: [],
        preferred_regions: [],
        price_min: null,
        price_max: null,
        include_unknown_price: true,
        all_in_max: null,
        required_fields: [],
        source_ids: [],
      },
      prompt: "",
      ai_enabled: false,
      active: true,
      notifications_enabled: true,
    };
  }

  function renderEditorBody() {
    var body = editorBody();
    if (!body) return;
    body.innerHTML = "";

    var intro = document.createElement("p");
    intro.className = "text-small text-faint";
    intro.textContent = t("Editor de búsquedas guardadas");
    body.appendChild(intro);

    // Nombre
    body.appendChild(labelFor("name", t("Nombre")));
    var inputName = document.createElement("input");
    inputName.id='search-editor-name';
    inputName.type = "text";
    inputName.className = "input";
    inputName.required = true;
    inputName.minLength = 2;
    inputName.maxLength = 80;
    inputName.value = editorState.draft.name || "";
    inputName.addEventListener("input", function () {
      editorState.draft.name = inputName.value;
    });
    body.appendChild(inputName);

    // Categories
    body.appendChild(labelFor("categories", t("Categorías")));
    var categoriesField = renderCategoryCheckboxes(editorState.draft.criteria.categories || []);
    body.appendChild(categoriesField);

    // Texto libre
    body.appendChild(labelFor("query", t("Texto libre (título, ubicación)")));
    var qInput = document.createElement("input");
    qInput.id='search-editor-query';
    qInput.type = "text";
    qInput.className = "input";
    qInput.value = (editorState.draft.criteria && editorState.draft.criteria.query) || "";
    qInput.addEventListener("input", function () {
      editorState.draft.criteria.query = qInput.value;
    });
    body.appendChild(qInput);

    // Ubicaciones preferidas y estrictas
    body.appendChild(labelFor("regions-preferred", t("Ubicaciones preferidas")));
    var prefWrap = document.createElement("div");
    prefWrap.className = "regions-list";
    prefWrap.setAttribute("data-regions-preferred", "");
    var regionNames = collectRegionNames();
    var prefSelected = Array.isArray(editorState.draft.criteria.preferred_regions) ? editorState.draft.criteria.preferred_regions : [];
    regionNames.forEach(function (name) {
      var row = document.createElement("label");
      row.className = "checkbox-row";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = name;
      cb.checked = prefSelected.indexOf(name) >= 0;
      cb.addEventListener("change", function () {
        if (cb.checked) {
          if (editorState.draft.criteria.preferred_regions.indexOf(name) < 0) {
            editorState.draft.criteria.preferred_regions.push(name);
          }
        } else {
          editorState.draft.criteria.preferred_regions = editorState.draft.criteria.preferred_regions.filter(function (n) { return n !== name; });
        }
      });
      var span = document.createElement("span");
      span.textContent = name;
      row.appendChild(cb);
      row.appendChild(span);
      prefWrap.appendChild(row);
    });
    body.appendChild(prefWrap);

    body.appendChild(labelFor("regions-strict", t("Ubicaciones estrictas (opcional)")));
    var strictSel = document.createElement("select");
    strictSel.id='search-editor-regions-strict';
    strictSel.className = "select";
    strictSel.multiple = true;
    strictSel.size = 4;
    var strictSelected = Array.isArray(editorState.draft.criteria.regions) ? editorState.draft.criteria.regions : [];
    regionNames.forEach(function (name) {
      var o = document.createElement("option");
      o.value = name;
      o.textContent = name;
      o.selected = strictSelected.indexOf(name) >= 0;
      strictSel.appendChild(o);
    });
    strictSel.addEventListener("change", function () {
      editorState.draft.criteria.regions = Array.from(strictSel.selectedOptions).map(function (o) { return o.value; });
    });
    body.appendChild(strictSel);

    // Presupuesto y tope
    body.appendChild(labelFor("price_max", t("Importe publicado máximo (EUR)")));
    var priceMaxInput = document.createElement("input");
    priceMaxInput.id='search-editor-price_max';
    priceMaxInput.type = "number";
    priceMaxInput.className = "input";
    priceMaxInput.min = "0";
    priceMaxInput.max = "1000000000";
    priceMaxInput.step = "100";
    priceMaxInput.inputMode = "numeric";
    priceMaxInput.value = (typeof editorState.draft.criteria.price_max === "number") ? String(editorState.draft.criteria.price_max) : "";
    priceMaxInput.addEventListener("input", function () {
      var v = priceMaxInput.value;
      editorState.draft.criteria.price_max = (v === "" || v === null) ? null : Number(v);
    });
    body.appendChild(priceMaxInput);

    body.appendChild(labelFor("all_in_max", t("Coste total máximo (EUR, opcional)")));
    var allInMaxInput = document.createElement("input");
    allInMaxInput.id='search-editor-all_in_max';
    allInMaxInput.type = "number";
    allInMaxInput.className = "input";
    allInMaxInput.min = "0";
    allInMaxInput.max = "1000000000";
    allInMaxInput.step = "100";
    allInMaxInput.inputMode = "numeric";
    allInMaxInput.value = (typeof editorState.draft.criteria.all_in_max === "number") ? String(editorState.draft.criteria.all_in_max) : "";
    allInMaxInput.addEventListener("input", function () {
      var v = allInMaxInput.value;
      editorState.draft.criteria.all_in_max = (v === "" || v === null) ? null : Number(v);
    });
    body.appendChild(allInMaxInput);

    body.appendChild(labelFor("include_unknown_price", t("Incluir anuncios sin precio publicado")));
    var unkCb = document.createElement("label");
    unkCb.className = "checkbox-row";
    var unkInput = document.createElement("input");
    unkInput.id='search-editor-include_unknown_price';
    unkInput.type = "checkbox";
    unkInput.checked = editorState.draft.criteria.include_unknown_price !== false;
    unkInput.addEventListener("change", function () {
      editorState.draft.criteria.include_unknown_price = !!unkInput.checked;
    });
    var unkSpan = document.createElement("span");
    unkSpan.textContent = t("Incluir avisos sin precio");
    unkCb.appendChild(unkInput);
    unkCb.appendChild(unkSpan);
    body.appendChild(unkCb);

    // Prompt IA
    body.appendChild(labelFor("prompt", t("Prompt de IA")));
    var ta = document.createElement("textarea");
    ta.id='search-editor-prompt';
    ta.className = "textarea";
    ta.rows = 4;
    ta.maxLength = 6000;
    ta.placeholder = t("Sin instrucciones de IA. La búsqueda usa los criterios sin filtrado adicional.");
    ta.value = editorState.draft.prompt || "";
    ta.addEventListener("input", function () {
      editorState.draft.prompt = ta.value;
    });
    body.appendChild(ta);

    // Toggle IA + avisos
    var aiLabel = document.createElement("label");
    aiLabel.className = "checkbox-row mt-md";
    var aiCb = document.createElement("input");
    aiCb.type = "checkbox";
    aiCb.checked = !!editorState.draft.ai_enabled;
    aiCb.addEventListener("change", function () {
      editorState.draft.ai_enabled = !!aiCb.checked;
      updateAiHint();
    });
    var aiSpan = document.createElement("span");
    aiSpan.textContent = t("Activar IA para esta búsqueda");
    aiLabel.appendChild(aiCb);
    aiLabel.appendChild(aiSpan);
    body.appendChild(aiLabel);

    var aiHint = document.createElement("p");
    aiHint.className = "text-small text-faint mt-0";
    aiHint.setAttribute("data-ai-hint", "");
    body.appendChild(aiHint);
    function updateAiHint() {
      var aiCfg = moduleState.aiConfig || (window.AuctionRadarUI ? null : null);
      if (!editorState.draft.ai_enabled) {
        aiHint.textContent = "";
        return;
      }
      if (moduleState.aiConfig && !moduleState.aiConfig.configured) {
        aiHint.textContent = t("IA pendiente de configurar");
      } else {
        aiHint.textContent = t("IA activada para esta búsqueda");
      }
    }
    updateAiHint();

    var notifLabel = document.createElement("label");
    notifLabel.className = "checkbox-row";
    var notifCb = document.createElement("input");
    notifCb.type = "checkbox";
    notifCb.checked = !!editorState.draft.notifications_enabled;
    notifCb.addEventListener("change", function () {
      editorState.draft.notifications_enabled = !!notifCb.checked;
    });
    var notifSpan = document.createElement("span");
    notifSpan.textContent = t("Avisos activos");
    notifLabel.appendChild(notifCb);
    notifLabel.appendChild(notifSpan);
    body.appendChild(notifLabel);

    var activeLabel = document.createElement("label");
    activeLabel.className = "checkbox-row";
    var activeCb = document.createElement("input");
    activeCb.type = "checkbox";
    activeCb.checked = editorState.draft.active !== false;
    activeCb.addEventListener("change", function () {
      editorState.draft.active = !!activeCb.checked;
    });
    var activeSpan = document.createElement("span");
    activeSpan.textContent = t("Activa");
    activeLabel.appendChild(activeCb);
    activeLabel.appendChild(activeSpan);
    body.appendChild(activeLabel);

    var saveRow = document.createElement("div");
    saveRow.className = "save-row mt-md";
    var saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "btn btn-primary";
    saveBtn.textContent = t("Guardar búsqueda");
    saveBtn.addEventListener("click", function () { submitSearch(); });
    saveRow.appendChild(saveBtn);
    body.appendChild(saveRow);

    // Editor error message
    var errBox = document.createElement("p");
    errBox.className = "alert alert-error mt-sm";
    errBox.setAttribute("data-search-editor-error", "");
    errBox.hidden = true;
    body.appendChild(errBox);
  }

  function labelFor(id, text) {
    var l = document.createElement("label");
    l.className = "field-label mt-md";
    l.htmlFor = "search-editor-" + id;
    l.textContent = text;
    return l;
  }

  function renderCategoryCheckboxes(selectedIds) {
    var wrap = document.createElement("div");
    wrap.className = "categories-list";
    var cats = moduleState.categoriesCatalog && moduleState.categoriesCatalog.length
      ? moduleState.categoriesCatalog
      : [{ id: "housing", label_es: "Vivienda" }];
    cats.forEach(function (c) {
      var row = document.createElement("label");
      row.className = "checkbox-row";
      var cb = document.createElement("input");
      cb.type = "checkbox";
      cb.value = c.id;
      cb.checked = selectedIds.indexOf(c.id) >= 0;
      cb.addEventListener("change", function () {
        var cur = editorState.draft.criteria.categories || [];
        if (cb.checked) {
          if (cur.indexOf(c.id) < 0) cur.push(c.id);
        } else {
          cur = cur.filter(function (x) { return x !== c.id; });
        }
        editorState.draft.criteria.categories = cur;
      });
      var span = document.createElement("span");
      span.textContent = window.AuctionRadarUI && window.AuctionRadarUI.language()==='en' ? (c.label_en || c.label_es || c.id) : (c.label_es || c.id);
      row.appendChild(cb);
      row.appendChild(span);
      wrap.appendChild(row);
    });
    return wrap;
  }

  function collectRegionNames() {
    var fromSettings = document.querySelectorAll('[data-regions-preferred] .checkbox-row');
    // If the static settings list has already been rendered (when Settings is opened),
    // reuse it. Otherwise, show the minimum Spain catalog.
    if (fromSettings && fromSettings.length) {
      var names = [];
      fromSettings.forEach(function (row) {
        var sp = row.querySelector("span");
        if (sp) names.push(sp.textContent);
      });
      return names;
    }
    return [
      "Andalucía", "Aragón", "Asturias", "Illes Balears", "Canarias",
      "Cantabria", "Castilla y León", "Castilla-La Mancha", "Cataluña",
      "Comunitat Valenciana", "Extremadura", "Galicia", "Comunidad de Madrid",
      "Región de Murcia", "Navarra", "País Vasco", "La Rioja", "Ceuta", "Melilla",
    ];
  }

  function showEditorError(msg) {
    var box = document.querySelector("[data-search-editor-error]");
    if (!box) return;
    box.textContent = msg || t("No se pudo guardar la búsqueda.");
    box.hidden = false;
  }
  function clearEditorError() {
    var box = document.querySelector("[data-search-editor-error]");
    if (!box) return;
    box.hidden = true;
    box.textContent = "";
  }

  function submitSearch() {
    clearEditorError();
    var draft = editorState.draft;
    if (!draft) return;
    if (!draft.name || draft.name.length < 2) {
      showEditorError(t("Búsqueda no configurada") + " — nombre obligatorio");
      return;
    }
    var body = {
      name: draft.name,
      criteria: draft.criteria,
      prompt: draft.prompt || "",
      ai_enabled: !!draft.ai_enabled,
      active: draft.active !== false,
      notifications_enabled: !!draft.notifications_enabled,
    };
    var method = "POST";
    var path = "/api/searches";
    if (editorState.mode === "edit" && draft.id) {
      method = "PATCH";
      path = "/api/searches/" + encodeURIComponent(draft.id);
    }
    apiFetch(path, { method: method, body: body })
      .then(function () {
        closeEditor();
        loadSearches();
      }).catch(function (err) {
        showEditorError(err && err.error ? err.error : t("No se pudo guardar la búsqueda."));
      });
  }

  function archiveSearch(id) {
    if (!id) return;
    if (!window.confirm(t("¿Archivar esta búsqueda?"))) return;
    apiFetch("/api/searches/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function () { loadSearches(); })
      .catch(function (err) {
        showEditorError(err && err.error ? err.error : t("No se pudo archivar la búsqueda."));
      });
  }

  function evaluateSearch(id) {
    if (!id) return;
    apiFetch("/api/searches/" + encodeURIComponent(id) + "/evaluate", {
      method: "POST", body: {}
    }).then(function () {
      // Fetch again to get the updated match_count.
      loadSearches();
    }).catch(function (err) {
      showEditorError(err && err.error ? err.error : t("No se pudo evaluar la búsqueda."));
    });
  }

  // ----------------- Polling de avisos (60s visible) -----------------

  function startNotificationsPolling() {
    if (moduleState.notificationsPollHandle) {
      clearInterval(moduleState.notificationsPollHandle);
      moduleState.notificationsPollHandle = null;
    }
    moduleState.notificationsPollHandle = setInterval(function () {
      if (document.visibilityState !== "visible") return;
      loadNotifications(false);
    }, 60000);
  }

  // ----------------- Bootstrap -----------------

  function setupEditorListeners() {
    var create=document.querySelector('[data-action="new-saved-search"]');
    if (create) create.addEventListener('click',function(){openEditor({mode:'create'});});
    document.addEventListener('auctionradar:close-search-editor',closeEditor);
    var closeBtn = document.querySelector("[data-search-editor-close]");
    if (closeBtn) closeBtn.addEventListener("click", closeEditor);
    var backdrop = editorBackdrop();
    if (backdrop) backdrop.addEventListener("click", closeEditor);
    var markAllBtn = document.querySelector('[data-action="mark-all-notifications"]');
    if (markAllBtn) markAllBtn.addEventListener("click", markAllRead);
    // Escape cierra el editor.
    document.addEventListener("keydown", function (e) {
      var drawer = editorDrawer();
      if (drawer && drawer.classList.contains("open")) {
        var help=document.querySelector('[data-shortcuts-dialog]');
        if (help && !help.hidden) return;
        if (e.key==='Escape') {closeEditor();e.preventDefault();e.stopImmediatePropagation();}
        if ((e.ctrlKey||e.metaKey) && e.key==='Enter') {submitSearch();e.preventDefault();e.stopImmediatePropagation();}
        if (e.key==='Tab') {
          var focusable=Array.from(drawer.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea,select,a[href]')).filter(function(el){return el.offsetParent!==null;});
          var first=focusable[0],last=focusable[focusable.length-1];
          if (first && ((e.shiftKey&&document.activeElement===first)||(!e.shiftKey&&document.activeElement===last))) {e.preventDefault();(e.shiftKey?last:first).focus();e.stopImmediatePropagation();}
        }
      }
    },true);
  }

  function onLanguageChange() {
    // Quick re-render: recreate free-text labels so they are translated.
    renderSearches();
    renderNotifications();
    if (editorDrawer() && editorDrawer().classList.contains("open") && editorState.draft) {
      var active=document.activeElement;var id=active && active.id;
      var selection=active && typeof active.selectionStart==='number'?[active.selectionStart,active.selectionEnd]:null;
      renderEditorBody();
      if (editorTitle()) editorTitle().textContent=t(editorState.mode==='create'?'Nueva búsqueda':'Editar búsqueda');
      var next=id && document.getElementById(id);if(next){next.focus();if(selection&&next.setSelectionRange)next.setSelectionRange(selection[0],selection[1]);}
    }
  }

  function onViewSuscripciones() {
    loadSearches();
  }

  function onCategoriesLoaded(evt) {
    if (evt && evt.detail && Array.isArray(evt.detail.categories)) {
      moduleState.categoriesCatalog = evt.detail.categories;
    }
    // If the editor is open, redraw the category checkboxes.
    if (editorDrawer() && editorDrawer().classList.contains("open") && editorState.draft) {
      renderEditorBody();
    }
  }

  function init() {
    setupEditorListeners();
    document.addEventListener("auctionradar:view-suscripciones", onViewSuscripciones);
    document.addEventListener("auctionradar:categories-loaded", onCategoriesLoaded);
    document.addEventListener("auctionradar:languagechange", onLanguageChange);
    startNotificationsPolling();
    // Load initially when the page loads (do not wait for the view to open).
    loadSearches();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
