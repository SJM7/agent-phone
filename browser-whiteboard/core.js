/* Shared geometry / model handoff helpers; also loaded by the review page. */
(() => {
  if (globalThis.AgentWhiteboardCore) return;
  const rect = (a, b) => ({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y) });
  const summary = sheet => {
    const lines = [
      `# UI review · iteration ${sheet.iteration}`, `Page: ${sheet.title}`, `URL: ${sheet.url}`,
      `Started: ${sheet.createdAt}`, `Finished: ${sheet.finishedAt || "Draft"}`, "",
      "Use the numbered screenshots as visual references alongside my narration.",
      "Numbers identify marks, not automatically requested changes. Boundaries indicate the region I referred to; use my words to determine scope.",
      "Screenshots capture separate moments. Selectors are hints from that moment, not proof of the current DOM.", ""
    ];
    if (sheet.narration) lines.push("## Narration", sheet.narration, "");
    lines.push("## References");
    for (const m of sheet.marks) {
      lines.push(`### ${m.number} · ${m.kind}`, `Marked: ${m.createdAt} · screenshot: ${m.capturedAt}`,
        `URL: ${m.page.url}`, `Viewport: ${m.viewport.width} × ${m.viewport.height} CSS px · DPR ${m.viewport.dpr}`,
        `Scroll: ${m.viewport.scrollX}, ${m.viewport.scrollY}`, `Image: mark-${m.number}.png`);
      if (m.element) lines.push(`Element: ${m.element.tag}`, `Selector hint: ${m.element.selector}`, `Visible text: ${m.element.text || "(none)"}`);
      if (m.kind === "region") lines.push(`Viewport boundary: ${JSON.stringify(m.geometry)}`);
      if (m.note) lines.push(`Note: ${m.note}`);
      lines.push("");
    }
    return lines.join("\n");
  };
  function paint(ctx, mark, scale = 1) {
    ctx.save(); ctx.scale(scale, scale);
    ctx.strokeStyle = "#df4f30"; ctx.fillStyle = "rgba(223,79,48,.08)";
    ctx.lineWidth = 2.5; ctx.lineCap = "round"; ctx.lineJoin = "round";
    const g = mark.geometry;
    let x = g.x, y = g.y;
    if (mark.kind === "ink") {
      [x, y] = [g.points[0].x, g.points[0].y];
      ctx.beginPath(); g.points.forEach((p, i) => i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)); ctx.stroke();
    } else {
      ctx.fillRect(g.x, g.y, g.width, g.height); ctx.strokeRect(g.x, g.y, g.width, g.height);
    }
    x = Math.max(15, Math.min(mark.viewport.width - 15, x));
    y = Math.max(15, Math.min(mark.viewport.height - 15, y));
    ctx.fillStyle = "#df4f30"; ctx.beginPath(); ctx.arc(x, y, 13, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "white"; ctx.font = "bold 12px system-ui"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(String(mark.number), x, y); ctx.restore();
  }
  globalThis.AgentWhiteboardCore = { rect, summary, paint };
})();
