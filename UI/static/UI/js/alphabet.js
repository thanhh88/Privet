
/*
 * PRIVET ALPHABET UI - V3
 *
 * Two modes:
 * 1. Learn by lessons
 * 2. Full alphabet
 *
 * Data comes from the existing read-only API.
 */

(() => {
  "use strict";

  function initAlphabet() {
    const root = document.getElementById("alphabet-app");
    if (!root) return;

    const $ = (id) => document.getElementById(id);

    const requiredIds = [
      "alpha-status",
      "alpha-tab-lessons",
      "alpha-tab-table",
      "alpha-panel-lessons",
      "alpha-panel-table",
      "alpha-lessons-grid",
      "alpha-lesson-workspace",
      "alpha-current-lesson-number",
      "alpha-current-lesson-title",
      "alpha-current-lesson-objective",
      "alpha-lesson-letters",
      "alpha-table-grid",
      "alpha-detail-card",
      "alpha-lesson-detail-slot",
      "alpha-table-detail-slot",
    ];

    const missing = requiredIds.filter((id) => !$(id));

    if (missing.length) {
      console.error(
        "Alphabet HTML thiếu id:",
        missing
      );

      const status = $("alpha-status");

      if (status) {
        status.textContent =
          "Trang Alphabet thiếu thành phần giao diện.";
      }

      return;
    }

    const status = $("alpha-status");
    const detail = $("alpha-detail-card");

    const state = {
      overview: null,
      allLetters: [],
      byId: new Map(),

      mode: "lessons",

      lessonId: null,
      lessonLetterId: null,
      tableLetterId: null,

      playing: null,
    };

    // ==========================================
    // DOM HELPERS
    // ==========================================

    function element(tag, className = "", text = null) {
      const node = document.createElement(tag);

      if (className) {
        node.className = className;
      }

      if (text !== null) {
        node.textContent = String(text);
      }

      return node;
    }

    function ru(tag, className, text) {
      const node = element(tag, className, text);
      node.lang = "ru";
      return node;
    }

    function announce(message = "", error = false) {
      status.textContent = message;
      status.classList.toggle("is-error", error);
    }

    // ==========================================
    // API
    // ==========================================

    async function getJSON(url) {
      if (!url) {
        throw new Error("Thiếu đường dẫn API Alphabet.");
      }

      let response;

      try {
        response = await fetch(url, {
          method: "GET",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
          },
        });
      } catch (_error) {
        throw new Error(
          "Không kết nối được tới Django. Kiểm tra server."
        );
      }

      if (!response.ok) {
        throw new Error(
          `API Alphabet trả về HTTP ${response.status}.`
        );
      }

      const type =
        response.headers.get("content-type") || "";

      if (!type.includes("application/json")) {
        throw new Error(
          "API trả về HTML thay vì JSON. Kiểm tra đăng nhập."
        );
      }

      try {
        return await response.json();
      } catch (_error) {
        throw new Error(
          "Không đọc được JSON từ API Alphabet."
        );
      }
    }

    // ==========================================
    // AUDIO
    // ==========================================

    function stopAudio() {
      const current = state.playing;

      if (!current) return;

      state.playing = null;

      current.button.disabled = false;
      current.button.textContent = current.label;
      current.button.removeAttribute("aria-busy");

      current.audio.pause();

      try {
        current.audio.currentTime = 0;
      } catch (_error) {
        // Audio may already be unloaded.
      }
    }

    async function playAudio(url, button) {
      if (!url) {
        announce(
          "Chưa có đường dẫn âm thanh cho mục này.",
          true
        );
        return;
      }

      stopAudio();

      const audio = new Audio(url);

      const current = {
        audio,
        button,
        label: button.textContent,
      };

      state.playing = current;

      button.disabled = true;
      button.textContent = "Đang phát…";
      button.setAttribute("aria-busy", "true");

      audio.addEventListener("ended", () => {
        if (state.playing === current) {
          stopAudio();
        }
      });

      audio.addEventListener("error", () => {
        if (state.playing === current) {
          stopAudio();

          announce(
            "Chưa có MP3 hoặc đường dẫn audio bị sai.",
            true
          );
        }
      });

      try {
        await audio.play();

        if (state.playing === current) {
          announce();
        }
      } catch (_error) {
        if (state.playing === current) {
          stopAudio();

          announce(
            "Không phát được audio. Kiểm tra file MP3.",
            true
          );
        }
      }
    }

    function soundButton(label, url, compact = false) {
      const button = element(
        "button",
        `alpha-audio-btn${compact ? " is-compact" : ""}`,
        `🔊 ${label}`
      );

      button.type = "button";

      button.setAttribute(
        "aria-label",
        `Nghe ${label}`
      );

      button.addEventListener("click", () => {
        playAudio(url, button);
      });

      return button;
    }

    // ==========================================
    // DATA HELPERS
    // ==========================================

    function lessonNow() {
      return (
        state.overview.lessons.find(
          (lesson) => lesson.id === state.lessonId
        ) || null
      );
    }

    function letterNow() {
      const id =
        state.mode === "table"
          ? state.tableLetterId
          : state.lessonLetterId;

      return state.byId.get(id) || null;
    }

    function availableLetters() {
      if (state.mode === "table") {
        return state.allLetters;
      }

      const lesson = lessonNow();

      if (!lesson) return [];

      return lesson.letter_ids
        .map((id) => state.byId.get(id))
        .filter(Boolean);
    }

    // ==========================================
    // LETTER BUTTON
    // ==========================================

    function letterButton(letter) {
      const isSelected =
        letterNow()?.id === letter.id;

      const button = element(
        "button",
        `alpha-letter-btn${isSelected ? " is-selected" : ""}`
      );

      button.type = "button";
      button.dataset.letterId = letter.id;

      button.setAttribute(
        "aria-label",
        `Xem chữ ${letter.upper}, ${letter.lower}`
      );

      button.setAttribute(
        "aria-pressed",
        String(isSelected)
      );

      button.append(
        ru("strong", "", letter.upper),
        ru("small", "", letter.lower)
      );

      button.addEventListener("click", () => {
        if (state.mode === "table") {
          state.tableLetterId = letter.id;
        } else {
          state.lessonLetterId = letter.id;
        }

        render();

        const panel = $(
          state.mode === "table"
            ? "alpha-panel-table"
            : "alpha-panel-lessons"
        );

        const selected = [
          ...panel.querySelectorAll(".alpha-letter-btn"),
        ].find(
          (btn) => btn.dataset.letterId === letter.id
        );

        if (selected) {
          selected.focus({
            preventScroll: true,
          });
        }
      });

      return button;
    }

    // ==========================================
    // RENDER MODE TABS
    // ==========================================

    function renderTabs() {
      ["lessons", "table"].forEach((mode) => {
        const active = mode === state.mode;
        const tab = $(`alpha-tab-${mode}`);

        tab.classList.toggle("is-active", active);

        tab.setAttribute(
          "aria-selected",
          String(active)
        );

        tab.tabIndex = active ? 0 : -1;

        $(`alpha-panel-${mode}`).hidden = !active;
      });

      const slot = $(
        state.mode === "table"
          ? "alpha-table-detail-slot"
          : "alpha-lesson-detail-slot"
      );

      slot.appendChild(detail);
      detail.hidden = false;
    }

    // ==========================================
    // RENDER SEVEN LESSONS
    // ==========================================

    function renderLessons() {
      const list = $("alpha-lessons-grid");

      const fragment =
        document.createDocumentFragment();

      state.overview.lessons.forEach(
        (lesson, index) => {
          const selected =
            lesson.id === state.lessonId;

          const card = element(
            "button",
            `alpha-lesson-card${selected ? " is-selected" : ""}`
          );

          card.type = "button";

          card.setAttribute(
            "aria-pressed",
            String(selected)
          );

          card.setAttribute(
            "aria-label",
            `Mở bài ${index + 1}: ${lesson.title}`
          );

          // Lesson number
          const number = element(
            "span",
            "alpha-lesson-index",
            String(index + 1).padStart(2, "0")
          );

          // Header: number + letter count
          const cardTop = element(
            "span",
            "alpha-lesson-card-head"
          );

          cardTop.append(
            number,
            element(
              "span",
              "alpha-lesson-count",
              `${lesson.letter_count} CHỮ CÁI`
            )
          );

          // Separate Cyrillic tiles
          const glyphs = element(
            "span",
            "alpha-lesson-glyphs"
          );

          lesson.letter_ids.forEach((id) => {
            const letter = state.byId.get(id);

            if (letter) {
              glyphs.appendChild(
                ru(
                  "span",
                  "alpha-lesson-glyph",
                  letter.upper
                )
              );
            }
          });

          card.append(
            cardTop,

            element(
              "strong",
              "alpha-lesson-title",
              lesson.title
            ),

            glyphs,

            element(
              "span",
              "alpha-lesson-open",
              "Mở bài →"
            )
          );

          card.addEventListener("click", () => {
            state.lessonId = lesson.id;

            state.lessonLetterId =
              lesson.letter_ids[0];

            render();

            $("alpha-lesson-workspace").scrollIntoView({
              behavior: "smooth",
              block: "start",
            });
          });

          fragment.appendChild(card);
        }
      );

      list.replaceChildren(fragment);

      const selected = lessonNow();

      if (!selected) return;

      $("alpha-lesson-workspace").hidden = false;

      const position =
        state.overview.lessons.findIndex(
          (lesson) => lesson.id === selected.id
        ) + 1;

      $("alpha-current-lesson-number").textContent =
        `Bài ${String(position).padStart(2, "0")}`;

      $("alpha-current-lesson-title").textContent =
        selected.title;

      $("alpha-current-lesson-objective").textContent =
        selected.objective;

      const strip = selected.letter_ids
        .map((id) => state.byId.get(id))
        .filter(Boolean);

      $("alpha-lesson-letters").replaceChildren(
        ...strip.map(letterButton)
      );
    }

    // ==========================================
    // RENDER FULL TABLE
    // ==========================================

    function renderTable() {
      $("alpha-table-grid").replaceChildren(
        ...state.allLetters.map(letterButton)
      );
    }

    // ==========================================
    // CONTENT SECTION
    // ==========================================

    function section(title, contents) {
      const node = element(
        "section",
        "alpha-detail-section"
      );

      node.append(
        element("h4", "", title),
        ...contents
      );

      return node;
    }

    // ==========================================
    // PREVIOUS / NEXT
    // ==========================================

    function neighbor(direction) {
      const letters = availableLetters();

      const index = letters.findIndex(
        (letter) => letter.id === letterNow()?.id
      );

      const next = index + direction;

      if (next >= 0 && next < letters.length) {
        return {
          type: "letter",
          id: letters[next].id,
        };
      }

      if (state.mode === "table") {
        return null;
      }

      const lessonIndex =
        state.overview.lessons.findIndex(
          (lesson) => lesson.id === state.lessonId
        );

      const nextLesson =
        state.overview.lessons[
          lessonIndex + direction
        ];

      return nextLesson
        ? {
            type: "lesson",
            id: nextLesson.id,
          }
        : null;
    }

    function move(direction) {
      const target = neighbor(direction);

      if (!target) return;

      if (target.type === "letter") {
        if (state.mode === "table") {
          state.tableLetterId = target.id;
        } else {
          state.lessonLetterId = target.id;
        }
      } else {
        const lesson =
          state.overview.lessons.find(
            (item) => item.id === target.id
          );

        state.lessonId = lesson.id;

        state.lessonLetterId =
          direction > 0
            ? lesson.letter_ids[0]
            : lesson.letter_ids[
                lesson.letter_ids.length - 1
              ];
      }

      render();

      detail.focus({
        preventScroll: true,
      });

      detail.scrollIntoView({
        behavior: "smooth",
        block: "nearest",
      });
    }

    // ==========================================
    // RENDER LETTER DETAIL
    // ==========================================

    function renderDetail() {
      const letter = letterNow();

      if (!letter) return;

      const names = {
        vowel: "Nguyên âm",
        consonant: "Phụ âm",
        sign: "Dấu đặc biệt",
      };

      // LETTER HEADER
      const header = element(
        "div",
        "alpha-detail-head"
      );

      const glyph = element(
        "div",
        "alpha-glyph"
      );

      glyph.append(
        ru("strong", "", letter.upper),
        ru("span", "", letter.lower)
      );

      const heading = element(
        "div",
        "alpha-detail-title"
      );

      heading.append(
        element(
          "span",
          "alpha-kind",
          names[letter.kind] || "Chữ cái"
        ),

        element(
          "h3",
          "",
          `Tên chữ: ${letter.name_ru}`
        ),

        element(
          "p",
          "",
          `Âm mẫu: ${letter.sound_sample}`
        )
      );

      header.append(glyph, heading);

      // MAIN AUDIO
      const audios = element(
        "div",
        "alpha-audio-actions"
      );

      audios.append(
        soundButton(
          "Tên chữ",
          letter.audio?.name_url
        ),

        soundButton(
          "Âm mẫu",
          letter.audio?.sound_url
        ),

        soundButton(
          "Hướng dẫn",
          letter.audio?.guide_url
        )
      );

      // PRONUNCIATION
      const guide = section(
        "Hướng dẫn phát âm",
        [
          element(
            "p",
            "alpha-sound-caption",
            letter.sound_vi || ""
          ),

          element(
            "p",
            "",
            letter.guide_vi || ""
          ),
        ]
      );

      // BLENDING
      const blends = element(
        "div",
        "alpha-blends"
      );

      (letter.blends || []).forEach((item) => {
        const tile = element(
          "div",
          "alpha-blend-item"
        );

        tile.append(
          ru(
            "strong",
            "",
            item.text
          ),

          soundButton(
            item.text,
            item.audio_url,
            true
          )
        );

        blends.appendChild(tile);
      });

      // EXAMPLE WORDS
      const words = element(
        "div",
        "alpha-words"
      );

      (letter.examples || []).forEach((word) => {
        const tile = element(
          "div",
          "alpha-word"
        );

        const copy = element(
          "div",
          "alpha-word-copy"
        );

        copy.append(
          ru(
            "strong",
            "",
            word.ru
          ),

          element(
            "small",
            "",
            word.vi
          )
        );

        tile.append(
          copy,

          soundButton(
            word.ru,
            word.audio_url,
            true
          )
        );

        words.appendChild(tile);
      });

      // NAVIGATION
      const before = neighbor(-1);
      const after = neighbor(1);

      const actions = element(
        "div",
        "alpha-detail-footer"
      );

      const prev = element(
        "button",
        "alpha-step",
        before?.type === "lesson"
          ? "← Bài trước"
          : "← Chữ trước"
      );

      const next = element(
        "button",
        "alpha-step is-primary",
        after?.type === "lesson"
          ? "Bài tiếp theo →"
          : "Chữ tiếp →"
      );

      prev.type = "button";
      next.type = "button";

      prev.disabled = !before;
      next.disabled = !after;

      prev.addEventListener("click", () => {
        move(-1);
      });

      next.addEventListener("click", () => {
        move(1);
      });

      actions.append(prev, next);

      // DETAIL HERO
      const hero = element(
        "div",
        "alpha-detail-hero"
      );

      hero.append(header, audios);

      // DISTINCT CONTENT SECTIONS
      const sections = element(
        "div",
        "alpha-detail-sections"
      );

      sections.append(
        guide,

        section(
          "Ghép âm và tổ hợp chữ",
          [blends]
        ),

        section(
          "Từ ví dụ",
          [words]
        )
      );

      detail.replaceChildren(
        hero,
        sections,
        actions
      );
    }

    // ==========================================
    // RENDER ALL
    // ==========================================

    function render() {
      stopAudio();
      announce();

      renderTabs();
      renderLessons();
      renderTable();
      renderDetail();
    }

    // ==========================================
    // SETUP MODE TABS
    // ==========================================

    function setupTabs() {
      ["lessons", "table"].forEach((mode) => {
        const tab = $(`alpha-tab-${mode}`);

        tab.addEventListener("click", () => {
          state.mode = mode;

          if (state.overview) {
            render();
          }
        });

        tab.addEventListener("keydown", (event) => {
          const targets = {
            ArrowLeft: "lessons",
            ArrowRight: "table",
            Home: "lessons",
            End: "table",
          };

          if (!Object.prototype.hasOwnProperty.call(
            targets,
            event.key
          )) {
            return;
          }

          event.preventDefault();

          state.mode = targets[event.key];

          if (state.overview) {
            render();
          }

          $(`alpha-tab-${state.mode}`).focus();
        });
      });
    }

    // ==========================================
    // INITIALIZE
    // ==========================================

    async function initialize() {
      setupTabs();

      try {
        const [overview, table] = await Promise.all([
          getJSON(root.dataset.overviewUrl),
          getJSON(root.dataset.tableUrl),
        ]);

        if (
          !Array.isArray(overview.lessons) ||
          overview.lessons.length !== 7 ||
          !Array.isArray(table.letters) ||
          table.letters.length !== 33
        ) {
          throw new Error(
            "API chưa trả đủ 7 bài và 33 chữ cái."
          );
        }

        state.overview = overview;
        state.allLetters = table.letters;

        state.byId = new Map(
          table.letters.map((letter) => [
            letter.id,
            letter,
          ])
        );

        state.lessonId = overview.lessons[0].id;

        state.lessonLetterId =
          overview.lessons[0].letter_ids[0];

        state.tableLetterId =
          table.letters[0].id;

        render();

      } catch (error) {
        console.error(
          "Alphabet load error:",
          error
        );

        announce(
          error.message ||
            "Không thể tải nội dung Alphabet.",
          true
        );

        $("alpha-panel-lessons").hidden = true;
        $("alpha-panel-table").hidden = true;
      }
    }

    initialize();
  }

  // ==========================================
  // DOM READY
  // ==========================================

  if (document.readyState === "loading") {
    document.addEventListener(
      "DOMContentLoaded",
      initAlphabet,
      { once: true }
    );
  } else {
    initAlphabet();
  }
})();
