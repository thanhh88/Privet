
(() => {
  "use strict";

  const root = document.getElementById("vocabulary-app");

  if (!root || root.dataset.level === "B1") {
    return;
  }

  const $ = (id) => document.getElementById(id);

  const grid = $("voc-grid");
  const status = $("voc-status");
  const dialog = $("voc-detail-dialog");
  const totalLabel = $("voc-total");
  const searchBox = $("voc-search");
  const prev = $("voc-prev");
  const next = $("voc-next");
  const pager = $("voc-pagination");
  const pageNumber = $("voc-page-number");
  const detailSave = $("voc-detail-save");
  const startButton = $("voc-start");
  const csrf = $("voc-csrf").value;

  const PAGE_SIZE = 28;

  let page = 0;
  let query = "";
  let loadingVersion = 0;
  let detailVersion = 0;
  let searchTimer = null;

  let savedIds = new Set();
  let currentItems = [];
  let activeItem = null;

  function itemUrl(template, sourceId) {
    return template.replace(
      "WORD_ID",
      encodeURIComponent(sourceId)
    );
  }

  function readableError(error) {
    const code = error?.message || "unknown";

    const messages = {
      authentication_required:
        "Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.",
      daily_new_limit:
        "Bạn đã đạt giới hạn 15 từ mới trong ngày hôm nay.",
      item_not_found:
        "Từ này hiện không còn khả dụng.",
    };

    return (
      messages[code] ||
      `Không thể xử lý yêu cầu (${code}). Hãy thử lại.`
    );
  }

  async function requestJSON(url, options = {}) {
    const response = await fetch(url, {
      credentials: "same-origin",
      ...options,
    });

    let data = {};

    try {
      data = await response.json();
    } catch (_) {
      // Error handling below.
    }

    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }

    return data;
  }

  function postJSON(url, payload) {
    return requestJSON(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify(payload),
    });
  }

  function setStatus(message) {
    status.textContent = message;
  }

  function setDetailStatus(message, isError = false) {
    const element = $("voc-detail-status");

    element.textContent = message || "";
    element.classList.toggle("is-error", isError);
  }

  // =========================================================
  // SAVED WORDS
  // =========================================================

  function updateSaveButton(button, sourceId, isDetail = false) {
    const saved = savedIds.has(sourceId);

    button.setAttribute("aria-pressed", String(saved));

    if (isDetail) {
      button.textContent = saved ? "★ Đã lưu" : "☆ Lưu từ";
      button.setAttribute(
        "aria-label",
        saved ? "Bỏ lưu từ này" : "Lưu từ này"
      );
    } else {
      button.textContent = saved ? "★" : "☆";
      button.setAttribute(
        "aria-label",
        saved ? "Bỏ lưu từ" : "Lưu từ"
      );
      button.title = saved ? "Bỏ lưu từ" : "Lưu từ";
    }
  }

  function refreshSavedButtons() {
    for (const card of grid.querySelectorAll(".voc-card")) {
      const sourceId = card.dataset.sourceId;
      const saved = savedIds.has(sourceId);

      card.classList.toggle("is-saved", saved);

      const button = card.querySelector(".voc-save-card");

      if (button) {
        updateSaveButton(button, sourceId);
      }
    }

    if (activeItem) {
      updateSaveButton(
        detailSave,
        activeItem.source_id,
        true
      );
    }
  }

  async function toggleSave(sourceId, button) {
    if (button.disabled) return;

    button.disabled = true;

    const desired = !savedIds.has(sourceId);

    try {
      const data = await postJSON(
        root.dataset.savedToggleUrl,
        {
          source_id: sourceId,
          saved: desired,
        }
      );

      if (data.saved) {
        savedIds.add(sourceId);
      } else {
        savedIds.delete(sourceId);
      }

      refreshSavedButtons();

      if (
        activeItem &&
        activeItem.source_id === sourceId
      ) {
        setDetailStatus(
          data.saved
            ? "Đã lưu từ này."
            : "Đã bỏ lưu từ này."
        );
      }
    } catch (error) {
      if (
        activeItem &&
        activeItem.source_id === sourceId
      ) {
        setDetailStatus(
          readableError(error),
          true
        );
      } else {
        setStatus(readableError(error));
      }
    } finally {
      button.disabled = false;
    }
  }

  // =========================================================
  // WORD CARDS
  // =========================================================

  function buildCard(item) {
    const card = document.createElement("article");

    card.className = "voc-card";
    card.dataset.sourceId = item.source_id;

    const badge = document.createElement("span");

    badge.className = "voc-card-label";
    badge.textContent = item.srs
      ? "Đã học"
      : root.dataset.level;

    const saveButton = document.createElement("button");

    saveButton.type = "button";
    saveButton.className = "voc-save-card";

    updateSaveButton(saveButton, item.source_id);

    saveButton.addEventListener("click", () => {
      toggleSave(item.source_id, saveButton);
    });

    const openButton = document.createElement("button");

    openButton.type = "button";
    openButton.className = "voc-card-open";
    openButton.setAttribute(
      "aria-label",
      `Xem từ ${item.lemma}`
    );

    const word = document.createElement("span");
    word.className = "voc-word";
    word.lang = "ru";
    word.textContent = item.stress_marked || item.lemma;

    const meaning = document.createElement("span");
    meaning.className = "voc-meaning";

    meaning.textContent =
      item.display_meaning || "Đang bổ sung nghĩa";

    openButton.append(word, meaning);

    openButton.addEventListener("click", () => {
      showDetail(item);
    });

    card.append(badge, saveButton, openButton);

    card.classList.toggle(
      "is-saved",
      savedIds.has(item.source_id)
    );

    return card;
  }

  // =========================================================
  // FETCH VOCABULARY
  // =========================================================

  async function loadPage() {
    const version = ++loadingVersion;

    grid.setAttribute("aria-busy", "true");
    grid.replaceChildren();

    pager.hidden = true;

    setStatus("Đang tải từ vựng…");

    try {
      const params = new URLSearchParams({
        level: root.dataset.level,
        limit: String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
      });

      if (query) {
        params.set("q", query);
      }

      const catalog = await requestJSON(
        `${root.dataset.itemsUrl}?${params}`
      );

      if (version !== loadingVersion) return;

      const words = catalog.items || [];

      // Fetch saved status for the current page.
      const savedParams = new URLSearchParams();

      words.forEach((item) => {
        savedParams.append("ids", item.source_id);
      });

      const savedResult = words.length
        ? await requestJSON(
            `${root.dataset.savedStatusUrl}?${savedParams}`
          )
        : { saved_ids: [] };

      if (version !== loadingVersion) return;

      currentItems = words;
      savedIds = new Set(savedResult.saved_ids || []);

      const total = Number(catalog.total || 0);

      totalLabel.textContent = total.toLocaleString("vi-VN");

      const fragment = document.createDocumentFragment();

      currentItems.forEach((item) => {
        fragment.appendChild(buildCard(item));
      });

      grid.appendChild(fragment);

      // Pagination
      const pages = Math.max(
        1,
        Math.ceil(total / PAGE_SIZE)
      );

      pager.hidden = total === 0 || pages < 2;

      pageNumber.textContent =
        `Trang ${page + 1} / ${pages}`;

      prev.disabled = page === 0;
      next.disabled = page + 1 >= pages;

      if (total === 0) {
        setStatus(
          "Không tìm thấy từ phù hợp. Thử từ khóa khác nhé!"
        );
      } else {
        const from = page * PAGE_SIZE + 1;
        const to = page * PAGE_SIZE + words.length;

        setStatus(
          `Đang hiển thị ${from}–${to} / ` +
          `${total.toLocaleString("vi-VN")} từ.`
        );
      }
    } catch (error) {
      if (version === loadingVersion) {
        setStatus(readableError(error));
      }
    } finally {
      if (version === loadingVersion) {
        grid.setAttribute("aria-busy", "false");
      }
    }
  }

  // =========================================================
  // WORD DETAIL
  // =========================================================

  function showItemDetails(item) {
    activeItem = item;

    $("voc-detail-level").textContent =
      `Từ vựng ${root.dataset.level}`;

    $("voc-detail-title").textContent =
      item.stress_marked || item.lemma;

    $("voc-detail-meaning").textContent =
      item.display_meaning || "Đang bổ sung nghĩa";

    $("voc-detail-language").textContent =
      item.display_language === "en"
        ? "Nghĩa tiếng Anh · đang bổ sung nghĩa tiếng Việt"
        : "Nghĩa tiếng Việt";

    const exampleBox = $("voc-example-box");

    exampleBox.hidden =
      !item.example_ru && !item.example_vi;

    $("voc-example-ru").textContent =
      item.example_ru || "";

    $("voc-example-vi").textContent =
      item.example_vi || "";

    if (item.srs) {
      const nextReview = new Date(
        item.srs.next_review_at
      );

      const formatted = Number.isNaN(nextReview.getTime())
        ? "đã lên lịch"
        : nextReview.toLocaleDateString("vi-VN");

      $("voc-review-info").textContent =
        `Đã thêm vào Leitner · Hộp ${item.srs.box}` +
        ` · Ôn tiếp: ${formatted}`;

      startButton.textContent = "Đã có trong lịch ôn";
      startButton.disabled = true;
    } else {
      $("voc-review-info").textContent =
        "Bắt đầu học để đưa từ vào lịch ôn Leitner.";

      startButton.textContent = "Bắt đầu học";
      startButton.disabled = false;
    }

    updateSaveButton(
      detailSave,
      item.source_id,
      true
    );
  }

  async function showDetail(item) {
    const version = ++detailVersion;

    activeItem = item;

    showItemDetails(item);
    setDetailStatus("");

    if (!dialog.open) {
      dialog.showModal();
    }

    try {
      const detailURL = itemUrl(
        root.dataset.detailUrl,
        item.source_id
      );

      const params = new URLSearchParams({
        level: root.dataset.level,
      });

      const result = await requestJSON(
        `${detailURL}?${params}`
      );

      if (
        version !== detailVersion ||
        !dialog.open
      ) {
        return;
      }

      showItemDetails(result.item);
    } catch (error) {
      if (
        version === detailVersion &&
        dialog.open
      ) {
        setDetailStatus(
          readableError(error),
          true
        );
      }
    }
  }

  // =========================================================
  // START LEARNING — BACKEND SRS v2
  // =========================================================

  async function startCurrentWord() {
    if (!activeItem || startButton.disabled) return;

    const item = activeItem;

    startButton.disabled = true;

    setDetailStatus("Đang thêm từ vào lịch ôn…");

    try {
      const url = itemUrl(
        root.dataset.startUrl,
        item.source_id
      );

      const result = await postJSON(url, {});

      item.srs = result.srs;

      const listedItem = currentItems.find(
        (word) => word.source_id === item.source_id
      );

      if (listedItem) {
        listedItem.srs = result.srs;
      }

      if (
        activeItem &&
        activeItem.source_id === item.source_id &&
        dialog.open
      ) {
        showItemDetails(item);

        setDetailStatus(
          result.created
            ? "Đã thêm từ vào lịch ôn. Lần ôn đầu sau 1 ngày."
            : "Từ này đã có trong lịch ôn của bạn."
        );
      }

      const card = Array.from(grid.children).find(
        (element) =>
          element.dataset.sourceId === item.source_id
      );

      if (card) {
        card.querySelector(
          ".voc-card-label"
        ).textContent = "Đã học";
      }
    } catch (error) {
      if (
        activeItem &&
        activeItem.source_id === item.source_id &&
        dialog.open
      ) {
        setDetailStatus(
          readableError(error),
          true
        );

        startButton.disabled = false;
      }
    }
  }

  // =========================================================
  // EVENTS
  // =========================================================

  searchBox.addEventListener("input", () => {
    clearTimeout(searchTimer);

    searchTimer = setTimeout(() => {
      query = searchBox.value.trim();
      page = 0;
      loadPage();
    }, 280);
  });

  prev.addEventListener("click", () => {
    if (page <= 0) return;

    page -= 1;
    loadPage();
  });

  next.addEventListener("click", () => {
    page += 1;
    loadPage();
  });

  detailSave.addEventListener("click", () => {
    if (activeItem) {
      toggleSave(
        activeItem.source_id,
        detailSave
      );
    }
  });

  startButton.addEventListener(
    "click",
    startCurrentWord
  );

  $("voc-close").addEventListener("click", () => {
    dialog.close();
  });

  dialog.addEventListener("close", () => {
    ++detailVersion;
    activeItem = null;
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });

  // Initial load
  loadPage();
})();
