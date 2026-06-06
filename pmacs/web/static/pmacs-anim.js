/**
 * PMACS Animation System v2 — scroll reveals, smooth counters,
 * morphing transitions, ambient micro-interactions.
 * Respects prefers-reduced-motion.
 */

var PMACS_ANIM = (function() {
    "use strict";

    var prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // ─── Scroll Reveal (IntersectionObserver) ────────────────────────────

    var revealObserver = null;

    function initScrollReveal() {
        if (prefersReducedMotion) {
            // Show everything immediately
            document.querySelectorAll('.reveal, [data-stagger]').forEach(function(el) {
                el.style.opacity = '1';
                el.style.transform = 'none';
            });
            return;
        }

        if (!('IntersectionObserver' in window)) return;

        if (revealObserver) revealObserver.disconnect();

        revealObserver = new IntersectionObserver(function(entries) {
            entries.forEach(function(entry) {
                if (entry.isIntersecting) {
                    var el = entry.target;
                    var delay = parseInt(el.getAttribute('data-reveal-delay') || '0', 10);
                    setTimeout(function() {
                        el.style.opacity = '';
                        el.style.transform = '';
                        el.classList.add('revealed');
                        el.classList.add('persona-card-visible');
                    }, delay);
                    revealObserver.unobserve(el);
                }
            });
        }, {
            threshold: 0.08,
            rootMargin: '0px 0px -40px 0px'
        });

        document.querySelectorAll('.reveal, [data-stagger]').forEach(function(el, i) {
            // Set stagger delay
            if (!el.getAttribute('data-reveal-delay')) {
                el.setAttribute('data-reveal-delay', String(i * 60));
            }
            // Initial hidden state
            if (!el.classList.contains('revealed')) {
                el.style.opacity = '0';
                el.style.transform = 'translateY(20px)';
                el.style.transition = 'opacity 0.5s cubic-bezier(0.16,1,0.3,1), transform 0.5s cubic-bezier(0.16,1,0.3,1)';
            }
            revealObserver.observe(el);
        });
    }

    // ─── Animated Number Transition (spring-like) ────────────────────────

    function animateNumber(element, newValue, opts) {
        if (!element) return;
        opts = opts || {};
        var duration = opts.duration || 600;
        var prefix = opts.prefix || '';
        var suffix = opts.suffix || '';
        var decimals = opts.decimals !== undefined ? opts.decimals : 2;
        var separator = opts.separator || ',';

        var startValue = parseFloat(element.getAttribute('data-anim-value')) || 0;
        element.setAttribute('data-anim-value', newValue);

        if (prefersReducedMotion || Math.abs(newValue - startValue) < 0.001) {
            element.textContent = prefix + formatNumber(newValue, decimals, separator) + suffix;
            return;
        }

        var startTime = null;
        function step(timestamp) {
            if (!startTime) startTime = timestamp;
            var progress = Math.min((timestamp - startTime) / duration, 1);
            // Spring-like easing
            var eased = 1 - Math.pow(2, -10 * progress) * Math.cos(progress * Math.PI * 0.8);
            var current = startValue + (newValue - startValue) * eased;
            element.textContent = prefix + formatNumber(current, decimals, separator) + suffix;
            if (progress < 1) {
                requestAnimationFrame(step);
            } else {
                element.textContent = prefix + formatNumber(newValue, decimals, separator) + suffix;
            }
        }
        requestAnimationFrame(step);

        // Flash highlight
        element.classList.remove('anim-value-flash');
        void element.offsetWidth;
        element.classList.add('anim-value-flash');
        element.classList.add('number-updated');
        setTimeout(function() {
            element.classList.remove('number-updated');
        }, 300);
    }

    function formatNumber(n, decimals, separator) {
        var parts = n.toFixed(decimals).split('.');
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, separator);
        return parts.join('.');
    }

    // ─── SSE Value Update with Animation ─────────────────────────────────

    function sseUpdate(elementId, newValue, opts) {
        var el = document.getElementById(elementId);
        if (!el) return;
        if (typeof newValue === 'number') {
            animateNumber(el, newValue, opts);
        } else {
            // Text morphing transition
            if (!prefersReducedMotion) {
                el.style.transition = 'opacity 0.15s ease, transform 0.15s ease';
                el.style.opacity = '0';
                el.style.transform = 'translateY(-4px)';
                setTimeout(function() {
                    el.textContent = newValue;
                    el.style.opacity = '1';
                    el.style.transform = 'translateY(0)';
                }, 150);
            } else {
                el.textContent = newValue;
            }
        }
    }

    // ─── Staggered Entrance ──────────────────────────────────────────────

    function staggerEntrance(selector, opts) {
        if (prefersReducedMotion) {
            document.querySelectorAll(selector).forEach(function(el) {
                el.style.opacity = '1';
                el.style.transform = 'none';
            });
            return;
        }
        opts = opts || {};
        var delay = opts.delay || 60;
        var elements = document.querySelectorAll(selector);
        elements.forEach(function(el, i) {
            el.style.opacity = '0';
            el.style.transform = 'translateY(16px)';
            el.style.transition = 'opacity 0.4s cubic-bezier(0.16,1,0.3,1), transform 0.4s cubic-bezier(0.16,1,0.3,1)';
            setTimeout(function() {
                el.style.opacity = '1';
                el.style.transform = 'translateY(0)';
            }, i * delay + 50);
        });
    }

    // ─── Animate data-anim-value elements on page load ───────────────────

    function initAnimatedValues() {
        document.querySelectorAll('[data-anim-target]').forEach(function(el) {
            var target = parseFloat(el.getAttribute('data-anim-target'));
            if (!isNaN(target)) {
                el.setAttribute('data-anim-value', '0');
                animateNumber(el, target, {
                    prefix: el.getAttribute('data-anim-prefix') || '',
                    suffix: el.getAttribute('data-anim-suffix') || '',
                    decimals: parseInt(el.getAttribute('data-anim-decimals') || '2', 10),
                    duration: 800,
                });
            }
        });
    }

    // ─── Smooth Counter (for dashboard hero values) ──────────────────────

    function countUp(el, target, opts) {
        if (!el) return;
        opts = opts || {};
        var duration = opts.duration || 1200;
        var prefix = opts.prefix || '';
        var suffix = opts.suffix || '';
        var decimals = opts.decimals || 2;

        if (prefersReducedMotion) {
            el.textContent = prefix + formatNumber(target, decimals, ',') + suffix;
            return;
        }

        var start = 0;
        var startTime = null;

        function tick(ts) {
            if (!startTime) startTime = ts;
            var progress = Math.min((ts - startTime) / duration, 1);
            // Ease out cubic
            var eased = 1 - Math.pow(1 - progress, 3);
            var val = start + (target - start) * eased;
            el.textContent = prefix + formatNumber(val, decimals, ',') + suffix;
            if (progress < 1) {
                requestAnimationFrame(tick);
            }
        }
        requestAnimationFrame(tick);
    }

    // ─── Page Transition Helper ──────────────────────────────────────────

    function pageTransition(contentEl) {
        if (prefersReducedMotion || !contentEl) return;
        contentEl.style.opacity = '0';
        contentEl.style.transform = 'translateY(8px)';
        requestAnimationFrame(function() {
            contentEl.style.transition = 'opacity 0.3s cubic-bezier(0.16,1,0.3,1), transform 0.3s cubic-bezier(0.16,1,0.3,1)';
            contentEl.style.opacity = '1';
            contentEl.style.transform = 'translateY(0)';
        });
    }

    // ─── Initialize ──────────────────────────────────────────────────────

    function init() {
        initAnimatedValues();
        initScrollReveal();
    }

    document.addEventListener('DOMContentLoaded', init);

    document.addEventListener('htmx:afterSwap', function(e) {
        if (e.detail && e.detail.target && e.detail.target.id === 'main-content') {
            // Brief delay to let DOM settle
            requestAnimationFrame(function() {
                init();
                pageTransition(e.detail.target);
            });
        }
    });

    return {
        animateNumber: animateNumber,
        sseUpdate: sseUpdate,
        staggerEntrance: staggerEntrance,
        initAnimatedValues: initAnimatedValues,
        initScrollReveal: initScrollReveal,
        countUp: countUp,
        pageTransition: pageTransition,
        init: init,
    };
})();
