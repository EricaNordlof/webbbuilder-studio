(() => {
  const overlay = document.getElementById("loadingOverlay");

  document.querySelectorAll("[data-loading-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      if (overlay) overlay.hidden = false;
    });
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
