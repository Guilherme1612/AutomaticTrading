/**
 * PMACS Agents Page — D3 Sankey Visualization Module.
 *
 * Three views toggled by chip group:
 *   Process: horizontal timeline Evidence → Personas → Arbitration → Crucible → Sizing → Risk Gate → Verdict
 *   Network: D3 Sankey diagram (evidence sources → personas → arbitrated output)
 *   Math: per-persona probabilities, arbitration formula step-by-step
 *
 * Spec: Source.md §13.3 (Sankey), §15 (Agents page)
 *
 * All animations respect prefers-reduced-motion.
 * Local-only: no CDN. D3 is vendored at /static/vendor/d3.min.js.
 */

var PMACS_SANKEY = (function () {
    "use strict";

    var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var TRANSITION_MS = prefersReducedMotion ? 0 : 200;

    // ─── Data Store ──────────────────────────────────────────────────────────

    var sankeyData = null;

    /**
     * Fetch Sankey data from the server endpoint.
     * @param {Function} callback - called with (error, data)
     */
    function fetchData(callback) {
        fetch("/agents/sankey-data")
            .then(function (resp) {
                if (!resp.ok) throw new Error("HTTP " + resp.status);
                return resp.json();
            })
            .then(function (data) {
                sankeyData = data;
                if (callback) callback(null, data);
            })
            .catch(function (err) {
                if (callback) callback(err, null);
            });
    }

    // ─── Process View ────────────────────────────────────────────────────────

    /**
     * Render the process timeline view.
     * Stages: Evidence → Personas → Arbitration → Crucible → Sizing → Risk Gate → Verdict
     * Completed stages fill with result color. Pending stages gray. Animates left-to-right.
     */
    function renderProcessView(container, data) {
        container.innerHTML = "";

        var stages = [
            { id: "evidence", label: "Evidence", icon: "e" },
            { id: "personas", label: "Personas", icon: "p" },
            { id: "arbitration", label: "Arbitration", icon: "a" },
            { id: "crucible", label: "Crucible", icon: "c" },
            { id: "sizing", label: "Sizing", icon: "s" },
            { id: "risk_gate", label: "Risk Gate", icon: "r" },
            { id: "verdict", label: "Verdict", icon: "v" },
        ];

        var statusMap = {};
        if (data && data.stages) {
            data.stages.forEach(function (s) {
                statusMap[s.id] = s;
            });
        }

        var wrapper = document.createElement("div");
        wrapper.className = "flex items-center gap-2 text-xs overflow-x-auto pb-4";
        wrapper.setAttribute("role", "list");
        wrapper.setAttribute("aria-label", "Analysis pipeline stages");

        stages.forEach(function (stage, idx) {
            var status = statusMap[stage.id];
            var isComplete = status && status.status === "complete";
            var isPending = status && status.status === "running";
            var isResult = status && status.result;

            var stageEl = document.createElement("div");
            stageEl.setAttribute("role", "listitem");
            stageEl.className =
                "flex flex-col items-center gap-1 px-3 py-2 rounded border transition-all duration-200 min-w-[80px]";

            if (isComplete) {
                var resultColor = getResultColor(isResult);
                stageEl.className += " border-green-300 bg-green-50";
                if (prefersReducedMotion) {
                    stageEl.style.opacity = "1";
                }
            } else if (isPending) {
                stageEl.className += " border-blue-300 bg-blue-50 animate-pulse";
            } else {
                stageEl.className += " border-zinc-200 bg-zinc-50";
            }

            // Stage badge
            var badge = document.createElement("div");
            badge.className = "w-8 h-8 rounded-full flex items-center justify-center text-sm font-mono font-semibold ";
            if (isComplete) {
                badge.className += "bg-green-600 text-white";
                badge.textContent = "\u2713";
            } else if (isPending) {
                badge.className += "bg-blue-600 text-white";
                badge.textContent = stage.icon;
            } else {
                badge.className += "bg-zinc-300 text-zinc-600";
                badge.textContent = stage.icon;
            }
            badge.setAttribute("aria-label", stage.label + (isComplete ? " (complete)" : isPending ? " (running)" : " (pending)"));

            var label = document.createElement("span");
            label.className = "font-medium whitespace-nowrap";
            label.textContent = stage.label;

            if (isResult) {
                var resultEl = document.createElement("span");
                resultEl.className = "text-xs font-mono " + resultColor;
                resultEl.textContent = isResult;
                stageEl.appendChild(badge);
                stageEl.appendChild(label);
                stageEl.appendChild(resultEl);
            } else {
                stageEl.appendChild(badge);
                stageEl.appendChild(label);
            }

            wrapper.appendChild(stageEl);

            // Arrow between stages
            if (idx < stages.length - 1) {
                var arrow = document.createElement("div");
                arrow.className = "text-zinc-400 text-lg flex-shrink-0";
                arrow.setAttribute("aria-hidden", "true");
                arrow.textContent = "\u2192";
                wrapper.appendChild(arrow);
            }
        });

        container.appendChild(wrapper);

        // Animate fill left-to-right
        if (!prefersReducedMotion) {
            var items = wrapper.querySelectorAll("[role='listitem']");
            items.forEach(function (item, idx) {
                item.style.opacity = "0";
                item.style.transform = "translateX(-10px)";
                setTimeout(function () {
                    item.style.transition = "opacity 0.3s ease, transform 0.3s ease";
                    item.style.opacity = "1";
                    item.style.transform = "translateX(0)";
                }, idx * 80);
            });
        }
    }

    function getResultColor(result) {
        if (!result) return "text-zinc-500";
        var r = result.toUpperCase();
        if (r.indexOf("STRONG_BUY") >= 0 || r.indexOf("BUY") >= 0) return "text-green-600";
        if (r.indexOf("HOLD") >= 0) return "text-amber-500";
        if (r.indexOf("SKIP") >= 0) return "text-red-500";
        if (r.indexOf("ABORT") >= 0) return "text-red-600";
        return "text-zinc-500";
    }

    // ─── Network View (D3 Sankey) ────────────────────────────────────────────

    /**
     * Render the D3 Sankey network diagram.
     * Left: evidence sources. Middle: personas. Right: Arbitrated output.
     * Flow widths = evidence relevance weights. Hover reveals specific evidence.
     * Second smaller Sankey after Arbitration showing Crucible.
     */
    function renderNetworkView(container, data) {
        container.innerHTML = "";

        if (typeof d3 === "undefined") {
            container.innerHTML = '<div class="flex items-center justify-center h-full text-sm text-zinc-400">D3 library not loaded</div>';
            return;
        }

        if (!data || !data.evidence_sources || data.evidence_sources.length === 0) {
            container.innerHTML = '<div class="flex items-center justify-center h-full text-sm text-zinc-400">No cycle data available. Run a cycle to see the Sankey diagram.</div>';
            return;
        }

        var width = container.clientWidth || 700;
        var height = container.clientHeight || 256;

        // Build nodes and links from data
        var nodes = [];
        var links = [];
        var nodeIndex = {};

        // Left column: evidence sources
        data.evidence_sources.forEach(function (src) {
            var idx = nodes.length;
            nodeIndex["ev_" + src.id] = idx;
            nodes.push({ name: src.name, column: 0 });
        });

        // Middle column: personas
        if (data.personas) {
            data.personas.forEach(function (p) {
                var idx = nodes.length;
                nodeIndex["per_" + p.id] = idx;
                nodes.push({ name: p.name, column: 1 });
            });
        }

        // Right column: arbitrated output
        if (data.arbitration_result) {
            var idx = nodes.length;
            nodeIndex["arb"] = idx;
            nodes.push({ name: "Arbitrated Output", column: 2 });
        }

        // Links: evidence → personas
        if (data.flows) {
            data.flows.forEach(function (flow) {
                var srcIdx = nodeIndex["ev_" + flow.source];
                var tgtIdx = nodeIndex["per_" + flow.target];
                if (srcIdx !== undefined && tgtIdx !== undefined) {
                    links.push({
                        source: srcIdx,
                        target: tgtIdx,
                        value: Math.max(flow.value || 1, 1),
                        label: flow.label || "",
                    });
                }
            });
        }

        // Links: personas → arbitration
        if (data.personas && data.arbitration_result) {
            data.personas.forEach(function (p) {
                var srcIdx = nodeIndex["per_" + p.id];
                var tgtIdx = nodeIndex["arb"];
                if (srcIdx !== undefined && tgtIdx !== undefined) {
                    links.push({
                        source: srcIdx,
                        target: tgtIdx,
                        value: Math.max(p.weight || 1, 1),
                        label: p.name,
                    });
                }
            });
        }

        if (nodes.length === 0 || links.length === 0) {
            container.innerHTML = '<div class="flex items-center justify-center h-full text-sm text-zinc-400">Insufficient data for Sankey diagram</div>';
            return;
        }

        // Render with D3
        var svg = d3.select(container)
            .append("svg")
            .attr("width", width)
            .attr("height", height)
            .attr("role", "img")
            .attr("aria-label", "Evidence-to-persona-to-arbitration Sankey diagram");

        // Simple horizontal layout (no d3-sankey plugin — custom positioning)
        var colWidth = width / 3;
        var nodeHeight = 24;
        var nodePadding = 8;

        // Position nodes by column
        nodes.forEach(function (node) {
            var colNodes = nodes.filter(function (n) { return n.column === node.column; });
            var colIdx = colNodes.indexOf(node);
            var totalHeight = colNodes.length * (nodeHeight + nodePadding);
            var startY = (height - totalHeight) / 2;
            node.x = node.column * colWidth + 40;
            node.y = startY + colIdx * (nodeHeight + nodePadding);
            node.width = 120;
            node.height = nodeHeight;
        });

        // Tooltip div
        var tooltip = d3.select(container)
            .append("div")
            .attr("class", "sankey-tooltip")
            .style("position", "absolute")
            .style("background", "#18181b")
            .style("color", "white")
            .style("font-size", "11px")
            .style("font-family", "'JetBrains Mono', monospace")
            .style("padding", "4px 8px")
            .style("border-radius", "4px")
            .style("pointer-events", "none")
            .style("opacity", "0")
            .style("z-index", "10");

        // Draw links with curved paths
        var linkGroup = svg.append("g").attr("class", "sankey-links");
        linkGroup.selectAll("path")
            .data(links)
            .enter()
            .append("path")
            .attr("d", function (d) {
                var src = nodes[d.source];
                var tgt = nodes[d.target];
                var sx = src.x + src.width;
                var sy = src.y + src.height / 2;
                var tx = tgt.x;
                var ty = tgt.y + tgt.height / 2;
                var midX = (sx + tx) / 2;
                return "M" + sx + "," + sy + " C" + midX + "," + sy + " " + midX + "," + ty + " " + tx + "," + ty;
            })
            .attr("fill", "none")
            .attr("stroke", "#93c5fd")
            .attr("stroke-opacity", 0.4)
            .attr("stroke-width", function (d) { return Math.max(d.value * 2, 1); })
            .attr("class", "sankey-link")
            .on("mouseenter", function (event, d) {
                d3.select(this).attr("stroke-opacity", 0.8);
                tooltip.style("opacity", "1")
                    .html(d.label ? d.label + " (weight: " + d.value + ")" : "weight: " + d.value);
            })
            .on("mousemove", function (event) {
                tooltip.style("left", (event.offsetX + 10) + "px")
                    .style("top", (event.offsetY - 10) + "px");
            })
            .on("mouseleave", function () {
                d3.select(this).attr("stroke-opacity", 0.4);
                tooltip.style("opacity", "0");
            });

        // Draw nodes
        var nodeGroup = svg.append("g").attr("class", "sankey-nodes");
        var nodeRects = nodeGroup.selectAll("rect")
            .data(nodes)
            .enter()
            .append("rect")
            .attr("x", function (d) { return d.x; })
            .attr("y", function (d) { return d.y; })
            .attr("width", function (d) { return d.width; })
            .attr("height", function (d) { return d.height; })
            .attr("rx", 4)
            .attr("fill", function (d) {
                if (d.column === 0) return "#dbeafe"; // evidence: blue-100
                if (d.column === 1) return "#fef3c7"; // personas: amber-100
                return "#d1fae5"; // arbitration: green-100
            })
            .attr("stroke", function (d) {
                if (d.column === 0) return "#93c5fd";
                if (d.column === 1) return "#fcd34d";
                return "#6ee7b7";
            })
            .attr("stroke-width", 1)
            .attr("class", "sankey-node");

        // Node labels
        nodeGroup.selectAll("text")
            .data(nodes)
            .enter()
            .append("text")
            .attr("x", function (d) { return d.x + d.width / 2; })
            .attr("y", function (d) { return d.y + d.height / 2; })
            .attr("dy", "0.35em")
            .attr("text-anchor", "middle")
            .attr("font-size", "10px")
            .attr("font-family", "'JetBrains Mono', monospace")
            .attr("fill", "#18181b")
            .text(function (d) {
                return d.name.length > 14 ? d.name.substring(0, 12) + ".." : d.name;
            });

        // Animate entrance
        if (!prefersReducedMotion) {
            linkGroup.selectAll("path")
                .attr("stroke-dasharray", function () {
                    var totalLength = this.getTotalLength();
                    return totalLength + " " + totalLength;
                })
                .attr("stroke-dashoffset", function () {
                    return this.getTotalLength();
                })
                .transition()
                .duration(800)
                .attr("stroke-dashoffset", 0);

            nodeRects.attr("opacity", 0)
                .transition()
                .delay(400)
                .duration(TRANSITION_MS)
                .attr("opacity", 1);
        }
    }

    // ─── Math View ───────────────────────────────────────────────────────────

    /**
     * Render the math breakdown view.
     * Per persona: p_up, p_flat, p_down, weight.
     * Below: arbitration formula step-by-step. Numbers fill progressively.
     */
    function renderMathView(container, data) {
        container.innerHTML = "";

        var wrapper = document.createElement("div");
        wrapper.className = "text-xs font-mono space-y-3 overflow-y-auto max-h-full";

        // Per-persona outputs
        var personaHeader = document.createElement("div");
        personaHeader.className = "text-zinc-500 mb-1";
        personaHeader.textContent = "Per-persona outputs:";
        wrapper.appendChild(personaHeader);

        var personaData = (data && data.personas) || [];
        if (personaData.length === 0) {
            var empty = document.createElement("div");
            empty.className = "text-zinc-400 ml-2";
            empty.id = "math-personas";
            empty.textContent = "Waiting for persona outputs...";
            wrapper.appendChild(empty);
        } else {
            personaData.forEach(function (p) {
                var row = document.createElement("div");
                row.className = "ml-2 flex items-center gap-3 py-1";

                var name = document.createElement("span");
                name.className = "text-zinc-700 w-32 truncate";
                name.textContent = p.name;

                var pUp = document.createElement("span");
                pUp.className = "text-green-600 w-12";
                pUp.textContent = "\u2191" + (p.p_up !== undefined ? p.p_up.toFixed(2) : "\u2014");

                var pFlat = document.createElement("span");
                pFlat.className = "text-zinc-400 w-12";
                pFlat.textContent = "\u2192" + (p.p_flat !== undefined ? p.p_flat.toFixed(2) : "\u2014");

                var pDown = document.createElement("span");
                pDown.className = "text-red-500 w-12";
                pDown.textContent = "\u2193" + (p.p_down !== undefined ? p.p_down.toFixed(2) : "\u2014");

                var weight = document.createElement("span");
                weight.className = "text-blue-600 w-16";
                weight.textContent = "w=" + (p.weight !== undefined ? p.weight.toFixed(3) : "\u2014");

                row.appendChild(name);
                row.appendChild(pUp);
                row.appendChild(pFlat);
                row.appendChild(pDown);
                row.appendChild(weight);
                wrapper.appendChild(row);
            });
        }

        // Arbitration formula
        var arbHeader = document.createElement("div");
        arbHeader.className = "text-zinc-500 mt-3 border-t border-zinc-200 pt-2";
        arbHeader.textContent = "Arbitration:";
        wrapper.appendChild(arbHeader);

        var arbFormula = document.createElement("div");
        arbFormula.className = "ml-2 space-y-1";
        arbFormula.id = "math-arbitration";

        if (data && data.arbitration_result) {
            var arb = data.arbitration_result;

            var step1 = document.createElement("div");
            step1.className = "text-zinc-600";
            step1.textContent = "Step 1: Weighted probability aggregation";
            arbFormula.appendChild(step1);

            var step1Calc = document.createElement("div");
            step1Calc.className = "ml-3 text-zinc-500";
            step1Calc.textContent = "p_up = \u03A3(w_i \u00D7 p_up_i) / \u03A3(w_i)";
            arbFormula.appendChild(step1Calc);

            if (arb.p_up !== undefined) {
                var step1Result = document.createElement("div");
                step1Result.className = "ml-3 text-green-600";
                step1Result.textContent = "= " + arb.p_up.toFixed(4);
                arbFormula.appendChild(step1Result);
            }

            if (arb.p_down !== undefined) {
                var stepDown = document.createElement("div");
                stepDown.className = "ml-3 text-red-500";
                stepDown.textContent = "p_down = " + arb.p_down.toFixed(4);
                arbFormula.appendChild(stepDown);
            }
        } else {
            var noArb = document.createElement("div");
            noArb.className = "text-zinc-400";
            noArb.textContent = "p_up = \u03A3(w_i \u00D7 p_up_i) / \u03A3(w_i)";
            arbFormula.appendChild(noArb);
        }

        wrapper.appendChild(arbFormula);

        // Conviction
        var convHeader = document.createElement("div");
        convHeader.className = "text-zinc-500 mt-2 border-t border-zinc-200 pt-2";
        convHeader.textContent = "Conviction:";
        wrapper.appendChild(convHeader);

        var convFormula = document.createElement("div");
        convFormula.className = "ml-2 text-zinc-400";
        convFormula.id = "math-conviction";

        if (data && data.arbitration_result && data.arbitration_result.conviction !== undefined) {
            convFormula.textContent = "conviction = " + data.arbitration_result.conviction.toFixed(3);
            convFormula.className = "ml-2 text-blue-600";
        } else {
            convFormula.textContent = "conviction = f(arbitrated, evidence_strength, crucible_severity)";
        }

        wrapper.appendChild(convFormula);
        container.appendChild(wrapper);
    }

    // ─── View Switching ──────────────────────────────────────────────────────

    var currentView = "process";
    var views = {
        process: renderProcessView,
        network: renderNetworkView,
        math: renderMathView,
    };

    /**
     * Switch between Process, Network, Math views.
     * Updates chip group active state and renders the selected view.
     */
    function switchView(viewName) {
        currentView = viewName;

        // Update chip group
        var chips = document.querySelectorAll("[data-sankey-view]");
        chips.forEach(function (chip) {
            var isActive = chip.getAttribute("data-sankey-view") === viewName;
            chip.className = isActive
                ? "px-2.5 py-1 text-xs rounded bg-blue-50 text-blue-600 border border-blue-200"
                : "px-2.5 py-1 text-xs rounded bg-zinc-100 text-zinc-500 hover:bg-zinc-200";
            chip.setAttribute("aria-pressed", isActive ? "true" : "false");
        });

        // Hide all views, show selected
        ["process", "network", "math"].forEach(function (v) {
            var el = document.getElementById("viz-" + v);
            if (el) el.classList.toggle("hidden", v !== viewName);
        });

        // Render
        var container = document.getElementById("viz-" + viewName);
        if (container && views[viewName]) {
            views[viewName](container, sankeyData);
        }
    }

    /**
     * Initialize the Sankey module: fetch data, bind chip clicks, render default view.
     */
    function init() {
        // Bind chip group clicks
        var chips = document.querySelectorAll("[data-sankey-view]");
        chips.forEach(function (chip) {
            chip.addEventListener("click", function () {
                switchView(chip.getAttribute("data-sankey-view"));
            });
            chip.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    switchView(chip.getAttribute("data-sankey-view"));
                }
            });
        });

        // Fetch data then render
        fetchData(function (err, data) {
            if (err) {
                // Render with empty data (will show placeholder states)
                switchView(currentView);
                return;
            }
            switchView(currentView);
        });
    }

    // Public API
    return {
        init: init,
        switchView: switchView,
        fetchData: fetchData,
        renderProcessView: renderProcessView,
        renderNetworkView: renderNetworkView,
        renderMathView: renderMathView,
    };
})();
