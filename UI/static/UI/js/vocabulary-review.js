(() => {
  "use strict";

  const root = document.getElementById("r9-vocabulary-review");
  if (!root) return;

  const csrf = document.getElementById("r9-csrf").value;
  const status = document.getElementById("r9-vocab-status");
  const progress = document.getElementById("r9-vocab-progress");
  const card = document.getElementById("r9-vocab-card");
  const actions = document.getElementById("r9-vocab-actions");
  const finish = document.getElementById("r9-vocab-finish");
  const remembered = document.getElementById("r9-remembered");
  const forgot = document.getElementById("r9-forgot");
  const finishButton = document.getElementById("r9-finish-button");

  let queue = [];
  let reviewed = 0;
  let reviewLogIds = [];
  let shownAt = null;

  async function requestJSON(url, options = {}) {
    const response = await fetch(url, { credentials: "same-origin", ...options });
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data.error || data.detail || `HTTP ${response.status}`);
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

  function render() {
    card.replaceChildren();
    if (!queue.length) {
      actions.hidden = true;
      finish.hidden = reviewLogIds.length === 0;
      progress.textContent = reviewed ? `Đã ôn ${reviewed} từ.` : "Không còn từ đến hạn.";
      status.textContent = reviewLogIds.length ? "Bạn có thể kết thúc phiên ôn." : "Không có review mới để ghi nhận.";
      return;
    }

    const item = queue[0];
    shownAt = performance.now();
    progress.textContent = `Đã ôn ${reviewed} từ · Còn ${queue.length} từ trong phiên này`;
    const word = document.createElement("div");
    word.className = "r9-word";
    word.lang = "ru";
    word.textContent = item.stress_marked || item.lemma;
    const meaning = document.createElement("p");
    meaning.className = "r9-meaning";
    meaning.textContent = item.display_meaning || item.meaning_vi || item.meaning_en || "";
    card.append(word, meaning);
    if (item.example_ru || item.example_vi) {
      const example = document.createElement("div");
      example.className = "r9-example";
      example.textContent = [item.example_ru, item.example_vi].filter(Boolean).join(" — ");
      card.appendChild(example);
    }
    actions.hidden = false;
    finish.hidden = true;
    status.textContent = "Bạn có nhớ nghĩa của từ này không?";
  }

  async function rate(rating) {
    if (!queue.length) return;
    const item = queue[0];
    remembered.disabled = true;
    forgot.disabled = true;
    status.textContent = "Đang lưu kết quả…";
    try {
      const elapsed = Math.max(0, Math.round(performance.now() - shownAt));
      const result = await postJSON(root.dataset.reviewUrl, {
        source_id: item.source_id,
        request_id: crypto.randomUUID(),
        rating,
        expected_version: item.srs.version,
        response_time_ms: elapsed,
      });
      if (result.review?.review_log_id) {
        reviewLogIds.push(result.review.review_log_id);
      }
      reviewed += 1;
      queue.shift();
      render();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      remembered.disabled = false;
      forgot.disabled = false;
    }
  }

  async function finishSession() {
    if (!reviewLogIds.length) return;
    finishButton.disabled = true;
    status.textContent = "Đang hoàn tất phiên ôn…";
    try {
      await postJSON(root.dataset.completeUrl, {
        request_id: crypto.randomUUID(),
        vocabulary_review_log_ids: reviewLogIds,
      });
      window.location.assign(root.dataset.dashboardUrl);
    } catch (error) {
      status.textContent = error.message;
      finishButton.disabled = false;
    }
  }

  remembered.addEventListener("click", () => rate("remembered"));
  forgot.addEventListener("click", () => rate("forgot"));
  finishButton.addEventListener("click", finishSession);

  async function load() {
    if (root.dataset.completed === "1") {
      progress.textContent = "Phiên ôn đã hoàn thành.";
      status.textContent = "Kết quả đã được lưu.";
      return;
    }
    try {
      const data = await requestJSON(`${root.dataset.dueUrl}?limit=50&offset=0`);
      queue = data.items || [];
      render();
    } catch (error) {
      status.textContent = error.message;
    }
  }

  load();
})();
