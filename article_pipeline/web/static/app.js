(function () {
  "use strict";
  const csrf = document.querySelector('meta[name="csrf-token"]').content;

  function toast(text) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 1800);
  }

  // Project switcher
  document.querySelectorAll("[data-project-switch]").forEach((sel) => {
    sel.addEventListener("change", () => { window.location = sel.value; });
  });

  // Confirm before destructive or slow actions
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => { if (!confirm(form.dataset.confirm)) e.preventDefault(); });
  });
  document.querySelectorAll("[data-confirm-click]").forEach((btn) => {
    btn.addEventListener("click", (e) => { if (!confirm(btn.dataset.confirmClick)) e.preventDefault(); });
  });

  // Copy buttons
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied"); }
    catch (e) { toast("Copy failed. Select the text and copy it manually."); }
  }
  document.querySelectorAll("[data-copy-text]").forEach((btn) => {
    btn.addEventListener("click", () => copyText(btn.dataset.copyText));
  });
  // Copies formatted HTML (headings, bold, lists) so it pastes nicely into Substack's editor.
  document.querySelectorAll("[data-copy-html]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const el = document.querySelector(btn.dataset.copyHtml);
      if (!el) return;
      try {
        await navigator.clipboard.write([new ClipboardItem({
          "text/html": new Blob([el.innerHTML], { type: "text/html" }),
          "text/plain": new Blob([el.innerText], { type: "text/plain" }),
        })]);
        toast("Article copied. Paste it into the Substack editor.");
      } catch (e) {
        copyText(el.innerText);
      }
    });
  });

  // Tabs on the draft page (no reload; the URL keeps the tab)
  const tabs = document.querySelector("[data-tabs]");
  if (tabs) {
    tabs.addEventListener("click", (e) => {
      const a = e.target.closest("[data-tab]");
      if (!a) return;
      e.preventDefault();
      tabs.querySelectorAll("[data-tab]").forEach((t) => t.classList.toggle("active", t === a));
      document.querySelectorAll("[data-panel]").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== a.dataset.tab));
      history.replaceState(null, "", "?tab=" + a.dataset.tab);
    });
  }

  // Live job progress
  const banner = document.getElementById("job-banner");
  const logEl = document.getElementById("job-log");
  let since = logEl ? logEl.querySelectorAll("span").length : 0;
  let wasRunning = banner && banner.dataset.running === "1";

  async function poll() {
    let job;
    try {
      const res = await fetch("/api/job?since=" + since, { cache: "no-store" });
      job = await res.json();
    } catch (e) { return setTimeout(poll, 4000); }
    if (!job) return setTimeout(poll, 4000);

    const running = job.status === "running";
    const lines = job.lines || [];
    since = job.total_lines;
    if (banner) {
      banner.classList.toggle("hidden", !running);
      banner.querySelector("[data-job-label]").textContent = job.label;
      const last = lines.length ? lines[lines.length - 1].text : null;
      if (last) banner.querySelector("[data-job-line]").textContent = last.replace(/^\d\d:\d\d:\d\d\s+/, "");
      if (job.stopping) banner.querySelector("[data-job-line]").textContent = "Stopping after the current step…";
    }
    if (logEl) {
      const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 30;
      lines.forEach((l) => {
        const span = document.createElement("span");
        span.className = "log-" + l.level;
        span.textContent = l.text + "\n";
        logEl.appendChild(span);
      });
      if (atBottom) logEl.scrollTop = logEl.scrollHeight;
      const st = document.querySelector("[data-job-status]");
      if (st) { st.textContent = job.status; st.className = "status status-" + job.status; }
      const sm = document.querySelector("[data-job-summary]");
      if (sm) sm.textContent = job.summary || "";
    }
    if (wasRunning && !running) {
      toast("Task finished: " + (job.summary || job.status));
      // Refresh pages whose content the task changed.
      if (document.querySelector("[data-autoreload], [data-tabs], .stats, .tabs")) setTimeout(() => location.reload(), 1200);
    }
    if (running && !wasRunning && document.querySelector(".stats")) location.reload();
    wasRunning = running;
    setTimeout(poll, running ? 1500 : 5000);
  }
  if (banner) {
    if (logEl) logEl.scrollTop = logEl.scrollHeight;
    poll();
    banner.querySelector("[data-job-stop]").addEventListener("click", async () => {
      await fetch("/jobs/cancel", { method: "POST", headers: { "X-CSRF-Token": csrf } });
      banner.querySelector("[data-job-line]").textContent = "Stopping after the current step…";
    });
  }
})();
