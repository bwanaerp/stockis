(function () {
  "use strict";

  const appEl = document.getElementById("app");
  const AUTH_REQUIRED = appEl.dataset.authRequired === "true";
  const ALREADY_AUTHED = appEl.dataset.alreadyAuthed === "true";

  const state = {
    sessionId: null,
    meta: {},
    checklist: [],       // [{line_no, store_product_id, product_name, unit, category, system_eod_qty}]
    lineStates: {},      // { "12": {physical_qty, ocr_note, confidence, status, source_page} }
    pages: [],           // [{id, order, status, first_line_no, row_count, header_frac, ...}]
    reviewFilter: "review",
    reviewSearch: "",
    reviewOrder: [],     // ordered list of line_no strings matching current filter, for prev/next
    currentLineNo: null,
    currentAcceptPageId: null,
  };

  // ---------------------------------------------------------------
  // small helpers
  // ---------------------------------------------------------------
  function $(id) { return document.getElementById(id); }

  async function api(path, opts) {
    opts = opts || {};
    const res = await fetch(path, opts);
    let body = null;
    try { body = await res.json(); } catch (e) { /* non-JSON (e.g. blob) handled by caller */ }
    if (!res.ok) {
      const err = new Error((body && (body.message || body.error)) || `Request failed (${res.status})`);
      err.status = res.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  function showScreen(name) {
    document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
    const el = $("screen-" + name);
    if (el) el.classList.add("active");
    document.querySelectorAll(".bottombar-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.screen === name);
    });
  }

  function setError(elId, message) {
    const el = $(elId);
    if (!message) { el.hidden = true; el.textContent = ""; return; }
    el.hidden = false;
    el.textContent = message;
  }

  function statusDotClass(status) {
    if (status === "ocr_green" || status === "confirmed" || status === "manual") return "dot-green";
    if (status === "ocr_amber") return "dot-amber";
    if (status === "ocr_red") return "dot-red";
    return "dot-blank";
  }

  function fmtQty(v) {
    if (v === null || v === undefined) return "";
    return Number.isInteger(v) ? String(v) : String(v);
  }

  // ---------------------------------------------------------------
  // Login
  // ---------------------------------------------------------------
  function initAuth() {
    if (!AUTH_REQUIRED || ALREADY_AUTHED) {
      $("screen-login").classList.remove("active");
      $("shell").hidden = false;
      showScreen("new");
      return;
    }
    $("screen-login").classList.add("active");
    $("login-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      setError("login-error", null);
      try {
        await api("/api/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: $("login-password").value }),
        });
        $("screen-login").classList.remove("active");
        $("shell").hidden = false;
        showScreen("new");
      } catch (err) {
        setError("login-error", err.message);
      }
    });
  }

  // ---------------------------------------------------------------
  // New session
  // ---------------------------------------------------------------
  $("new-session-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    setError("new-session-error", null);

    const fd = new FormData();
    fd.append("checklist_csv", $("checklist-file").files[0]);
    fd.append("property_id", $("meta-property").value);
    fd.append("department", $("meta-department").value);
    fd.append("stock_take_date", $("meta-date").value);
    fd.append("stock_take_id", $("meta-id").value);
    fd.append("enhance_mode", $("meta-enhance").value);

    try {
      const res = await api("/api/sessions", { method: "POST", body: fd });
      state.sessionId = res.session_id;
      state.meta = res.meta;
      await refreshSession();
      $("bottombar").hidden = false;
      $("progress-strip").hidden = false;
      updateTopbar();
      showScreen("capture");
    } catch (err) {
      setError("new-session-error", err.message);
    }
  });

  function updateTopbar() {
    $("topbar-dept").textContent = state.meta.department || "Stock take";
    $("topbar-sub").textContent = [state.meta.property_id, state.meta.stock_take_date]
      .filter(Boolean).join(" \u00b7 ");
  }

  async function refreshSession() {
    const res = await api(`/api/sessions/${state.sessionId}`);
    state.meta = res.meta;
    state.checklist = res.checklist;
    state.lineStates = res.line_states;
    state.pages = res.pages;
    renderProgress();
    renderPageStrip();
  }

  function renderProgress() {
    let filled = 0, review = 0, blank = 0;
    state.checklist.forEach((l) => {
      const s = state.lineStates[String(l.line_no)] || {};
      if (s.status === "ocr_amber" || s.status === "ocr_red") review++;
      else if (s.physical_qty !== null && s.physical_qty !== undefined) filled++;
      else blank++;
    });
    const total = state.checklist.length || 1;
    $("progress-fill").style.width = Math.round((filled / total) * 100) + "%";
    $("tally-filled").textContent = filled;
    $("tally-review").textContent = review;
    $("tally-blank").textContent = blank;
  }

  // ---------------------------------------------------------------
  // Capture
  // ---------------------------------------------------------------
  $("btn-camera").addEventListener("click", () => $("input-camera").click());
  $("btn-library").addEventListener("click", () => $("input-library").click());
  $("btn-pdf").addEventListener("click", () => $("input-pdf").click());

  $("input-camera").addEventListener("change", (e) => uploadFiles(e.target.files));
  $("input-library").addEventListener("change", (e) => uploadFiles(e.target.files));
  $("input-pdf").addEventListener("change", (e) => uploadFiles(e.target.files));

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    const statusEl = $("upload-status");
    statusEl.hidden = false;

    let uploadedPages = [];
    for (let i = 0; i < files.length; i++) {
      statusEl.textContent = `Uploading page ${i + 1} of ${files.length}\u2026`;
      const fd = new FormData();
      fd.append("file", files[i]);
      fd.append("enhance_mode", state.meta.enhance_mode || "color");
      try {
        const res = await api(`/api/sessions/${state.sessionId}/pages`, { method: "POST", body: fd });
        uploadedPages = uploadedPages.concat(res.pages);
      } catch (err) {
        statusEl.textContent = `Upload failed: ${err.message}`;
        return;
      }
    }
    statusEl.textContent = `${uploadedPages.length} page(s) added. Tap a page to line it up and read it.`;
    await refreshSession();

    // Jump straight into aligning the first newly-added page.
    if (uploadedPages.length) openAcceptScreen(uploadedPages[0].id);
  }

  function renderPageStrip() {
    const strip = $("page-strip");
    strip.innerHTML = "";
    const sorted = [...state.pages].sort((a, b) => a.order - b.order);
    sorted.forEach((p) => {
      const card = document.createElement("div");
      card.className = "page-card";
      card.innerHTML = `
        <img src="/api/sessions/${state.sessionId}/pages/${p.id}/image?variant=processed&t=${Date.now()}" alt="Page">
        <button class="page-card-del" data-id="${p.id}" title="Delete page">&times;</button>
        <div class="page-card-label">
          <span>${p.status === "read" ? `L${p.first_line_no}\u2013${p.first_line_no + p.row_count - 1}` : "Unaligned"}</span>
          <span class="page-status-dot ${p.status === "read" ? "read" : "pending"}"></span>
        </div>`;
      card.addEventListener("click", (ev) => {
        if (ev.target.closest(".page-card-del")) return;
        openAcceptScreen(p.id);
      });
      card.querySelector(".page-card-del").addEventListener("click", async (ev) => {
        ev.stopPropagation();
        await api(`/api/sessions/${state.sessionId}/pages/${p.id}`, { method: "DELETE" });
        await refreshSession();
      });
      strip.appendChild(card);
    });
    $("btn-go-review").hidden = sorted.length === 0;
  }

  $("btn-go-review").addEventListener("click", () => { renderReviewList(); showScreen("review"); });

  // ---------------------------------------------------------------
  // Accept / align a page
  // ---------------------------------------------------------------
  function openAcceptScreen(pageId) {
    state.currentAcceptPageId = pageId;
    const page = state.pages.find((p) => p.id === pageId);
    $("accept-image").src = `/api/sessions/${state.sessionId}/pages/${pageId}/image?variant=processed&t=${Date.now()}`;

    const usedLineNos = state.pages
      .filter((p) => p.status === "read" && p.id !== pageId)
      .map((p) => p.first_line_no + p.row_count - 1);
    const suggestedFirst = usedLineNos.length ? Math.max(...usedLineNos) + 1 : (state.checklist[0]?.line_no ?? 1);

    $("accept-first-line").value = page && page.first_line_no ? page.first_line_no : suggestedFirst;
    $("accept-row-count").value = page && page.row_count ? page.row_count : "";
    $("accept-col-left").value = page && page.column_left_frac ? Math.round(page.column_left_frac * 100) : 78;
    $("accept-header").value = page && page.header_frac ? Math.round(page.header_frac * 100) : 12;
    setError("accept-error", null);
    showScreen("accept");
  }

  $("btn-accept-back").addEventListener("click", () => showScreen("capture"));

  $("btn-accept-run").addEventListener("click", async () => {
    setError("accept-error", null);
    const body = {
      first_line_no: parseInt($("accept-first-line").value, 10),
      row_count: parseInt($("accept-row-count").value, 10),
      column_left_frac: parseFloat($("accept-col-left").value) / 100,
      header_frac: parseFloat($("accept-header").value) / 100,
    };
    if (!body.first_line_no || !body.row_count) {
      setError("accept-error", "Enter both the first line number and the row count.");
      return;
    }
    const btn = $("btn-accept-run");
    btn.disabled = true;
    btn.textContent = "Reading\u2026";
    try {
      await api(`/api/sessions/${state.sessionId}/pages/${state.currentAcceptPageId}/accept`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      await refreshSession();
      showScreen("capture");
    } catch (err) {
      setError("accept-error", err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Read this page";
    }
  });

  // ---------------------------------------------------------------
  // Review
  // ---------------------------------------------------------------
  document.querySelectorAll(".review-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".review-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.reviewFilter = tab.dataset.filter;
      renderReviewList();
    });
  });
  $("review-search").addEventListener("input", (e) => {
    state.reviewSearch = e.target.value.trim().toLowerCase();
    renderReviewList();
  });

  function matchesFilter(l, s) {
    if (state.reviewFilter === "review") return s.status === "ocr_amber" || s.status === "ocr_red";
    if (state.reviewFilter === "blank") return s.physical_qty === null || s.physical_qty === undefined;
    return true;
  }
  function matchesSearch(l) {
    if (!state.reviewSearch) return true;
    return l.product_name.toLowerCase().includes(state.reviewSearch) ||
      String(l.line_no).includes(state.reviewSearch);
  }

  function renderReviewList() {
    const list = $("review-list");
    list.innerHTML = "";
    const rows = state.checklist.filter((l) => {
      const s = state.lineStates[String(l.line_no)] || {};
      return matchesFilter(l, s) && matchesSearch(l);
    });
    state.reviewOrder = rows.map((l) => String(l.line_no));

    if (!rows.length) {
      list.innerHTML = `<p class="screen-hint">Nothing here \u2014 nice work.</p>`;
      return;
    }

    rows.forEach((l) => {
      const s = state.lineStates[String(l.line_no)] || {};
      const row = document.createElement("div");
      row.className = "review-row";
      row.innerHTML = `
        <span class="review-row-dot ${statusDotClass(s.status)}"></span>
        <div class="review-row-main">
          <div class="review-row-product">${escapeHtml(l.product_name)}</div>
          <div class="review-row-meta">L${l.line_no} \u00b7 ${escapeHtml(l.unit || "")} \u00b7 ${escapeHtml(l.category || "")}</div>
        </div>
        <div class="review-row-qty ${s.physical_qty === null || s.physical_qty === undefined ? "is-blank" : ""}">
          ${s.physical_qty === null || s.physical_qty === undefined ? "\u2014" : fmtQty(s.physical_qty)}
        </div>`;
      row.addEventListener("click", () => openLineSheet(String(l.line_no)));
      list.appendChild(row);
    });
  }

  function escapeHtml(str) {
    const d = document.createElement("div");
    d.textContent = str || "";
    return d.innerHTML;
  }

  // ---------------------------------------------------------------
  // Line detail sheet
  // ---------------------------------------------------------------
  function openLineSheet(lineNoStr) {
    state.currentLineNo = lineNoStr;
    const line = state.checklist.find((l) => String(l.line_no) === lineNoStr);
    const s = state.lineStates[lineNoStr] || {};

    $("line-category").textContent = line.category || "";
    $("line-product").textContent = line.product_name;
    $("line-linenum").textContent = `Line ${line.line_no}${line.unit ? " \u00b7 " + line.unit : ""}`;
    $("line-qty-input").value = s.physical_qty === null || s.physical_qty === undefined ? "" : fmtQty(s.physical_qty);

    drawLineCrop(line, s);

    $("sheet-line").hidden = false;
  }

  function drawLineCrop(line, s) {
    const img = $("line-crop-img");
    const page = s.source_page ? state.pages.find((p) => p.id === s.source_page) : null;
    if (!page || !page.row_count) {
      img.removeAttribute("src");
      img.alt = "No scanned page linked to this line yet \u2014 enter the count manually.";
      return;
    }
    const rowIndex = line.line_no - page.first_line_no;
    const usableTop = page.header_frac;
    const usableBottom = 1 - page.footer_frac;
    const rowHeight = (usableBottom - usableTop) / page.row_count;
    const top = usableTop + rowIndex * rowHeight;
    const bottom = top + rowHeight;
    const pad = rowHeight * 0.4;

    // Draw a cropped region of the full processed page onto a canvas so
    // the reviewer sees exactly the handwriting the OCR engine read,
    // without the backend needing to persist a separate file per line.
    const src = new Image();
    src.crossOrigin = "anonymous";
    src.onload = () => {
      const canvas = document.createElement("canvas");
      const y1 = Math.max(0, (top - pad) * src.naturalHeight);
      const y2 = Math.min(src.naturalHeight, (bottom + pad) * src.naturalHeight);
      const x1 = Math.max(0, (page.column_left_frac - 0.03) * src.naturalWidth);
      const x2 = Math.min(src.naturalWidth, page.column_right_frac * src.naturalWidth);
      canvas.width = x2 - x1;
      canvas.height = y2 - y1;
      const ctx = canvas.getContext("2d");
      ctx.drawImage(src, x1, y1, x2 - x1, y2 - y1, 0, 0, x2 - x1, y2 - y1);
      img.src = canvas.toDataURL("image/jpeg", 0.9);
      img.alt = "Crop of the Counted box for this line";
    };
    src.src = `/api/sessions/${state.sessionId}/pages/${page.id}/image?variant=processed`;
  }

  $("btn-line-close").addEventListener("click", closeLineSheet);
  function closeLineSheet() {
    $("sheet-line").hidden = true;
    renderReviewList();
    renderProgress();
  }

  async function patchLine(action, extra) {
    const body = Object.assign({ action }, extra || {});
    const res = await api(`/api/sessions/${state.sessionId}/lines/${state.currentLineNo}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    state.lineStates[state.currentLineNo] = res;
  }

  $("btn-line-confirm").addEventListener("click", async () => {
    const val = $("line-qty-input").value.trim();
    if (val === "") { await patchLine("skip"); } else { await patchLine("edit", { physical_qty: val }); }
    goToNextOrClose();
  });
  $("btn-line-zero").addEventListener("click", async () => { await patchLine("zero"); goToNextOrClose(); });
  $("btn-line-skip").addEventListener("click", async () => { await patchLine("skip"); goToNextOrClose(); });

  function goToNextOrClose() {
    const idx = state.reviewOrder.indexOf(state.currentLineNo);
    const next = state.reviewOrder.slice(idx + 1).find((ln) => {
      const s = state.lineStates[ln] || {};
      return s.status === "ocr_amber" || s.status === "ocr_red";
    });
    if (next) { openLineSheet(next); } else { closeLineSheet(); }
  }

  $("btn-line-next-empty").addEventListener("click", goToNextOrClose);
  $("btn-line-prev").addEventListener("click", () => {
    const idx = state.reviewOrder.indexOf(state.currentLineNo);
    if (idx > 0) openLineSheet(state.reviewOrder[idx - 1]);
  });

  // ---------------------------------------------------------------
  // Export
  // ---------------------------------------------------------------
  $("btn-go-export").addEventListener("click", () => { renderExport(); showScreen("export"); });

  $("btn-notes-save").addEventListener("click", async () => {
    await api(`/api/sessions/${state.sessionId}/notes`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ notes: $("session-notes").value }),
    });
    const btn = $("btn-notes-save");
    const original = btn.textContent;
    btn.textContent = "Saved";
    setTimeout(() => { btn.textContent = original; }, 1200);
  });

  async function loadNotes() {
    try {
      const res = await api(`/api/sessions/${state.sessionId}/notes`);
      $("session-notes").value = res.notes || "";
    } catch (e) { /* ignore */ }
  }

  function renderExport() {
    loadNotes();
    let filled = 0, review = 0, blank = 0;
    state.checklist.forEach((l) => {
      const s = state.lineStates[String(l.line_no)] || {};
      if (s.status === "ocr_amber" || s.status === "ocr_red") review++;
      else if (s.physical_qty !== null && s.physical_qty !== undefined) filled++;
      else blank++;
    });
    $("export-summary").innerHTML = `
      <span>${filled} filled</span><span>${review} need review</span><span>${blank} blank</span>`;
    $("export-blocked").hidden = review === 0;
    $("allow-blanks-check").checked = false;
  }

  $("btn-export-counts").addEventListener("click", async () => {
    setError("export-error", null);
    const allowBlanks = $("allow-blanks-check").checked;
    const url = `/api/sessions/${state.sessionId}/export/counts.csv?allow_blanks=${allowBlanks}`;
    try {
      const res = await fetch(url);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.message || body.error || "Export failed");
      }
      await triggerDownload(res);
    } catch (err) {
      setError("export-error", err.message);
    }
  });

  $("btn-export-review").addEventListener("click", async () => {
    const res = await fetch(`/api/sessions/${state.sessionId}/export/review.csv`);
    await triggerDownload(res);
  });
  $("btn-export-images").addEventListener("click", async () => {
    const res = await fetch(`/api/sessions/${state.sessionId}/export/images.zip`);
    await triggerDownload(res);
  });

  async function triggerDownload(res) {
    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const filename = match ? match[1] : "download";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
  }

  $("btn-back-to-capture").addEventListener("click", () => showScreen("capture"));

  // ---------------------------------------------------------------
  // Bottom nav
  // ---------------------------------------------------------------
  document.querySelectorAll(".bottombar-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const screen = btn.dataset.screen;
      if (screen === "review") renderReviewList();
      if (screen === "export") renderExport();
      showScreen(screen);
    });
  });

  // ---------------------------------------------------------------
  initAuth();
})();
