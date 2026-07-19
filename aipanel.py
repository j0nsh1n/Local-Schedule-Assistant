"""Daily Scheduler — the AI assistant panel.

Copyright (C) 2026 Jonathan Shin
GPL-3.0-or-later — see LICENSE. Split out of app.py in v4.2.0;
app.py remains the entry point.
"""

import json
from typing import Optional, List, Dict

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QMessageBox, QComboBox,
)
from PySide6.QtCore import (
    Qt, QTimer,
)

import theme
import ai
from core import (
    DEFAULT_MODEL,
    DEFAULT_SETTINGS,
    activity_type_prompt_block,
    allday_cal_events,
    fmt_time,
    timed_cal_events,
)
from theme import _rgba
from ai import (
    AI_TOOLS,
    MAX_TOOL_ROUNDS,
    OllamaCheckThread,
    OllamaPullThread,
    OllamaThread,
    extract_tool_calls,
    looks_like_tool_text,
    memory_warning_for,
    model_guidance,
    model_is_installed,
    model_profile,
    model_when_text,
    show_model_guide,
    start_ollama,
    stop_ollama,
    unload_ollama_model,
)


# ══════════════════════════════════════════════════════════════════════════
#  AI ASSISTANT PANEL
# ══════════════════════════════════════════════════════════════════════════
class AIPanel(QWidget):
    def __init__(self, get_ctx_fn, parent=None):
        super().__init__(parent)
        self.get_ctx    = get_ctx_fn
        self.model       = DEFAULT_MODEL
        self.temperature = DEFAULT_SETTINGS["temperature"]
        self.num_ctx     = DEFAULT_SETTINGS["num_ctx"]
        self.on_model_edited = None       # set by MainWindow to persist model changes
        self.mode       = "chat"
        # Mode frozen for the in-flight turn so tab switches mid-stream can't
        # misroute tokens/tools into another transcript (or IndexError).
        self._turn_mode: Optional[str] = None
        # Restore last transcript (v3.8.0) so an OOM/kill doesn't eat the chat.
        self.history: Dict[str, List[Dict]] = ai.load_chat_histories()
        self._thread: Optional[OllamaThread] = None
        self._check_thread: Optional[OllamaCheckThread] = None  # status poll (v3.7.1)
        # Installed-model cache, refreshed by the poll thread (v4.1.0). Seeded with
        # ONE bounded call here (the v2.5.5-vetted HTTP path) so the picker starts
        # populated; after this, the GUI thread never blocks on HTTP for it.
        self._installed_models: List[str] = ai.list_ollama_models()
        self._cur_text  = ""
        self._ollama_up = False
        self._mem_warned: set = set()     # models we already soft-warned this session
        self.execute_tool = None          # set by MainWindow: fn(name, args) -> str
        self.on_turn_start = None         # set by MainWindow: snapshot schedule for undo
        self.on_turn_end = None           # set by MainWindow: unlock Undo, drop no-op snapshots
        self.on_undo = None               # set by MainWindow: restore the last snapshot
        self._loop_msgs: List[Dict] = []  # running conversation for the tool loop
        self._depth = 0                   # tool-round counter (loop guard)
        self._user_stopped = False        # Stop must not apply tools after cancel

        # Preferred width when the body splitter shows this panel; user can drag.
        self._panel_w = 340
        self.setObjectName("aiPanel")
        self.setMinimumWidth(220)
        self.setMaximumWidth(560)
        self.setStyleSheet(
            f"#aiPanel {{ background: {theme.C_SURFACE.name()}; color: {theme.C_TEXT.name()}; "
            f"border-left: 1px solid {theme.C_BORDER.name()}; }}")

        lay = QVBoxLayout(self); lay.setSpacing(0); lay.setContentsMargins(0,0,0,0)

        # Header
        hdr = QWidget()
        hdr.setStyleSheet(f"border-bottom: 1px solid {theme.C_BORDER.name()};")
        hl  = QVBoxLayout(hdr); hl.setContentsMargins(12,10,12,8); hl.setSpacing(6)

        tr = QHBoxLayout()
        t  = QLabel("Assistant"); t.setStyleSheet("font-size: 13px; font-weight: bold;")
        tr.addWidget(t)
        self._dot = QLabel("●"); self._dot.setStyleSheet(f"color: {theme.C_MUTED.name()};")
        self._stxt = QLabel("Checking…"); self._stxt.setStyleSheet(f"color: {theme.C_MUTED.name()}; font-size: 11px;")
        tr.addWidget(self._dot); tr.addWidget(self._stxt); tr.addStretch()

        self._unload_btn = QPushButton("⏏")
        self._unload_btn.setToolTip("Unload model from memory (keeps Ollama running)")
        self._unload_btn.setFixedSize(26, 24)
        self._unload_btn.setCursor(Qt.PointingHandCursor)
        self._unload_btn.setEnabled(False)
        self._unload_btn.setStyleSheet(f"""
            QPushButton {{ background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
            color: {theme.C_MUTED.name()}; border-radius: {theme.RAD}px; font-size: 12px; }}
            QPushButton:hover {{ background: {_rgba(theme.C_ACCENT, .18)}; border-color: {theme.C_ACCENT.name()}; color: {theme.C_ACCENT.name()}; }}
            QPushButton:disabled {{ color: {theme.C_BORDER2.name()}; border-color: {theme.C_BORDER.name()}; }}
        """)
        self._unload_btn.clicked.connect(self._unload_model)
        tr.addWidget(self._unload_btn)

        self._power_btn = QPushButton("▶")
        self._power_btn.setFixedSize(26, 24)
        self._power_btn.setCursor(Qt.PointingHandCursor)
        self._power_btn.clicked.connect(self._toggle_power)
        tr.addWidget(self._power_btn)
        self._set_power_state(False)
        hl.addLayout(tr)

        mr = QHBoxLayout()
        mr.addWidget(QLabel("Model:", styleSheet=f"color:{theme.C_MUTED.name()}; font-size:10px;"))
        self._model_in = QComboBox(); self._model_in.setEditable(True)
        self._model_in.setFixedHeight(24)
        self._model_in.addItems(self._model_choices())
        self._model_in.setCurrentText(self.model)
        self._model_in.setStyleSheet(f"""
            QComboBox {{ background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
            color: {theme.C_TEXT.name()}; padding: 2px 6px; border-radius: {theme.RAD}px; font-size: 11px; }}
            QComboBox QAbstractItemView {{ background: {theme.C_SURFACE.name()}; color: {theme.C_TEXT.name()};
            selection-background-color: {theme.C_SURF2.name()}; }}
        """)
        self._model_in.currentTextChanged.connect(self._on_model_changed)
        mr.addWidget(self._model_in, 1)
        self._model_info_btn = QPushButton("?")
        self._model_info_btn.setFixedSize(22, 24)
        self._model_info_btn.setCursor(Qt.PointingHandCursor)
        self._model_info_btn.setToolTip("When to use each model")
        self._model_info_btn.setStyleSheet(f"""
            QPushButton {{ background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
            color: {theme.C_MUTED.name()}; border-radius: {theme.RAD}px; font-size: 11px; font-weight: bold; }}
            QPushButton:hover {{ background: {_rgba(theme.C_ACCENT, .18)}; border-color: {theme.C_ACCENT.name()};
            color: {theme.C_ACCENT.name()}; }}
        """)
        self._model_info_btn.clicked.connect(self._show_model_guide)
        mr.addWidget(self._model_info_btn)
        self._pull_btn = QPushButton("⬇")
        self._pull_btn.setFixedSize(22, 24)
        self._pull_btn.setCursor(Qt.PointingHandCursor)
        self._pull_btn.setToolTip("Download this model with Ollama")
        self._pull_btn.setStyleSheet(f"""
            QPushButton {{ background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
            color: {theme.C_MUTED.name()}; border-radius: {theme.RAD}px; font-size: 11px; }}
            QPushButton:hover {{ background: {_rgba(theme.C_OK, .18)}; border-color: {theme.C_OK.name()};
            color: {theme.C_OK_TXT.name()}; }}
            QPushButton:disabled {{ color: {theme.C_BORDER2.name()}; }}
        """)
        self._pull_btn.clicked.connect(self._pull_selected_model)
        mr.addWidget(self._pull_btn)
        hl.addLayout(mr)
        self._model_hint = QLabel()
        self._model_hint.setWordWrap(True)
        self._model_hint.setStyleSheet(
            f"color:{theme.C_MUTED.name()}; font-size:10px; padding:0 2px;")
        hl.addWidget(self._model_hint)
        self._pull_prog = QLabel()
        self._pull_prog.setWordWrap(True)
        self._pull_prog.setStyleSheet(
            f"color:{theme.C_ACCENT.name()}; font-size:10px; padding:0 2px 2px 2px;")
        self._pull_prog.hide()
        hl.addWidget(self._pull_prog)
        self._pull_thread: Optional[OllamaPullThread] = None
        self._refresh_model_hint()
        lay.addWidget(hdr)

        # Tabs
        tabs = QWidget(); tabs.setStyleSheet(f"border-bottom: 1px solid {theme.C_BORDER.name()};")
        tl   = QHBoxLayout(tabs); tl.setContentsMargins(0,0,0,0); tl.setSpacing(0)
        self._tabs = {}
        for mode, lbl in [("chat","Chat"), ("plan","Plan"), ("suggest","Analyze")]:
            b = QPushButton(lbl); b.setCheckable(True); b.setChecked(mode=="chat")
            b.setStyleSheet(self._tab_style(mode == "chat"))
            b.clicked.connect(lambda _, m=mode: self._set_mode(m))
            self._tabs[mode] = b; tl.addWidget(b)
        tl.addStretch()
        # Undo the assistant's last schedule change (enabled once it makes one).
        self._undo_btn = QPushButton("↶ Undo")
        self._undo_btn.setEnabled(False)
        self._undo_btn.setCursor(Qt.PointingHandCursor)
        self._undo_btn.setToolTip(
            "Undo the assistant's last change (Ctrl+Z undoes your own edits)")
        self._undo_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.C_MUTED.name()}; border: none;"
            f" padding: 4px 12px; font-size: 11px; }}"
            f"QPushButton:hover:enabled {{ color: {theme.C_TEXT.name()}; }}"
            f"QPushButton:disabled {{ color: {theme.C_BORDER2.name()}; }}")
        self._undo_btn.clicked.connect(self._do_undo)
        tl.addWidget(self._undo_btn)
        lay.addWidget(tabs)

        # Messages
        self._msgs_view = QTextEdit()
        self._msgs_view.setReadOnly(True)
        self._msgs_view.setStyleSheet(f"""
            QTextEdit {{ background: {theme.C_BG.name()}; border: none;
            color: {theme.C_TEXT.name()}; font-size: 12px; padding: 8px; }}
        """)
        lay.addWidget(self._msgs_view, 1)

        self._thinking = QLabel("⟳  Thinking…")
        self._thinking.setStyleSheet(f"color:{theme.C_MUTED.name()}; font-size:11px; padding:4px 12px;")
        self._thinking.hide()
        lay.addWidget(self._thinking)

        # Input
        inp = QWidget(); il = QVBoxLayout(inp); il.setContentsMargins(8,6,8,8); il.setSpacing(4)
        self._inp = QTextEdit()
        self._inp.setMaximumHeight(72)
        self._inp.setPlaceholderText("Ask me anything about your day…")
        self._inp.setStyleSheet(f"""
            QTextEdit {{ background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
            color: {theme.C_TEXT.name()}; padding: 6px; border-radius: {theme.RAD}px; font-size: 12px; }}
            QTextEdit:focus {{ border-color: {theme.C_ACCENT.name()}; }}
        """)
        il.addWidget(self._inp)

        br = QHBoxLayout()
        self._stop_btn = QPushButton("■ Stop")
        self._stop_btn.setStyleSheet(f"background:{_rgba(theme.C_ERR, .2)}; color:{theme.C_ERR_TXT.name()}; border-radius:{theme.RAD}px; padding:3px 10px;")
        self._stop_btn.hide()
        self._stop_btn.clicked.connect(self._stop)

        send = QPushButton("Send ↑")
        send.setStyleSheet(f"""
            QPushButton {{ background:{theme.C_ACCENT.name()}; color:{theme.C_ON_ACCENT.name()}; border-radius:{theme.RAD}px;
            font-weight:bold; padding:5px 14px; border:none; }}
            QPushButton:hover {{ background:{theme.C_ACCENT2.name()}; }}
        """)
        send.clicked.connect(self._send)
        br.addWidget(self._stop_btn); br.addStretch(); br.addWidget(send)
        il.addLayout(br)
        lay.addWidget(inp)

        self._render()
        self._poll_ollama()
        self._timer = QTimer(self); self._timer.timeout.connect(self._poll_ollama); self._timer.start(30_000)

    def _model_choices(self):
        seen, out = set(), []
        # Installed first, then curated recommendations not yet present.
        # Reads the poll-thread cache — never blocks the GUI thread on HTTP.
        for m in self._installed_models + ai.RECOMMENDED_MODELS:
            if m and m not in seen:
                seen.add(m); out.append(m)
        return out

    def _refresh_model_list(self):
        """Rebuild the picker after a pull / Ollama reconnect."""
        cur = self.model
        self._model_in.blockSignals(True)
        self._model_in.clear()
        self._model_in.addItems(self._model_choices())
        self._model_in.setCurrentText(cur)
        self._model_in.blockSignals(False)
        self._refresh_model_hint()

    def _on_model_changed(self, text):
        self.model = text.strip() or DEFAULT_MODEL
        self._refresh_model_hint()
        if callable(self.on_model_edited):
            self.on_model_edited(self.model)

    def _refresh_model_hint(self):
        """Show badge / install state + when-to-use tooltip; enable ⬇ if missing.
        Uses the cached install list (poll thread) — no HTTP on the GUI thread."""
        have = (model_is_installed(self.model, self._installed_models)
                if self._ollama_up else False)
        p = model_profile(self.model)
        if not self._ollama_up:
            status = "Ollama not running"
        elif have:
            status = "Installed"
        else:
            status = "Not installed — click ⬇ to download"
        if p:
            self._model_hint.setText(f"{p['badge']}  ·  {p['vram']} VRAM  ·  {status}")
        else:
            self._model_hint.setText(f"Custom model  ·  {status}")
        tip = model_when_text(self.model)
        if not have and self._ollama_up:
            tip += f"\n\nNot installed. Pull with:  ollama pull {self.model}"
        self._model_in.setToolTip(tip)
        self._model_hint.setToolTip(tip)
        pulling = self._pull_thread is not None and self._pull_thread.isRunning()
        self._pull_btn.setEnabled(self._ollama_up and bool(self.model) and not have and not pulling)
        self._pull_btn.setToolTip(
            "Download this model with Ollama" if not have else "Already installed")

    def _pull_selected_model(self):
        tag = (self._model_in.currentText() or self.model or "").strip()
        if not tag:
            return
        if not self._ollama_up:
            QMessageBox.information(self, "Ollama", "Start Ollama (▶) before pulling a model.")
            return
        if model_is_installed(tag, self._installed_models):
            self._refresh_model_hint(); return
        if self._pull_thread is not None and self._pull_thread.isRunning():
            return
        self._pull_prog.setText(f"Pulling {tag}…"); self._pull_prog.show()
        self._pull_btn.setEnabled(False)
        t = OllamaPullThread(tag)
        t.progress.connect(self._on_pull_progress)
        t.finished_ok.connect(self._on_pull_ok)
        t.failed.connect(self._on_pull_fail)
        t.finished.connect(t.deleteLater)
        t.finished.connect(lambda: setattr(self, "_pull_thread", None))
        self._pull_thread = t
        t.start()

    def _on_pull_progress(self, msg: str):
        self._pull_prog.setText(msg); self._pull_prog.show()

    def _on_pull_ok(self, tag: str):
        self._pull_prog.setText(f"✓  {tag} ready"); self._pull_prog.show()
        # One bounded fetch right after a successful pull (server is up + idle);
        # the 30-s poll keeps it fresh afterwards.
        self._installed_models = ai.list_ollama_models()
        self._refresh_model_list()
        QTimer.singleShot(4000, lambda: self._pull_prog.hide()
                          if not (self._pull_thread and self._pull_thread.isRunning()) else None)

    def _on_pull_fail(self, msg: str):
        self._pull_prog.setText(f"Pull failed: {msg}"); self._pull_prog.show()
        self._refresh_model_hint()
        QMessageBox.warning(self, "Model pull", msg)

    def _show_model_guide(self):
        show_model_guide(self)

    def apply_settings(self, s):
        """Apply persisted AI settings — on launch and after the Settings dialog."""
        self._settings   = dict(s or {})
        self.model       = s.get("model", DEFAULT_MODEL)
        self.temperature = float(s.get("temperature", 0.3))
        self.num_ctx     = int(s.get("num_ctx", 16384))
        self._model_in.blockSignals(True)
        self._model_in.setCurrentText(self.model)
        self._model_in.blockSignals(False)
        self._refresh_model_hint()

    def _tab_style(self, active):
        return (f"QPushButton {{ background:transparent; border:none; border-bottom:2px solid {theme.C_ACCENT.name()};"
                f"color:{theme.C_ACCENT.name()}; padding:8px 4px; font-size:12px; }}" if active else
                f"QPushButton {{ background:transparent; border:none; border-bottom:2px solid transparent;"
                f"color:{theme.C_MUTED.name()}; padding:8px 4px; font-size:12px; }}"
                f"QPushButton:hover {{ color:{theme.C_TEXT.name()}; }}")

    def _poll_ollama(self):
        """Kick a status check. Hold the QThread ref until finished — dropping it
        left the only Python reference dying during main-thread GC while the C++
        thread was still mid-request (segfault in crash.log 2026-07-07). Same
        pattern as MainWindow._check_for_update / _update_thread."""
        if self._check_thread is not None and self._check_thread.isRunning():
            return
        t = OllamaCheckThread()                       # unparented; ref held below
        t.result.connect(self._on_ollama)
        t.models.connect(self._on_models)
        t.finished.connect(t.deleteLater)
        t.finished.connect(lambda: setattr(self, "_check_thread", None))
        self._check_thread = t
        t.start()

    def _on_models(self, tags: list):
        """Cache the installed-model list from the poll thread. UI reads ONLY this
        cache — calling ai.list_ollama_models() (blocking HTTP) on the GUI thread froze
        the app up to 2 s per 30-s poll / per keystroke while Ollama loaded a model."""
        changed = tags != self._installed_models
        self._installed_models = list(tags)
        if changed:
            self._refresh_model_list()

    def _on_ollama(self, ok: bool):
        was = self._ollama_up
        self._ollama_up = ok
        self._dot.setStyleSheet(f"color: {(theme.C_OK if ok else theme.C_ERR).name()};")
        if not self._stxt.text().startswith("Starting"):
            self._stxt.setText("Connected" if ok else "Not running")
        self._set_power_state(ok)
        self._unload_btn.setEnabled(ok)
        # Refresh install-state labels when the up/down state changes (cheap: label
        # text only — the models cache arrives via the poll thread's models signal).
        if ok != was:
            self._refresh_model_hint()

    def _set_power_state(self, up: bool):
        """Power button is a toggle: ▶ Start when down, ⏻ Stop when up."""
        if up:
            self._power_btn.setText("⏻")
            self._power_btn.setToolTip("Stop Ollama (shuts down the local LLM server)")
            self._power_btn.setStyleSheet(f"""
                QPushButton {{ background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
                color: {theme.C_MUTED.name()}; border-radius: {theme.RAD}px; font-size: 13px; }}
                QPushButton:hover {{ background: {_rgba(theme.C_ERR, .18)}; border-color: {theme.C_ERR.name()}; color: {theme.C_ERR_TXT.name()}; }}
            """)
        else:
            self._power_btn.setText("▶")
            self._power_btn.setToolTip("Start Ollama (launches the local LLM server)")
            self._power_btn.setStyleSheet(f"""
                QPushButton {{ background: {theme.C_SURF2.name()}; border: 1px solid {theme.C_BORDER.name()};
                color: {theme.C_MUTED.name()}; border-radius: {theme.RAD}px; font-size: 12px; }}
                QPushButton:hover {{ background: {_rgba(theme.C_OK, .18)}; border-color: {theme.C_OK.name()}; color: {theme.C_OK_TXT.name()}; }}
            """)

    def _toggle_power(self):
        if self._ollama_up:
            self._shutdown_ollama()
        else:
            self._start_ollama()

    def _start_ollama(self):
        ok, msg = start_ollama(getattr(self, "_settings", None))
        if not ok:
            QMessageBox.information(self, "Ollama", msg)
            return
        self._stxt.setText("Starting…")
        self._dot.setStyleSheet(f"color: {theme.C_WARN.name()};")  # amber while booting
        # Server takes a moment to bind the port — poll a few times as it comes up
        for delay in (700, 1500, 2500, 4000, 6000, 9000):
            QTimer.singleShot(delay, self._poll_ollama)

    def _unload_model(self):
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        ok, msg = unload_ollama_model(self.model)
        self._poll_ollama()  # server stays up, so dot should remain green
        QMessageBox.information(self, "Ollama", msg)

    def _shutdown_ollama(self):
        confirm = QMessageBox.question(
            self, "Stop Ollama",
            "Stop the local Ollama server?\n\n"
            "The AI assistant won't respond until you start it again "
            "(the ▶ button, the Ollama app, or 'ollama serve').",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        if self._thread and self._thread.isRunning():
            self._thread.stop()
        ok, msg = stop_ollama()
        self._poll_ollama()  # refresh status dot immediately
        if not ok:
            QMessageBox.information(self, "Ollama", msg)

    def _set_mode(self, mode):
        self.mode = mode
        for m, b in self._tabs.items():
            b.setChecked(m == mode)
            b.setStyleSheet(self._tab_style(m == mode))
        hints = {"plan": "Describe what you need to accomplish today…",
                 "suggest": "Ask for specific suggestions…"}
        self._inp.setPlaceholderText(hints.get(mode, "Ask me anything about your day…"))
        self._render()
        if mode == "suggest" and not self.history["suggest"]:
            QTimer.singleShot(200, lambda: self._generate(None))

    def _render(self):
        msgs = self.history[self.mode]
        if not msgs:
            hints = {
                "plan":    "Tell me what you need to accomplish today and I'll build a schedule.",
                "suggest": "Analyzing your schedule…",
            }
            h = hints.get(self.mode, "Ask about your day, tasks, or how to be more productive.")
            self._msgs_view.setHtml(
                f'<p style="color:{theme.C_MUTED.name()}; font-style:italic; text-align:center; margin-top:20px;">{h}</p>')
            return

        html = ""
        for msg in msgs:
            c = msg["content"].replace("&","&amp;").replace("<","&lt;").replace("\n","<br>")
            r = msg["role"]
            if r == "user":
                html += (f'<div style="text-align:right;margin:4px 0;">'
                         f'<span style="background:{theme.C_ACCENT.name()};color:{theme.C_ON_ACCENT.name()};padding:6px 10px;'
                         f'border-radius:{theme.RAD}px;display:inline-block;max-width:88%;font-size:12px;">'
                         f'{c}</span></div>')
            elif r == "assistant":
                html += (f'<div style="margin:4px 0;">'
                         f'<span style="background:{theme.C_SURF2.name()};border:1px solid {theme.C_BORDER.name()};'
                         f'color:{theme.C_TEXT.name()};padding:8px 10px;border-radius:{theme.RAD}px;'
                         f'display:inline-block;font-size:12px;white-space:pre-wrap;">{c}</span></div>')
            elif r == "tool_note":
                html += (f'<div style="margin:4px 0;background:{_rgba(theme.C_OK, .08)};'
                         f'border:1px solid {_rgba(theme.C_OK, .25)};color:{theme.C_OK_TXT.name()};padding:6px 8px;'
                         f'border-radius:{theme.RAD}px;font-size:11px;">{c}</div>')
            elif r == "error":
                html += (f'<div style="margin:4px 0;background:{_rgba(theme.C_ERR, .1)};'
                         f'border:1px solid {_rgba(theme.C_ERR, .3)};color:{theme.C_ERR_TXT.name()};padding:8px;'
                         f'border-radius:{theme.RAD}px;font-size:12px;">{c}</div>')
        self._msgs_view.setHtml(html)
        self._msgs_view.verticalScrollBar().setValue(self._msgs_view.verticalScrollBar().maximum())

    def _persist_chat(self, *, force: bool = False):
        """Best-effort write of the transcript (throttled mid-stream)."""
        ai.save_chat_histories(self.history, force=force)

    def _maybe_memory_warning(self):
        """Soft, once-per-model-per-session note before a generate. Never blocks."""
        if self.model in self._mem_warned:
            return
        warn = memory_warning_for(self.model)
        if not warn:
            return
        self._mem_warned.add(self.model)
        self.history[self.mode].append({"role": "error", "content": warn})
        self._persist_chat(force=True)

    def _sys_prompt(self):
        ctx = self.get_ctx()
        p = (
            "You are the scheduling assistant built into Daily Scheduler, a desktop "
            "day-planner. You help the user (a high-school student) plan study, projects, "
            "exercise, downtime, and social time, and you edit their calendar directly "
            "with tools.\n\n"
            "RIGHT NOW\n"
            f"It is {ctx.get('weekday', '')}, {ctx.get('today', '')} at "
            f"{fmt_time(ctx.get('now_min', 0))} (24-hour clock). "
            f"The day on screen is {ctx.get('view_date', '')}"
            f"{' — that is today.' if ctx.get('viewing_today') else ' (not today).'}\n"
            "Use this real date and time to judge urgency and deadlines, to resolve "
            "'today' / 'tomorrow' / weekday names, and — when scheduling on today — to "
            "avoid placing anything earlier than the current time.\n\n"
            "THE DAY\n"
            "Anything the user asks for without a date goes on the day on screen. For "
            "another day the user names it (e.g. \"Thursday\", \"tomorrow\", \"6/14\") — pass "
            "that word STRAIGHT into the tool's date argument; the app resolves the exact "
            "date. Do NOT convert a weekday or 'tomorrow' into a calendar date yourself — your "
            "date arithmetic is unreliable, and the app does it correctly. So for \"copy to "
            "Thursday\" pass to_date=\"Thursday\", NOT a date you counted out. "
            "Omit the date for the day on screen.\n\n"
            + activity_type_prompt_block() + "\n\n"
            "SCHEDULE (day on screen)\n"
            "Google Calendar (READ-ONLY — you cannot move or delete these). Timed events are "
            "FIXED obstacles — schedule around them. All-day events (holidays, due dates, "
            "spirit week) do not block free time but are real deadlines/context you must "
            "respect when planning:\n")
        cal = ctx.get("cal_events", [])
        ads = allday_cal_events(cal)
        tms = sorted(timed_cal_events(cal), key=lambda e: e["startMin"])
        if ads:
            p += "All-day:\n"
            p += "".join(f"  - {e['title']} (all day)\n" for e in ads)
        if tms:
            p += "Timed (fixed):\n"
            p += "".join(f"  - {e['title']}: {fmt_time(e['startMin'])}–{fmt_time(e['endMin'])}\n"
                         for e in tms)
        if not ads and not tms:
            p += "  (none)\n"
        p += "Your editable blocks:\n"
        acts = ctx.get("activities", [])
        p += "".join(f"  - \"{a['title']}\" [{a['type']}]: "
                     f"{fmt_time(a['startMin'])}–{fmt_time(a['endMin'])}\n"
                     for a in acts) or "  (none yet)\n"
        week = ctx.get("week_ahead", "")
        if week:
            p += ("\nTHE WEEK AHEAD (read-only Google Calendar, next 7 days from today — "
                  "use for deadlines and planning ahead; each is a FIXED obstacle on its "
                  "day). Pass the date the user names when scheduling on one of these days:\n"
                  + week + "\n")
        p += (
            "\nTOOLS — pick the ONE that fits; never chain small calls for a bulk job:\n"
            "  add_block      – add one block\n"
            "  add_recurring  – add the same block to many days (weekdays/weekends/daily or a dates list)\n"
            "  move_block     – change a block's time, length, day, or title (match by title and/or 'at')\n"
            "  delete_block   – remove a block by title and/or 'at' (its start time)\n"
            "  clear_range    – delete blocks within a time window (\"clear my afternoon\")\n"
            "  clear_day      – wipe a whole day\n"
            "  shift_blocks   – move EVERY block on a day by an offset (\"push everything 2h later\")\n"
            "  copy_day       – duplicate all blocks from one day to another (\"copy today to 6/14\")\n"
            "  split_block    – split one block into focus chunks with breaks (pomodoro)\n"
            "  schedule_tasks – PLAN: fit UNORDERED tasks into free time safely (never deletes)\n"
            "  plan_day       – BUILD a full day: ORDERED tasks + fixed anchors (meals/workout) "
            "+ chunking, laid out around meetings (rebuilds the day). Best for 'X then Y, lunch "
            "at 13:00, workout at 16:00, 30-min chunks with breaks'.\n"
            "  make_room      – add ONE fixed appointment at a set time to an ALREADY-PLANNED day "
            "and shift the existing blocks around it WITHOUT deleting them (keep some 'pin'ned). "
            "Best for 'I have a meeting 12:00–13:30, adjust my day around it'.\n"
            "  find_free_time – (read-only) list open gaps; use to answer \"when am I free?\"\n"
            "  reflow_from_now– push the rest of today later/earlier when running late\n"
            "  plan_for_deadline – spread work across the days before a due date\n"
            "  week_summary   – (read-only) time per category over a week; balance check\n"
            "  replace_day    – rebuild a whole day from a complete list (full reset).\n"
            "                   It DELETES blocks you don't include, so list everything to keep.\n"
            "  list_blocks    – read a day's schedule\n\n"
            "PLANNING — when the user asks you to plan their day or fit tasks in, REASON it out, "
            "then use schedule_tasks:\n"
            "  - Infer sensible durations if not given (homework ~1h, big project ~2h, quick "
            "errand ~30m); ask only if truly unclear. Pass each as minutes.\n"
            "  - Judge urgency from wording: urgent / due today / ASAP → priority \"high\" (it gets "
            "an earlier slot); \"sometime\" / \"if I have time\" → \"low\".\n"
            "  - Use 'prefer' (morning/afternoon/evening or a time) when the task has a natural "
            "time. Keep to waking hours via day_start/day_end (defaults to the user's waking-hours "
            "setting; on today, planning starts no earlier than the current time) unless the "
            "user is an early bird / night owl. Never plan work in the middle of the night.\n"
            "  - schedule_tasks places tasks around existing blocks and calendar events and NEVER "
            "deletes them — so meals, classes, and anything the user keeps are safe automatically. "
            "Prefer it over replace_day for planning. Only use replace_day for an explicit "
            "from-scratch rebuild (and then include every block to keep). Verify with list_blocks.\n"
            "  - USE plan_day (not replace_day, not hand-built lists) whenever the request has an "
            "ORDER ('X first, then Y'), FIXED times (lunch at 13:00, workout at 16:00), and/or "
            "chunking with breaks. Give each task its TOTAL focus minutes (NEVER subtract break "
            "time — breaks are added on top), set chunk/break, and pass the fixed items in 'fixed'. "
            "The app lays everything out in order, splits into chunks, and flows the rest past the "
            "anchors and meetings — so you don't compute any times yourself. Don't shrink a task's "
            "minutes to 'make room' for breaks; plan_day handles that.\n"
            "  - For a NEW fixed appointment in an ALREADY-PLANNED day ('I have a meeting at 3pm, "
            "rearrange around it'), use make_room — ONE call that drops the appointment at the "
            "exact time and shifts the existing blocks around it without deleting any (pass 'pin' "
            "for blocks that must not move, e.g. a workout). Do NOT use add_block (it would just "
            "drop the appointment in a random free slot), and do NOT chain move_block calls.\n\n"
            "RULES\n"
            "  - ALWAYS call a tool when asked to add/move/remove/rename/copy/clear/shift/plan "
            "— never just describe the change. Saying you did it without a tool call is a "
            "failure — the schedule only changes when a tool runs.\n"
            "  - Times are 24-hour HH:MM. For another day pass the user's OWN word for it — a "
            "weekday name (\"Thursday\"), \"tomorrow\", or \"6/14\" — NOT a date you computed; "
            "omit it for the day on screen.\n"
            "  - Blocks can't overlap — the app auto-adjusts, so don't fuss over exact gaps.\n"
            "  - To delete/move/rename a block, identify it by title and/or by its start "
            "time using 'at' (24h HH:MM). To remove ONE time slot, use 'at' with that "
            "block's start time — e.g. delete the 2pm block → delete_block(at=\"14:00\"). "
            "When several blocks share a title, add 'at' to pick the exact one. Don't "
            "delete by title alone if the user pointed at a specific time. To wipe the "
            "WHOLE day use clear_day.\n"
            "  - Google Calendar events are READ-ONLY; if asked to change one, say it must "
            "be edited in Google Calendar. PLAN AROUND THEM: when you shift / rebuild / reflow "
            "a day the app automatically pushes your blocks off any meeting, but you should "
            "still arrange things sensibly around it (e.g. resume the displaced work right "
            "after the meeting ends rather than leaving a hole).\n"
            "  - CHECK YOUR WORK: after any edit — especially several at once or a whole-day "
            "rebuild — call list_blocks. It ends with a CONFLICTS section that flags every "
            "overlap (block-on-block AND block-on-meeting). If it lists conflicts, fix them "
            "and call list_blocks again. Also confirm the right blocks exist, times/durations "
            "match the request, and nothing was deleted by accident. Repeat until it reports "
            "'No conflicts' — only then tell the user it's done.\n"
            "  - After it's verified, confirm in one short sentence — don't restate the whole "
            "schedule.\n"
            "  - Be friendly and concise.\n\n"
            "EXAMPLES\n"
            "  \"delete the 2pm block\"             → delete_block(at=\"14:00\")\n"
            "  \"delete the 9am study block\"       → delete_block(title=\"study\", at=\"09:00\")\n"
            "  \"remove my gym session\"           → delete_block(title=\"gym\")\n"
            "  \"move the 9am block to 11\"         → move_block(at=\"09:00\", start=\"11:00\")\n"
            "  \"move AP work to 1pm\"              → move_block(title=\"AP work\", start=\"13:00\")\n"
            "  \"make gym 30 minutes longer\"       → move_block(title=\"gym\", end=\"...\")\n"
            "  \"copy my schedule to 6/14\"         → copy_day(to_date=\"6/14\")\n"
            "  \"copy today's schedule to Thursday\" → copy_day(to_date=\"Thursday\")  ← pass the weekday word, don't compute the date\n"
            "  \"shift everything two hours later\"  → shift_blocks(minutes=120)\n"
            "  \"clear my afternoon\"               → clear_range(start=\"12:00\", end=\"18:00\")\n"
            "  \"study 16:00-18:00 every weekday\"  → add_recurring(title=\"Study\", start=\"16:00\", end=\"18:00\", weekdays=[\"weekdays\"])\n"
            "  \"when am I free for 2 hours?\"      → find_free_time(duration=120)\n"
            "  \"split my study block into 30-min chunks\" → split_block(title=\"study\", chunk=30, break=5)\n"
            "  \"plan my day: finish the essay (urgent), gym, read; keep dinner\" → "
            "schedule_tasks(tasks=[{title:\"Finish essay\",minutes:120,priority:\"high\"}, "
            "{title:\"Gym\",minutes:60,prefer:\"evening\"}, {title:\"Read\",minutes:30,priority:\"low\"}])\n"
            "  \"college essays, then history, then AP — 2h each in 30-min chunks with 15-min "
            "breaks; lunch 13:00 for 1h; workout 16:00 for 1h; wake 10:00\" → plan_day("
            "start=\"10:00\", fixed=[{title:\"Lunch\",start:\"13:00\",minutes:60,type:\"meals\"}, "
            "{title:\"Workout\",start:\"16:00\",minutes:60,type:\"exercise\"}], tasks=["
            "{title:\"College Essays\",minutes:120,type:\"project\",chunk:30,break:15}, "
            "{title:\"History\",minutes:120,type:\"study\",chunk:30,break:15}, "
            "{title:\"AP Psychology\",minutes:120,type:\"study\",chunk:30,break:15}])  "
            "← each task keeps a full 2h of focus; breaks + lunch + workout are extra, placed around it.\n"
            "  \"I have a college-apps meeting 12:00–13:30 on 6/22 (25 min buffer each side); shift "
            "my day around it but don't move my workout\" → make_room(date=\"6/22\", title=\"College "
            "Applications Meeting\", start=\"12:00\", end=\"13:30\", buffer_before=25, buffer_after=25, "
            "pin=[\"Workout/Break\"])  ← inserts the meeting + buffers and shifts everything else "
            "around it, keeping the workout fixed; nothing is deleted.\n")
        add = {"plan": "\nThe user wants help planning. Gather what they need to get done, "
                       "reason out durations / urgency / preferred times, then place them with "
                       "ONE schedule_tasks call (it keeps existing blocks safe) and verify.",
               "suggest": "\nGive 3-5 specific, actionable schedule improvements."}.get(self.mode, "")
        return p + add + model_guidance(self.model)

    def _send(self):
        txt = self._inp.toPlainText().strip()
        if not txt: return
        self._inp.clear()
        self.history[self.mode].append({"role": "user", "content": txt})
        self._persist_chat(force=True)
        self._render(); self._generate(txt)

    def _do_undo(self):
        if callable(self.on_undo):
            self.on_undo()

    def set_undo_enabled(self, on: bool):
        self._undo_btn.setEnabled(bool(on))

    def _hist_mode(self) -> str:
        """Transcript key for the active turn (frozen) or the current UI tab."""
        return self._turn_mode if self._turn_mode is not None else self.mode

    def _turn_ended(self):
        """Every way a turn finishes (final text, error, round limit, Stop) funnels
        here so MainWindow can unlock Undo exactly once per turn."""
        self._thinking.hide(); self._stop_btn.hide()
        self._turn_mode = None
        self._persist_chat(force=True)
        if callable(self.on_turn_end):
            self.on_turn_end()

    def _generate(self, user_msg):
        if self._thread and self._thread.isRunning(): return
        self._user_stopped = False
        self._turn_mode = self.mode       # freeze for the whole turn (incl. tool rounds)
        self._maybe_memory_warning()
        if callable(self.on_turn_start):   # let MainWindow snapshot the schedule for undo
            self.on_turn_start()
        mode = self._hist_mode()
        hist = [m for m in self.history[mode] if m["role"] in ("user","assistant")]
        msgs = [{"role":"system","content":self._sys_prompt()}] + \
               [{"role":m["role"],"content":m["content"]} for m in hist if m["content"]]

        self.history[mode].append({"role":"assistant","content":""})
        self._cur_text  = ""
        self._loop_msgs = msgs
        self._depth     = 0
        self._thinking.show(); self._stop_btn.show()
        self._persist_chat(force=True)
        self._spawn_thread()

    def _effective_temp(self):
        # Analyze/suggest mode runs a touch warmer for more varied ideas; editing modes
        # (chat/plan) stay precise for reliable tool-calling.
        mode = self._hist_mode()
        return (min(1.2, self.temperature + 0.3) if mode == "suggest"
                else self.temperature)

    def _spawn_thread(self):
        # Hold the QThread ref until finished + deleteLater (same lifecycle as
        # OllamaCheckThread / pull). Replacing an unparented running thread used to
        # let GC destroy the wrapper mid-teardown → segfault.
        if self._thread is not None and self._thread.isRunning():
            return
        t = OllamaThread(self._loop_msgs, self.model, tools=AI_TOOLS,
                         num_ctx=self.num_ctx, temperature=self._effective_temp())
        t.token.connect(self._on_token)
        t.done.connect(self._on_done)
        t.tool_calls.connect(self._on_tool_calls)
        t.error.connect(self._on_error)
        t.finished.connect(t.deleteLater)
        def _clear(th=t):
            if self._thread is th:
                self._thread = None
        t.finished.connect(_clear)
        self._thread = t
        t.start()

    def _on_tool_calls(self, calls):
        # Stop must cancel schedule mutations (native tool_calls path).
        if getattr(self, "_user_stopped", False):
            self._user_stopped = False
            self._turn_ended()
            return
        h = self.history[self._hist_mode()]
        # drop the empty streaming bubble; tool notes take its place
        if h and h[-1]["role"] == "assistant" and not h[-1]["content"].strip():
            h.pop()
        self._loop_msgs = self._loop_msgs + [
            {"role": "assistant", "content": self._cur_text or "", "tool_calls": calls}]
        for call in calls:
            fn   = call.get("function") or {}
            name = fn.get("name", "?")
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try: args = json.loads(args)
                except Exception: args = {}
            result = self.execute_tool(name, args) if callable(self.execute_tool) \
                     else "Tool execution unavailable."
            h.append({"role": "tool_note", "content": f"{name} → {result}"})
            self._loop_msgs.append({"role": "tool", "tool_name": name,
                                    "name": name, "content": str(result)})
        self._render()
        self._persist_chat(force=True)
        self._depth += 1
        if self._depth >= MAX_TOOL_ROUNDS:   # guard against tool-call loops
            self._turn_ended()
            return
        h.append({"role": "assistant", "content": ""})
        self._cur_text = ""
        self._spawn_thread()

    def _on_token(self, tok):
        if getattr(self, "_user_stopped", False):
            return
        self._cur_text += tok
        h = self.history[self._hist_mode()]
        if looks_like_tool_text(self._cur_text):
            # Model is printing a tool call as text — don't show raw JSON; keep the
            # "Thinking…" indicator up. _on_done will execute it.
            if h:
                h[-1]["content"] = ""
            self._render()
        else:
            if h:
                h[-1]["content"] = self._cur_text
            self._render(); self._thinking.hide()
        # Mid-stream crash insurance (throttled).
        self._persist_chat(force=False)

    def _on_done(self):
        # User hit Stop — never recover/execute tools from partial text.
        if getattr(self, "_user_stopped", False):
            self._user_stopped = False
            h = self.history[self._hist_mode()]
            if h and h[-1]["role"] == "assistant":
                if not (h[-1].get("content") or "").strip():
                    h[-1]["content"] = "(Stopped.)"
                self._render()
            self._turn_ended()
            return
        # Small models sometimes print the tool call as text (<|python_tag|>, ``` fences,
        # bare JSON, arrays…) instead of using the native tool_calls channel. Recover it.
        extracted = extract_tool_calls(self._cur_text) if self._depth < MAX_TOOL_ROUNDS else []
        if extracted:
            h = self.history[self._hist_mode()]
            if h and h[-1]["role"] == "assistant":
                h.pop()   # drop the (hidden) raw-text bubble
            self._cur_text = ""
            self._on_tool_calls([{"function": {"name": e["name"], "arguments": e["args"]}}
                                 for e in extracted])
            return
        # Not a tool call. Restore the real text (it may have been hidden mid-stream
        # because it looked tool-like), or show a fallback if it was unparseable JSON.
        h = self.history[self._hist_mode()]
        if h and h[-1]["role"] == "assistant":
            if self._cur_text and not looks_like_tool_text(self._cur_text):
                h[-1]["content"] = self._cur_text
            elif looks_like_tool_text(self._cur_text):
                h[-1]["content"] = ("I tried to update your schedule but couldn't read "
                                    "the result — could you rephrase that?")
            self._render()
        self._turn_ended()

    def _on_error(self, msg):
        if getattr(self, "_user_stopped", False):
            self._user_stopped = False
            self._turn_ended()
            return
        h = self.history[self._hist_mode()]
        if h:
            h.pop()
        h.append({"role":"error","content":msg})
        self._render(); self._turn_ended()

    def _stop(self):
        self._user_stopped = True
        if self._thread:
            self._thread.stop()
        self._persist_chat(force=True)
