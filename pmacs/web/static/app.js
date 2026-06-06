/**
 * PMACS Dashboard client-side JavaScript.
 * Handles SSE, Cmd-K palette, TOTP input, toast notifications,
 * keyboard shortcuts, notification policy, and accessibility.
 *
 * Spec: Source.md §13.2 (chrome), §13.5 (notifications), §13.6 (shortcuts), §13.7 (a11y)
 */

// ─── Utilities ────────────────────────────────────────────────────────────────

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#x27;");
}

// ─── Relative Time ──────────────────────────────────────────────────────────

/**
 * Convert an ISO timestamp to a compact human-readable relative time string.
 * @param {string} isoStr - ISO 8601 date string (e.g. "2026-06-01T18:52:00+00:00")
 * @returns {string} e.g. "2d 4h ago", "1h 32m ago", "5m ago", "just now"
 */
function timeAgo(isoStr) {
    if (!isoStr || isoStr === "--") return "--";
    var then = new Date(isoStr);
    if (isNaN(then.getTime())) return isoStr;
    var now = new Date();
    var diffMs = now - then;
    if (diffMs < 0) return "just now";
    var seconds = Math.floor(diffMs / 1000);
    var minutes = Math.floor(seconds / 60);
    var hours = Math.floor(minutes / 60);
    var days = Math.floor(hours / 24);
    var remHours = hours % 24;
    var remMins = minutes % 60;
    if (days > 0) return remHours > 0 ? days + "d " + remHours + "h ago" : days + "d ago";
    if (hours > 0) return remMins > 0 ? hours + "h " + remMins + "m ago" : hours + "h ago";
    if (minutes > 0) return minutes + "m ago";
    return "just now";
}

/**
 * Open a modal popup with full text content (for "Read more" expansions).
 * @param {string} title - Modal heading
 * @param {string} content - Full text to display (plain text, escaped internally)
 */
function openReadMoreModal(title, content) {
    var modal = document.getElementById("read-more-modal");
    if (!modal) {
        modal = document.createElement("div");
        modal.id = "read-more-modal";
        modal.className = "fixed inset-0 z-[60] flex items-center justify-center p-4";
        modal.setAttribute("role", "dialog");
        modal.setAttribute("aria-modal", "true");
        modal.setAttribute("aria-labelledby", "read-more-modal-title");
        modal.innerHTML =
            '<div class="absolute inset-0 bg-black/50 backdrop-blur-sm" onclick="document.getElementById(\'read-more-modal\').classList.add(\'hidden\')"></div>' +
            '<div class="relative bg-surface-elevated border border-border-subtle rounded-2xl shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col">' +
            '<div class="flex items-center justify-between px-6 py-4 border-b border-border-subtle flex-shrink-0">' +
            '<h3 id="read-more-modal-title" class="text-sm font-semibold text-text-primary"></h3>' +
            '<button onclick="document.getElementById(\'read-more-modal\').classList.add(\'hidden\')" ' +
            'class="text-text-muted hover:text-text-primary transition-colors p-1 rounded-lg hover:bg-surface-sunken" aria-label="Close">' +
            '<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
            '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>' +
            '</button></div>' +
            '<div id="read-more-modal-content" class="px-6 py-5 overflow-y-auto text-sm text-text-secondary leading-relaxed flex-1"></div>' +
            '</div>';
        modal.setAttribute("tabindex", "-1");
        document.body.appendChild(modal);
        modal.addEventListener("keydown", function(e) {
            if (e.key === "Escape") modal.classList.add("hidden");
        });
    }
    document.getElementById("read-more-modal-title").textContent = title || "";
    var contentEl = document.getElementById("read-more-modal-content");
    // Render paragraphs from newlines
    var escaped = (content || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    contentEl.innerHTML = "<p>" + escaped.replace(/\n\n+/g, "</p><p class='mt-3'>").replace(/\n/g, "<br>") + "</p>";
    modal.classList.remove("hidden");
    modal.focus();
}

/**
 * Convert all [data-time-ago] elements to relative time.
 * Supports optional [data-time-ago-prefix] to prepend a label.
 * Re-runs every 60s to keep times current.
 */
function initTimeAgo() {
    document.querySelectorAll("[data-time-ago]").forEach(function (el) {
        var ts = el.getAttribute("data-time-ago");
        if (!ts || ts === "--") return;
        var prefix = el.getAttribute("data-time-ago-prefix");
        el.textContent = prefix ? prefix + timeAgo(ts) : timeAgo(ts);
    });
}

function updateNavActive() {
    var path = window.location.pathname;
    document.querySelectorAll("nav a[href]").forEach(function (link) {
        var href = link.getAttribute("href");
        var isActive = href === path || (href !== "/" && path.startsWith(href));
        link.classList.toggle("active", isActive);
        link.classList.toggle("bg-accent-soft", isActive);
        link.classList.toggle("text-accent", isActive);
        link.classList.toggle("font-medium", isActive);
        link.classList.toggle("text-text-secondary", !isActive);
        link.setAttribute("aria-current", isActive ? "page" : "false");
    });
}

document.addEventListener("DOMContentLoaded", function () {
    initTimeAgo();
    setInterval(initTimeAgo, 60000);
    updateNavActive();
});
// Re-run after HTMX content swaps (hx-boost navigation skips DOMContentLoaded)
document.addEventListener("htmx:afterSettle", function () {
    initTimeAgo();
    updateNavActive();
});

// ─── CSRF Token (Architecture.md §18) ─────────────────────────────────────────

function getCsrfToken() {
    var name = "pmacs_csrf=";
    var decodedCookie = decodeURIComponent(document.cookie);
    var ca = decodedCookie.split(";");
    for (var i = 0; i < ca.length; i++) {
        var c = ca[i].trim();
        if (c.indexOf(name) === 0) {
            return c.substring(name.length);
        }
    }
    return "";
}

function _csrfHeaders(headers) {
    if (!headers) headers = {};
    if (typeof headers === "object" && !(headers instanceof Headers)) {
        var token = getCsrfToken();
        if (token) {
            headers["x-csrf-token"] = token;
        }
    }
    return headers;
}

// Auto-attach CSRF token to all POST requests (fetch)
// - Always reads fresh from cookie (no stale cache)
// - Primes with a GET request if cookie missing (once only, no infinite loop)
(function () {
    var _fetch = window.fetch;
    var _priming = false;

    window.fetch = function (input, init) {
        init = init || {};
        if (init.method && init.method.toUpperCase() === "POST") {
            init.headers = _csrfHeaders(init.headers);
            var token = getCsrfToken();
            if (!token && !_priming) {
                // Cookie not set yet — prime with a GET, then retry (once only)
                _priming = true;
                return _fetch.call(window, "/?_csrf_prime=1", {method: "GET"}).then(function () {
                    init.headers = _csrfHeaders(init.headers);
                    _priming = false;
                    return _fetch.call(window, input, init);
                }).catch(function () {
                    _priming = false;
                    // Proceed without CSRF — server will return 403 if required
                    return _fetch.call(window, input, init);
                });
            }
        }
        return _fetch.call(window, input, init);
    };
})();

// Auto-attach CSRF token to HTMX POST requests
// Attached to document (not body) outside DOMContentLoaded to avoid race condition
document.addEventListener("htmx:configRequest", function (event) {
    if (event.detail && event.detail.verb === "post") {
        event.detail.headers["x-csrf-token"] = getCsrfToken();
    }
});

// ─── Feature Detection ─────────────────────────────────────────────────────

var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ─── SSE Connection ─────────────────────────────────────────────────────────

var SSE_URL = "/events";
var eventSource = null;
var eventHandlers = {};
var sseRetryCount = 0;
var SSE_MAX_RETRIES = 20;
var sseReconnectTimer = null;
var sseLastEventId = "";

/**
 * Register a handler for a named SSE stream.
 * @param {string} stream - Stream name to listen for.
 * @param {function} handler - Callback receiving parsed JSON data.
 */
function onSSE(stream, handler) {
    eventHandlers[stream] = eventHandlers[stream] || [];
    eventHandlers[stream].push(handler);
}

function connectSSE() {
    if (sseReconnectTimer) {
        clearTimeout(sseReconnectTimer);
        sseReconnectTimer = null;
    }
    if (eventSource) {
        eventSource.close();
    }

    if (sseRetryCount >= SSE_MAX_RETRIES) {
        showToast("SSE connection permanently lost. Reload the page.", "error", 0);
        return;
    }

    try {
        var sseUrl = SSE_URL;
        // Resume from last received event on reconnect
        if (sseLastEventId) {
            sseUrl = SSE_URL + "?last_event_id=" + encodeURIComponent(sseLastEventId);
        }
        eventSource = new EventSource(sseUrl);

        eventSource.onopen = function () {
            sseRetryCount = 0;
        };

        eventSource.onmessage = function (event) {
            // Track last event ID for reconnection resume
            if (event.lastEventId) {
                sseLastEventId = event.lastEventId;
            }
            try {
                var data = JSON.parse(event.data);
                var stream = data.stream || "";
                var handlers = eventHandlers[stream] || [];
                handlers.forEach(function (handler) {
                    handler(data);
                });
            } catch (e) {
                console.warn("SSE parse error:", e);
            }
        };

        eventSource.onerror = function () {
            var delay = Math.min(5000 * Math.pow(1.5, sseRetryCount), 60000);
            console.warn("SSE connection lost, reconnecting in", delay, "ms (attempt", sseRetryCount + 1, "/", SSE_MAX_RETRIES, ")");
            var es = eventSource;
            if (es) { es.close(); }
            sseRetryCount++;
            sseReconnectTimer = setTimeout(connectSSE, delay);
        };
    } catch (e) {
        console.warn("SSE unavailable:", e);
        var delay = Math.min(5000 * Math.pow(1.5, sseRetryCount), 60000);
        sseRetryCount++;
        sseReconnectTimer = setTimeout(connectSSE, delay);
    }
}

// Auto-connect on load
document.addEventListener("DOMContentLoaded", connectSSE);

// ─── Toast Notifications (Source.md §13.5) ──────────────────────────────────

var TOAST_CONTAINER_ID = "toast-container";
var TOAST_MAX = 5;

/**
 * Show a toast notification.
 * @param {string} message - Toast message text.
 * @param {string} [type="info"] - Toast type: info, success, warning, error, critical.
 * @param {number} [duration=5000] - Auto-dismiss duration in ms. 0 = persistent.
 */
function showToast(message, type, duration) {
    type = type || "info";
    duration = duration !== undefined ? duration : 5000;

    var container = document.getElementById(TOAST_CONTAINER_ID);
    if (!container) return;

    // Cap at TOAST_MAX toasts
    while (container.children.length >= TOAST_MAX) {
        container.removeChild(container.firstChild);
    }

    var colorMap = {
        info: "bg-blue-600",
        success: "bg-green-600",
        warning: "bg-amber-500",
        error: "bg-red-600",
        critical: "bg-red-700",
    };

    var toast = document.createElement("div");
    toast.className =
        "toast-enter px-4 py-3 rounded-lg shadow-lg text-white text-sm flex items-center gap-2 " +
        (colorMap[type] || colorMap.info);
    toast.setAttribute("role", "status");

    var text = document.createElement("span");
    text.textContent = message;
    toast.appendChild(text);

    // Persistent toasts (warning/error/critical) get a dismiss button
    if (duration === 0) {
        var dismiss = document.createElement("button");
        dismiss.textContent = "✕";
        dismiss.className = "ml-2 text-white/70 hover:text-white text-sm";
        dismiss.setAttribute("aria-label", "Dismiss");
        dismiss.onclick = function () {
            removeToast(toast);
        };
        toast.appendChild(dismiss);
    }

    container.appendChild(toast);

    if (duration > 0) {
        setTimeout(function () {
            removeToast(toast);
        }, duration);
    }
}

function removeToast(toast) {
    if (!toast.parentNode) return;
    toast.classList.remove("toast-enter");
    toast.classList.add("toast-exit");
    setTimeout(function () {
        if (toast.parentNode) {
            toast.parentNode.removeChild(toast);
        }
    }, 200);
}

// ─── Blocking Modal (kill switch, audit chain failure — Source.md §13.5) ────

function showBlockingModal(title, message, buttons) {
    var modal = document.getElementById("blocking-modal");
    if (!modal) return;

    document.getElementById("blocking-modal-title").textContent = title;
    document.getElementById("blocking-modal-message").textContent = message;

    var actionsDiv = document.getElementById("blocking-modal-actions");
    actionsDiv.innerHTML = "";

    (buttons || []).forEach(function (btn) {
        var button = document.createElement("button");
        button.textContent = btn.label;
        button.type = "button";
        button.className = "px-4 py-2 text-sm rounded " + (btn.primary ? "bg-red-600 text-white hover:bg-red-700" : "bg-surface-sunken text-text-primary hover:bg-border");
        button.onclick = function () {
            modal.classList.add("hidden");
            if (btn.action) btn.action();
        };
        actionsDiv.appendChild(button);
    });

    modal.classList.remove("hidden");
}

// ─── Notification Policy (Source.md §13.5 event→surface mapping) ────────────

var NOTIFICATION_POLICY = {
    trade_filled_paper: { surface: "toast", type: "info", duration: 5000, sound: false },
    trade_filled_live: { surface: "toast", type: "info", duration: 0, sound: false },
    stop_loss_triggered: { surface: "toast", type: "warning", duration: 0, sound: "click" },
    kill_switch_engaged: { surface: "modal", type: "critical", sound: "alert" },
    cycle_complete: { surface: "toast", type: "info", duration: 5000, sound: false },
    mutation_candidate_ready: { surface: "toast", type: "info", duration: 5000, sound: false, badge: "settings" },
    mutation_approved: { surface: "toast", type: "info", duration: 5000, sound: false },
    audit_chain_failure: { surface: "modal", type: "critical", sound: "alert" },
    disk_low: { surface: "toast", type: "warning", duration: 0, sound: false },
    reconciliation_mismatch: { surface: "toast", type: "warning", duration: 0, sound: false },
    source_degraded_important: { surface: "toast", type: "warning", duration: 30000, sound: false },
    source_degraded_nice: { surface: "silent", sound: false },
    source_recovered: { surface: "toast", type: "info", duration: 5000, sound: false },
};

// Events that ALWAYS show modal regardless of saved level
var NON_DISABLEABLE_EVENTS = {
    kill_switch_engaged: true,
    audit_chain_failure: true
};

// Saved notification levels from backend (populated on page load)
var savedNotificationLevels = {};

// Fetch saved notification levels from backend on page load
document.addEventListener("DOMContentLoaded", function () {
    fetch("/api/settings/notifications")
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            savedNotificationLevels = data || {};
        })
        .catch(function () {
            // Fallback: use defaults from NOTIFICATION_POLICY
            savedNotificationLevels = {};
        });
});

function playSound(type) {
    if (!type || prefersReducedMotion) return;
    try {
        var audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        var oscillator = audioCtx.createOscillator();
        var gainNode = audioCtx.createGain();
        oscillator.connect(gainNode);
        gainNode.connect(audioCtx.destination);
        gainNode.gain.value = 0.1;

        if (type === "click") {
            oscillator.frequency.value = 800;
            oscillator.start();
            oscillator.stop(audioCtx.currentTime + 0.1);
        } else if (type === "alert") {
            oscillator.frequency.value = 600;
            oscillator.start();
            oscillator.stop(audioCtx.currentTime + 0.5);
        }
    } catch (e) {
        // Audio not available — silent fallback
    }
}

function handleNotification(eventType, data) {
    var policy = NOTIFICATION_POLICY[eventType];
    if (!policy || policy.surface === "silent") return;

    // Kill switch and audit chain failure ALWAYS show modal — bypass saved level
    var isNonDisableable = !!NON_DISABLEABLE_EVENTS[eventType];

    // Check saved notification level from backend
    var savedLevel = savedNotificationLevels[eventType] || null;
    if (savedLevel === "none" && !isNonDisableable) {
        // Silently suppressed by operator preference
        return;
    }

    // Determine effective surface and sound from saved level
    var effectiveSurface = policy.surface;
    var effectiveSound = policy.sound;

    if (!isNonDisableable && savedLevel) {
        if (savedLevel === "toast") {
            effectiveSurface = "toast";
            effectiveSound = false;
        } else if (savedLevel === "toast+sound") {
            effectiveSurface = "toast";
            effectiveSound = policy.sound || "click";
        } else if (savedLevel === "modal") {
            effectiveSurface = "modal";
            effectiveSound = "alert";
        }
    }

    playSound(effectiveSound);

    if (effectiveSurface === "modal") {
        showBlockingModal(
            eventType === "kill_switch_engaged" ? "KILL SWITCH ENGAGED" : "CRITICAL ALERT",
            data.message || "A critical event occurred.",
            [
                { label: "Acknowledge", primary: true },
            ]
        );
    } else if (effectiveSurface === "toast") {
        showToast(data.message || eventType, policy.type, policy.duration);
    }

    // Update badge if applicable
    if (policy.badge) {
        var badgeEl = document.querySelector('[data-badge="' + policy.badge + '"]');
        if (badgeEl) badgeEl.classList.remove("hidden");
    }
}

// ─── Cmd-K Command Palette (Source.md §13.2, §13.6) ─────────────────────────

var CMD_K_PAGES = [
    { name: "Dashboard", href: "/", category: "page" },
    { name: "Agents", href: "/agents", category: "page" },
    { name: "Pipeline", href: "/pipeline", category: "page" },
    { name: "Universe", href: "/universe", category: "page" },
    { name: "Cortex", href: "/cortex", category: "page" },
    { name: "Debug", href: "/debug", category: "page" },
    { name: "Settings", href: "/settings", category: "page" },
];

var CMD_K_ACTIONS = [
    { name: "Run cycle now", action: "runCycleNow", category: "action" },
    { name: "Engage kill switch", action: "handleKillSwitch", category: "action" },
    { name: "Compare cycles", action: "openCycleCompare", category: "action" },
    { name: "Failures by taxonomy", action: "openTaxonomyBrowser", category: "action" },
    { name: "Promote all P1 queue", action: "promoteAllP1Global", category: "action" },
    { name: "Show shortcuts", action: "showShortcuts", category: "action" },
    { name: "Open TOTP modal", action: "openTOTPManual", category: "action" },
];

// Error codes from Architecture.md §5.5 — searchable in palette
var CMD_K_ERROR_CODES = [
    { name: "E001 — Queue pop on empty", href: "/debug?event=E001", category: "audit" },
    { name: "E002 — Cycle timeout exceeded", href: "/debug?event=E002", category: "audit" },
    { name: "E003 — Inference connection lost", href: "/debug?event=E003", category: "audit" },
    { name: "E004 — TOTP verification failed", href: "/debug?event=E004", category: "audit" },
    { name: "E005 — Audit chain hash mismatch", href: "/debug?event=E005", category: "audit" },
    { name: "E006 — Broker connection error", href: "/debug?event=E006", category: "audit" },
    { name: "E010 — Holding state invalid transition", href: "/debug?event=E010", category: "audit" },
    { name: "E011 — Schema validation failure", href: "/debug?event=E011", category: "audit" },
    { name: "E012 — Probability range violation", href: "/debug?event=E012", category: "audit" },
    { name: "E013 — Persona output sanity fail", href: "/debug?event=E013", category: "audit" },
    { name: "E014 — Crucible budget exceeded", href: "/debug?event=E014", category: "audit" },
    { name: "E015 — Mutation auto-rollback triggered", href: "/debug?event=E015", category: "audit" },
];

var CMD_K_ALL = CMD_K_PAGES.concat(CMD_K_ACTIONS).concat(CMD_K_ERROR_CODES);
var cmdKActiveIndex = -1;

function toggleCmdK() {
    var el = document.getElementById("cmd-k");
    if (!el) return;
    var isHidden = el.classList.contains("hidden");
    el.classList.toggle("hidden");
    if (isHidden) {
        var input = document.getElementById("cmd-k-input");
        if (input) {
            input.value = "";
            input.focus();
        }
        cmdKActiveIndex = -1;
        renderCmdKResults("");
    }
}

function closeCmdK() {
    var el = document.getElementById("cmd-k");
    if (el) el.classList.add("hidden");
}

function renderCmdKResults(query) {
    var results = document.getElementById("cmd-k-results");
    if (!results) return;

    var q = query.toLowerCase();
    var filtered = CMD_K_ALL.filter(function (item) {
        return item.name.toLowerCase().indexOf(q) >= 0;
    });

    // If query looks like a ticker (1-5 uppercase letters), add ticker search
    if (/^[A-Z]{1,5}$/i.test(query)) {
        var safeQuery = query.toUpperCase().replace(/[^A-Z]/g, "");
        filtered.unshift({
            name: 'Go to Pipeline filtered: ' + safeQuery,
            href: '/pipeline?ticker=' + encodeURIComponent(safeQuery),
            category: "ticker",
        });
    }

    // If query looks like audit/cycle search
    if (/^(cycle|c-|CYCLE)/i.test(query)) {
        filtered.unshift({
            name: 'Search audit: ' + query.replace(/[<>"'&]/g, ""),
            href: '/debug?q=' + encodeURIComponent(query),
            category: "audit",
        });
    }

    // If query looks like error code search (E followed by digits)
    if (/^E\d{1,3}$/i.test(query)) {
        filtered.unshift({
            name: 'Search debug events: ' + query.toUpperCase(),
            href: '/debug?event=' + query.toUpperCase(),
            category: "audit",
        });
    }

    results.innerHTML = "";
    cmdKActiveIndex = -1;

    var categoryLabel = {
        page: "Page",
        action: "Action",
        ticker: "Ticker",
        audit: "Audit",
    };

    filtered.forEach(function (item, idx) {
        var li = document.createElement("li");
        li.setAttribute("role", "option");
        li.className = "flex items-center px-4 py-2.5 cursor-pointer hover:bg-surface-sunken text-sm text-text-primary";

        li.innerHTML =
            '<span class="text-xs font-mono mr-3 px-1.5 py-0.5 rounded ' +
            (item.category === "page" ? "bg-blue-50 text-blue-500" : "") +
            (item.category === "action" ? "bg-green-50 text-green-600" : "") +
            (item.category === "ticker" ? "bg-amber-50 text-amber-600" : "") +
            (item.category === "audit" ? "bg-purple-50 text-purple-600" : "") +
            '">' + (categoryLabel[item.category] || "") + '</span>' +
            '<span class="flex-1">' + escapeHtml(item.name) + '</span>';

        li.addEventListener("click", function () {
            executeCmdKItem(item);
        });
        li.addEventListener("mouseenter", function () {
            cmdKActiveIndex = idx;
            updateCmdKActiveItem(results);
        });
        results.appendChild(li);
    });

    // No results state
    if (filtered.length === 0) {
        var empty = document.createElement("li");
        empty.className = "px-4 py-3 text-sm text-text-muted text-center";
        empty.textContent = 'No results for "' + query + '"';
        results.appendChild(empty);
    }
}

function updateCmdKActiveItem(results) {
    var items = results.querySelectorAll("li[role='option']");
    items.forEach(function (li, idx) {
        if (idx === cmdKActiveIndex) {
            li.classList.add("bg-surface-sunken");
        } else {
            li.classList.remove("bg-surface-sunken");
        }
    });
}

function executeCmdKItem(item) {
    closeCmdK();
    if (item.href) {
        // Push URL state via history API for HTMX compatibility
        history.pushState({ page: item.href }, "", item.href);
        window.location.href = item.href;
    } else if (item.action && typeof window[item.action] === "function") {
        window[item.action]();
    }
}

function runCycleNow() {
    // Immediate UI feedback — disable button, show spinner
    var btn = document.getElementById("run-cycle-btn");
    var spinner = document.getElementById("run-cycle-spinner");
    var label = document.getElementById("run-cycle-label");
    if (btn) btn.disabled = true;
    if (btn) btn.classList.add("opacity-60", "cursor-not-allowed");
    if (spinner) spinner.classList.remove("hidden");
    if (label) label.textContent = "Starting...";
    showToast("Starting cycle...", "info");
    // Immediately update cycle log on agents page
    var feed = document.getElementById("cycle-log-feed");
    if (feed) {
        var empty = document.getElementById("cycle-log-empty");
        if (empty) empty.classList.add("hidden");
    }

    fetch("/api/cycle/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trigger: "manual" })
    }).then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.ok) {
            showToast(data.message || "Cycle started", "success");
            var bar = document.getElementById("cycle-progress-bar");
            if (bar) bar.classList.remove("hidden");
            var indicator = document.getElementById("cycle-indicator");
            if (indicator) indicator.textContent = "Cycle running...";
        } else {
            showToast("Failed: " + (data.error || "Unknown"), "error");
            // Re-enable button on failure
            if (btn) { btn.disabled = false; btn.classList.remove("opacity-60", "cursor-not-allowed"); }
            if (spinner) spinner.classList.add("hidden");
            if (label) label.textContent = "Run cycle";
        }
    }).catch(function(err) {
        showToast("Error: " + err.message, "error");
        if (btn) { btn.disabled = false; btn.classList.remove("opacity-60", "cursor-not-allowed"); }
        if (spinner) spinner.classList.add("hidden");
        if (label) label.textContent = "Run cycle";
    });
}

function runQueueNow(tickers) {
    if (!tickers || tickers.length === 0) {
        showToast("Queue is empty", "warning");
        return;
    }
    showToast("Starting queue cycle for: " + tickers.join(", "), "info");
    fetch("/api/cycle/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ trigger: "queue", tickers: tickers })
    }).then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.ok) {
            showToast(data.message || "Queue cycle started", "success");
            var bar = document.getElementById("cycle-progress-bar");
            if (bar) bar.classList.remove("hidden");
        } else {
            showToast("Failed: " + (data.error || "Unknown"), "error");
        }
    }).catch(function(err) {
        showToast("Error: " + err.message, "error");
    });
}

function runSmokeTest() {
    showToast("Running smoke-test cycle...", "info");
    fetch("/api/cycle/smoke-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" }
    }).then(function (resp) {
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json();
    }).then(function (data) {
        showToast(data.message || "Smoke-test passed", "success");
        if (data.reload) {
            setTimeout(function () { window.location.reload(); }, 1500);
        }
    }).catch(function (err) {
        showToast("Smoke-test failed: " + err.message, "error");
    });
}

function openCycleCompare() {
    var modal = document.getElementById("cycle-compare-modal");
    if (!modal) {
        modal = document.createElement("div");
        modal.id = "cycle-compare-modal";
        modal.className = "hidden fixed inset-0 z-50 bg-black/50 flex items-start justify-center pt-24";
        modal.setAttribute("role", "dialog");
        modal.setAttribute("aria-label", "Compare cycles");
        modal.setAttribute("aria-modal", "true");
        modal.innerHTML =
            '<div class="bg-surface-elevated rounded-lg shadow-xl w-full max-w-2xl border border-border p-6">' +
            '<h3 class="text-lg font-semibold text-text-primary mb-4">Compare Cycles</h3>' +
            '<p class="text-sm text-text-secondary mb-4">Select two cycle IDs to compare side-by-side (Source.md §15.9).</p>' +
            '<div class="grid grid-cols-2 gap-4 mb-4">' +
            '  <div><label class="text-xs text-text-secondary block mb-1">Cycle A</label>' +
            '  <input id="cycle-a" type="text" class="w-full px-3 py-2 border border-border rounded text-sm font-mono bg-surface text-text-primary" placeholder="e.g. 2026-05-10T08:00"></div>' +
            '  <div><label class="text-xs text-text-secondary block mb-1">Cycle B</label>' +
            '  <input id="cycle-b" type="text" class="w-full px-3 py-2 border border-border rounded text-sm font-mono bg-surface text-text-primary" placeholder="e.g. 2026-05-11T08:00"></div>' +
            '</div>' +
            '<div id="compare-result" class="hidden mb-4 max-h-80 overflow-auto"></div>' +
            '<div class="flex justify-end gap-2">' +
            '  <button onclick="document.getElementById(\'cycle-compare-modal\').classList.add(\'hidden\')" class="px-4 py-2 text-sm border border-border rounded hover:bg-surface-sunken text-text-primary">Cancel</button>' +
            '  <button onclick="fetchCycleComparison()" class="px-4 py-2 text-sm bg-accent text-white rounded hover:bg-accent/90">Compare</button>' +
            '</div></div>';
        document.body.appendChild(modal);
        // Click outside to close
        modal.addEventListener("click", function(e) {
            if (e.target === modal) modal.classList.add("hidden");
        });
    }
    modal.classList.remove("hidden");
    var inputA = document.getElementById("cycle-a");
    if (inputA) inputA.focus();
}

function fetchCycleComparison() {
    var inputA = document.getElementById("cycle-a");
    var inputB = document.getElementById("cycle-b");
    var a = inputA ? inputA.value.trim() : "";
    var b = inputB ? inputB.value.trim() : "";
    if (!a || !b) { showToast("Enter both cycle IDs", "warning"); return; }
    var resultDiv = document.getElementById("compare-result");
    if (!resultDiv) return;
    resultDiv.classList.remove("hidden");
    resultDiv.textContent = "";
    var loading = document.createElement("p");
    loading.className = "text-sm text-text-secondary";
    loading.textContent = "Comparing " + a + " vs " + b + "...";
    resultDiv.appendChild(loading);
    fetch("/api/cycle/compare?cycle_a=" + encodeURIComponent(a) + "&cycle_b=" + encodeURIComponent(b))
        .then(function(r) {
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
        }).then(function(data) {
            resultDiv.textContent = "";
            var pre = document.createElement("pre");
            pre.className = "text-xs font-mono bg-surface-sunken text-text-primary p-3 rounded overflow-auto";
            pre.textContent = JSON.stringify(data, null, 2);
            resultDiv.appendChild(pre);
        }).catch(function(err) {
            resultDiv.textContent = "";
            var errP = document.createElement("p");
            errP.className = "text-sm text-red-600";
            errP.textContent = "Comparison failed: " + err.message;
            resultDiv.appendChild(errP);
        });
}

function openTaxonomyBrowser() {
    history.pushState({ page: "/debug" }, "", "/debug?filter=taxonomy");
    window.location.href = "/debug?filter=taxonomy";
}

function promoteAllP1Global() {
    open_totp_modal({
        actionId: "pipeline.promote_all_p1",
        description: "Promote all P1 queue items",
        consequences: "All items in the P1 priority queue will be promoted for immediate processing.",
        callbackUrl: "/pipeline/queue/promote",
        onSuccess: function(data) {
            showToast("Promoted " + (data.promoted_count || "all") + " P1 items", "success");
        }
    });
}

function showShortcuts() {
    document.getElementById("shortcut-overlay").classList.remove("hidden");
}

function openTOTPManual() {
    open_totp_modal({
        actionId: "manual_totp_verify",
        description: "Manual TOTP verification",
        consequences: "No specific action gated — verify your TOTP code.",
    });
}

// ─── Keyboard Shortcuts (Source.md §13.6) ───────────────────────────────────

var PAGE_SHORTCUTS = ["/", "/agents", "/pipeline", "/universe", "/cortex", "/debug", "/settings"];

function isElementVisible(id) {
    var el = document.getElementById(id);
    return el ? !el.classList.contains("hidden") : false;
}

function toggleSidebar() {
    var sidebar = document.getElementById("sidebar");
    var main = document.getElementById("main-content");
    if (!sidebar || !main) return;
    var isCollapsed = sidebar.classList.toggle("collapsed");
    main.classList.toggle("sidebar-collapsed", isCollapsed);
    try { localStorage.setItem("sidebar-collapsed", isCollapsed); } catch (e) {}
}

document.addEventListener("keydown", function (e) {
    var isCmd = e.metaKey || e.ctrlKey;
    var isInput = e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT";
    var activeModal = isElementVisible("cmd-k") || isElementVisible("totp-modal") ||
                      isElementVisible("shortcut-overlay") || isElementVisible("blocking-modal");

    // Cmd-K: command palette
    if (isCmd && e.key === "k") {
        e.preventDefault();
        toggleCmdK();
        return;
    }

    // Cmd-1..7: page navigation
    if (isCmd && e.key >= "1" && e.key <= "7") {
        e.preventDefault();
        window.location.href = PAGE_SHORTCUTS[parseInt(e.key) - 1];
        return;
    }

    // Cmd-R: refresh current page
    if (isCmd && e.key === "r") {
        e.preventDefault();
        window.location.reload();
        return;
    }

    // Cmd-/: show keyboard shortcut overlay
    if (isCmd && e.key === "/") {
        e.preventDefault();
        document.getElementById("shortcut-overlay").classList.toggle("hidden");
        return;
    }

    // Cmd-Shift-K: engage kill switch (Agents page, with confirmation)
    if (isCmd && e.shiftKey && e.key === "K") {
        e.preventDefault();
        handleKillSwitch();
        return;
    }

    // Cmd-T: open TOTP modal with generic context (when no text input focused)
    if (isCmd && e.key === "t" && !isInput) {
        e.preventDefault();
        open_totp_modal({
            actionId: "manual_totp_verify",
            description: "Manual TOTP verification",
            consequences: "No specific action gated — verify your TOTP code.",
        });
        return;
    }

    // Esc: close modals/drawers/dismiss toasts
    if (e.key === "Escape") {
        var ccModal = document.getElementById("cycle-compare-modal");
        if (ccModal && !ccModal.classList.contains("hidden")) {
            ccModal.classList.add("hidden");
            return;
        }
        closeCmdK();
        closeTotpModal();
        var _shortcutOverlay = document.getElementById("shortcut-overlay");
        if (_shortcutOverlay) _shortcutOverlay.classList.add("hidden");
        // blocking-modal: Don't close with Esc (requires explicit acknowledgment)
        return;
    }

    // /: focus search/filter on current page
    if (e.key === "/" && !isInput && !activeModal) {
        e.preventDefault();
        var searchInput = document.querySelector("[data-page-search]") ||
                          document.querySelector('input[type="text"][placeholder*="earch"]') ||
                          document.querySelector('input[type="text"][placeholder*="ilter"]');
        if (searchInput) searchInput.focus();
        return;
    }

    // ?: contextual help
    if (e.key === "?" && !isInput && !activeModal) {
        e.preventDefault();
        var _shortcutOverlay2 = document.getElementById("shortcut-overlay");
        if (_shortcutOverlay2) _shortcutOverlay2.classList.toggle("hidden");
        return;
    }

    // Cmd-K palette: arrow keys + enter
    var _cmdK = document.getElementById("cmd-k");
    if (_cmdK && !_cmdK.classList.contains("hidden")) {
        var results = document.getElementById("cmd-k-results");
        var items = results ? results.querySelectorAll("li") : [];

        if (e.key === "ArrowDown") {
            e.preventDefault();
            cmdKActiveIndex = Math.min(cmdKActiveIndex + 1, items.length - 1);
            updateCmdKActiveItem(results);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            cmdKActiveIndex = Math.max(cmdKActiveIndex - 1, 0);
            updateCmdKActiveItem(results);
        } else if (e.key === "Enter" && cmdKActiveIndex >= 0 && items[cmdKActiveIndex]) {
            e.preventDefault();
            items[cmdKActiveIndex].click();
        }
    }
});

// Bind search input
document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("cmd-k-input");
    if (input) {
        input.addEventListener("input", function (e) {
            renderCmdKResults(e.target.value);
        });
    }
});

// Close Cmd-K when clicking backdrop
document.addEventListener("click", function (e) {
    var cmdK = document.getElementById("cmd-k");
    if (cmdK && e.target === cmdK) {
        closeCmdK();
    }
    var overlay = document.getElementById("shortcut-overlay");
    if (overlay && e.target === overlay) {
        overlay.classList.add("hidden");
    }
});

// ─── Kill Switch Handler (Source.md §21.6) ──────────────────────────────────

function handleKillSwitch() {
    showBlockingModal(
        "Engage Kill Switch?",
        "All trading halts. Stop-loss execution continues. TOTP not required to engage.",
        [
            { label: "Cancel", primary: false },
            {
                label: "Engage",
                primary: true,
                action: function () {
                    fetch("/api/cortex/kill-switch/engage", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" }
                    }).then(function (resp) {
                        if (!resp.ok) throw new Error("HTTP " + resp.status);
                        return resp.json();
                    }).then(function () {
                        var btn = document.getElementById("kill-switch-btn");
                        if (btn) {
                            btn.classList.add("bg-red-600");
                            btn.classList.remove("bg-surface-sunken");
                        }
                        showToast("Kill switch ENGAGED. To disengage: Cortex page.", "error", 0);
                    }).catch(function (err) {
                        showToast("Kill switch engage failed: " + err.message, "critical", 0);
                    });
                },
            },
        ]
    );
}

// ─── TOTP Modal (Source.md §13.2, §13.3 TOTPField) ───────────────────────
//
// Reusable parameterizable TOTP modal. Any gated action calls open_totp_modal({...}).
//
// Gated actions per Source.md §6 (Decision rights matrix):
//   Settings: broker key edit, catastrophe-net % change, kill-switch threshold,
//             mutation promote/reject/rollback, persona enable/disable, mode override
//   Universe: add/remove ticker, bulk tag/remove
//   Pipeline: force exit (active only)
//   Cortex: kill switch disengage (engage does NOT require TOTP)

var totpModalState = {
    actionId: "",
    callbackUrl: "",
    confirmText: "",
    pendingAction: null,  // optional function to call on success
    extra: {},            // action-specific data (e.g. ticker, tickers)
    verifiedCode: "",     // TOTP code after verification
};

/**
 * Open the TOTP modal with action context.
 * @param {Object} opts
 * @param {string} opts.actionId         — unique action identifier
 * @param {string} opts.description      — human-readable action description
 * @param {string} opts.consequences     — what happens if confirmed
 * @param {string} [opts.confirmText]    — text operator must type (e.g. "KILL")
 * @param {string} [opts.callbackUrl]    — URL to POST after TOTP verified
 * @param {Function} [opts.onSuccess]    — function to call on verification success
 * @param {Object} [opts.extra]          — action-specific data to pass through
 */
function open_totp_modal(opts) {
    var modal = document.getElementById("totp-modal");
    if (!modal) return;

    // Store state
    totpModalState.actionId = opts.actionId || "";
    totpModalState.callbackUrl = opts.callbackUrl || "";
    totpModalState.confirmText = opts.confirmText || "";
    totpModalState.pendingAction = opts.onSuccess || null;
    totpModalState.extra = opts.extra || {};
    totpModalState.verifiedCode = "";

    // Set data attributes
    modal.setAttribute("data-action-id", totpModalState.actionId);
    modal.setAttribute("data-action-description", opts.description || "");
    modal.setAttribute("data-consequences", opts.consequences || "");
    modal.setAttribute("data-confirm-text", totpModalState.confirmText);
    modal.setAttribute("data-callback-url", totpModalState.callbackUrl);

    // Populate visible elements
    document.getElementById("totp-action-description").textContent = opts.description || "";
    document.getElementById("totp-consequences").textContent = opts.consequences || "";

    // Confirmation text field (for destructive actions)
    var confirmGroup = document.getElementById("totp-confirm-group");
    var confirmInput = document.getElementById("totp-confirm-input");
    var confirmRequiredText = document.getElementById("totp-confirm-required-text");
    if (totpModalState.confirmText) {
        confirmGroup.classList.remove("hidden");
        confirmRequiredText.textContent = totpModalState.confirmText;
        confirmInput.value = "";
    } else {
        confirmGroup.classList.add("hidden");
        confirmInput.value = "";
    }

    // Clear previous state
    var digits = modal.querySelectorAll(".totp-digit");
    digits.forEach(function (d) { d.value = ""; });
    document.getElementById("totp-error").classList.add("hidden");
    document.getElementById("totp-confirm-mismatch").classList.add("hidden");

    // Reset confirm button state
    updateTotpConfirmButton();

    // Show modal
    modal.classList.remove("hidden");

    // Focus first TOTP digit
    if (digits.length > 0) {
        digits[0].focus();
    }
}

function closeTotpModal() {
    var modal = document.getElementById("totp-modal");
    if (modal) modal.classList.add("hidden");
    totpModalState.pendingAction = null;
}

/**
 * Check whether the Confirm button should be enabled.
 * Enabled when: TOTP code is 6 digits AND (no confirm text required OR confirm text matches).
 */
function updateTotpConfirmButton() {
    var btn = document.getElementById("totp-confirm-btn");
    if (!btn) return;

    // Check TOTP digits
    var digits = document.querySelectorAll("#totp-modal .totp-digit");
    var code = "";
    digits.forEach(function (d) { code += d.value; });
    var totpComplete = code.length === 6;

    // Check confirmation text
    var confirmRequired = totpModalState.confirmText || "";
    var confirmInput = document.getElementById("totp-confirm-input");
    var confirmMatch = true;
    if (confirmRequired) {
        confirmMatch = confirmInput.value === confirmRequired;
        var mismatch = document.getElementById("totp-confirm-mismatch");
        if (confirmInput.value && !confirmMatch) {
            mismatch.classList.remove("hidden");
        } else {
            mismatch.classList.add("hidden");
        }
    }

    btn.disabled = !(totpComplete && confirmMatch);
}

/**
 * Submit TOTP code to /api/totp/verify, then execute the gated action on success.
 */
function submitTotp() {
    var digits = document.querySelectorAll("#totp-modal .totp-digit");
    var code = "";
    digits.forEach(function (d) { code += d.value; });

    if (code.length !== 6) {
        showToast("Enter all 6 digits", "warning");
        return;
    }

    // Check confirmation text if required
    if (totpModalState.confirmText) {
        var confirmInput = document.getElementById("totp-confirm-input");
        if (confirmInput.value !== totpModalState.confirmText) {
            document.getElementById("totp-confirm-mismatch").classList.remove("hidden");
            return;
        }
    }

    var errorEl = document.getElementById("totp-error");
    var confirmBtn = document.getElementById("totp-confirm-btn");
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Verifying...";

    fetch("/api/totp/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            code: code,
            action_id: totpModalState.actionId,
        }),
    })
    .then(function (resp) {
        return resp.json();
    })
    .then(function (data) {
        if (!data.verified) {
            throw new Error(data.error || "Verification failed");
        }
        // TOTP verified — close modal
        totpModalState.verifiedCode = code;
        closeTotpModal();
        showToast("Action confirmed", "success");

        // Execute gated action
        if (totpModalState.callbackUrl) {
            executeGatedAction(totpModalState.callbackUrl, totpModalState.actionId, code);
        } else if (totpModalState.pendingAction) {
            totpModalState.pendingAction({verified: true, code: code, extra: totpModalState.extra});
        }
    })
    .catch(function (err) {
        // Keep modal open, show error
        errorEl.textContent = err.message || "Verification failed. Try again.";
        errorEl.classList.remove("hidden");
        confirmBtn.disabled = false;
        confirmBtn.textContent = "Confirm";
        // Clear digits for retry
        digits.forEach(function (d) { d.value = ""; });
        if (digits.length > 0) digits[0].focus();
        updateTotpConfirmButton();
    });
}

/**
 * Execute a gated action by POSTing to the callback URL.
 * Includes TOTP code and any extra data from the modal state.
 */
function executeGatedAction(callbackUrl, actionId, totpCode) {
    var body = Object.assign(
        { action_id: actionId, totp_code: totpCode || "" },
        totpModalState.extra || {}
    );
    fetch(callbackUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    })
    .then(function (resp) {
        if (resp.ok) {
            return resp.json();
        }
        return resp.json().then(function (data) {
            throw new Error(data.detail || "Action failed");
        });
    })
    .then(function (data) {
        showToast(data.message || "Action completed", "success");
        // Reload page to reflect changes
        if (data.reload) {
            window.location.reload();
        }
    })
    .catch(function (err) {
        showToast("Action failed: " + (err.message || "Unknown error"), "error");
    });
}

// TOTP input auto-advance and confirmation-text validation
document.addEventListener("DOMContentLoaded", function () {
    var modal = document.getElementById("totp-modal");
    if (!modal) return;

    var digits = modal.querySelectorAll(".totp-digit");
    digits.forEach(function (digit, index) {
        // Handle digit keys via keydown for reliable auto-advance
        digit.addEventListener("keydown", function (e) {
            // Digit keys (0-9)
            if (e.key >= "0" && e.key <= "9" && !e.metaKey && !e.ctrlKey && !e.altKey) {
                e.preventDefault();
                e.target.value = e.key;
                updateTotpConfirmButton();
                if (index < digits.length - 1) {
                    digits[index + 1].focus();
                }
                // Auto-submit on last digit
                if (index === digits.length - 1) {
                    var confirmRequired = totpModalState.confirmText || "";
                    if (!confirmRequired || document.getElementById("totp-confirm-input").value === confirmRequired) {
                        submitTotp();
                    }
                }
                return;
            }
            // Backspace: clear current or go back
            if (e.key === "Backspace") {
                if (!e.target.value && index > 0) {
                    e.preventDefault();
                    digits[index - 1].focus();
                } else {
                    e.target.value = "";
                    updateTotpConfirmButton();
                }
                return;
            }
        });

        // Handle paste: distribute digits across all fields
        digit.addEventListener("paste", function (e) {
            e.preventDefault();
            var text = (e.clipboardData || window.clipboardData).getData("text").replace(/[^0-9]/g, "");
            for (var j = 0; j < Math.min(text.length, digits.length); j++) {
                digits[j].value = text[j];
            }
            var focusIndex = Math.min(text.length, digits.length) - 1;
            if (focusIndex >= 0) digits[focusIndex].focus();
            updateTotpConfirmButton();
            if (text.length >= 6) {
                var confirmRequired = totpModalState.confirmText || "";
                if (!confirmRequired || document.getElementById("totp-confirm-input").value === confirmRequired) {
                    submitTotp();
                }
            }
        });
    });

    // Confirmation text input validation
    var confirmInput = document.getElementById("totp-confirm-input");
    if (confirmInput) {
        confirmInput.addEventListener("input", function () {
            updateTotpConfirmButton();
        });
    }

    // Cancel button
    var cancelBtn = document.getElementById("totp-cancel-btn");
    if (cancelBtn) {
        cancelBtn.addEventListener("click", closeTotpModal);
    }

    // Confirm button
    var confirmBtn = document.getElementById("totp-confirm-btn");
    if (confirmBtn) {
        confirmBtn.addEventListener("click", submitTotp);
    }
});

// ─── Debug Page: Event Filtering ────────────────────────────────────────────

function filterEvents(type) {
    var rows = document.querySelectorAll("[data-event-level]");
    rows.forEach(function (row) {
        if (type === "ALL" || row.getAttribute("data-event-level") === type) {
            row.classList.remove("hidden");
        } else {
            row.classList.add("hidden");
        }
    });
}

function toggleEventDetail(el) {
    var detail = el.querySelector(".event-detail-row");
    if (detail) {
        detail.classList.toggle("hidden");
    }
}

// ─── Copy for Claude Code ───────────────────────────────────────────────────

/**
 * Copy a formatted Claude Code prompt from a debug event button.
 * Reads event metadata from data attributes on the button element.
 */
function copyForClaudeCode(btn) {
    var eventId = btn.getAttribute("data-event-id") || "";
    var errorCode = btn.getAttribute("data-error-code") || "";
    var specRef = btn.getAttribute("data-spec-ref") || "";
    var message = btn.getAttribute("data-message") || "";
    var level = btn.getAttribute("data-level") || "";
    var stream = btn.getAttribute("data-stream") || "";
    var cycleId = btn.getAttribute("data-cycle-id") || "";
    var timestamp = btn.getAttribute("data-timestamp") || "";

    // Read the payload pre block from the parent container
    var detailContainer = btn.closest("div");
    var preEl = detailContainer ? detailContainer.querySelector("pre") : null;
    var payload = preEl ? preEl.textContent.trim() : "No payload available";

    // Build paste-ready prompt for Claude Code
    var lines = [
        "## PMACS Debug Event",
        "",
        "**Event ID:** " + eventId,
        "**Level:** " + level,
        "**Stream:** " + stream,
        "**Timestamp:** " + timestamp,
    ];
    if (errorCode) {
        lines.push("**Error Code:** " + errorCode);
    }
    if (cycleId) {
        lines.push("**Cycle ID:** " + cycleId);
    }
    if (specRef) {
        lines.push("**Spec Reference:** " + specRef);
    }
    lines.push("");
    lines.push("**Message:** " + message);
    lines.push("");
    lines.push("### Payload");
    lines.push("```json");
    lines.push(payload);
    lines.push("```");
    lines.push("");
    if (errorCode) {
        lines.push("### Reproduction Steps");
        lines.push("1. Run a PMACS cycle");
        lines.push("2. Look for **" + errorCode + "** in debug events");
        lines.push("3. Check the " + stream + " stream for related warnings");
    }
    lines.push("");
    lines.push("Please investigate this error and suggest a fix. Reference the spec at " + (specRef || "the relevant Architecture.md section") + ".");

    var prompt = lines.join("\n");

    navigator.clipboard.writeText(prompt).then(function () {
        showToast("Claude Code prompt copied to clipboard", "success");
    }).catch(function () {
        showToast("Failed to copy — check clipboard permissions", "warning");
    });
}

/**
 * Copy error state context as a Claude Code prompt.
 * Reads error-state-specific data attributes (Source.md §13.4).
 * Separate from copyForClaudeCode which serves debug events.
 */
function copyErrorForClaude(btn) {
    var errorCode = btn.getAttribute("data-error-code") || "UNKNOWN";
    var description = btn.getAttribute("data-error-description") || "";
    var explanation = btn.getAttribute("data-error-explanation") || "";
    // Try to find spec link in sibling elements
    var specLink = btn.parentElement ? btn.parentElement.querySelector("a[href]") : null;
    var specRef = specLink ? specLink.getAttribute("href") || "" : "";

    var lines = [
        "## PMACS Error State",
        "",
        "**Error Code:** " + errorCode,
        "**Description:** " + description,
        ""
    ];
    if (explanation) {
        lines.push("**Explanation:** " + explanation);
        lines.push("");
    }
    if (specRef) {
        lines.push("**Spec Reference:** " + specRef);
        lines.push("");
    }
    lines.push("Please analyze this error and suggest a fix.");

    var text = lines.join("\n");
    navigator.clipboard.writeText(text).then(function() {
        showToast("Error context copied to clipboard", "success", 3000);
    }).catch(function() {
        showToast("Failed to copy to clipboard", "error");
    });
}

/**
 * Copy raw event JSON from the detail container.
 */
function copyEventJSON(btn) {
    var detailContainer = btn.closest("div");
    var preEl = detailContainer ? detailContainer.querySelector("pre") : null;
    var payload = preEl ? preEl.textContent.trim() : "";

    navigator.clipboard.writeText(payload).then(function () {
        showToast("JSON copied to clipboard", "success");
    }).catch(function () {
        showToast("Failed to copy", "warning");
    });
}

/**
 * Legacy function: copy debug event from row data attribute.
 */
function copyDebugEvent(btn) {
    var row = btn.closest("[data-debug-event]");
    var text = row ? row.getAttribute("data-debug-event") : "";
    var prompt = "PMACS debug event:\n" + text + "\nPlease investigate.";

    navigator.clipboard.writeText(prompt).then(function () {
        showToast("Debug event copied for Claude Code", "success");
    }).catch(function () {
        showToast("Failed to copy", "warning");
    });
}

// ─── SSE Event Handler Registration ─────────────────────────────────────────

// System events — dispatch notifications, errors, and kill switch alerts
onSSE("system", function (data) {
    if (data.event_type) {
        handleNotification(data.event_type, data);
    }
    // Kill switch engagement
    if (data.event_type === "system.kill_switch_engaged" || data.engaged) {
        handleNotification("kill_switch_engaged", data);
    }
    // Kill switch disengaged
    if (data.event_type === "system.kill_switch_disengaged") {
        handleNotification("kill_switch_disengaged", data);
    }
    // System heartbeat
    if (data.event_type === "system.heartbeat") {
        var indicator = document.getElementById("system-status");
        if (indicator) { indicator.textContent = "Connected"; }
    }
    // Error events from system stream
    if (data.event === "error" || data.level === "ERROR") {
        showToast("Error: " + (data.message || "Unknown"), "error");
    }
});

// Cycle events
function _setCycleIndicator(html) {
    var indicator = document.getElementById("cycle-indicator");
    if (!indicator) return;
    indicator.innerHTML = '<span class="inline-flex items-center gap-2"><span class="live-dot"></span>' + html + '</span>';
}

onSSE("cycle", function (data) {
    if (data.event_type === "cycle.opened" || data.event === "cycle_start") {
        _setCycleIndicator("Running: " + (data.tickers ? data.tickers.length : 0) + " tickers — ETA " + escapeHtml(data.eta || "calculating..."));
        var bar = document.getElementById("cycle-progress-bar");
        if (bar) { bar.classList.remove("hidden"); bar.style.width = "0%"; }
    }
    if (data.event_type === "cycle.closed" || data.event === "cycle_complete") {
        var timeLabel = data.completed_at ? timeAgo(data.completed_at) : "just now";
        _setCycleIndicator('Idle. Last cycle: <span id="cycle-indicator-time"' +
            (data.completed_at ? ' data-time-ago="' + escapeHtml(data.completed_at) + '"' : '') +
            '>' + timeLabel + '</span>');
        var bar = document.getElementById("cycle-progress-bar");
        if (bar) bar.classList.add("hidden");
        showToast("Cycle complete: " + (data.tickers_processed || 0) + " tickers processed", "info");
        // Re-enable run cycle button
        var _btn = document.getElementById("run-cycle-btn");
        var _spinner = document.getElementById("run-cycle-spinner");
        var _label = document.getElementById("run-cycle-label");
        if (_btn) { _btn.disabled = false; _btn.classList.remove("opacity-60", "cursor-not-allowed"); }
        if (_spinner) _spinner.classList.add("hidden");
        if (_label) _label.textContent = "Run cycle";
        // Update last-cycle-time element with relative time
        var lctEl = document.getElementById("last-cycle-time");
        if (lctEl && data.completed_at) {
            lctEl.setAttribute("data-time-ago", data.completed_at);
            lctEl.textContent = timeLabel;
        }
        // Partial refresh via HTMX — debounced to prevent stacking if event fires twice
        if (typeof htmx !== "undefined") {
            if (window._cycleRefreshTimer) clearTimeout(window._cycleRefreshTimer);
            window._cycleRefreshTimer = setTimeout(function() {
                window._cycleRefreshTimer = null;
                htmx.ajax("GET", window.location.pathname, {
                    target: "#main-content",
                    select: "#main-content",
                    swap: "outerHTML"
                });
            }, 800);
        }
    }
    if (data.event_type === "ticker_progress" || data.event === "ticker_progress") {
        var progress = data.progress || "";
        var match = progress.match(/(\d+)\/(\d+)/);
        var pctStr = match ? " (" + Math.round((parseInt(match[1]) / parseInt(match[2])) * 100) + "%)" : (progress ? " (" + progress + ")" : "");
        _setCycleIndicator("Processing: " + (data.ticker || "") + pctStr);
        if (match) {
            var pct = Math.round((parseInt(match[1]) / parseInt(match[2])) * 100);
            var bar = document.getElementById("cycle-progress-bar");
            if (bar) bar.style.width = pct + "%";
        }
    }
    // Notification-policy-driven cycle events
    if (data.event_type) {
        handleNotification(data.event_type, data);
    }
});

// Trade events
onSSE("trade", function (data) {
    if (data.event_type === "trade.filled" || data.event === "filled") {
        handleNotification(data.mode === "LIVE" ? "trade_filled_live" : "trade_filled_paper", data);
    }
    if (data.event_type === "trade.signed") {
        showToast("Trade signed: " + (data.ticker || ""), "info");
    }
    if (data.event_type === "trade.submitted") {
        showToast("Order submitted: " + (data.ticker || ""), "info");
    }
    if (data.event_type === "trade.rejected") {
        showToast("Trade rejected: " + (data.ticker || "") + " — " + (data.reason || ""), "error");
    }
    if (data.event_type) {
        handleNotification(data.event_type, data);
    }
});

// Decision events
onSSE("decision", function (data) {
    if (data.event_type === "decision.arbitrated") {
        var verdictEl = document.getElementById("latest-verdict");
        if (verdictEl) {
            verdictEl.textContent = (data.ticker || "") + ": " + (data.decision || "") +
                " (p\u2191" + (data.p_up || "\u2014") + " p\u2193" + (data.p_down || "\u2014") + ")";
        }
    }
    if (data.event_type === "decision.final") {
        // Only show toast for actionable decisions — SKIP is expected and noisy
        if (data.verdict && data.verdict !== "SKIP" && data.verdict !== "ERROR") {
            var cPct = ((data.conviction || 0) * 100).toFixed(0);
            showToast((data.ticker || "") + ": " + data.verdict + " (" + cPct + "% conviction)", "info");
        }
        // Always update the verdict display element
        var verdictEl2 = document.getElementById("latest-verdict");
        if (verdictEl2) {
            verdictEl2.textContent = (data.ticker || "") + ": " + (data.verdict || "—");
        }
    }
    if (data.event_type) {
        handleNotification(data.event_type, data);
    }
});

// Agent events
onSSE("agent", function (data) {
    // agents.html has its own richer handler with design-system classes.
    // Skip card DOM updates on /agents to avoid class conflicts.
    if (window.location.pathname !== "/agents") {
        var card = document.querySelector('[data-persona="' + (data.persona || "").toLowerCase() + '"]');
        if (card) {
            var badge = card.querySelector("[data-status-badge]");
            var progressBar = card.querySelector("[data-progress-bar]");
            var statusText = card.querySelector("[data-status-text]");

            if (data.event_type === "agent.queued") {
                if (badge) { badge.textContent = "queued"; badge.className = "px-2.5 py-0.5 text-xs rounded-xl font-medium bg-surface-sunken text-text-muted"; }
                if (statusText) { statusText.textContent = "Queued for " + (data.ticker || ""); }
            }
            if (data.event_type === "agent.running") {
                if (badge) { badge.textContent = "running"; badge.className = "px-2.5 py-0.5 text-xs rounded-xl font-medium bg-accent-soft text-accent"; }
                if (progressBar) { progressBar.style.width = "30%"; progressBar.className = "persona-progress-fill status-running"; }
                if (statusText) { statusText.textContent = "Analyzing " + (data.ticker || "") + "..."; }
            }
            if (data.event_type === "agent.complete") {
                if (badge) { badge.textContent = "complete"; badge.className = "px-2.5 py-0.5 text-xs rounded-xl font-medium bg-positive-soft text-positive"; }
                if (progressBar) { progressBar.style.width = "100%"; progressBar.className = "persona-progress-fill status-complete"; }
                if (statusText) { statusText.textContent = "Complete"; }
            }
            if (data.event_type === "agent.failed") {
                if (badge) { badge.textContent = "error"; badge.className = "px-2.5 py-0.5 text-xs rounded-xl font-medium bg-negative-soft text-negative"; }
                if (progressBar) { progressBar.style.width = "100%"; progressBar.className = "persona-progress-fill status-error"; }
                if (statusText) { statusText.textContent = "Failed: " + (data.reason || "unknown"); }
            }
        }
    }
    if (data.event_type) {
        handleNotification(data.event_type, data);
    }
});

// Mutation events
onSSE("mutation", function (data) {
    if (data.event === "candidate_ready" || data.event_type === "mutation.candidate_ready") {
        handleNotification("mutation_candidate_ready", data);
    }
    if (data.event === "mutation_approved" || data.event_type === "mutation.promoted") {
        handleNotification("mutation_approved", data);
    }
    if (data.event_type) {
        handleNotification(data.event_type, data);
    }
});

// Sparkline update events — triggered by cycle stream (event: "sparkline_update")
// Also registered on "cycle" stream for cycle_complete refresh
onSSE("cycle", function (data) {
    if (data.event === "sparkline_update" && data.metric) {
        refreshSparkline(data.metric);
    }
});

function refreshSparkline(metric) {
    var container = document.querySelector('[data-sparkline-metric="' + metric + '"]');
    if (!container) return;
    var activeBtn = document.querySelector(".sparkline-window-btn.bg-blue-50");
    var windowParam = activeBtn ? (activeBtn.getAttribute("data-window") || "1W") : "1W";
    fetch("/api/dashboard/sparkline?metric=" + encodeURIComponent(metric) + "&window=" + encodeURIComponent(windowParam))
        .then(function (resp) {
            if (!resp.ok) return null;
            return resp.json();
        })
        .then(function (points) {
            if (!points || points.length < 2) {
                container.innerHTML = '<div class="w-full h-10 flex items-center justify-center">' +
                    '<span class="text-xs text-text-muted">No data yet</span></div>';
                return;
            }
            container.innerHTML = renderSparklineSVG(points);
        })
        .catch(function () {
            // Graceful degradation — leave existing sparkline unchanged
        });
}

function renderSparklineSVG(points) {
    var values = points.map(function (p) { return p.v; });
    var vmin = Math.min.apply(null, values);
    var vmax = Math.max.apply(null, values);
    var vrange = Math.max(vmax - vmin, 0.001);
    var n = points.length;
    var H = 36; // drawable height in viewBox
    var VB_H = 40; // viewBox total height
    var pad = 2; // top/bottom padding
    var pts = [];
    for (var i = 0; i < n; i++) {
        var x = (i / (n - 1) * 100).toFixed(1);
        var y = (pad + (1 - (points[i].v - vmin) / vrange) * H).toFixed(1);
        pts.push(x + "," + y);
    }
    var lastY = (pad + (1 - (values[n - 1] - vmin) / vrange) * H).toFixed(0);
    var firstY = (pad + (1 - (values[0] - vmin) / vrange) * H).toFixed(1);
    // Detect trend
    var trend = values[n - 1] >= values[0] ? 'positive' : 'negative';
    var trendClass = 'sparkline-' + trend;
    // Area fill polygon: line points + bottom corners
    var areaPoints = pts.join(" ") + " 100," + VB_H + " 0," + VB_H;
    return '<div class="sparkline-container ' + trendClass + '">' +
        '<svg viewBox="0 0 100 ' + VB_H + '" preserveAspectRatio="none" class="w-full" style="height:40px">' +
        '<polygon class="sparkline-area" points="' + areaPoints + '"/>' +
        '<polyline class="sparkline-line" points="' + pts.join(" ") + '"/>' +
        '</svg>' +
        '<div class="sparkline-point absolute w-1.5 h-1.5 rounded-full' +
        (trend === 'positive' ? ' bg-green-600' : ' bg-red-500') +
        '" style="left:100%;top:' + lastY + 'px;transform:translate(-50%,-50%)"></div>' +
        '</div>';
}

// ─── Viewport Guard (Source.md §13.7: minimum 1024px) ──────────────────────

/**
 * Refresh all sparkline metrics for a given time window.
 * Called by sparkline window buttons on dashboard.
 * @param {string} window - Time window (1D, 1W, 1M, 3M, ALL).
 * @param {HTMLElement} clickedBtn - The button that was clicked (for active state).
 */
function refreshAllSparklines(window, clickedBtn) {
    // Update active button state
    var container = document.getElementById("sparkline-window-btns");
    if (container) {
        container.querySelectorAll(".sparkline-window-btn").forEach(function (btn) {
            btn.classList.remove("bg-blue-50", "text-blue-600");
            btn.classList.add("text-text-muted");
        });
    }
    if (clickedBtn) {
        clickedBtn.classList.remove("text-text-muted");
        clickedBtn.classList.add("bg-blue-50", "text-blue-600");
    }

    // Fetch and update each sparkline metric
    var metrics = document.querySelectorAll("[data-sparkline-metric]");
    metrics.forEach(function (el) {
        var metric = el.getAttribute("data-sparkline-metric");
        fetch("/api/dashboard/sparkline?metric=" + encodeURIComponent(metric) + "&window=" + encodeURIComponent(window))
            .then(function (resp) {
                if (!resp.ok) return null;
                return resp.json();
            })
            .then(function (points) {
                if (!points || points.length < 2) {
                    el.innerHTML = '<div class="w-full h-10 flex items-center justify-center">' +
                        '<span class="text-xs text-text-muted">No data yet</span></div>';
                    return;
                }
                el.innerHTML = renderSparklineSVG(points);
            })
            .catch(function () {
                // Leave existing sparkline unchanged on fetch failure
            });
    });
}

// ─── Viewport Guard End ────────────────────────────────────────────────────

function checkViewportWidth() {
    // No hard block for mobile — responsive layout handles it
}

window.addEventListener("resize", checkViewportWidth);
document.addEventListener("DOMContentLoaded", checkViewportWidth);

// ─── Mobile Sidebar Toggle ─────────────────────────────────────────────────

function toggleMobileSidebar() {
    var sidebar = document.getElementById("sidebar");
    var overlay = document.getElementById("mobile-sidebar-overlay");
    if (!sidebar) return;
    var isOpen = sidebar.classList.contains("mobile-open");
    if (isOpen) {
        sidebar.classList.remove("mobile-open");
        if (overlay) overlay.classList.add("hidden");
    } else {
        sidebar.classList.add("mobile-open");
        if (overlay) overlay.classList.remove("hidden");
    }
}

document.addEventListener("DOMContentLoaded", function() {
    var overlay = document.getElementById("mobile-sidebar-overlay");
    if (overlay) {
        overlay.addEventListener("click", toggleMobileSidebar);
    }
});

// ─── Sidebar State Restoration ─────────────────────────────────────────────

// Restore sidebar state from localStorage
(function() {
    try {
        if (localStorage.getItem("sidebar-collapsed") === "true") {
            var sidebar = document.getElementById("sidebar");
            var main = document.getElementById("main-content");
            if (sidebar) sidebar.classList.add("collapsed");
            if (main) main.classList.add("sidebar-collapsed");
        }
    } catch (e) {}
})();

// ─── Focus Trap (Source.md §13.7 a11y) ────────────────────────────────────────

var focusTrapState = { el: null, previouslyFocused: null };

function trapFocus(modalEl) {
    if (!modalEl) return;
    focusTrapState.previouslyFocused = document.activeElement;
    focusTrapState.el = modalEl;

    var focusable = modalEl.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    );
    if (focusable.length === 0) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];

    first.focus();

    modalEl._trapHandler = function (e) {
        if (e.key !== "Tab") return;
        if (e.shiftKey) {
            if (document.activeElement === first) {
                e.preventDefault();
                last.focus();
            }
        } else {
            if (document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    };
    modalEl.addEventListener("keydown", modalEl._trapHandler);
}

function releaseFocus() {
    if (focusTrapState.el && focusTrapState.el._trapHandler) {
        focusTrapState.el.removeEventListener("keydown", focusTrapState.el._trapHandler);
        delete focusTrapState.el._trapHandler;
    }
    if (focusTrapState.previouslyFocused) {
        focusTrapState.previouslyFocused.focus();
        focusTrapState.previouslyFocused = null;
    }
    focusTrapState.el = null;
}

// ─── Cycle Timing (Source.md §15 performance) ────────────────────────────────

var cycleTimings = [];

onSSE("cycle", function (data) {
    if (data.event === "cycle_start") {
        cycleTimings.push({ start: Date.now(), ticker: data.ticker || "" });
    }
    if (data.event === "cycle_complete") {
        var el = document.getElementById("cycle-timing");
        if (!el) return;
        var last = cycleTimings[cycleTimings.length - 1];
        if (!last) return;
        var duration = ((Date.now() - last.start) / 1000).toFixed(1);
        el.textContent = duration + "s";
        // Keep last 20
        if (cycleTimings.length > 20) cycleTimings.shift();
    }
});

// ─── Staggered Entrance (Source.md §13.7 a11y-safe) ──────────────────────────

function initStaggeredEntrance() {
    if (prefersReducedMotion) return;
    var cards = document.querySelectorAll("[data-stagger]");
    cards.forEach(function (card, i) {
        card.style.opacity = "0";
        card.style.transform = "translateY(8px)";
        setTimeout(function () {
            card.style.transition = "opacity 0.2s ease-out, transform 0.2s ease-out";
            card.style.opacity = "1";
            card.style.transform = "translateY(0)";
        }, 40 * i);
    });
}

document.addEventListener("DOMContentLoaded", initStaggeredEntrance);
document.addEventListener("htmx:afterSwap", function (e) {
    if (e.detail && e.detail.target && e.detail.target.id === "main-content") {
        initStaggeredEntrance();
    }
});

// ─── Modal focus trap wiring ─────────────────────────────────────────────────

// Trap focus when modals open, release when they close
var _origToggleCmdK = toggleCmdK;
toggleCmdK = function () {
    var el = document.getElementById("cmd-k");
    var wasHidden = el && el.classList.contains("hidden");
    _origToggleCmdK();
    if (wasHidden) {
        trapFocus(el);
    } else {
        releaseFocus();
    }
};

var _origCloseCmdK = closeCmdK;
closeCmdK = function () {
    releaseFocus();
    _origCloseCmdK();
};

// TOTP modal focus trap wiring
var _origOpenTotpModal = open_totp_modal;
open_totp_modal = function (opts) {
    _origOpenTotpModal(opts);
    var modal = document.getElementById("totp-modal");
    if (modal) trapFocus(modal);
};

var _origCloseTotpModal = closeTotpModal;
closeTotpModal = function () {
    releaseFocus();
    _origCloseTotpModal();
};

// Blocking modal focus trap wiring
// The blocking modal's buttons close by adding .hidden directly,
// so we observe attribute changes to release focus on close.
var _origShowBlockingModal = showBlockingModal;
showBlockingModal = function (title, message, buttons) {
    _origShowBlockingModal(title, message, buttons);
    var modal = document.getElementById("blocking-modal");
    if (modal) {
        trapFocus(modal);
        // Observe hidden class to release focus when modal is dismissed
        if (!modal._blockObserver) {
            modal._blockObserver = new MutationObserver(function (mutations) {
                mutations.forEach(function (m) {
                    if (m.attributeName === "class" && modal.classList.contains("hidden")) {
                        releaseFocus();
                    }
                });
            });
            modal._blockObserver.observe(modal, { attributes: true, attributeFilter: ["class"] });
        }
    }
};

// ─── HTMX afterSwap — reinitialize page-specific JS after navigation ─────────

document.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail && event.detail.target;
    if (!target) return;
    var targetId = target.id || "";

    // Re-run viewport check after any swap
    checkViewportWidth();

    // After main content swap, reinitialize page-specific JS
    if (targetId === "main-content") {
        // Reconnect SSE if not already connected
        if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
            connectSSE();
        }

        // Update sidebar active state based on current URL
        var currentPath = window.location.pathname;
        var navLinks = document.querySelectorAll("#sidebar a[href]");
        navLinks.forEach(function (link) {
            var href = link.getAttribute("href");
            if (href === currentPath) {
                link.classList.add("bg-blue-50", "text-blue-600", "font-medium");
                link.style.borderLeft = "2px solid #2563eb";
                link.setAttribute("aria-current", "page");
            } else {
                link.classList.remove("bg-blue-50", "text-blue-600", "font-medium");
                link.style.borderLeft = "";
                link.removeAttribute("aria-current");
            }
        });

        // Re-initialize Sankey if on agents page
        if (currentPath === "/agents" && typeof PMACS_SANKEY !== "undefined") {
            PMACS_SANKEY.init();
        }
    }
});

// Handle HTMX history navigation (back/forward)
document.addEventListener("htmx:historyRestore", function () {
    checkViewportWidth();
});

// Update active window button on sparkline window swap
document.addEventListener("htmx:afterRequest", function (event) {
    var elt = event.detail && event.detail.elt;
    if (!elt) return;
    if (elt.classList && elt.classList.contains("sparkline-window-btn")) {
        var container = document.getElementById("sparkline-window-btns");
        if (!container) return;
        var buttons = container.querySelectorAll(".sparkline-window-btn");
        buttons.forEach(function (btn) {
            btn.classList.remove("bg-blue-50", "text-blue-600");
            btn.classList.add("text-text-muted");
        });
        elt.classList.remove("text-text-muted");
        elt.classList.add("bg-blue-50", "text-blue-600");
    }
});
