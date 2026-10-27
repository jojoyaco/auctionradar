/* AuctionRadar · ui.js
 * Shared lightweight i18n + theme + helpers.
 * Loaded synchronously in <head> BEFORE the stylesheet to avoid FOUC.
 * Vanilla JS, no dependencies. Exposes a single AuctionRadarUI namespace.
 *
 * Re-rendering after language change: dispatches "auctionradar:languagechange" on
 * document. Listeners should rebuild text and re-bind any strings that
 * were captured at module-load time. Event does NOT clear textarea
 * drafts or focus; listeners must preserve them.
 */
(function (root) {
  "use strict";

  var STORAGE_LANG = "auctionradar.language";
  var STORAGE_THEME = "auctionradar.theme";
  var DEFAULT_LANG = "en";
  var SUPPORTED = { es: "es-ES", en: "en-GB" };

  // ----------------------------- Dictionary -----------------------------
  // Keys are the original Spanish source strings (as they appear in
  // templates or app.js). The values are the English translations with
  // {placeholder} tokens for parameter substitution.
  //
  // IMPORTANT: do not translate data coming from the backend (titles,
  // locations, notes, run reports). Only translate UI chrome.
  var DICT = {
    // --- Brand / nav ---
    "AuctionRadar": "AuctionRadar",
    "Subastas bajo seguimiento": "Auction watchlist",
    "Acceso al radar de subastas AuctionRadar.":
      "Sign in to the AuctionRadar watchlist.",
    "Vista pública de una oportunidad compartida desde AuctionRadar.":
      "Public view of an opportunity shared from the AuctionRadar.",
    "Dashboard de subastas para AuctionRadar.":
      "Auction dashboard for AuctionRadar.",
    "Accede para revisar las oportunidades de subastas detectadas en tu último recorrido.":
      "Sign in to review the auction opportunities detected on your latest run.",
    "Crear cuenta · AuctionRadar": "Create account · AuctionRadar",
    "Iniciar sesión · AuctionRadar": "Sign in · AuctionRadar",
    "Oportunidad compartida · AuctionRadar": "Shared opportunity · AuctionRadar",

    // --- Login ---
    "Iniciar sesión": "Sign in",
    "Accede para revisar las oportunidades de vivienda y furgonetas detectadas en tu último recorrido.":
      "Sign in to review the housing and van opportunities detected on your latest run.",
    "Usuario": "Username",
    "Contraseña": "Password",
    "Crear cuenta": "Create account",
    "Crea una cuenta para gestionar tu espacio privado de subastas.":
      "Create an account to manage your private auction workspace.",
    "La contraseña debe tener entre 12 y 128 caracteres.":
      "Password must be between 12 and 128 characters.",
    "El usuario debe tener entre 3 y 32 caracteres: letras, números, punto, guion o guion bajo.":
      "Username must be 3 to 32 characters: letters, numbers, period, hyphen or underscore.",
    "No se pudo crear la cuenta con esos datos.":
      "The account could not be created with those details.",
    "No se pudo crear la cuenta. Vuelve a intentarlo.":
      "The account could not be created. Please try again.",
    "Entrar": "Sign in",
    "Acceso restringido. Si necesitas una cuenta, pide al administrador que la cree desde Configuración.":
      "Restricted access. If you need an account, ask the administrator to create one from Settings.",
    "Credenciales no válidas.": "Invalid credentials.",
    "Demasiados intentos. Vuelve a intentarlo en unos minutos.":
      "Too many attempts. Try again in a few minutes.",

    // --- Sidebar ---
    "General": "General",
    "Administración": "Administration",
    "Sesión": "Session",
    "Oportunidades": "Opportunities",
    "Oportunidad": "Opportunity",
    "Búsquedas": "Runs",
    "Mis búsquedas": "My searches",
    "Configuración": "Settings",
    "Usuarios": "Users",
    "Cuenta": "Account",
    "Mi espacio": "My workspace",
    "Añade y activa al menos una fuente en Fuentes y accesos antes de buscar.": "Add and enable at least one source in Sources and access before searching.",
    "Búsquedas manuales": "Manual searches",
    "Búsquedas manuales. La programación automática no está habilitada.": "Manual searches. Automatic scheduling is not enabled.",
    "Fuentes y accesos": "Sources and access",
    "Mis notas privadas": "My private notes",
    "Tus subastas, decisiones y notas. Solo se comparte lo que tú elijas mediante un enlace.":
      "Your auctions, decisions and notes. Only what you choose to share through a link is shared.",
    "Configura tus fuentes para empezar": "Set up your sources to get started",
    "Administra las cuentas de la instancia. Cada usuario mantiene su espacio privado.":
      "Manage instance accounts. Each user keeps a private workspace.",
    "Menú": "Menu",
    "Navegación principal": "Main navigation",
    "Secciones": "Sections",
    "Saltar al contenido principal": "Skip to main content",
    "Cerrar sesión": "Sign out",
    "Administrador": "Administrator",
    "Usuario": "User",

    // --- Sections ---
    "Filtra por tipo de activo, decisión del equipo o cambios desde la última revisión.":
      "Filter by asset type, team decision, or changes since the last review.",
    "Define búsquedas guardadas con criterios, avisos y, opcionalmente, un prompt de IA por búsqueda.":
      "Define saved searches with criteria, alerts and, optionally, an AI prompt per search.",
    "Historial de recorridos y cola de ejecuciones. Lanza una nueva búsqueda para refrescar los datos.":
      "Run history and queue. Trigger a new search to refresh the data.",
    "Lanzar búsqueda": "Queue run",
    "Presupuestos, regiones preferidas, fuentes y límites de ejecución.":
      "Budgets, preferred regions, sources and execution limits.",
    "Crea cuentas para los revisores y desactiva cuentas que ya no necesites. No hay registro público.":
      "Create accounts for reviewers and deactivate accounts you no longer need. There is no public sign-up.",
    "Cambia tu contraseña cuando lo necesites. La sesión actual se mantiene hasta que cierres el navegador.":
      "Change your password whenever you need. The current session stays until you close the browser.",
    "Cambiar contraseña": "Change password",
    "Crear usuario": "Create user",

    // --- Toolbar ---
    "Filtros de oportunidades": "Opportunity filters",
    "Búsqueda guardada": "Saved search",
    "Todas las búsquedas": "All searches",
    "Filtra los resultados con los criterios de una búsqueda guardada.":
      "Filter the results using the criteria of a saved search.",
    "Tipo de activo": "Asset type",
    "Familia de activo": "Asset family",
    "Categoría": "Category",
    "Todas las categorías": "All categories",
    "Todas las familias": "All families",
    "Local comercial": "Commercial property",
    "Terreno": "Land",
    "Embarcaciones": "Boats",
    "Barcos": "Boats",
    "Maquinaria": "Machinery",
    "Electrónica": "Electronics",
    "Mobiliario": "Furniture",
    "Objetos de colección": "Collectibles",
    "Otros": "Other",
    "Sin clasificar": "Unclassified",
    "Vivienda": "Housing",
    "Vehículos": "Vehicles",
    "Viviendas y furgonetas": "Housing and vans",
    "Viviendas": "Housing",
    "Furgonetas": "Vans",
    "Ordenar por": "Sort by",
    "Actualizadas recientemente": "Recently updated",
    "Más nuevas": "Newest first",
    "Más antiguas": "Oldest first",
    "Precio: menor a mayor": "Price: low to high",
    "Precio: mayor a menor": "Price: high to low",
    "Puntuación IA": "AI score",
    "Veredicto IA": "AI verdict",
    "Todos los veredictos": "All verdicts",
    "Recomendadas": "Recommended",
    "Para revisar": "Needs review",
    "Descartadas": "Discarded",
    "Ubicación": "Location",
    "Ciudad, provincia…": "City, province…",
    "Con fotos": "With photos",
    "Estado": "Status",
    "Estado de revisión": "Review status",
    "Todos": "All",
    "Todas": "All",
    "Pendientes": "Pending",
    "Cambios desde revisión": "Changes since review",
    "Con cambios desde revisión": "With changes since review",
    "Buscar": "Search",
    "Título, ID, ubicación…": "Title, ID, location…",
    "Fuente": "Source",
    "Todas las fuentes": "All sources",
    "Con cambios desde revisión": "With changes since review",
    "Limpiar": "Clear",
    "Actualizar": "Refresh",
    "Pendientes: —": "Pending: —",
    "OK: —": "OK: —",
    "KO: —": "KO: —",
    "Cambios: —": "Changes: —",
    "Última actualización:": "Last updated:",
    "Última actualización: —": "Last updated: —",
    "Última actualización: ": "Last updated: ",
    "Último recorrido: —": "Last run: —",
    "Último recorrido: ": "Last run: ",
    "reg.": "records",
    "Hora de la última ingestión": "Last ingestion time",
    "Hora de la última ingestión:": "Last ingestion time:",
    "Sin datos disponibles": "No data available",

    // --- Cards / list ---
    "Cargando oportunidades…": "Loading opportunities…",
    "Cargar más": "Load more",
    "Reintentar": "Retry",
    "Mostrando {loaded} de {total} resultados": "Showing {loaded} of {total} results",
    "Sin resultados": "No results",
    "No hay oportunidades que coincidan con los filtros activos. Prueba a limpiar o cambiar el estado.":
      "No opportunities match the active filters. Try clearing or changing the status.",
    "Todavía no se han registrado oportunidades. Lanza una búsqueda para empezar.":
      "No opportunities recorded yet. Queue a search to get started.",
    "No se pudieron cargar las oportunidades": "Could not load the opportunities",
    "Inténtalo de nuevo en unos segundos.": "Try again in a few seconds.",
    "Página {page} de {pages} · {total} resultados":
      "Page {page} of {pages} · {total} results",
    "Anterior": "Previous",
    "Siguiente": "Next",
    "Vivienda": "Housing",
    "Furgoneta": "Van",
    "Pendiente": "Pending",
    "Pendiente de comprobar": "Pending review",
    "Candidato": "Candidate",
    "Excluido por criterios": "Excluded by criteria",
    "En cola": "Queued",
    "En curso": "Running",
    "Cobertura parcial": "Partial coverage",
    "Finalizada": "Completed",
    "Error": "Error",
    "Cancelada": "Cancelled",
    "Bloqueada": "Blocked",
    "Deshabilitada": "Disabled",
    "Elegibilidad sin acreditar": "Eligibility not accredited",
    "Requiere cuenta": "Requires sign-in",
    "No seleccionada": "Not selected",
    "No aplica al activo": "Not applicable to this asset",
    "Cambios": "Changes",
    "Cambio": "Change",
    "Excluido por criterios": "Excluded by criteria",
    "Fuente: ": "Source: ",
    "Visto ": "Seen ",
    "Sin fecha": "No date",
    "{n} foto": "{n} photo",
    "{n} fotos": "{n} photos",
    "Importe no publicado": "Price not published",
    "Precio a confirmar": "Price to be confirmed",
    "Sin foto": "No photo",
    "Vivienda sin fotos": "Housing without photos",
    "Furgoneta sin fotos": "Van without photos",
    "Sin fotos disponibles": "No photos available",
    "Foto {current} de {total}": "Photo {current} of {total}",

    // --- Drawer / detail ---
    "Cargando…": "Loading…",
    "Cargando detalle…": "Loading detail…",
    "Cargando oportunidad…": "Loading shared opportunity…",
    "Cargando oportunidad compartida…": "Loading shared opportunity…",
    "Detalle": "Detail",
    "Cerrar detalle": "Close detail",
    "Ver detalle de {title}": "View details of {title}",
    "No se encontró la oportunidad.": "Opportunity not found.",
    "No se pudo cargar el detalle.": "Could not load the detail.",
    "Galería": "Gallery",
    "Documentos": "Documents",
    "Datos publicados": "Published data",
    "Fuente original": "Original source",
    "Abrir anuncio en el portal de origen": "Open listing on the original portal",
    "Historial de revisión": "Review history",
    "Sin eventos de revisión registrados.": "No review events recorded.",
    "Decisión de revisión": "Review decision",
    "Marcar como OK": "Mark as OK",
    "Marcar como KO": "Mark as KO",
    "Notas del equipo": "Team notes",
    "Visibles para los usuarios; no se incluyen en el enlace de solo lectura":
      "Visible to users; not included in the read-only share link",
    "Guardar decisión": "Save decision",
    "Guardando…": "Saving…",
    "Decisión guardada.": "Decision saved.",
    "Sin revisión previa.": "No prior review.",
    "Última edición {when} {who}": "Last edited {when} {who}",
    "Última edición {when} · por {who}": "Last edited {when} · by {who}",
    "Última edición · por {who}": "Last edited · by {who}",
    "Última edición {when}": "Last edited {when}",
    "por {who}": "by {who}",
    "por ": "by ",
    "fecha desconocida": "unknown date",
    "Razones registradas": "Recorded reasons",

    // --- Quick actions ---
    "OK": "OK",
    "KO": "KO",
    "Marcar OK": "Mark OK",
    "Marcar KO": "Mark KO",
    "Cambios detectados en esta tarjeta.": "Changes detected on this card.",

    // --- Facts / prices ---
    "Sin dato publicado": "No published data",
    "Importes publicados": "Published prices",
    "Importe publicado": "Published price",
    "Importe principal": "Main price",
    "Tipo de importe": "Price type",
    "Estimación coste total": "Estimated total cost",
    "Precio anunciado": "Asking price",
    "Reserva": "Reserve",
    "Precio de salida": "Starting price",
    "Puja actual": "Current bid",
    "Puja mínima": "Minimum bid",
    "Puja": "Bid",
    "Valor de subasta": "Auction value",
    "Adjudicación": "Award",
    "Tasación": "Appraisal",
    "Depósito": "Deposit",
    "Ubicación": "Location",
    "Superficie construida": "Built area",
    "Superficie útil": "Usable area",
    "Habitaciones": "Bedrooms",
    "Baños": "Bathrooms",
    "Planta": "Floor",
    "Ascensor": "Elevator",
    "Año construcción": "Year built",
    "Estado físico": "Physical condition",
    "Reforma necesaria": "Renovation scope",
    "Posesión": "Possession",
    "Disponibilidad": "Availability",
    "Primera detección": "First seen",
    "Última detección": "Last seen",
    "Marca": "Make",
    "Modelo": "Model",
    "Año": "Year",
    "Kilometraje": "Mileage",
    "Combustible": "Fuel",
    "Tipo de cambio": "Transmission",
    "Documentación": "Documentation",
    "Estado mecánico": "Mechanical condition",
    "ITV": "MOT",
    "Cargas": "Charges",
    "Cuota IVA": "VAT status",
    "Comisión": "Fees",
    "Estado de posesión": "Possession status",
    "Estado de lectura: {status}": "Read status: {status}",
    "Documento": "Document",
    "PDF": "PDF",
    "DOC": "DOC",
    "Enlace": "Link",

    // --- Lightbox ---
    "Imagen ampliada": "Enlarged image",
    "Cerrar imagen": "Close image",
    "Cerrar": "Close",
    "Anterior foto": "Previous photo",
    "Siguiente foto": "Next photo",
    "Foto {n}": "Photo {n}",
    "Ampliar foto {n}": "Enlarge photo {n}",
    "Foto {current} de {total}": "Photo {current} of {total}",
    "Sin atajo": "No shortcut",
    "No hay fotos del anuncio original.": "No photos from the original listing.",
    "No hay fotos válidas del anuncio original.": "No valid photos from the original listing.",
    "Foto anterior": "Previous photo",
    "Foto siguiente": "Next photo",

    // --- Share ---
    "Vista pública. Sólo se muestran los campos publicados del anuncio original; las notas privadas y los datos de revisión no se incluyen.":
      "Public view. Only the published fields of the original listing are shown; private notes and review data are not included.",
    "Datos publicados por el portal de origen; esta página no constituye recomendación, tasación ni coste total.":
      "Data published by the source portal; this page is not a recommendation, appraisal or total cost.",
    "Token: {token}": "Token: {token}",
    "Token": "Token",
    "Enlace de solo lectura": "Read-only link",
    "Vehículo": "Vehicle", "Coche": "Car", "Moto": "Motorcycle", "Camión": "Truck",
    "Local comercial": "Commercial property", "Plaza de garaje": "Garage", "Terreno / Solar": "Land",
    "Embarcación": "Boat", "Maquinaria": "Machinery", "Electrónica": "Electronics",
    "Mobiliario": "Furniture", "Objeto de colección": "Collectible", "Otro bien": "Other asset",
    "Sin clasificar": "Unclassified", "Sin fotos publicadas": "No published photos",
    "Recomendadas y pendientes": "Recommended and pending",
    "Leído": "Read",
    "Importe publicado máximo (EUR)": "Maximum published amount (EUR)",
    "Coste total máximo (EUR, opcional)": "Maximum all-in cost (EUR, optional)",
    "Importe mínimo (EUR)": "Minimum amount (EUR)",
    "Importe máximo (EUR)": "Maximum amount (EUR)",
    "Eslora": "Length", "Manga": "Beam", "Horas de motor": "Engine hours",
    "Más filtros": "More filters", "Nombre": "Name", "Texto libre (título, ubicación)": "Search text (title, location)",
    "Recientes": "Updated",
    "Precio ↑": "Price ↑",
    "Precio ↓": "Price ↓",
    "Ver ubicación en Google Maps (nueva pestaña)": "View location in Google Maps (new tab)",
    "Datos insuficientes para IA": "Insufficient data for AI",
    "Guardar notas": "Save notes",
    "El estado se guarda al instante. Guarda las notas con el botón.": "Status changes save immediately. Use the button to save notes.",
    "Información adicional tras acceso al BOE": "Additional information requires BOE sign-in",
    "El BOE requiere iniciar sesión para consultar la información complementaria del bien. El radar no utiliza tu sesión del portal.": "BOE requires sign-in to access additional asset information. The radar does not use your portal session.",
    "Abrir información en el BOE": "Open information on BOE",
    "La fuente no proporciona una ficha individual verificable.": "The source does not provide a verifiable individual listing.",
    "La IA no ha citado datos publicados válidos para justificar su decisión.": "AI has not cited valid published facts to support its decision.",
    "El servicio de IA no está disponible temporalmente.": "The AI service is temporarily unavailable.",
    "La evaluación de IA ha superado el tiempo disponible.": "The AI evaluation exceeded its time limit.",
    "La IA no ha podido valorar esta ficha.": "AI could not assess this listing.",
    "IA desactivada": "AI disabled",
    "Buscar fuente": "Search sources",
    "Buscar fuente…": "Search sources…",
    "Incluir avisos sin precio": "Keep results with an unknown published price",
    "IA activada": "AI enabled", "Evaluación en curso": "Evaluation running", "En cola": "Queued",
    "Pendiente de revisión": "Pending review", "Evaluación parcial": "Partial evaluation", "Evaluación completada": "Evaluation complete",
    "{n} evaluaciones pendientes; continúa ahora o en la próxima búsqueda.": "{n} evaluations pending; continue now or on the next search.",
    "IA pendiente": "AI pending", "IA no disponible": "AI unavailable", "No se pudo evaluar": "Could not evaluate",
    "El modelo no ha señalado campos pendientes.": "The model has not reported missing fields.",
    "Datos que respaldan la evaluación": "Evidence supporting the evaluation",
    "La IA analiza datos publicados; no verifica el bien ni interpreta las fotos.": "AI analyzes published data; it does not verify the asset or interpret photos.",
    "Programación:": "Schedule:",
    "Preferencias": "Preferences",
    "Paginación": "Pagination",
    "Fuentes a consultar": "Sources to search",
    "Datos básicos": "Basic information",
    "Enlace al anuncio publicado:": "Link to the original listing:",
    "No se pudo cargar la lista.": "Could not load the list.",
    "Pide a quien te lo ha enviado un enlace nuevo o revisa que la URL esté completa.": "Ask the sender for a new link or check that the URL is complete.",
    "abrir ficha original": "open original listing",
    "La sesión del formulario ha caducado. Recarga la página e inténtalo de nuevo.": "The form session has expired. Reload the page and try again.",
    "Revisa los datos introducidos.": "Please check the values you entered.",
    "No se encontró el elemento solicitado.": "The requested item was not found.",
    "Las notas no pueden superar los 4000 caracteres.": "Notes cannot exceed 4,000 characters.",
    "La contraseña debe tener entre 12 y 1024 caracteres.": "The password must contain between 12 and 1,024 characters.",
    "La contraseña actual no es correcta.": "The current password is incorrect.",
    "Ese nombre de usuario ya está registrado.": "That username is already registered.",
    "No puedes desactivar tu propia cuenta.": "You cannot deactivate your own account.",
    "Debe quedar al menos una cuenta administradora activa.": "At least one administrator account must remain active.",
    "Ya hay una búsqueda en curso.": "A search is already running.",
    "No se pudo iniciar la búsqueda. Inténtalo de nuevo.": "The search could not be started. Try again.",
    "No se pudo completar la operación. Inténtalo de nuevo.": "The operation could not be completed. Try again.",
    "Enlace no disponible": "Link unavailable",
    "El enlace ha caducado, fue revocado o no existe.":
      "The link has expired, been revoked, or does not exist.",
    "No se pudo cargar el enlace (HTTP {status}).":
      "Could not load the link (HTTP {status}).",
    "La respuesta del servidor no es válida.": "The server response is not valid.",
    "El enlace no contiene datos.": "The link contains no data.",
    "No se pudo conectar con el servidor.": "Could not reach the server.",
    "Falta el identificador del enlace.": "The link identifier is missing.",
    "Abrir ficha original": "Open original listing",

    // --- Sharing block (drawer) ---
    "Compartir": "Share",
    "El enlace con token funciona en tu LAN/VPN y NO incluye tus notas privadas.":
      "The token link works on your LAN/VPN and does NOT include your private notes.",
    "Enlace al portal original": "Original portal link",
    "URL del anuncio tal como la publica la fuente.":
      "URL of the listing as published by the source.",
    "Copiar": "Copy",
    "¡Copiado!": "Copied!",
    "Copiado al portapapeles.": "Copied to clipboard.",
    "No hay valor para copiar.": "Nothing to copy.",
    "No se pudo copiar.": "Could not copy.",
    "WhatsApp": "WhatsApp",
    "Correo": "Email",
    "Enlace interno autenticado": "Authenticated internal link",
    "Quien lo abra necesitará cuenta. Útil para coordinarte con otro revisor.":
      "Recipients will need an account. Useful to coordinate with another reviewer.",
    "Enlace sin login (solo lectura)": "Sign-in-free link (read only)",
    "No requiere cuenta. Muestra fotos, datos básicos y documentos del anuncio.":
      "No account required. Shows photos, basic data and documents of the listing.",
    "Crear enlace": "Create link",
    "Enlaces públicos creados": "Created public links",
    "Activo": "Active",
    "Revisor": "Reviewer",
    "Revocado": "Revoked",
    "Revocar": "Revoke",
    "Recién creado": "Just created",
    "URL sólo visible al crear": "URL only visible when created",
    "Enlace de solo lectura creado para LAN/VPN.": "Read-only link created for LAN/VPN.",
    "Enlace revocado.": "Link revoked.",
    "No se pudo crear el enlace.": "Could not create the link.",
    "No se pudo revocar.": "Could not revoke the link.",

    // --- Settings ---
    "Vivienda": "Housing",
    "Vehículos": "Vehicles",
    "IA": "AI",
    "Avanzado": "Advanced",
    "Búsqueda automática": "Automatic search",
    "La programación de la búsqueda automática es global y solo puede modificarse desde la configuración del servidor.":
      "The automatic search schedule is global and can only be changed from the server configuration.",
    "El horario no es editable aquí. La próxima búsqueda usa la última configuración guardada.":
      "The schedule is not editable here. The next run uses the latest saved configuration.",
    "Horario": "Schedule",
    "Lunes a viernes, 07:00": "Monday to Friday, 07:00",
    "Presupuesto total (EUR)": "Total budget (EUR)",
    "Incluye precio, impuestos, gastos, reforma y financiación si aplica. Déjalo en blanco para no aplicar.":
      "Includes price, taxes, fees, renovation and financing if applicable. Leave blank to disable.",
    "Regiones preferidas": "Preferred regions",
    "Regiones estrictas (opcional)": "Strict regions (optional)",
    "La próxima búsqueda solo incorporará ubicaciones confirmadas en esas regiones. Las desconocidas quedan fuera; el historial previo se conserva.":
      "Next run will only include locations confirmed in those regions. Unknown ones are skipped; prior history is preserved.",
    "Furgonetas": "Vans",
    "Presupuesto (EUR)": "Budget (EUR)",
    "Kilometraje máximo": "Max mileage",
    "Fuentes": "Sources",
    "Fuentes activas": "Active sources",
    "Catálogo de fuentes no disponible.": "Source catalog is not available.",
    "(deshabilitada)": "(disabled)",
    "La configuración del proveedor y del modelo solo se modifica desde el servidor. Aquí se muestra el estado actual.":
      "Provider and model configuration can only be changed from the server. The current state is shown here.",
    "Indicaciones por búsqueda": "Prompts per search",
    "Edita el prompt y los criterios específicos desde Mis búsquedas.":
      "Edit the prompt and specific criteria from My searches.",
    "Abrir Mis búsquedas": "Open My searches",
    "IA pendiente de configurar": "AI pending configuration",
    "IA configurada": "AI configured",
    "habilitada": "enabled",
    "deshabilitada": "disabled",
    "Archivada": "Archived",
    "Límites avanzados": "Advanced limits",
    "Timeout (s)": "Timeout (s)",
    "Páginas máx. por fuente": "Max pages per source",
    "Fichas máx. por fuente": "Max listings per source",
    "Documentos máx. por ficha": "Max documents per listing",
    "Concurrencia (workers)": "Concurrency (workers)",
    "Duración máxima del recorrido (s)": "Maximum run duration (s)",
    "Límites técnicos de la ejecución. Solo cámbialos si entiendes el efecto en el rendimiento.":
      "Technical execution limits. Only change them if you understand the impact on performance.",
    "Guardar cambios": "Save changes",
    "Guardado para la próxima búsqueda. El historial se conserva.":
      "Saved for the next run. History is preserved.",
    "Configuración guardada.": "Settings saved.",
    "Cargando configuración…": "Loading settings…",
    "No se pudo cargar la configuración.": "Could not load settings.",

    // --- Users ---
    "Cargando usuarios…": "Loading users…",
    "Sin usuarios registrados.": "No users registered.",
    "Desactivado": "Disabled",
    "Reactivar": "Reactivate",
    "Desactivar": "Deactivate",
    "Usuario actualizado.": "User updated.",
    "Usuario y contraseña son obligatorios.": "Username and password are required.",
    "Usuario creado.": "User created.",
    "No se pudo crear el usuario.": "Could not create the user.",
    "No se pudo actualizar.": "Could not update.",
    "Crear": "Create",
    "Contraseña (mínimo 12)": "Password (minimum 12)",

    // --- Account ---
    "Contraseña actual": "Current password",
    "Nueva contraseña": "New password",
    "Repite la nueva contraseña": "Repeat the new password",
    "Actualizar contraseña": "Update password",
    "Completa los tres campos.": "Fill in all three fields.",
    "La nueva contraseña no coincide.": "The new password does not match.",
    "La nueva contraseña debe tener al menos 12 caracteres.":
      "The new password must have at least 12 characters.",
    "Contraseña actualizada. Vuelve a iniciar sesión.":
      "Password updated. Please sign in again.",
    "Sesión cerrada. Redirigiendo…": "Signed out. Redirecting…",
    "No se pudo guardar.": "Could not save.",

    // --- Saved searches (suscripciones) ---
    "Cargando búsquedas guardadas…": "Loading saved searches…",
    "No se pudieron cargar las búsquedas guardadas.": "Could not load the saved searches.",
    "No tienes búsquedas guardadas todavía.": "You don't have any saved searches yet.",
    "Crear primera búsqueda": "Create first search",
    "Nueva búsqueda": "New search",
    "Editar búsqueda": "Edit search",
    "Editar": "Edit",
    "Eliminar": "Delete",
    "Archivar": "Archive",
    "Archivada": "Archived",
    "Activar": "Activate",
    "Desactivar": "Deactivate",
    "Activa": "Active",
    "Inactiva": "Inactive",
    "Avisos activos": "Alerts active",
    "Avisos desactivados": "Alerts disabled",
    "Resultados coincidentes: {n}": "{n} matching results",
    "Última evaluación: {when}": "Last evaluation: {when}",
    "Sin evaluar": "Not evaluated yet",
    "Ver resultados": "View results",
    "Reevaluar": "Evaluate",
    "Total máx.": "Max total",
    "Coste total máx.": "Max all-in cost",
    "Quitar": "Remove",
    "Orden:": "Sort:",
    "IA:": "AI:",
    "Filtros activos": "Active filters",
    "Más categorías": "More categories",
    "Búsquedas guardadas": "Saved searches",
    "Limpiar filtros": "Clear filters",
    "Categoría:": "Category:",
    "Búsqueda:": "Search:",
    "Buscar:": "Search:",
    "Ubicación:": "Location:",
    "Fuente:": "Source:",
    "Familia:": "Family:",
    "Importe mín.": "Min amount",
    "Importe máx.": "Max amount",
    "Sin categoría": "No category",
    "Prompt de IA": "AI prompt",
    "Activar IA para esta búsqueda": "Enable AI for this search",
    "IA activada para esta búsqueda": "AI enabled for this search",
    "Sin instrucciones de IA. La búsqueda usa los criterios sin filtrado adicional.":
      "No AI instructions. The search uses the criteria without additional filtering.",
    "Criterios de la búsqueda": "Search criteria",
    "Categorías": "Categories",
    "Ubicaciones preferidas": "Preferred locations",
    "Ubicaciones estrictas (opcional)": "Strict locations (optional)",
    "Presupuesto total": "Total budget",
    "Tope por anuncio (opcional)": "Per-item cap (optional)",
    "Incluir anuncios sin precio publicado": "Include listings without a published price",
    "Campos requeridos": "Required fields",
    "Fuentes a consultar": "Sources to search",
    "Todas las fuentes disponibles": "All available sources",
    "Guardar búsqueda": "Save search",
    "Evaluación guardada.": "Evaluation saved.",
    "Búsqueda guardada.": "Search saved.",
    "Búsqueda actualizada.": "Search updated.",
    "Búsqueda archivada.": "Search archived.",
    "No se pudo guardar la búsqueda.": "Could not save the search.",
    "No se pudo actualizar la búsqueda.": "Could not update the search.",
    "No se pudo archivar la búsqueda.": "Could not archive the search.",
    "No se pudo evaluar la búsqueda.": "Could not evaluate the search.",
    "No se pudieron cargar los resultados.": "Could not load the results.",
    "Búsqueda no encontrada.": "Search not found.",
    "Inicia una evaluación ahora": "Run evaluation now",
    "Evaluación iniciada. Los resultados aparecerán al terminar.": "Evaluation started. Results will appear once it finishes.",
    "Ver resultados": "View results",
    "Selecciona esta búsqueda": "Select this search",
    "Resultados de la búsqueda": "Search results",
    "Búsqueda: {name}": "Search: {name}",
    "Valores iniciales": "Initial values",
    "Filtra los resultados con esta búsqueda guardada.": "Filter the results with this saved search.",
    "Avisos recientes": "Recent alerts",
    "Solo se muestran en la web. No se envían emails, ni a Telegram, ni se piden permisos del navegador.":
      "Shown in the web only. No emails, no Telegram, no browser permission prompts.",
    "Marcar todo como leído": "Mark all as read",
    "Sin avisos nuevos.": "No new alerts.",
    "Aviso: {title}": "Alert: {title}",
    "Cargando avisos…": "Loading alerts…",
    "No se pudieron cargar los avisos.": "Could not load the alerts.",
    "Recomendada por IA": "AI recommended",
    "Para revisar (IA)": "Needs review (AI)",
    "Descartada por IA": "Discarded by AI",
    "Veredicto: {verdict}": "Verdict: {verdict}",
    "Motivo: {reason}": "Reason: {reason}",
    "Campos que faltan: {fields}": "Missing fields: {fields}",
    "Sin campos pendientes.": "No fields pending.",
    "Puntuación: {score}": "Score: {score}",
    "Sin puntuación IA.": "No AI score.",
    "Pendiente de evaluar": "Awaiting evaluation",
    "Selecciona el veredicto de IA a mostrar": "Choose which AI verdicts to show",
    "Mostrar": "Show",
    "Búsqueda no configurada": "Search not configured",
    "Cargar resultados": "Load results",
    "Editar prompt": "Edit prompt",
    "Editor de búsquedas guardadas": "Saved searches editor",
    "Cerrar editor": "Close editor",

    // --- Source health ---
    "Fuentes con resultados": "Sources with results",
    "Fuentes con problemas": "Sources with issues",
    "Fuentes no exploradas en profundidad": "Sources not deeply searched",
    "Fuentes deshabilitadas": "Disabled sources",
    "Cobertura del recorrido": "Run coverage",
    "Ver detalles de {name}": "View details of {name}",
    "Ocultar detalles de {name}": "Hide details of {name}",
    "Motivo: {reason}": "Reason: {reason}",
    "Hora: {when}": "Time: {when}",
    "Sin detalles disponibles": "No details available",
    "Limitaciones compartidas": "Shared limitations",
    "Activos encontrados: {n}": "Listings found: {n}",

    // --- AI / evaluations ---
    "Veredicto IA": "AI verdict",
    "Sin evaluar por IA": "Not evaluated by AI",
    "Evaluación de IA": "AI evaluation",
    "Evaluaciones disponibles": "Evaluations available",
    "Evaluaciones pendientes": "Evaluations pending",
    "Recargar evaluaciones": "Reload evaluations",
    "Evaluación en curso…": "Evaluating…",
    "Esperando evaluación": "Waiting for evaluation",

    // --- Run history ---
    "Cargando historial…": "Loading history…",
    "No se pudo cargar el historial.": "Could not load the history.",
    "Error al cargar el historial.": "Error loading the history.",
    "Error al cargar la configuración.": "Could not load settings.",
    "Error al cargar usuarios.": "Could not load users.",
    "No se pudo cargar el enlace.": "Could not load the link.",
    "Oportunidad compartida": "Shared opportunity",
    "Imagen del anuncio": "Listing image",
    "No hay búsquedas registradas todavía.": "No runs recorded yet.",
    "Recorrido {id}": "Run {id}",
    "Recorrido sin ID": "Run without ID",
    "Estado: {status}": "Status: {status}",
    "Estado: {status} (en curso)": "Status: {status} (running)",
    "Inicio: {when}": "Start: {when}",
    "Fin: {when}": "End: {when}",
    "Activos observados: {ingested} · Nuevos: {new} · Actualizados: {updated}":
      "Listings observed: {ingested} · New: {new} · Updated: {updated}",
    "Cobertura parcial de fuentes.": "Partial source coverage.",
    "Error: {msg}": "Error: {msg}",
    "Ver cobertura y limitaciones de {n} fuentes":
      "Show coverage and limitations of {n} sources",
    "Ocultar cobertura y limitaciones": "Hide coverage and limitations",
    "En cola": "Queued",
    "{n} reg.": "{n} records",
    "Actualizando…": "Updating…",
    "Búsqueda encolada.": "Run queued.",
    "No se pudo encolar la búsqueda.": "Could not queue the run.",
    "Cobertura y limitaciones": "Coverage and limitations",
    "Limitaciones:": "Limitations:",
    "Fuentes consultadas": "Sources",
    "Sin limitaciones registradas.": "No limitations recorded.",

    // --- Misc / generic ---
    "Cancelar": "Cancel",
    "Sin resultados.": "No results.",
    "Pendiente de configurar.": "Pending configuration.",
    "Sí": "Yes",
    "No": "No",
    "desconocida": "unknown",
    "vacante": "vacant",
    "vacante libre": "vacant (free)",
    "vacante documentada libre": "vacant (documented free)",
    "anunciada libre por el vendedor": "advertised as free by the seller",
    "ocupada": "occupied",
    "ocupada con derecho de arrendamiento": "occupied with tenancy rights",
    "ocupada (renta antigua)": "occupied (old rent)",
    "ocupada por okupación": "occupied by squatters",
    "ocupada (derechos por confirmar)": "occupied (rights to confirm)",
    "proindiviso": "co-owned share",
    "nuda propiedad": "bare ownership",
    "cesión de deuda": "debt assignment",
    "disponible": "available",
    "anunciada disponible por el vendedor": "advertised as available by the seller",
    "vendido": "sold",
    "retirada": "withdrawn",
    "bloqueada": "blocked",
    "pendiente de revisar": "pending review",

    // --- Field key labels (used by humanValue) ---
    "dirección": "address",
    "localidad": "locality",
    "ciudad": "city",
    "municipio": "municipality",
    "provincia": "province",
    "región": "region",
    "país": "country",
    "CP": "Postcode",
    "estado": "status",
    "entrega documentada libre": "documented free delivery",
    "verificación": "verification",
    "verificado": "verified",
    "etiqueta": "label",
    "nombre": "name",
    "título": "title",
    "valor": "value",
    "texto": "text",
    "tipo": "type",
    "código": "code",

    // --- Category labels (es -> en) used by categoryLabel() ---
    "Plaza de garaje": "Garage",
    "Terreno / Solar": "Land",
    "Coche": "Car",
    "Moto": "Motorcycle",
    "Camión": "Truck",
    "Vehículo (sin especificar)": "Vehicle (unspecified)",
    "Embarcación": "Boat",
    "Maquinaria": "Machinery",
    "Electrónica": "Electronics",
    "Mobiliario": "Furniture",
    "Objeto de colección": "Collectible",
    "Otro": "Other",
    "Sin clasificar": "Unclassified",

    // --- Drawer counts (pending/ok/ko) ---
    "Ver detalle de {title}": "View details of {title}",
    "Ver detalle de {title} ({id})": "View details of {title} ({id})",
    "Ver detalle de {id}": "View detail of {id}",

    // --- Help / keyboard dialog ---
    "Atajos de teclado": "Keyboard shortcuts",
    "Pulsa {key} para abrir esta ayuda en cualquier momento.":
      "Press {key} anywhere to open this help.",
    "Cerrar ayuda": "Close help",
    "Tecla": "Key",
    "Acción": "Action",
    "Abrir esta ayuda": "Open this help",
    "Cerrar la capa superior": "Close the top layer",
    "Siguiente foto (lightbox)": "Next photo (lightbox)",
    "Foto anterior (lightbox)": "Previous photo (lightbox)",
    "Primera foto": "First photo",
    "Última foto": "Last photo",
    "Siguiente tarjeta": "Next card",
    "Tarjeta anterior": "Previous card",
    "Marcar OK (detalle abierto)": "Mark OK (detail open)",
    "Marcar KO (detalle abierto)": "Mark KO (detail open)",
    "Buscar (no en editor)": "Search (not in editor)",
    "Barajar: lo atajos no se aplican dentro de un editor.":
      "Note: shortcuts are disabled inside editors.",
    "Ayuda": "Help",
    "Idioma: {lang}": "Language: {lang}",
    "Tema: {theme}": "Theme: {theme}",

    // --- Language picker ---
    "Idioma": "Language",
    "Español": "Spanish",
    "English": "English",
    "Cambiar idioma": "Change language",
    "Tema": "Theme",
    "Cambiar tema": "Toggle theme",
    "Tema claro": "Light theme",
    "Tema oscuro": "Dark theme",
    "Claro": "Light",
    "Oscuro": "Dark",
    "Cambiar a tema claro": "Switch to light mode",
    "Cambiar a tema oscuro": "Switch to dark mode",
    "Lunes a viernes, 07:00": "Monday to Friday, 07:00",
    "Los atajos no se activan mientras escribes en un campo.": "Shortcuts do not activate while you type in a field.",
    "Al cambiar la contraseña se cerrarán tus sesiones abiertas.": "Changing your password will sign you out of your open sessions.",

    // --- Backend error translations (code -> friendly) ---
    // Friendly text used by app.js when backend returns an error code
    // or untranslated message.
    "Sesión expirada": "Session expired",
    "Acceso denegado": "Access denied",
    "Sin autor registrado": "No author recorded",
    "Token": "Token",
    "—": "—",
  };

  // Sensitive keys whose VALUES must never be surfaced to the UI. We use
  // this list both for humanValue redacting and for any incidental source
  // reports summary that backend may include.
  var SENSITIVE_KEYS = {
    password: true,
    passwd: true,
    pwd: true,
    token: true,
    api_key: true,
    apikey: true,
    secret: true,
    authorization: true,
    bearer: true,
    cookie: true,
    csrf: true,
    csrf_token: true,
    session: true,
    private_key: true,
    fingerprint: true,
    access_token: true,
    refresh_token: true,
    client_secret: true,
  };

  // ----------------------------- State -----------------------------
  var currentLang = DEFAULT_LANG;
  var currentTheme = "light";

  function readStoredLang() {
    try {
      var v = root.localStorage && root.localStorage.getItem(STORAGE_LANG);
      if (v === "es" || v === "en") return v;
    } catch (e) { /* storage unavailable */ }
    return null;
  }

  function readStoredTheme() {
    try {
      var v = root.localStorage && root.localStorage.getItem(STORAGE_THEME);
      if (v === "light" || v === "dark") return v;
    } catch (e) { /* storage unavailable */ }
    return null;
  }

  function storeLang(v) {
    try { root.localStorage && root.localStorage.setItem(STORAGE_LANG, v); } catch (e) {}
  }
  function storeTheme(v) {
    try { root.localStorage && root.localStorage.setItem(STORAGE_THEME, v); } catch (e) {}
  }

  // ----------------------------- Theme -----------------------------
  function detectTheme() {
    var stored = readStoredTheme();
    if (stored) return stored;
    try {
      if (root.matchMedia && root.matchMedia("(prefers-color-scheme: dark)").matches) {
        return "dark";
      }
    } catch (e) {}
    return "light";
  }

  function applyTheme(theme, persist) {
    var html = document.documentElement;
    if (!html) return;
    html.setAttribute("data-theme", theme);
    currentTheme = theme;
    if (persist !== false) storeTheme(theme);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", theme === "dark" ? "#0c1422" : "#14233a");
    }
    // Update toggle aria
    var btns = document.querySelectorAll("[data-theme-toggle]");
    btns.forEach(function (b) {
      b.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
      var action = translate(theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro");
      b.setAttribute("aria-label", action);
      b.setAttribute("title", action);
      var label = b.querySelector('[data-theme-label]');
      if (label) label.textContent = (theme === "dark" ? "☾ " : "☀ ") + translate(theme === "dark" ? "Oscuro" : "Claro");
    });
  }

  function initTheme() {
    currentTheme = detectTheme();
    applyTheme(currentTheme, false);
  }

  function setTheme(next) {
    if (next !== "light" && next !== "dark") return;
    applyTheme(next);
  }

  function toggleTheme() {
    applyTheme(currentTheme === "dark" ? "light" : "dark");
  }

  // ----------------------------- i18n -----------------------------
  function initLang() {
    var stored = readStoredLang();
    currentLang = stored || DEFAULT_LANG;
    document.documentElement.setAttribute("lang", currentLang === "en" ? "en" : "es");
  }

  function translate(source, params) {
    if (currentLang === "es") {
      return formatParams(source, params);
    }
    var key = source;
    var v = DICT.hasOwnProperty(key) ? DICT[key] : source;
    return formatParams(v, params);
  }

  function formatParams(value, params) {
    if (!params) return value;
    return String(value).replace(/\{(\w+)\}/g, function (_, name) {
      return Object.prototype.hasOwnProperty.call(params, name)
        ? String(params[name])
        : "{" + name + "}";
    });
  }

  function setLanguage(lang) {
    if (lang !== "es" && lang !== "en") return;
    currentLang = lang;
    storeLang(lang);
    document.documentElement.setAttribute("lang", lang === "en" ? "en" : "es");
    // Re-translate the entire static document (skipping data we own).
    applyI18n(root.document);
    // Update language picker state
    syncLanguagePickers();
    applyTheme(currentTheme, false);
    // Notify listeners so they can rebuild dynamic content.
    document.dispatchEvent(new CustomEvent("auctionradar:languagechange", { detail: { lang: lang }, bubbles: true }));
  }

  function language() { return currentLang; }
  function locale() { return SUPPORTED[currentLang] || SUPPORTED[DEFAULT_LANG]; }
  function mapsURL(location) {
    function clean(value){if(typeof value!=='string')return '';var text=value.trim();return /^(?:unknown|desconocid[oa]|sin dato(?: publicado)?|no consta|n\/a)$/i.test(text)?'':text;}
    var parts=[];
    if(typeof location==='string')parts=[clean(location)];
    else if(location && typeof location==='object') {
      ['address','streetAddress','postal_code','postalCode','locality','city','municipality','addressLocality','province','region','addressRegion','country','addressCountry'].forEach(function(key){
        var value=location[key];
        if(value&&typeof value==='object'&&!Array.isArray(value))value=value.name||value.streetAddress;
        var text=clean(value);if(text&&parts.indexOf(text)<0)parts.push(text);
      });
    }
    var query=parts.filter(Boolean).join(', ');
    if(!query)return null;
    var characters=Array.from(query).slice(0,500);
    while(characters.length && encodeURIComponent(characters.join('')).length>1850)characters.pop();
    return characters.length?'https://www.google.com/maps/search/?api=1&query='+encodeURIComponent(characters.join('')):null;
  }
  function mapsLink(location,label) {
    var url=mapsURL(location);if(!url)return null;
    var link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.referrerPolicy='no-referrer';link.className='location-link';
    link.setAttribute('data-map-link','');
    var text=label || (typeof location==='string'?location:decodeURIComponent(url.split('query=')[1]));
    link.title=translate('Ver ubicación en Google Maps (nueva pestaña)');
    link.setAttribute('aria-label',text+' — '+link.title);
    var svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('focusable','false');svg.classList.add('location-pin');
    var path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('d','M20 10c0 6-8 11-8 11S4 16 4 10a8 8 0 1 1 16 0Z M15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0');path.setAttribute('fill','none');path.setAttribute('stroke','currentColor');path.setAttribute('stroke-width','1.6');svg.appendChild(path);link.appendChild(svg);
    var span=document.createElement('span');span.textContent=text;link.appendChild(span);
    link.addEventListener('click',function(event){event.stopPropagation();});
    return link;
  }
  function renderPhotoNotice(gallery,notice) {
    if(!gallery||!notice||notice.code!=='boe_login_required')return;
    var empty=gallery.querySelector('.gallery-empty');
    var block=empty||document.createElement('div');block.classList.add('photo-access-notice');block.setAttribute('data-photo-notice','');
    block.textContent=translate('El BOE requiere iniciar sesión para consultar la información complementaria del bien. El radar no utiliza tu sesión del portal.');
    try{
      var url=new URL(notice.source_url);
      if(url.protocol==='https:'&&url.hostname==='subastas.boe.es'&&!url.username&&!url.password){
        var link=document.createElement('a');link.className='btn btn-ghost';link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';link.referrerPolicy='no-referrer';link.textContent=translate('Abrir información en el BOE');block.appendChild(link);
      }
    }catch(error){}
    if(!empty)gallery.appendChild(block);
  }
  function assetLabel(value) {
    value=value || {};
    var code=typeof value==='string'?value:(value.category || value.asset_type);
    var names={housing:'Vivienda',vehicles:'Vehículo',vehicles_unspec:'Vehículo',vans:'Furgoneta',cars:'Coche',motorcycles:'Moto',trucks:'Camión',commercial:'Local comercial',garages:'Plaza de garaje',land:'Terreno / Solar',boats:'Embarcación',machinery:'Maquinaria',electronics:'Electrónica',furniture:'Mobiliario',collectibles:'Objeto de colección',other:'Otro bien',unknown:'Sin clasificar'};
    return translate(names[code] || 'Otro bien');
  }
  function errorMessage(code) {
    var messages = {
      csrf_missing: "La sesión del formulario ha caducado. Recarga la página e inténtalo de nuevo.",
      csrf_invalid: "La sesión del formulario ha caducado. Recarga la página e inténtalo de nuevo.",
      auth_required: "Sesión expirada", forbidden: "Acceso denegado",
      invalid_current_password: "La contraseña actual no es correcta.",
      password_too_short: "La contraseña debe tener entre 12 y 1024 caracteres.",
      username_taken: "Ese nombre de usuario ya está registrado.",
      notes_too_long: "Las notas no pueden superar los 4000 caracteres.",
      cannot_deactivate_self: "No puedes desactivar tu propia cuenta.",
      cannot_deactivate_last_admin: "Debe quedar al menos una cuenta administradora activa.",
      run_already_active: "Ya hay una búsqueda en curso.",
      runner_unavailable: "No se pudo iniciar la búsqueda. Inténtalo de nuevo.",
      no_sources_configured: "Añade y activa al menos una fuente en Fuentes y accesos antes de buscar.",
      not_found: "No se encontró el elemento solicitado.", item_not_found: "No se encontró el elemento solicitado.",
      not_found_or_revoked: "No se encontró el elemento solicitado."
    };
    if (Object.prototype.hasOwnProperty.call(messages, code)) return translate(messages[code]);
    if (typeof code === "string" && (/^invalid_|^validation_failed$|^settings_required$/.test(code))) return translate("Revisa los datos introducidos.");
    if (typeof code === "string" && DICT[code]) return translate(code);
    if (typeof code === "string" && /^HTTP \d{3}$/.test(code)) return code;
    return translate("No se pudo completar la operación. Inténtalo de nuevo.");
  }

  // Translate everything that opts-in via data-i18n / data-i18n-attr.
  function applyI18n(scope) {
    if (!scope || !scope.querySelectorAll) return;
    var nodes = scope.querySelectorAll("[data-i18n]");
    nodes.forEach(function (n) {
      var key = n.getAttribute("data-i18n");
      if (!key) return;
      n.textContent = translate(key);
    });
    var attrNodes = scope.querySelectorAll("[data-i18n-attr]");
    attrNodes.forEach(function (n) {
      var raw = n.getAttribute("data-i18n-attr");
      if (!raw) return;
      raw.split(";").forEach(function (pair) {
        var idx = pair.indexOf(":");
        if (idx <= 0) return;
        var attr = pair.slice(0, idx).trim();
        var key = pair.slice(idx + 1).trim();
        if (!attr || !key) return;
        n.setAttribute(attr, translate(key));
      });
    });
    var registrationErrors = scope.querySelectorAll("[data-registration-error-key]");
    registrationErrors.forEach(function (n) {
      var key = n.getAttribute("data-registration-error-key");
      if (key) n.textContent = translate(key);
    });
  }

  function syncLanguagePickers() {
    var sels = document.querySelectorAll("[data-language-select]");
    sels.forEach(function (el) {
      // Both <select> and buttons with data-language-option are supported.
      if (el.tagName === "SELECT") {
        el.value = currentLang;
      } else {
        el.setAttribute("aria-pressed", currentLang === "en" ? "true" : "false");
      }
    });
  }

  // ----------------------------- Helpers -----------------------------
  function isEditor(node) {
    if (!node) return false;
    if (node.isContentEditable) return true;
    var tag = node.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    return false;
  }

  function safeUrl(value) {
    if (typeof value !== "string") return null;
    var trimmed = value.trim();
    if (!trimmed) return null;
    if (/^javascript:/i.test(trimmed)) return null;
    if (/^data:/i.test(trimmed)) return null;
    if (!/^https?:\/\//i.test(trimmed)) return null;
    try {
      var u = new URL(trimmed);
      if (u.protocol !== "http:" && u.protocol !== "https:") return null;
      return u.toString();
    } catch (e) { return null; }
  }

  function formatMoney(value, currency) {
    if (value === null || value === undefined || isNaN(value)) return null;
    var num = Number(value);
    if (!isFinite(num)) return null;
    var cur = currency || "EUR";
    try {
      return new Intl.NumberFormat(locale(), {
        style: "currency",
        currency: cur,
        maximumFractionDigits: 2,
      }).format(num);
    } catch (e) {
      return num.toLocaleString(locale(), { maximumFractionDigits: 2 }) + " " + cur;
    }
  }

  function formatDateTime(value) {
    if (!value) return null;
    var d = new Date(value);
    if (isNaN(d.getTime())) return null;
    try {
      return new Intl.DateTimeFormat(locale(), {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(d);
    } catch (e) {
      return d.toLocaleString(locale());
    }
  }

  function formatDate(value) {
    if (!value) return null;
    var d = new Date(value);
    if (isNaN(d.getTime())) return null;
    try {
      return new Intl.DateTimeFormat(locale(), { dateStyle: "long" }).format(d);
    } catch (e) {
      return d.toLocaleDateString(locale());
    }
  }

  function formatNumber(value, suffix) {
    if (value === null || value === undefined || isNaN(value)) return null;
    var num = Number(value);
    if (!isFinite(num)) return null;
    try {
      var s = new Intl.NumberFormat(locale()).format(num);
      return suffix ? s + " " + suffix : s;
    } catch (e) {
      return String(num);
    }
  }

  function isSensitive(key) {
    if (!key) return false;
    return SENSITIVE_KEYS[String(key).toLowerCase()] === true;
  }

  function redactValue(value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "string") return "•".repeat(Math.min(value.length, 8));
    return "[redacted]";
  }

  // ----------------------------- Language picker / theme toggle binding -----------------------------
  function setupLanguagePicker() {
    var sels = document.querySelectorAll("[data-language-select]");
    sels.forEach(function (el) {
      if (el.dataset.languageBound) return;
      el.dataset.languageBound = "true";
      if (el.tagName === "SELECT") {
        el.addEventListener("change", function () { setLanguage(el.value); });
      } else if (el.tagName === "BUTTON") {
        el.setAttribute("data-language-option", el.getAttribute("data-language-option") || "en");
        el.addEventListener("click", function () {
          var next = el.getAttribute("data-language-option") || (currentLang === "en" ? "es" : "en");
          setLanguage(next);
        });
      }
    });
    syncLanguagePickers();
  }

  function setupThemeToggle() {
    var btns = document.querySelectorAll("[data-theme-toggle]");
    btns.forEach(function (b) {
      if (b.dataset.themeBound) return;
      b.dataset.themeBound = "true";
      b.addEventListener("click", function () { toggleTheme(); });
    });
    applyTheme(currentTheme, false);
  }

  // ----------------------------- Initialise synchronously -----------------------------
  initTheme();
  initLang();

  // First pass of static translation (templates already include Spanish).
  // Done here so data-i18n nodes have content before the first paint,
  // reducing visible flash when language is already EN.
  try { applyI18n(document); } catch (e) {}

  // Expose API.
  root.AuctionRadarUI = {
    t: translate,
    locale: locale,
    mapsURL: mapsURL,
    mapsLink: mapsLink,
    renderPhotoNotice: renderPhotoNotice,
    assetLabel: assetLabel,
    errorMessage: errorMessage,
    language: language,
    setLanguage: setLanguage,
    setTheme: setTheme,
    toggleTheme: toggleTheme,
    theme: function () { return currentTheme; },
    applyI18n: applyI18n,
    isEditor: isEditor,
    safeUrl: safeUrl,
    formatMoney: formatMoney,
    formatDateTime: formatDateTime,
    formatDate: formatDate,
    formatNumber: formatNumber,
    isSensitive: isSensitive,
    redactValue: redactValue,
    setupLanguagePicker: setupLanguagePicker,
    setupThemeToggle: setupThemeToggle,
    STORAGE_LANG: STORAGE_LANG,
    STORAGE_THEME: STORAGE_THEME,
    SUPPORTED: SUPPORTED,
    DICT: DICT,
  };
  function ready() {
    applyI18n(document);
    setupLanguagePicker();
    setupThemeToggle();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready, { once: true });
  else ready();
  if (root.matchMedia) {
    var scheme = root.matchMedia("(prefers-color-scheme: dark)");
    if (scheme.addEventListener) scheme.addEventListener("change", function (event) {
      if (!readStoredTheme()) applyTheme(event.matches ? "dark" : "light", false);
    });
  }
})(typeof window !== "undefined" ? window : this);
