(function () {
    const TYPES = new Set(["click", "pointerenter", "pointerleave", "scroll", "keydown", "dragstart", "drop"]);
    const KEYS = new Set(["Tab", "Enter", "Escape", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown", "Space"]);

    function clean(value, limit) {
        if (typeof value !== "string") return undefined;
        const result = value.replace(/[\u0000-\u001f\u007f]/g, "").slice(0, limit);
        return result || undefined;
    }

    function finite(value) {
        return typeof value === "number" && Number.isFinite(value) ? value : undefined;
    }

    function clamp(value, min, max) {
        const number = finite(value);
        return number === undefined ? undefined : Math.min(max, Math.max(min, number));
    }

    function normalizeEvent(input) {
        if (!input || typeof input !== "object" || Array.isArray(input) || !TYPES.has(input.type) || finite(input.atMs) === undefined || input.atMs < 0) return null;
        if (input.type === "keydown" && !KEYS.has(input.key)) return null;
        const output = { type: input.type, atMs: input.atMs };
        if (typeof input.url === "string") {
            try {
                const url = new URL(input.url);
                if (url.protocol === "http:" || url.protocol === "https:") {
                    url.username = ""; url.password = ""; url.search = ""; url.hash = "";
                    output.url = url.toString();
                }
            } catch (_) {}
        }
        const viewport = {};
        const width = clamp(input.viewport && input.viewport.width, 0, 20000);
        const height = clamp(input.viewport && input.viewport.height, 0, 20000);
        if (width !== undefined && width > 0) viewport.width = width;
        if (height !== undefined && height > 0) viewport.height = height;
        if (Object.keys(viewport).length) output.viewport = viewport;
        const scroll = {};
        const scrollX = clamp(input.scroll && input.scroll.x, -100000, 100000);
        const scrollY = clamp(input.scroll && input.scroll.y, -100000, 100000);
        if (scrollX !== undefined) scroll.x = scrollX;
        if (scrollY !== undefined) scroll.y = scrollY;
        if (Object.keys(scroll).length) output.scroll = scroll;
        const source = input.target;
        if (source && typeof source === "object" && !Array.isArray(source)) {
            const target = {};
            const tag = clean(source.tag, 32);
            const role = clean(source.role, 32);
            if (tag !== undefined) target.tag = tag;
            if (role !== undefined) target.role = role;
            const textEntry = (tag && /^(input|textarea)$/i.test(tag)) || (role && /^(textbox|searchbox)$/i.test(role));
            if (!textEntry) {
                const name = clean(source.name, 80); if (name !== undefined) target.name = name;
                const testId = clean(source.testId, 80); if (testId !== undefined) target.testId = testId;
                const selector = clean(source.selector, 200); if (selector !== undefined) target.selector = selector;
            }
            if (source.bounds && typeof source.bounds === "object" && !Array.isArray(source.bounds)) {
                const bounds = {};
                const x = clamp(source.bounds.x, -100000, 100000); if (x !== undefined) bounds.x = x;
                const y = clamp(source.bounds.y, -100000, 100000); if (y !== undefined) bounds.y = y;
                const w = finite(source.bounds.width); if (w !== undefined && w >= 0) bounds.width = Math.min(100000, w);
                const h = finite(source.bounds.height); if (h !== undefined && h >= 0) bounds.height = Math.min(100000, h);
                if (Object.keys(bounds).length) target.bounds = bounds;
            }
            if (typeof source.expanded === "boolean") target.expanded = source.expanded;
            if (typeof source.disabled === "boolean") target.disabled = source.disabled;
            if (Object.keys(target).length) output.target = target;
        }
        if (input.type === "keydown") output.key = input.key;
        return output;
    }

    function selectFrames(frames, atMs) {
        if (!Array.isArray(frames)) return [];
        if (finite(atMs) === undefined) return frames.slice(0, 0);
        const choices = [];
        for (const threshold of [atMs - 2000, atMs - 500, atMs, atMs + 500, atMs + 1500]) {
            let candidate;
            for (const frame of frames) {
                if (!frame || typeof frame !== "object" || finite(frame.atMs) === undefined) continue;
                if (threshold <= atMs && frame.atMs <= threshold && (!candidate || frame.atMs > candidate.atMs)) candidate = frame;
                if (threshold > atMs && frame.atMs >= threshold && (!candidate || frame.atMs < candidate.atMs)) candidate = frame;
            }
            if (candidate && !choices.some(item => item === candidate)) choices.push(candidate);
        }
        return choices.sort((a, b) => a.atMs - b.atMs || frames.indexOf(a) - frames.indexOf(b)).slice(0, 5);
    }

    globalThis.AgentReviewEvents = { normalizeEvent, selectFrames };
}());
