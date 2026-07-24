(() => {
  "use strict";

  const previewFrame = document.getElementById("previewFrame");

  document
    .querySelector("[data-refresh-preview]")
    ?.addEventListener("click", () => {
      if (!previewFrame) return;
      const url = new URL(previewFrame.src, window.location.origin);
      url.searchParams.set("_", String(Date.now()));
      previewFrame.src = url.toString();
    });

  const projectRoot = document.querySelector("[data-project-id]");
  if (!projectRoot) return;

  const projectId = projectRoot.dataset.projectId;
  const overlay = document.getElementById("loadingOverlay");
  const statusText = overlay?.querySelector("[data-loading-status]");
  const clientError = document.querySelector("[data-client-error]");

  let pollTimer = null;
  let pollCount = 0;

  function showError(message) {
    if (!clientError) {
      window.alert(message);
      return;
    }
    clientError.textContent = message;
    clientError.hidden = false;
  }

  function clearError() {
    if (!clientError) return;
    clientError.textContent = "";
    clientError.hidden = true;
  }

  function showOverlay(message) {
    if (!overlay) return;
    overlay.hidden = false;
    if (statusText) statusText.textContent = message || "Startar…";
  }

  function hideOverlay() {
    if (overlay) overlay.hidden = true;
  }

  async function readJson(response) {
    try {
      return await response.json();
    } catch (_) {
      return {};
    }
  }

  async function pollStatus() {
    pollCount += 1;

    try {
      const response = await fetch(`/api/projects/${projectId}/status`, {
        credentials: "same-origin",
        cache: "no-store"
      });

      const data = await readJson(response);

      if (!response.ok) {
        throw new Error(data.detail || `Statusfel ${response.status}`);
      }

      const status = data.status;

      if (statusText) {
        if (status === "queued") statusText.textContent = "Jobbet väntar på att starta…";
        else if (status === "generating") statusText.textContent = `AI:n bygger projektet… ${pollCount * 2} s`;
        else if (status === "validating") statusText.textContent = "Kvalitetskontrollerar filer och bilder…";
        else if (status === "repairing") statusText.textContent = "Hittade fel. AI:n reparerar automatiskt…";
        else if (status === "ready") statusText.textContent = "Godkänt. Laddar projektet…";
        else if (status === "error") statusText.textContent = "Genereringen misslyckades.";
      }

      if (status === "ready") {
        window.clearInterval(pollTimer);
        window.location.reload();
        return;
      }

      if (status === "error") {
        window.clearInterval(pollTimer);
        hideOverlay();
        showError(data.error || "AI-genereringen misslyckades.");
      }

      if (pollCount >= 300) {
        window.clearInterval(pollTimer);
        hideOverlay();
        showError("Statuskontrollen stoppades efter tio minuter. Ladda om sidan och kontrollera projektstatus.");
      }
    } catch (error) {
      console.error("[v2] Status polling error:", error);
      if (statusText) statusText.textContent = "Tillfälligt statusfel. Försöker igen…";
    }
  }

  function startPolling() {
    if (pollTimer) window.clearInterval(pollTimer);
    pollCount = 0;
    pollStatus();
    pollTimer = window.setInterval(pollStatus, 2000);
  }

  const generateButton = document.querySelector("[data-generate]");
  generateButton?.addEventListener("click", async () => {
    clearError();
    generateButton.disabled = true;
    showOverlay("Skickar jobbet till servern…");

    try {
      const response = await fetch(`/api/projects/${projectId}/generate`, {
        method: "POST",
        credentials: "same-origin"
      });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      startPolling();
    } catch (error) {
      hideOverlay();
      showError(error instanceof Error ? error.message : String(error));
      generateButton.disabled = false;
    }
  });

  const refineForm = document.querySelector("[data-refine-form]");
  refineForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();

    const submitButton = refineForm.querySelector('button[type="submit"]');
    submitButton.disabled = true;
    showOverlay("Startar ny revision…");

    try {
      const response = await fetch(`/api/projects/${projectId}/refine`, {
        method: "POST",
        credentials: "same-origin",
        body: new FormData(refineForm)
      });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
      startPolling();
    } catch (error) {
      hideOverlay();
      showError(error instanceof Error ? error.message : String(error));
      submitButton.disabled = false;
    }
  });

  document
    .querySelector("[data-close-overlay]")
    ?.addEventListener("click", hideOverlay);

  const currentStatus = document
    .querySelector("[data-project-status]")
    ?.textContent
    ?.trim()
    ?.toLowerCase();

  if (["queued", "generating", "validating", "repairing"].includes(currentStatus)) {
    showOverlay("Ett AI-jobb pågår…");
    startPolling();
  }
})();
