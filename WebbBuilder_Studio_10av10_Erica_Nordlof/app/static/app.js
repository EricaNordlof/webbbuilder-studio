(() => {
  "use strict";

  const overlay = document.getElementById("loadingOverlay");

  let loadingTimer = null;
  let loadingSeconds = 0;
  let activeController = null;
  let generationInProgress = false;

  /*
   * Servern har egen timeout.
   * Klienten väntar lite längre så att servern hinner
   * returnera sitt riktiga felmeddelande först.
   */
  const CLIENT_TIMEOUT_MS = 225000; // 225 sekunder


  // ============================================================
  // HELPERS
  // ============================================================

  function clearLoadingTimer() {
    if (loadingTimer) {
      window.clearInterval(loadingTimer);
      loadingTimer = null;
    }
  }


  function getOverlayElements() {
    if (!overlay) {
      return {
        card: null,
        title: null,
        text: null,
        status: null,
        cancelButton: null
      };
    }

    const card = overlay.querySelector(".loading-card");
    const title = overlay.querySelector("h2");
    const text = overlay.querySelector("p");

    let status = overlay.querySelector(
      "[data-loading-status]"
    );

    if (!status && card) {
      status = document.createElement("small");

      status.dataset.loadingStatus = "true";

      status.style.display = "block";
      status.style.marginTop = "16px";
      status.style.opacity = "0.75";
      status.style.lineHeight = "1.5";

      card.appendChild(status);
    }

    let cancelButton = overlay.querySelector(
      "[data-cancel-generation]"
    );

    if (!cancelButton && card) {
      cancelButton = document.createElement("button");

      cancelButton.type = "button";
      cancelButton.dataset.cancelGeneration = "true";
      cancelButton.textContent = "Avbryt";

      cancelButton.className = "secondary";

      cancelButton.style.marginTop = "18px";
      cancelButton.style.width = "100%";

      cancelButton.hidden = true;

      cancelButton.addEventListener(
        "click",
        () => {
          if (activeController) {
            activeController.abort();
          }

          resetOverlay();

          window.location.reload();
        }
      );

      card.appendChild(cancelButton);
    }

    return {
      card,
      title,
      text,
      status,
      cancelButton
    };
  }


  function resetOverlay() {
    clearLoadingTimer();

    loadingSeconds = 0;
    generationInProgress = false;
    activeController = null;

    if (!overlay) {
      return;
    }

    overlay.hidden = true;

    const {
      title,
      text,
      status,
      cancelButton
    } = getOverlayElements();

    if (title) {
      title.textContent = "Bygger projektet…";
    }

    if (text) {
      text.textContent =
        "AI:n skriver kompletta filer, sparar en ny revision och kan därefter auto-publicera om du har aktiverat det.";
    }

    if (status) {
      status.textContent = "";
    }

    if (cancelButton) {
      cancelButton.hidden = true;
      cancelButton.textContent = "Avbryt";
    }
  }


  function showOverlay(mode = "generate") {
    if (!overlay) {
      return;
    }

    overlay.hidden = false;

    const {
      title,
      text,
      status,
      cancelButton
    } = getOverlayElements();

    if (mode === "refine") {
      if (title) {
        title.textContent = "Finslipar projektet…";
      }

      if (text) {
        text.textContent =
          "AI:n arbetar med din ändring och skapar en ny revision utan att skriva över historiken.";
      }
    } else {
      if (title) {
        title.textContent = "Bygger projektet…";
      }

      if (text) {
        text.textContent =
          "AI:n skapar en komplett första version av projektet. Vänta kvar på sidan medan genereringen pågår.";
      }
    }

    if (status) {
      status.textContent =
        "Startar AI-genereringen…";
    }

    if (cancelButton) {
      cancelButton.hidden = false;
      cancelButton.textContent = "Avbryt";
    }

    loadingSeconds = 0;

    clearLoadingTimer();

    loadingTimer = window.setInterval(
      () => {
        loadingSeconds += 1;

        if (!status) {
          return;
        }

        if (loadingSeconds < 10) {
          status.textContent =
            `Startar genereringen… ${loadingSeconds} s`;
        }

        else if (loadingSeconds < 45) {
          status.textContent =
            `AI:n bygger projektfiler… ${loadingSeconds} s`;
        }

        else if (loadingSeconds < 90) {
          status.textContent =
            `Fortfarande igång. Större svar kan ta lite tid… ${loadingSeconds} s`;
        }

        else if (loadingSeconds < 150) {
          status.textContent =
            `AI:n arbetar fortfarande… ${loadingSeconds} s`;
        }

        else if (loadingSeconds < 200) {
          status.textContent =
            `Genereringen tar längre tid än normalt… ${loadingSeconds} s`;
        }

        else {
          status.textContent =
            `Väntar på serverns slutsvar… ${loadingSeconds} s`;
        }
      },
      1000
    );
  }


  function showGenerationError(
    message,
    details = ""
  ) {
    clearLoadingTimer();

    generationInProgress = false;
    activeController = null;

    if (!overlay) {
      window.alert(
        details
          ? `${message}\n\n${details}`
          : message
      );

      return;
    }

    overlay.hidden = false;

    const {
      title,
      text,
      status,
      cancelButton
    } = getOverlayElements();

    if (title) {
      title.textContent =
        "Projektet kunde inte genereras";
    }

    if (text) {
      text.textContent =
        message ||
        "Ett fel uppstod under genereringen.";
    }

    if (status) {
      status.textContent =
        details ||
        "Ingen ändring har gjorts. Du kan försöka igen.";
    }

    if (cancelButton) {
      cancelButton.hidden = false;
      cancelButton.textContent =
        "Stäng och försök igen";

      cancelButton.onclick = () => {
        resetOverlay();
      };
    }
  }


  function extractErrorMessage(html) {
    if (!html) {
      return "";
    }

    try {
      const parser = new DOMParser();

      const doc = parser.parseFromString(
        html,
        "text/html"
      );

      const errorElement =
        doc.querySelector(".alert.error") ||
        doc.querySelector("[data-error]");

      if (errorElement) {
        return errorElement.textContent
          .trim()
          .replace(/\s+/g, " ");
      }

      const title =
        doc.querySelector("title");

      if (
        title &&
        title.textContent &&
        !title.textContent.includes(
          "WebbBuilder"
        )
      ) {
        return title.textContent.trim();
      }
    } catch (error) {
      console.error(
        "[WebbBuilder] Kunde inte läsa felmeddelande:",
        error
      );
    }

    return "";
  }


  function isGenerationForm(form) {
    if (!form) {
      return false;
    }

    const action =
      form.getAttribute("action") || "";

    return (
      action.includes("/generate") ||
      action.includes("/refine")
    );
  }


  function getGenerationMode(form) {
    const action =
      form.getAttribute("action") || "";

    return action.includes("/refine")
      ? "refine"
      : "generate";
  }


  function disableForm(form, disabled) {
    if (!form) {
      return;
    }

    const controls =
      form.querySelectorAll(
        "button, input, textarea, select"
      );

    controls.forEach(
      (control) => {
        /*
         * Spara ursprungligt disabled-läge så att exempelvis
         * knappar som redan var disabled inte aktiveras av misstag.
         */
        if (disabled) {
          control.dataset.wasDisabled =
            control.disabled
              ? "true"
              : "false";

          control.disabled = true;
        } else {
          const wasDisabled =
            control.dataset.wasDisabled === "true";

          control.disabled = wasDisabled;

          delete control.dataset.wasDisabled;
        }
      }
    );
  }


  // ============================================================
  // AI GENERATION / REFINE
  // ============================================================

  async function submitGenerationForm(form) {
    if (
      !form ||
      generationInProgress
    ) {
      return;
    }

    generationInProgress = true;

    const mode =
      getGenerationMode(form);

    showOverlay(mode);

    disableForm(
      form,
      true
    );

    const controller =
      new AbortController();

    activeController = controller;

    const timeoutId =
      window.setTimeout(
        () => {
          controller.abort();
        },
        CLIENT_TIMEOUT_MS
      );

    try {
      const action =
        new URL(
          form.action,
          window.location.href
        );

      const method =
        (
          form.method ||
          "POST"
        ).toUpperCase();

      const formData =
        new FormData(form);

      console.log(
        "[WebbBuilder] Skickar generering:",
        method,
        action.toString()
      );

      const response =
        await fetch(
          action.toString(),
          {
            method,
            body: formData,
            credentials: "same-origin",
            redirect: "follow",
            signal: controller.signal,
            headers: {
              "X-Requested-With":
                "XMLHttpRequest"
            }
          }
        );

      window.clearTimeout(
        timeoutId
      );

      console.log(
        "[WebbBuilder] Genereringssvar:",
        {
          status: response.status,
          ok: response.ok,
          redirected: response.redirected,
          url: response.url
        }
      );

      /*
       * FastAPI returnerar normalt en redirect efter lyckad
       * generering. fetch följer redirecten automatiskt.
       *
       * Då hamnar response.url på projektsidan.
       */
      if (
        response.ok &&
        response.redirected
      ) {
        clearLoadingTimer();

        const {
          title,
          text,
          status,
          cancelButton
        } = getOverlayElements();

        if (title) {
          title.textContent =
            mode === "refine"
              ? "Ändringen är klar"
              : "Projektet är klart";
        }

        if (text) {
          text.textContent =
            "Laddar den färdiga versionen…";
        }

        if (status) {
          status.textContent =
            "Klart.";
        }

        if (cancelButton) {
          cancelButton.hidden = true;
        }

        window.location.assign(
          response.url
        );

        return;
      }

      /*
       * Vissa FastAPI-flöden kan returnera den färdiga
       * projektsidan direkt med 200 istället för redirect.
       */
      const responseText =
        await response.text();

      if (response.ok) {
        const contentType =
          response.headers.get(
            "content-type"
          ) || "";

        if (
          contentType.includes(
            "text/html"
          )
        ) {
          /*
           * Kontrollera om HTML-svaret innehåller ett felmeddelande
           * trots HTTP 200.
           */
          const embeddedError =
            extractErrorMessage(
              responseText
            );

          if (embeddedError) {
            showGenerationError(
              embeddedError
            );

            disableForm(
              form,
              false
            );

            return;
          }

          /*
           * Genereringen lyckades men servern returnerade HTML
           * utan redirect. Ladda om projektet.
           */
          window.location.reload();

          return;
        }

        /*
         * Annat lyckat svar.
         */
        window.location.reload();

        return;
      }

      /*
       * Servern svarade med 4xx eller 5xx.
       */
      const extractedError =
        extractErrorMessage(
          responseText
        );

      showGenerationError(
        extractedError ||
          `Servern svarade med fel ${response.status}.`,
        `HTTP ${response.status} ${response.statusText || ""}`.trim()
      );

      disableForm(
        form,
        false
      );
    }

    catch (error) {
      window.clearTimeout(
        timeoutId
      );

      console.error(
        "[WebbBuilder] Genereringsfel:",
        error
      );

      if (
        error &&
        error.name === "AbortError"
      ) {
        showGenerationError(
          "Genereringen tog för lång tid och avbröts i webbläsaren.",
          "Serverns maximala genereringstid är cirka 210 sekunder. Kontrollera Render-loggen efter ett OpenAI-fel eller försök igen."
        );
      } else {
        showGenerationError(
          "Webbläsaren kunde inte slutföra genereringen.",
          error instanceof Error
            ? error.message
            : String(error)
        );
      }

      disableForm(
        form,
        false
      );
    }

    finally {
      activeController = null;
      generationInProgress = false;
    }
  }


  // ============================================================
  // FORM EVENTS
  // ============================================================

  document
    .querySelectorAll(
      "[data-loading-form]"
    )
    .forEach(
      (form) => {
        form.addEventListener(
          "submit",
          async (event) => {
            /*
             * Vi skickar AI-formulären explicit med fetch.
             *
             * Detta gör att overlayen inte kan stå och snurra
             * utan att ett riktigt POST-anrop har skickats.
             */
            if (
              isGenerationForm(form)
            ) {
              event.preventDefault();

              await submitGenerationForm(
                form
              );

              return;
            }

            /*
             * Fallback för eventuella framtida formulär som använder
             * data-loading-form men inte är generate/refine.
             */
            showOverlay(
              "generate"
            );
          }
        );
      }
    );


  // ============================================================
  // PAGE RESTORE
  // ============================================================

  window.addEventListener(
    "pageshow",
    () => {
      resetOverlay();
    }
  );


  /*
   * Säkerställ att overlay inte ligger kvar om användaren
   * går tillbaka med webbläsarens back-knapp.
   */
  window.addEventListener(
    "popstate",
    () => {
      resetOverlay();
    }
  );


  // ============================================================
  // QUICK REFINE PROMPTS
  // ============================================================

  const refineTextarea =
    document.querySelector(
      'textarea[name="instruction"]'
    );

  document
    .querySelectorAll(
      "[data-prompt]"
    )
    .forEach(
      (button) => {
        button.addEventListener(
          "click",
          () => {
            if (!refineTextarea) {
              return;
            }

            refineTextarea.value =
              button.dataset.prompt || "";

            refineTextarea.focus();

            /*
             * Flytta markören sist.
             */
            const length =
              refineTextarea.value.length;

            refineTextarea.setSelectionRange(
              length,
              length
            );
          }
        );
      }
    );


  // ============================================================
  // PREVIEW WIDTH
  // ============================================================

  const previewFrame =
    document.getElementById(
      "previewFrame"
    );

  document
    .querySelectorAll(
      "[data-preview-width]"
    )
    .forEach(
      (button) => {
        button.addEventListener(
          "click",
          () => {
            if (!previewFrame) {
              return;
            }

            const width =
              button.dataset.previewWidth ||
              "100%";

            if (
              width === "100%"
            ) {
              previewFrame.style.width =
                "100%";

              return;
            }

            const numericWidth =
              Number(width);

            if (
              Number.isFinite(
                numericWidth
              ) &&
              numericWidth > 0
            ) {
              previewFrame.style.width =
                `${numericWidth}px`;
            }
          }
        );
      }
    );


  // ============================================================
  // REFRESH PREVIEW
  // ============================================================

  document
    .querySelectorAll(
      "[data-refresh-preview]"
    )
    .forEach(
      (button) => {
        button.addEventListener(
          "click",
          () => {
            if (!previewFrame) {
              return;
            }

            try {
              const url =
                new URL(
                  previewFrame.src,
                  window.location.origin
                );

              url.searchParams.set(
                "_refresh",
                String(
                  Date.now()
                )
              );

              previewFrame.src =
                url.toString();
            } catch (error) {
              console.error(
                "[WebbBuilder] Kunde inte uppdatera preview:",
                error
              );

              /*
               * Enkel fallback.
               */
              previewFrame.src =
                previewFrame.src;
            }
          }
        );
      }
    );


  // ============================================================
  // DEBUG
  // ============================================================

  console.log(
    "[WebbBuilder] app.js laddad"
  );

  console.log(
    "[WebbBuilder] AI-formulär:",
    document.querySelectorAll(
      "[data-loading-form]"
    ).length
  );
})();
