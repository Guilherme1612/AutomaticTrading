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
    if (eventSource) {
        eventSource.close();
    }

    try {
        eventSource = new EventSource(SSE_URL);

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
            console.warn("SSE connection lost, reconnecting in 5s");
            eventSource.close();
            setTimeout(connectSSE, 5000);
        };
    } catch (e) {
        console.warn("SSE unavailable:", e);
        setTimeout(connectSSE, 5000);
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

// ─── Cmd-K Command Palette (Source.md §13.2) ───────────────────────────────

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
];

var CMD_K_ALL = CMD_K_PAGES.concat(CMD_K_ACTIONS);
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
            name: 'Go to Pipeline → ' + query.toUpperCase(),
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

    results.innerHTML = "";
    cmdKActiveIndex = -1;

    filtered.forEach(function (item, idx) {
        var li = document.createElement("li");
        li.setAttribute("role", "option");

        var categoryLabel = {
            page: "Page",
            action: "Action",
            ticker: "Ticker",
            audit: "Audit",
        };

        li.innerHTML =
            '<span class="text-xs text-zinc-400 font-mono mr-2">' + (categoryLabel[item.category] || "") + '</span>' +
            '<span>' + item.name + '</span>';
        li.addEventListener("click", function () {
            executeCmdKItem(item);
        });
        li.addEventListener("mouseenter", function () {
            cmdKActiveIndex = idx;
            updateCmdKActiveItem(results);
        });
        results.appendChild(li);
    });
}

function updateCmdKActiveItem(results) {
    var items = results.querySelectorAll("li");
    items.forEach(function (li, idx) {
        if (idx === cmdKActiveIndex) {
            li.classList.add("active");
        } else {
            li.classList.remove("active");
        }
    });
}

function executeCmdKItem(item) {
    closeCmdK();
    if (item.href) {
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
    window.location.href = "/debug?filter=taxonomy";
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

    // Cmd-T: open TOTP modal (when no text input focused)
    if (isCmd && e.key === "t" && !isInput) {
        e.preventDefault();
        document.getElementById("totp-modal").classList.remove("hidden");
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

// ─── TOTP Input Auto-Advance ────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", function () {
    var digits = document.querySelectorAll(".totp-digit");
    digits.forEach(function (digit, index) {
        digit.addEventListener("input", function (e) {
            e.target.value = e.target.value.replace(/[^0-9]/g, "");
            if (e.target.value && index < digits.length - 1) {
                digits[index + 1].focus();
            }
            // Auto-submit on 6th digit
            if (index === digits.length - 1 && e.target.value) {
                submitTotp();
            }
        });
        digit.addEventListener("keydown", function (e) {
            if (e.key === "Backspace" && !e.target.value && index > 0) {
                digits[index - 1].focus();
            }
        });
    });
});

function closeTotpModal() {
    var modal = document.getElementById("totp-modal");
    if (modal) modal.classList.add("hidden");
}

function submitTotp() {
    var digits = document.querySelectorAll(".totp-digit");
    var code = "";
    digits.forEach(function (d) {
        code += d.value;
    });
    if (code.length === 6) {
        // TODO: POST to pmacs-nervous TOTP endpoint
        console.log("TOTP submitted:", code);
        closeTotpModal();
        showToast("TOTP verified (stub)", "success");
    } else {
        showToast("Enter all 6 digits", "warning");
    }
}

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
    var detail = el.querySelector(".event-detail, [class*='hidden']");
    if (detail) {
        detail.classList.toggle("hidden");
    }
}

// ─── Copy for Claude Code ───────────────────────────────────────────────────

function copyForClaudeCode(btn) {
    var code = btn.getAttribute("data-error-code") || "";
    var desc = btn.getAttribute("data-error-description") || "";
    var explanation = btn.getAttribute("data-error-explanation") || "";

    var prompt = "PMACS error: " + code + "\n" +
        "Description: " + desc + "\n" +
        "Explanation: " + explanation + "\n" +
        "Please investigate and suggest a fix.";

    navigator.clipboard.writeText(prompt).then(function () {
        showToast("Debug context copied to clipboard", "success");
    }).catch(function () {
        showToast("Failed to copy — check clipboard permissions", "warning");
    });
}

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
