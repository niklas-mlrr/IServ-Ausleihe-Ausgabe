// web/teacher.js — Lehrkraft-Statusansicht (`/teacher?token=...`) einer
// einzelnen Modus-B-Klasse. Vor der Host-Freischaltung liefert der Server nur
// den Registrierungscode (`type: "registration"`); danach ausschließlich den
// minimierten `teacher_state` dieser einen Klasse (nie Host-`state_snapshot`,
// s. server/state.py::AppState.teacher_snapshot). Der Lehrkraft sind zwei
// Aktionen erlaubt: ein wartender Schüler wird per Wisch-Geste (nach links,
// Touch-first via Pointer Events) als abwesend markiert bzw. per Button
// zurückgesetzt (`pending <-> skipped`); zusätzlich kann sie je abgeschlossenem
// Schüler den unterschriebenen Leihschein einmalig als entgegengenommen
// markieren kann (`slip_collected`, rein informativ; nur bei aktivem
// `done_collected`).
// Alles andere ist reine Anzeige.

(() => {
  const token = new URLSearchParams(location.search).get('token') || '';

  const views = {
    invalid: document.getElementById('view-invalid'),
    register: document.getElementById('view-register'),
    forbidden: document.getElementById('view-forbidden'),
  };
  const classView = document.getElementById('view-class');
  const sortSelect = document.getElementById('student-sort');
  let sortMode = sortSelect.value;
  let latestClassData = null;
  // Lokaler UI-Zustand für den kurzen Zeitraum zwischen Klick und WS-/HTTP-
  // Antwort. Der Server bleibt die Quelle der Wahrheit; der lokale Zustand
  // verhindert nur Doppelklicks und hält die Darstellung während eines
  // laufenden Requests stabil.
  const slipActionState = new Map();

  function show(name) {
    for (const k in views) views[k].classList.toggle('show', k === name);
    classView.classList.toggle('show', name === 'class');
  }

  const connDot = document.getElementById('conn-dot');
  const connText = document.getElementById('conn-text');

  if (!token) {
    show('invalid');
    document.getElementById('conn').style.display = 'none';
    return;
  }

  const STATUS_LABEL = { pending: 'Noch nicht begonnen', active: 'In Ausgabe', done: 'Ausgabe abgeschlossen', skipped: 'Übersprungen', absent: 'Abwesend' };

  function statusText(s) {
    if (s.status === 'active') {
      if (s.slip_printing) return 'Leihschein wird gedruckt';
      const total = s.books_total == null ? '?' : s.books_total;
      return `In Ausgabe — ${s.books_done}/${total} Bücher erfasst`;
    }
    return STATUS_LABEL[s.status] || s.status;
  }

  const COUNT_PILLS = [
    ['done', 'abgeschlossen'], ['active', 'aktiv'], ['pending', 'offen'], ['skipped', 'übersprungen'], ['absent', 'abwesend'],
  ];

  const STATUS_ORDER = {
    active: 0,
    pending: 1,
    absent: 2,
    done: 3,
    skipped: 4,
  };
  const NAME_COLLATOR = new Intl.Collator('de-DE', { sensitivity: 'base', numeric: true });

  function compareNames(a, b) {
    const last = NAME_COLLATOR.compare(a.lastname || '', b.lastname || '');
    if (last !== 0) return last;
    const first = NAME_COLLATOR.compare(a.firstname || '', b.firstname || '');
    if (first !== 0) return first;
    return NAME_COLLATOR.compare(String(a.student_id), String(b.student_id));
  }

  function sortedStudents(students) {
    const copy = [...students];
    copy.sort((a, b) => {
      if (sortMode === 'status') {
        const statusDifference = (STATUS_ORDER[a.status] ?? Number.MAX_SAFE_INTEGER)
          - (STATUS_ORDER[b.status] ?? Number.MAX_SAFE_INTEGER);
        if (statusDifference !== 0) return statusDifference;
      }
      return compareNames(a, b);
    });
    return copy;
  }

  function renderClass(data) {
    latestClassData = data;
    document.getElementById('class-form').textContent = data.class_form || 'Klasse';
    const c = data.counts || { pending: 0, active: 0, done: 0, skipped: 0, absent: 0 };
    const pills = COUNT_PILLS.map(([k, l]) => `<div class="count-pill"><span class="n">${c[k] || 0}</span><span class="l">${l}</span></div>`);
    // Die Sammel-Funktion ist eine Klassenoption. Ohne sie weder den Counter
    // noch die Leihschein-Buttons anzeigen; `done_collected` kommt aus dem
    // minimierten Teacher-Snapshot (s. AppState.teacher_snapshot).
    if (data.done_collected === true) {
      pills.push(`<div class="count-pill count-pill-collected"><span class="n">${data.slip_collected_count || 0}</span><span class="l">Leihschein abgegeben</span></div>`);
    }
    const counts = document.getElementById('counts');
    counts.classList.toggle('has-collected', data.done_collected === true);
    counts.innerHTML = pills.join('');
    // Wisch-Hinweis nur zeigen, solange es überhaupt wartende Schüler gibt —
    // die Geste ist sonst nur durch Zufall auffindbar (s. Wisch-Chevron je Zeile).
    document.getElementById('swipe-tip').classList.toggle('show', (c.pending || 0) > 0);
    sortSelect.value = sortMode;

    const students = sortedStudents(data.students || []);
    document.getElementById('stud-list').innerHTML = students.map(s => {
      if (s.slip_collected) {
        slipActionState.set(s.student_id, 'done');
      } else if (s.status !== 'done' || !s.slip_printed || data.done_collected !== true) {
        // Ein neuer Durchlauf (oder ein anderer nicht mehr passender Status)
        // darf einen alten lokalen UI-Zustand nicht in die neue Zeile tragen.
        slipActionState.delete(s.student_id);
      }
      const currentSlipState = slipActionState.get(s.student_id);
      const name = `${escapeHtml(s.lastname)}, ${escapeHtml(s.firstname)}`;
      let extra = '';
      if ((s.status === 'skipped' || s.status === 'absent') && !s.auto_skipped) {
        extra = `<button class="act" data-undo="${s.student_id}">Nicht abwesend</button>`;
      } else if (s.status === 'done' && data.done_collected === true && s.slip_printed) {
        // Abwesender, dessen Bücher ein Helfer eingescant hat: die Lehrkraft
        // nimmt neben dem Leihschein auch den physischen Bücherstapel entgegen.
        const slipLabel = s.helper_scanned ? 'Leihschein & Bücherstapel entgegengenommen' : 'Leihschein entgegengenommen';
        const slipDone = s.slip_collected || currentSlipState === 'done';
        const slipPending = currentSlipState === 'pending';
        extra = `<button type="button" class="act slip-action${slipDone ? ' done' : ''}${slipPending ? ' pending' : ''}" data-slip="${s.student_id}"${slipDone || slipPending ? ' disabled' : ''}>${slipLabel}${slipDone ? ' ✓' : ''}</button>`;
      }
      const swipeable = s.status === 'pending';
      // Dezenter Wisch-Hinweis (Chevrons) am rechten Rand wartender Zeilen —
      // die Geste ist sonst nur durch Zufall auffindbar.
      const swipeHint = swipeable ? '<span class="swipe-hint" aria-hidden="true">‹&#8202;‹</span>' : '';
      const actions = extra ? `<div class="stud-actions">${extra}</div>` : '';
      const row = `<div class="stud-row${swipeable ? ' swipeable' : ''}" data-row-id="${s.student_id}" data-status="${s.status}">
        <div class="stud-row-head">
          <span class="dot ${s.status}"></span>
          <div class="stud-name"><div class="n">${name}</div></div>
          ${swipeHint}
        </div>
        <div class="stud-row-status">
          <div class="stud-status"><div class="s">${escapeHtml(statusText(s))}</div></div>
          ${actions}
        </div>
      </div>`;
      // Wartende Schüler bekommen den Wisch-Container mit der (verdeckten)
      // roten „Abwesend"-Fläche dahinter; alle anderen Status ohne Wisch-Geste.
      return swipeable
        ? `<div class="stud-swipe"><div class="stud-swipe-bg">Als abwesend markieren</div>${row}</div>`
        : row;
    }).join('') || '<p class="muted">Keine Schüler in dieser Klasse.</p>';

    wireSwipeRows();
  }

  // ---- Wisch-Geste „nach links = abwesend" (Touch-first, per Pointer Events
  // auch mit Maus nutzbar) — ersetzt den früheren „Als abwesend"-Button. Die
  // Auslöseschwelle dient zugleich als Bestätigung: ein kurzes Antippen/
  // Verrutschen löst nichts aus, erst ein bewusst weiter gezogener Wisch
  // committet. Schwelle + maximaler Zug sind relativ zur tatsächlichen
  // Zeilenbreite (nicht fest in px) — die rote „Als abwesend markieren"-Fläche
  // muss beim Ziehen bis zur Auslöseschwelle vollständig lesbar sein, auf
  // schmalen wie breiten Geräten.
  const SWIPE_THRESHOLD_RATIO = 0.55; // ab hier löst der Wisch aus
  const SWIPE_MAX_RATIO = 0.9; // weiter zieht der Finger die Zeile nicht raus

  function wireSwipeRows() {
    document.querySelectorAll('.stud-row.swipeable').forEach(row => {
      const studentId = parseInt(row.dataset.rowId, 10);
      let startX = 0, dx = 0, dragging = false, moved = false, threshold = 0, maxDrag = 0;

      function resetRow() {
        row.classList.remove('swiping');
        row.style.transform = '';
      }

      row.addEventListener('pointerdown', (e) => {
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        startX = e.clientX; dx = 0; dragging = true; moved = false;
        const width = row.getBoundingClientRect().width;
        threshold = width * SWIPE_THRESHOLD_RATIO;
        maxDrag = width * SWIPE_MAX_RATIO;
        row.setPointerCapture(e.pointerId);
      });
      row.addEventListener('pointermove', (e) => {
        if (!dragging) return;
        dx = e.clientX - startX;
        if (!moved && Math.abs(dx) > 6) moved = true;
        if (!moved) return;
        const clamped = Math.max(-maxDrag, Math.min(dx, 0));
        row.classList.add('swiping');
        row.style.transform = `translateX(${clamped}px)`;
      });
      function finish() {
        if (!dragging) return;
        dragging = false;
        if (moved && dx < -threshold) {
          // Committen: Zeile sichtbar rausschieben, dann Server-Aktion feuern
          // (der nächste teacher_state-Push rendert die Liste ohnehin neu).
          row.classList.remove('swiping');
          row.style.transform = 'translateX(-120%)';
          row.style.opacity = '0';
          setTimeout(() => postAction('/api/teacher/skip', studentId), 140);
        } else {
          resetRow();
        }
      }
      row.addEventListener('pointerup', finish);
      row.addEventListener('pointercancel', finish);
    });
  }

  // Bestätigungs-Modal — nur noch für die Rücknahme (Nicht abwesend); das
  // Markieren als abwesend läuft über die Wisch-Geste (Ziehweite = Bestätigung).
  const modalBg = document.getElementById('modal-bg');
  const modalText = document.getElementById('modal-text');
  const modalCancel = document.getElementById('modal-cancel');
  const modalConfirm = document.getElementById('modal-confirm');
  let pendingConfirm = null;

  function askConfirm(text, onConfirm) {
    modalText.textContent = text;
    pendingConfirm = onConfirm;
    modalBg.classList.add('show');
  }
  function closeModal() { modalBg.classList.remove('show'); pendingConfirm = null; }
  modalCancel.addEventListener('click', closeModal);
  modalBg.addEventListener('click', (e) => { if (e.target === modalBg) closeModal(); });
  modalConfirm.addEventListener('click', () => {
    const fn = pendingConfirm;
    closeModal();
    if (fn) fn();
  });

  async function postAction(path, studentId, extra) {
    try {
      const response = await fetch(path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, student_id: studentId, ...extra }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return true;
    } catch (_e) { /* nächster teacher_state-Push korrigiert die Anzeige ohnehin */ }
    return false;
  }

  async function collectSlip(button, studentId) {
    if (slipActionState.has(studentId)) return;
    slipActionState.set(studentId, 'pending');
    button.disabled = true;
    button.classList.add('pending');

    const ok = await postAction('/api/teacher/slip-collected', studentId, { collected: true });
    const currentButton = document.querySelector(`button[data-slip="${studentId}"]`) || button;
    currentButton.classList.remove('pending');
    if (ok) {
      slipActionState.set(studentId, 'done');
      currentButton.disabled = true;
      currentButton.classList.add('done');
      if (!currentButton.textContent.trim().endsWith('✓')) {
        currentButton.textContent = `${currentButton.textContent.trim()} ✓`;
      }
    } else {
      slipActionState.delete(studentId);
      currentButton.disabled = false;
    }
  }

  document.getElementById('stud-list').addEventListener('click', (e) => {
    const undoBtn = e.target.closest('[data-undo]');
    if (undoBtn) {
      const id = parseInt(undoBtn.dataset.undo, 10);
      askConfirm('Diesen Schüler als nicht abwesend markieren?', () => postAction('/api/teacher/undo-skip', id));
      return;
    }
    const slipBtn = e.target.closest('[data-slip]');
    if (slipBtn) {
      const id = parseInt(slipBtn.dataset.slip, 10);
      collectSlip(slipBtn, id);
    }
  });

  sortSelect.addEventListener('change', () => {
    sortMode = sortSelect.value;
    if (latestClassData) renderClass(latestClassData);
  });

  function handleServerMessage(msg) {
    if (msg.type === 'registration') {
      document.getElementById('reg-code').textContent = msg.code;
      show('register');
    } else if (msg.type === 'teacher_state') {
      renderClass(msg);
      show('class');
    } else if (msg.type === 'forbidden') {
      show('forbidden');
    }
  }

  connectWebSocket(() => `wss://${location.host}/ws/teacher?token=${encodeURIComponent(token)}`, {
    onOpen: () => { connDot.className = 'dot2 ok'; connText.textContent = 'verbunden'; },
    onClose: (e, reconnect) => {
      connDot.className = 'dot2'; connText.textContent = 'getrennt — neu verbinden…';
      // Server-seitig entwertete Session (4009) meldet vorher `forbidden`;
      // ein Reconnect-Versuch würde ohnehin wieder mit demselben Grund
      // abgewiesen — kein endloses Reconnect-Spam gegen eine tote Session.
      if (e && e.code === 4009) return;
      reconnect();
    },
    onError: () => { connDot.className = 'dot2'; connText.textContent = 'Verbindungsfehler'; },
    onMessage: (e) => {
      let msg; try { msg = JSON.parse(e.data); } catch (_err) { return; }
      handleServerMessage(msg);
    },
  });
})();
