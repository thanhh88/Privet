document.addEventListener("DOMContentLoaded", () => {
  const modal = document.querySelector("[data-auth-modal]");

  if (!modal) {
    return;
  }

  const panels = modal.querySelectorAll(
    "[data-auth-panel]"
  );

  const openButtons = document.querySelectorAll(
    "[data-auth-open]"
  );

  const switchButtons = modal.querySelectorAll(
    "[data-auth-switch]"
  );

  const closeButtons = modal.querySelectorAll(
    "[data-auth-close]"
  );


  function showPanel(mode) {
    panels.forEach((panel) => {
      panel.hidden =
        panel.dataset.authPanel !== mode;
    });

    modal.dataset.activeMode = mode;
  }


  function openModal(mode) {
    showPanel(mode);

    modal.classList.add("is-open");

    modal.setAttribute(
      "aria-hidden",
      "false"
    );

    document.body.classList.add(
      "auth-modal-open"
    );
  }


  function closeModal() {
    modal.classList.remove("is-open");

    modal.setAttribute(
      "aria-hidden",
      "true"
    );

    document.body.classList.remove(
      "auth-modal-open"
    );
  }


  /*
   * Landing buttons:
   * Đăng nhập / Đăng ký
   */
  openButtons.forEach((button) => {
    button.addEventListener(
      "click",
      (event) => {
        event.preventDefault();

        openModal(
          button.dataset.authOpen
        );
      }
    );
  });


  /*
   * Switch inside modal:
   * Login -> Register
   * Register -> Login
   */
  switchButtons.forEach((button) => {
    button.addEventListener(
      "click",
      (event) => {
        event.preventDefault();

        const mode =
          button.dataset.authSwitch;

        showPanel(mode);
      }
    );
  });


  /*
   * Close button + backdrop
   */
  closeButtons.forEach((button) => {
    button.addEventListener(
      "click",
      (event) => {
        event.preventDefault();

        closeModal();
      }
    );
  });


  /*
   * ESC closes modal
   */
  document.addEventListener(
    "keydown",
    (event) => {
      if (
        event.key === "Escape"
        && modal.classList.contains(
          "is-open"
        )
      ) {
        closeModal();
      }
    }
  );


  /*
   * Server may render Landing with a modal
   * already open after validation error.
   */
  const initialMode =
    modal.dataset.initialMode;

  if (
    initialMode === "login"
    || initialMode === "register"
  ) {
    openModal(initialMode);
  }
});