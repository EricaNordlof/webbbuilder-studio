(() => {
  const overlay = document.getElementById("loadingOverlay");
  let loadingTimer = null;
  let loadingSeconds = 0;

  function resetOverlay() {
    if (!overlay) return;

    overlay.hidden = true;
    loadingSeconds = 0;

    if (loadingTimer) {
      window.clearInterval(loadingTimer);
      loadingTimer = null;
    }
  }

  function showOverlay() {
    if (!overlay) return;

    overlay.hidden = false;

    const title = overlay.querySelector("h2");
    const text = overlay.querySelector("p");
    const card = overlay.querySelector(".loading-card");

    if (title) title.textContent = "Bygger projektet…";
    if (text) {
      text.textContent =
        "AI:n skriver projektfiler. Ett enkelt test ska normalt inte behöva stå här i flera minuter.";
    }

    let status = overlay.querySelector("[data-loading-status]");
    if (!status && card) {
      status = document.createElement("small");
      status.dataset.loadingStatus = "true";
      status.style.display = "block";
      status.style.marginTop = "16px";
      status.style.opacity = "0.75";
      card.appendChild(status);
    }

    let cancelButton = overlay.querySelector("[data-cancel-generation]");
    if (!cancelButton && card) {
      cancelButton = document.createElement("button");
      cancelButton.type = "button";
      cancelButton.dataset.cancelGeneration = "true";
      cancelButton.textContent = "Avbryt och ladda om";
      cancelButton.className = "secondary";
      cancelButton.style.marginTop = "16px";
      cancelButton.hidden = true;
      cancelButton.addEventListener("click", () => {
        window.location.reload();
      });
      card.appendChild(cancelButton);
    }

    loadingSeconds = 0;

    if (loadingTimer) window.clearInterval(loadingTimer);

    loadingTimer = window.setInterval(() => {
      loadingSeconds += 1;

      if (status) {
        status.textContent = `Väntat ${loadingSeconds} sekunder…`;
      }

      if (loadingSeconds >= 180) {
        if (title) title.textContent = "Genereringen tar för lång tid";
        if (text) {
          text.textContent =
            "Servern ska avbryta genereringen och visa ett tydligt fel. Du kan också ladda om sidan och kontrollera Render-loggen.";
        }
        if (cancelButton) cancelButton.hidden = false;
      }
    }, 1000);
  }

  document.querySelectorAll("[data-loading-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      showOverlay();
    });
  });

  window.addEventListener("pageshow", () => {
    resetOverlay();
  });

  const refineTextarea = document.querySelector('textarea[name="instruction"]');

  document.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!refineTextarea) return;
      refineTextarea.value = button.dataset.prompt || "";
      refineTextarea.focus();
    });
  });

  const previewFrame = document.getElementById("previewFrame");

  document.querySelectorAll("[data-preview-width]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!previewFrame) return;
      const width = button.dataset.previewWidth || "100%";
      previewFrame.style.width = width === "100%" ? "100%" : `${Number(width)}px`;
    });
  });

  document.querySelectorAll("[data-refresh-preview]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!previewFrame) return;
      const url = new URL(previewFrame.src, window.location.origin);
      url.searchParams.set("_refresh", String(Date.now()));
      previewFrame.src = url.toString();
    });
  });
})();
