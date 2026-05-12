/**
 * PMACS Dashboard client-side JavaScript.
 * Handles SSE, Cmd-K palette, TOTP input, toast notifications,
 * keyboard shortcuts, notification policy, and accessibility.
 *
 * Spec: Source.md §13.2 (chrome), §13.5 (notifications), §13.6 (shortcuts), §13.7 (a11y)
 */

// ─── Feature Detection ─────────────────────────────────────────────────────

var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ─── SSE Connection ─────────────────────────────────────────────────────────

var SSE_URL = "http://127.0.0.1:8000/events";
var eventSource = null;
var eventHandlers = {};
var sseRetryCount = 0;
var SSE_MAX_RETRIES = 20;
var sseReconnectTimer = null;

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
        eventSource = new EventSource(SSE_URL);

        eventSource.onopen = function () {
            sseRetryCount = 0;
        };

        eventSource.onmessage = function (event) {
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
            eventSource.close();
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
        button.className = "px-4 py-2 text-sm rounded " + (btn.primary ? "bg-red-600 text-white hover:bg-red-700" : "bg-zinc-200 text-zinc-700 hover:bg-zinc-300");
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

    playSound(policy.sound);

    if (policy.surface === "modal") {
        showBlockingModal(
            eventType === "kill_switch_engaged" ? "KILL SWITCH ENGAGED" : "CRITICAL ALERT",
            data.message || "A critical event occurred.",
            [
                { label: "Acknowledge", primary: true },
            ]
        );
    } else if (policy.surface === "toast") {
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
        filtered.unshift({
            name: 'Go to Pipeline filtered: ' + query.toUpperCase(),
            href: '/pipeline?ticker=' + query.toUpperCase(),
            category: "ticker",
        });
    }

    // If query looks like audit/cycle search
    if (/^(cycle|c-|CYCLE)/i.test(query)) {
        filtered.unshift({
            name: 'Search audit: ' + query,
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
        li.className = "flex items-center px-4 py-2.5 cursor-pointer hover:bg-zinc-100 text-sm";

        li.innerHTML =
            '<span class="text-xs font-mono mr-3 px-1.5 py-0.5 rounded ' +
            (item.category === "page" ? "bg-blue-50 text-blue-500" : "") +
            (item.category === "action" ? "bg-green-50 text-green-600" : "") +
            (item.category === "ticker" ? "bg-amber-50 text-amber-600" : "") +
            (item.category === "audit" ? "bg-purple-50 text-purple-600" : "") +
            '">' + (categoryLabel[item.category] || "") + '</span>' +
            '<span class="flex-1">' + item.name + '</span>';

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
        empty.className = "px-4 py-3 text-sm text-zinc-400 text-center";
        empty.textContent = 'No results for "' + query + '"';
        results.appendChild(empty);
    }
}

function updateCmdKActiveItem(results) {
    var items = results.querySelectorAll("li[role='option']");
    items.forEach(function (li, idx) {
        if (idx === cmdKActiveIndex) {
            li.classList.add("bg-zinc-100");
        } else {
            li.classList.remove("bg-zinc-100");
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
    showToast("Starting new cycle...", "info");
    // TODO: POST to pmacs-nervous /api/cycle/start
}

function openCycleCompare() {
    showToast("Select two cycles to compare", "info");
    // TODO: Open cycle compare modal
}

function openTaxonomyBrowser() {
    history.pushState({ page: "/debug" }, "", "/debug?filter=taxonomy");
    window.location.href = "/debug?filter=taxonomy";
}

function promoteAllP1Global() {
    fetch("/pipeline/queue/promote", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
    }).then(function (resp) {
        return resp.json();
    }).then(function (data) {
        if (data.ok) {
            showToast("Promoted " + data.promoted_count + " P1 items", "success");
        }
    }).catch(function () {
        showToast("Failed to promote P1 items", "error");
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

document.addEventListener("keydown", function (e) {
    var isCmd = e.metaKey || e.ctrlKey;
    var isInput = e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT";
    var activeModal = !document.getElementById("cmd-k").classList.contains("hidden") ||
                      !document.getElementById("totp-modal").classList.contains("hidden") ||
                      !document.getElementById("shortcut-overlay").classList.contains("hidden") ||
                      !document.getElementById("blocking-modal").classList.contains("hidden");

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
        closeCmdK();
        closeTotpModal();
        document.getElementById("shortcut-overlay").classList.add("hidden");
        var blockingModal = document.getElementById("blocking-modal");
        // Don't close blocking modals with Esc (they require explicit acknowledgment)
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
        document.getElementById("shortcut-overlay").classList.toggle("hidden");
        return;
    }

    // Cmd-K palette: arrow keys + enter
    if (!document.getElementById("cmd-k").classList.contains("hidden")) {
        var results = document.getElementById("cmd-k-results");
        var items = results.querySelectorAll("li");

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
                    // TODO: POST to pmacs-nervous /api/kill-switch/engage
                    document.getElementById("kill-switch-btn").classList.add("bg-red-600");
                    document.getElementById("kill-switch-btn").classList.remove("bg-zinc-700");
                    showToast("Kill switch ENGAGED. To disengage: Cortex page.", "error", 0);
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
 */
function open_totp_modal(opts) {
    var modal = document.getElementById("totp-modal");
    if (!modal) return;

    // Store state
    totpModalState.actionId = opts.actionId || "";
    totpModalState.callbackUrl = opts.callbackUrl || "";
    totpModalState.confirmText = opts.confirmText || "";
    totpModalState.pendingAction = opts.onSuccess || null;

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
        if (resp.ok) {
            return resp.json();
        }
        return resp.json().then(function (data) {
            throw new Error(data.detail || "Verification failed");
        });
    })
    .then(function (data) {
        // TOTP verified — close modal
        closeTotpModal();
        showToast("Action confirmed", "success");

        // Execute gated action
        if (totpModalState.callbackUrl) {
            executeGatedAction(totpModalState.callbackUrl, totpModalState.actionId);
        } else if (totpModalState.pendingAction) {
            totpModalState.pendingAction(data);
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
 */
function executeGatedAction(callbackUrl, actionId) {
    fetch(callbackUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action_id: actionId }),
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
        digit.addEventListener("input", function (e) {
            e.target.value = e.target.value.replace(/[^0-9]/g, "");
            if (e.target.value && index < digits.length - 1) {
                digits[index + 1].focus();
            }
            updateTotpConfirmButton();
            // Auto-submit on 6th digit (only if confirm text not required or already matched)
            if (index === digits.length - 1 && e.target.value) {
                var confirmRequired = totpModalState.confirmText || "";
                if (!confirmRequired || document.getElementById("totp-confirm-input").value === confirmRequired) {
                    submitTotp();
                }
            }
        });
        digit.addEventListener("keydown", function (e) {
            if (e.key === "Backspace" && !e.target.value && index > 0) {
                digits[index - 1].focus();
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

// Notification-policy-driven events
onSSE("notification", function (data) {
    if (data.event_type) {
        handleNotification(data.event_type, data);
    }
});

// Cycle events
onSSE("cycle", function (data) {
    if (data.event === "cycle_start") {
        var indicator = document.getElementById("cycle-indicator");
        if (indicator) {
            indicator.textContent = "Running: " + (data.ticker || "") + " — ETA " + (data.eta || "calculating...");
        }
    }
    if (data.event === "cycle_complete") {
        var indicator = document.getElementById("cycle-indicator");
        if (indicator) {
            indicator.textContent = "Idle. Last cycle: " + (data.completed_at || "just now");
        }
        showToast("Cycle complete: " + (data.tickers_processed || 0) + " tickers processed", "info");
    }
    if (data.event === "ticker_progress") {
        var indicator = document.getElementById("cycle-indicator");
        if (indicator) {
            indicator.textContent = "Processing: " + (data.ticker || "") + " (" + (data.progress || "") + ")";
        }
    }
});

// Error events
onSSE("error", function (data) {
    showToast("Error: " + (data.message || "Unknown"), "error");
});

// Kill switch events
onSSE("kill_switch", function (data) {
    if (data.engaged) {
        handleNotification("kill_switch_engaged", data);
    }
});

// Trade events
onSSE("trade", function (data) {
    if (data.event === "filled") {
        handleNotification(data.mode === "LIVE" ? "trade_filled_live" : "trade_filled_paper", data);
    }
});

// ─── Viewport Guard (Source.md §13.7: minimum 1024px) ──────────────────────

function checkViewportWidth() {
    var guard = document.getElementById("viewport-guard");
    if (!guard) return;
    if (window.innerWidth < 1024) {
        guard.classList.remove("hidden");
    } else {
        guard.classList.add("hidden");
    }
}

window.addEventListener("resize", checkViewportWidth);
document.addEventListener("DOMContentLoaded", checkViewportWidth);
