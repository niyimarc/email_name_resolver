"""Email Name Resolver: Kivy desktop app entry point.

Pipeline per email (run in a background thread, one email at a time):
    1. ``split_email_local``     rule-based token split (pure Python)
    2. ``judge_name_with_ai``    local Ollama judgment (structured output)
    3. ``WebsiteResolver``       optional company website lookup

Each result is pushed to the table with ``Clock.schedule_once`` as soon as
it is ready, so the UI streams results and never blocks.

NOTE: The brief mentions KivyMD, but its dependency whitelist only allows
``kivy`` (plus ollama, pydantic, requests, plyer, pyinstaller). KivyMD is not
on that list, so the UI is built with plain Kivy widgets. This is the safest
reading because it keeps the dependency contract exact.
"""

from __future__ import annotations

import os
import sys


def use_angle_on_remote_desktop() -> None:
    """Switch Kivy to the ANGLE (DirectX) GL backend inside Remote Desktop.

    RDP sessions usually expose only Microsoft's "GDI Generic" OpenGL 1.1,
    below Kivy's 2.0 minimum, so the app would abort on start. ANGLE renders
    OpenGL ES through Direct3D, which works over RDP. Must run before any
    Kivy import. An explicit ``KIVY_GL_BACKEND`` set by the user always wins.
    """
    if sys.platform != "win32" or os.environ.get("KIVY_GL_BACKEND"):
        return
    try:
        import ctypes

        sm_remotesession = 0x1000
        remote = bool(ctypes.windll.user32.GetSystemMetrics(sm_remotesession))
    except Exception:
        remote = os.environ.get("SESSIONNAME", "").upper().startswith("RDP")
    if remote:
        os.environ["KIVY_GL_BACKEND"] = "angle_sdl2"


use_angle_on_remote_desktop()
os.environ.setdefault("KIVY_NO_ARGS", "1")  # keep Kivy from eating CLI args

from kivy.config import Config  # noqa: E402  (must precede other kivy imports)

Config.set("input", "mouse", "mouse,multitouch_on_demand")  # no red dots
Config.set("kivy", "exit_on_escape", "0")


def _saved_ui_scale() -> str:
    """The "Interface size" setting, read before Kivy starts."""
    try:
        from storage.settings import load_settings as _load

        return _load().ui_scale
    except Exception:
        return "Auto"


import ui_layout  # noqa: E402  (no Kivy imports inside)

# Window sized from the screen (never larger than it) + Interface size.
ui_layout.prepare_window(Config, _saved_ui_scale())

import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import webbrowser  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Callable, Dict, List, Optional, Tuple  # noqa: E402

from kivy.app import App  # noqa: E402
from kivy.clock import Clock  # noqa: E402
from kivy.core.window import Window  # noqa: E402
from kivy.lang import Builder  # noqa: E402
from kivy.metrics import dp  # noqa: E402
from kivy.properties import (  # noqa: E402
    BooleanProperty,
    DictProperty,
    ListProperty,
    NumericProperty,
    StringProperty,
)
from kivy.uix.boxlayout import BoxLayout  # noqa: E402
from kivy.uix.button import Button  # noqa: E402
from kivy.uix.checkbox import CheckBox  # noqa: E402
from kivy.uix.gridlayout import GridLayout  # noqa: E402
from kivy.uix.label import Label  # noqa: E402
from kivy.uix.popup import Popup  # noqa: E402
from kivy.uix.recycleview import RecycleView  # noqa: E402
from kivy.uix.recycleview.views import RecycleDataViewBehavior  # noqa: E402
from kivy.uix.scrollview import ScrollView  # noqa: E402
from kivy.uix.spinner import Spinner  # noqa: E402
from kivy.uix.textinput import TextInput  # noqa: E402
from kivy.utils import escape_markup  # noqa: E402

from exporters.csv_export import (  # noqa: E402
    FORMAT_FULL,
    FORMAT_MAILING,
    ExportSelection,
    count_resolved,
    default_export_filename,
    export_mailing_csv,
    export_provider_zip,
    export_rows_to_csv,
    group_by_provider,
    is_resolved,
    is_undeliverable,
    is_valid_email,
    provider_counts,
    select_rows,
)
import version  # noqa: E402  (app version + developer info)
from help_guide import open_setup_guide  # noqa: E402
from pipeline.ai_judge import list_local_models, model_is_pulled  # noqa: E402
from pipeline.email_extract import (  # noqa: E402
    ExtractResult,
    dedupe_emails,
    email_key,
    extract_emails,
    parse_email_input,
    read_emails_from_file,
    read_file_text,
)
from pipeline.name_dictionary import (  # noqa: E402
    KIND_FIRST,
    KIND_ROLE,
    KIND_SURNAME,
    PARTICLES,
    DictionaryStore,
    add_user_words,
    clean_name,
    parse_word_lines,
)
from pipeline.mail_domain import (  # noqa: E402
    STATUS_CHECKING,
    STATUS_NOT_CHECKED,
    STATUS_ROLE,
    STATUS_UNKNOWN,
    STATUS_VALID,
    UNDELIVERABLE_STATUSES,
)
from pipeline.runner import (  # noqa: E402
    PipelineRunner,
    RunCallbacks,
    RunStats,
)
from pipeline.splitter import split_email_local  # noqa: E402
from storage.session import clear_session, load_session, save_session  # noqa: E402
from storage.settings import (  # noqa: E402
    UI_SCALE_CHOICES,
    Settings,
    dictionaries_dir,
    load_settings,
    save_settings,
)

APP_TITLE = f"{version.APP_NAME} v{version.__version__}"   # e.g. "Email Name Resolver v1.0.0"
USER_NOT_PERSON_REASON = "marked not a person by user"
# ---------------------------------------------------------------------------
# Native file dialogs (always called from a background thread)
# ---------------------------------------------------------------------------

class FileDialogs:
    """Open/save dialogs using plyer, with a tkinter fallback.

    Both methods block until the user answers, so callers must run them in a
    background thread, never on the Kivy main thread.
    """

    FILTERS_OPEN = [("Email lists", "*.txt *.csv *.tsv *.xlsx"), ("All files", "*.*")]

    @staticmethod
    def ask_open_path() -> Optional[str]:
        """Ask the user for a ``.txt``/``.csv``/``.tsv``/``.xlsx`` file.

        Returns:
            The chosen path, or None if cancelled.
        """
        try:
            from plyer import filechooser

            picked = filechooser.open_file(
                title="Load emails", filters=[["Email lists", "*.txt", "*.csv", "*.tsv", "*.xlsx"],
                         ["All files", "*.*"]],
                multiple=False)
            if picked is not None:
                return picked[0] if picked else None
        except Exception:  # plyer missing/unsupported backend -> tkinter
            pass
        return FileDialogs._tk_dialog("open")

    @staticmethod
    def ask_save_path(default_name: str, ext: str = ".csv") -> Optional[str]:
        """Ask the user where to save an export.

        Args:
            default_name: Suggested file name.
            ext: ``".csv"`` or ``".zip"``.

        Returns:
            The chosen path (``ext`` appended if missing), or None.
        """
        kind = "ZIP archives" if ext == ".zip" else "CSV files"
        path: Optional[str] = None
        used_plyer = False
        try:
            from plyer import filechooser

            start = str(Path.home() / "Documents" / default_name)
            picked = filechooser.save_file(
                title="Export", path=start, filters=[[kind, f"*{ext}"]])
            if picked is not None:
                used_plyer = True
                path = picked[0] if picked else None
        except Exception:
            pass
        if not used_plyer:
            path = FileDialogs._tk_dialog("save", default_name, ext)
        if path and not path.lower().endswith(ext):
            path += ext
        return path or None

    @staticmethod
    def _tk_dialog(kind: str, default_name: str = "", ext: str = ".csv") -> Optional[str]:
        """Show a tkinter dialog in the calling (background) thread.

        Args:
            kind: ``"open"`` or ``"save"``.
            default_name: Suggested file name for save dialogs.

        Returns:
            The chosen path, or None.
        """
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError:
            return None
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            if kind == "open":
                path = filedialog.askopenfilename(
                    parent=root, title="Load emails",
                    filetypes=FileDialogs.FILTERS_OPEN)
            else:
                path = filedialog.asksaveasfilename(
                    parent=root, title="Export", initialfile=default_name,
                    defaultextension=ext,
                    filetypes=[("ZIP archives" if ext == ".zip" else "CSV files", f"*{ext}"),
                               ("All files", "*.*")])
        finally:
            root.destroy()
        return path or None


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

KV = """
#:import dp kivy.metrics.dp

<CellLabel@Label>:
    text_size: max(self.width - dp(8), 1), self.height
    halign: 'left'
    valign: 'middle'
    shorten: True
    shorten_from: 'right'
    max_lines: 1
    font_size: '13sp'

<ClickCell@ButtonBehavior+Label>:
    text_size: max(self.width - dp(8), 1), self.height
    halign: 'left'
    valign: 'middle'
    shorten: True
    shorten_from: 'right'
    max_lines: 1
    font_size: '13sp'
    color: (0.78, 0.84, 1, 1)

<HeaderLabel@Label>:
    text_size: max(self.width - dp(8), 1), self.height
    halign: 'left'
    valign: 'middle'
    bold: True
    font_size: '13sp'

<ResultRow>:
    size_hint_y: None
    height: dp(38)
    spacing: dp(4)
    padding: dp(6), dp(2)
    canvas.before:
        Color:
            rgba: (0.17, 0.18, 0.21, 1) if self.row_index % 2 else (0.13, 0.14, 0.16, 1)
        Rectangle:
            pos: self.pos
            size: self.size
    CellLabel:
        size_hint_x: app.cols['email'] or None
        width: 0
        opacity: 1 if app.cols['email'] else 0
        text: root.email
    CellLabel:
        size_hint_x: app.cols['real'] or None
        width: 0
        opacity: 1 if app.cols['real'] else 0
        text: root.real_text
        color: (0.55, 0.9, 0.55, 1) if root.real_text == 'yes' else (0.95, 0.6, 0.6, 1)
    CellLabel:
        size_hint_x: app.cols['confidence'] or None
        width: 0
        opacity: 1 if app.cols['confidence'] else 0
        text: root.confidence_text
    TextInput:
        id: final_input
        size_hint_x: app.cols['final'] or None
        width: 0
        opacity: 1 if app.cols['final'] else 0
        multiline: False
        write_tab: False
        font_size: '13sp'
        padding: dp(6), dp(8)
        hint_text: 'name'
        on_text: root.on_final_name_edited(self.text)
        on_text_validate: self.focus = False
    CellLabel:
        size_hint_x: app.cols['parts'] or None
        width: 0
        opacity: 1 if app.cols['parts'] else 0
        text: root.parts_text
    ClickCell:
        size_hint_x: app.cols['reason'] or None
        width: 0
        opacity: 1 if app.cols['reason'] else 0
        text: root.reason
        on_release: root.show_details()
    CellLabel:
        size_hint_x: app.cols['website'] or None
        width: 0
        opacity: 1 if app.cols['website'] else 0
        markup: True
        text: root.website_markup
        on_ref_press: app.open_url(args[1])
    CellLabel:
        size_hint_x: app.cols['provider'] or None
        width: 0
        opacity: 1 if app.cols['provider'] else 0
        text: root.provider
    CellLabel:
        size_hint_x: app.cols['status'] or None
        width: 0
        opacity: 1 if app.cols['status'] else 0
        text: root.status
        color: root.status_color

<ResultsTable>:
    viewclass: 'ResultRow'
    bar_width: dp(10)
    scroll_type: ['bars', 'content']
    RecycleBoxLayout:
        default_size: None, dp(38)
        default_size_hint: 1, None
        size_hint_y: None
        height: self.minimum_height
        orientation: 'vertical'
        spacing: dp(1)

<RootView>:
    orientation: 'vertical'
    padding: dp(10)
    spacing: dp(8)
    canvas.before:
        Color:
            rgba: 0.09, 0.10, 0.12, 1
        Rectangle:
            pos: self.pos
            size: self.size

    BoxLayout:
        id: banner
        size_hint_y: None
        height: 0
        opacity: 0
        padding: dp(10), dp(4)
        spacing: dp(8)
        canvas.before:
            Color:
                rgba: 0.80, 0.42, 0.08, 1
            Rectangle:
                pos: self.pos
                size: self.size
        Label:
            id: banner_label
            text_size: self.size
            halign: 'left'
            valign: 'middle'
            bold: True
        Button:
            text: 'Setup guide'
            size_hint_x: None
            width: dp(110)
            on_release: app.open_setup_guide()
        Button:
            text: 'Dismiss'
            size_hint_x: None
            width: dp(90)
            on_release: app.hide_banner()

    BoxLayout:
        # Wide/medium: input left, buttons right. Narrow: buttons under input.
        size_hint_y: None
        orientation: 'vertical' if app.ui_mode == 'narrow' else 'horizontal'
        height: ((dp(80) if app.short_screen else dp(118)) + dp(8) + dp(150)) if app.ui_mode == 'narrow' else (dp(150) if app.short_screen else dp(176))
        spacing: dp(8)
        EmailInput:
            id: email_input
            hint_text: 'Paste emails or any text/CSV containing emails: duplicates and junk are removed automatically'
            multiline: True
            font_size: '14sp'
        BoxLayout:
            orientation: 'vertical'
            size_hint_x: 1 if app.ui_mode == 'narrow' else None
            width: dp(330)
            size_hint_y: None if app.ui_mode == 'narrow' else 1
            height: dp(150)
            spacing: dp(5)
            BoxLayout:
                spacing: dp(5)
                Button:
                    id: load_btn
                    text: 'Load file'
                    on_release: app.on_load_file()
                Button:
                    id: analyze_btn
                    text: 'Analyze'
                    bold: True
                    background_color: (0.35, 0.75, 0.45, 1)
                    on_release: app.on_analyze()
            BoxLayout:
                spacing: dp(5)
                Button:
                    id: pause_btn
                    text: 'Pause'
                    disabled: True
                    background_color: (0.95, 0.75, 0.3, 1)
                    on_release: app.on_pause_clicked()
                Button:
                    id: stop_btn
                    text: 'Stop'
                    disabled: True
                    background_color: (0.9, 0.4, 0.4, 1)
                    on_release: app.on_stop_clicked()
            Button:
                id: reanalyze_btn
                text: 'Re-analyze unresolved'
                disabled: True
                on_release: app.on_reanalyze_unresolved()
            BoxLayout:
                spacing: dp(5)
                Button:
                    text: 'Settings'
                    on_release: app.open_settings()
                Button:
                    id: export_btn
                    text: 'Export CSV'
                    on_release: app.on_export()
                Button:
                    id: clear_btn
                    text: 'Clear'
                    on_release: app.on_clear()
                Button:
                    text: 'Help'
                    size_hint_x: None
                    width: dp(56)
                    on_release: app.open_setup_guide()

    BoxLayout:
        size_hint_y: None
        height: dp(24)
        spacing: dp(8)
        Label:
            id: status_label
            text: 'Ready.'
            text_size: self.size
            halign: 'left'
            valign: 'middle'
            shorten: True
            shorten_from: 'right'
            font_size: '13sp'
        Label:
            id: counts_label
            size_hint_x: None
            width: min(dp(430), root.width * 0.55)
            shorten: True
            shorten_from: 'left'
            markup: True
            text: ''
            text_size: self.size
            halign: 'right'
            valign: 'middle'
            font_size: '13sp'

    BoxLayout:
        size_hint_y: None
        height: dp(30)
        spacing: dp(4)
        padding: dp(6), 0
        canvas.before:
            Color:
                rgba: 0.22, 0.24, 0.30, 1
            Rectangle:
                pos: self.pos
                size: self.size
        HeaderLabel:
            size_hint_x: app.cols['email'] or None
            width: 0
            opacity: 1 if app.cols['email'] else 0
            text: 'Email'
        HeaderLabel:
            size_hint_x: app.cols['real'] or None
            width: 0
            opacity: 1 if app.cols['real'] else 0
            text: 'Is Real Name'
        HeaderLabel:
            size_hint_x: app.cols['confidence'] or None
            width: 0
            opacity: 1 if app.cols['confidence'] else 0
            text: 'Confidence'
        HeaderLabel:
            size_hint_x: app.cols['final'] or None
            width: 0
            opacity: 1 if app.cols['final'] else 0
            text: 'Final Name (editable)'
        HeaderLabel:
            size_hint_x: app.cols['parts'] or None
            width: 0
            opacity: 1 if app.cols['parts'] else 0
            text: 'Name Parts'
        HeaderLabel:
            size_hint_x: app.cols['reason'] or None
            width: 0
            opacity: 1 if app.cols['reason'] else 0
            text: 'Reason (click)'
        HeaderLabel:
            size_hint_x: app.cols['website'] or None
            width: 0
            opacity: 1 if app.cols['website'] else 0
            text: 'Company Website'
        HeaderLabel:
            size_hint_x: app.cols['provider'] or None
            width: 0
            opacity: 1 if app.cols['provider'] else 0
            text: 'Mail Provider'
        HeaderLabel:
            size_hint_x: app.cols['status'] or None
            width: 0
            opacity: 1 if app.cols['status'] else 0
            text: 'Email Status'

    ResultsTable:
        id: table
"""


def status_color(status: str) -> List[float]:
    """Text colour for an Email Status cell."""
    if status == STATUS_VALID:
        return [0.55, 0.9, 0.55, 1]
    if status in UNDELIVERABLE_STATUSES:
        return [0.95, 0.5, 0.5, 1]
    if status in (STATUS_ROLE, STATUS_UNKNOWN):
        return [0.95, 0.8, 0.45, 1]
    return [0.65, 0.65, 0.65, 1]


class ResultRow(RecycleDataViewBehavior, BoxLayout):
    """One recycled table row. The row dict lives in ``ResultsTable.data``."""

    row_index = NumericProperty(0)
    email = StringProperty("")
    real_text = StringProperty("")
    confidence_text = StringProperty("")
    parts_text = StringProperty("")
    reason = StringProperty("")
    website_markup = StringProperty("")
    provider = StringProperty("")
    status = StringProperty("")
    status_color = ListProperty([0.8, 0.8, 0.8, 1])

    def __init__(self, **kwargs: Any) -> None:
        """Initialise the row with no bound data."""
        super().__init__(**kwargs)
        self._row: Optional[Dict[str, Any]] = None
        self._loading = False

    def refresh_view_attrs(self, rv: RecycleView, index: int, data: Dict[str, Any]) -> None:
        """Bind this view to row ``index`` (called by RecycleView on recycle).

        Args:
            rv: The owning RecycleView.
            index: Index of the row in ``rv.data``.
            data: The row dict.
        """
        final_input = self.ids.final_input
        same_row = self._row is data and self.row_index == index
        if not same_row and final_input.focus:
            final_input.focus = False  # never carry an edit onto another row

        self._loading = True
        try:
            self._row = data
            self.row_index = index
            self.email = data.get("email", "")
            self.real_text = "yes" if data.get("is_real_name") else "no"
            self.confidence_text = str(data.get("confidence", 0))
            self.parts_text = " | ".join(data.get("name_parts") or [])
            self.reason = data.get("reason", "")
            url = data.get("company_website") or ""
            if url:
                label = escape_markup(url.split("://", 1)[-1])
                suffix = "" if data.get("website_verified") else " [color=999999](unverified)[/color]"
                self.website_markup = (f"[ref={escape_markup(url)}][color=5aa9ff][u]{label}[/u]"
                                       f"[/color][/ref]{suffix}")
            else:
                self.website_markup = ""
            self.provider = data.get("mail_provider", "")
            self.status = data.get("email_status", "")
            self.status_color = status_color(self.status)
            if not (same_row and final_input.focus):
                final_input.text = data.get("final_name", "")
        finally:
            self._loading = False

    def on_final_name_edited(self, text: str) -> None:
        """Write a user edit of Final Name back into the row data.

        Args:
            text: The current text of the Final Name input.
        """
        if self._loading or self._row is None:
            return
        self._row["final_name"] = text
        app = App.get_running_app()
        if app is not None:
            app.mark_dirty()

    def show_details(self) -> None:
        """Open the detail popup for this row."""
        app = App.get_running_app()
        if app is not None and self._row is not None:
            app.show_row_details(self._row)


class EmailInput(TextInput):
    """Input box that cleans and deduplicates pasted email lists."""

    def paste(self) -> None:
        """Route pastes containing ``@`` through the email cleaner."""
        from kivy.core.clipboard import Clipboard

        data = Clipboard.paste() or ""
        app = App.get_running_app()
        if "@" in data and app is not None:
            app.on_paste_emails(data)
        else:
            super().paste()


class ResultsTable(RecycleView):
    """RecycleView holding result rows; ``data`` is the single source of truth."""


class RootView(BoxLayout):
    """Root layout (defined in KV)."""


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

def _run_in_background(work: Callable[[], Any],
                       on_done: Optional[Callable[[Any], None]] = None,
                       on_error: Optional[Callable[[Exception], None]] = None) -> None:
    """Run ``work`` in a daemon thread and deliver its result on the main thread.

    Args:
        work: Callable executed in the background.
        on_done: Receives the return value on the main thread.
        on_error: Receives any exception on the main thread.
    """
    def runner() -> None:
        try:
            result = work()
        except Exception as exc:
            if on_error is not None:
                # Bind exc now: Python deletes it when the except block ends.
                Clock.schedule_once(lambda _dt, err=exc: on_error(err), 0)
            return
        if on_done is not None:
            Clock.schedule_once(lambda _dt: on_done(result), 0)

    threading.Thread(target=runner, daemon=True).start()


def asset_path(name: str) -> str:
    """Absolute path of a file in ``assets/`` (works from source and from the .exe)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "assets", name)


def _post_to_ui(fn: Callable, *args: Any) -> None:
    """Run ``fn(*args)`` on the Kivy main thread (safe from any thread)."""
    Clock.schedule_once(lambda _dt: fn(*args), 0)


def format_duration(seconds: float) -> str:
    """Format seconds as ``45s``, ``3m 05s`` or ``1h 02m``."""
    seconds = int(round(max(0.0, seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _open_folder(folder: str) -> None:
    """Open ``folder`` in the system file manager without blocking."""
    if hasattr(os, "startfile"):
        os.startfile(folder)  # type: ignore[attr-defined]  # Windows
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])
    else:
        subprocess.Popen(["xdg-open", folder])


class EmailNameResolverApp(App):
    """The application: owns settings, the worker and all UI actions."""

    running = BooleanProperty(False)
    dialog_open = BooleanProperty(False)
    # Responsive layout, recomputed whenever the window is resized.
    ui_mode = StringProperty(ui_layout.MODE_WIDE)
    short_screen = BooleanProperty(False)
    cols = DictProperty(ui_layout.column_hints(ui_layout.MODE_WIDE))

    def __init__(self, **kwargs: Any) -> None:
        """Set up state containers; nothing touches disk here."""
        super().__init__(**kwargs)
        self.title = APP_TITLE
        self.settings = Settings()
        self._worker: Optional[PipelineRunner] = None
        self._pause_event = threading.Event()
        # Every row by email_key, for in-place updates from the runner.
        self._rows_by_key: Dict[str, Dict[str, Any]] = {}
        # Run timing (monotonic seconds); paused time is excluded.
        self._run_started = 0.0
        self._paused_total = 0.0
        self._pause_started: Optional[float] = None
        self._ai_phase_started: Optional[float] = None
        self._progress_state = (0, 0, 0, 0)
        self._timer_event: Any = None
        self._stop_event = threading.Event()
        # Change counters: the session is saved on close only if the table
        # changed after the last successful save (avoids a flag race with the
        # background auto-save).
        self._change_count = 0
        self._saved_count = 0
        self._run_summary = ""
        self.dictionary_store = DictionaryStore(dictionaries_dir)
        self._counts_trigger = Clock.create_trigger(self.update_counts, 0.15)
        self._refresh_trigger = Clock.create_trigger(
            lambda _dt: self.table.refresh_from_data(), 0.1)
        # Rows being re-analysed, keyed by email_key, updated in place.
        self._replace_targets: Dict[str, Dict[str, Any]] = {}

    # -- lifecycle ---------------------------------------------------------

    def build(self) -> RootView:
        """Build the UI from KV and set the window/taskbar icon."""
        # NOTE: The simplified logo variant is used for the window icon because
        # Windows shows it at 16-32 px, where the detailed logo turns to mush.
        icon = asset_path("icon_small.png")
        if os.path.exists(icon):
            self.icon = icon
        Builder.load_string(KV)
        Window.bind(size=self.update_layout)
        self.update_layout(Window, Window.size)
        return RootView()

    def update_layout(self, _window: Any, size: Tuple[float, float]) -> None:
        """Pick the wide/medium/narrow layout for the current window size."""
        width_dp, height_dp = size[0] / dp(1), size[1] / dp(1)
        mode = ui_layout.layout_mode(width_dp)
        if mode != self.ui_mode:
            self.ui_mode = mode
            self.cols = ui_layout.column_hints(mode)
        self.short_screen = height_dp < ui_layout.SHORT_MAX_DP

    def fit_popup(self, popup: Popup, width: float, height: Optional[float] = None) -> Popup:
        """Size ``popup`` to at most 95% of the window and keep it fitting.

        Args:
            popup: The popup (``size_hint_x`` must be None).
            width: Preferred width in pixels.
            height: Preferred height in pixels, or None to keep ``size_hint_y``.

        Returns:
            The same popup, for chaining.
        """
        def refit(*_: Any) -> None:
            w, h = ui_layout.fit_size((width, height or 0), Window.size)
            popup.width = w
            if height is not None:
                popup.height = h

        refit()
        Window.bind(size=refit)
        popup.bind(on_dismiss=lambda *_: Window.unbind(size=refit))
        return popup

    @property
    def table(self) -> ResultsTable:
        """The results RecycleView."""
        return self.root.ids.table

    def on_start(self) -> None:
        """Load settings and the last session in the background."""
        def load() -> Tuple[Settings, List[Dict[str, Any]]]:
            settings, rows = load_settings(), load_session()
            try:
                self.dictionary_store.get()  # warm up so the first run is fast
            except Exception:
                pass
            return settings, rows

        def apply(result: Tuple[Settings, List[Dict[str, Any]]]) -> None:
            settings, rows = result
            self.settings = settings
            if rows and not self.table.data:
                self.table.data = rows
                self.reindex_rows()
                self.update_counts()
                self.set_status(f"Loaded {len(rows)} rows from your last session.")

        _run_in_background(load, apply,
                           lambda exc: self.set_status(f"Could not load saved data: {exc}"))

    def on_stop(self) -> None:
        """Stop the worker and save the session when the window closes."""
        self._stop_event.set()
        rows = self.snapshot_rows()
        if rows and self._change_count != self._saved_count:
            try:
                save_session(rows)
            except OSError:
                pass

    # -- helpers -------------------------------------------------------------

    def snapshot_rows(self) -> List[Dict[str, Any]]:
        """Return a copy of the current table rows (including user edits)."""
        return [dict(r, name_parts=list(r.get("name_parts") or [])) for r in self.table.data]

    def mark_dirty(self) -> None:
        """Record that the table changed since the last save."""
        self._change_count += 1
        self._counts_trigger()

    def update_counts(self, *_: Any) -> None:
        """Show how many rows are resolved vs not (coalesced via a trigger)."""
        rows = self.table.data
        retry = len(self.reanalyze_candidates())
        btn = self.root.ids.reanalyze_btn
        btn.text = f"Re-analyze unresolved ({retry})" if retry else "Re-analyze unresolved"
        btn.disabled = self.running or retry == 0
        if not rows:
            self.root.ids.counts_label.text = ""
            return
        resolved, unresolved = count_resolved(rows)
        self.root.ids.counts_label.text = (
            f"[color=8be28b]Resolved: {resolved}[/color]   "
            f"[color=f09a9a]Not resolved: {unresolved}[/color]   "
            f"Total: {len(rows)}")

    def set_status(self, text: str) -> None:
        """Show ``text`` in the status line."""
        self.root.ids.status_label.text = text

    def show_banner(self, message: str) -> None:
        """Show the single, non-blocking banner at the top."""
        banner = self.root.ids.banner
        self.root.ids.banner_label.text = message
        banner.height = dp(40)
        banner.opacity = 1

    def hide_banner(self) -> None:
        """Hide the banner."""
        banner = self.root.ids.banner
        banner.height = 0
        banner.opacity = 0

    def _set_running(self, running: bool) -> None:
        """Toggle button states for a run."""
        self.running = running
        ids = self.root.ids
        ids.analyze_btn.disabled = running
        ids.stop_btn.disabled = not running
        ids.pause_btn.disabled = not running
        ids.pause_btn.text = "Pause"
        ids.clear_btn.disabled = running
        ids.reanalyze_btn.disabled = running or not self.reanalyze_candidates()

    def message_popup(self, title: str, text: str) -> None:
        """Show a simple modal message with a Close button."""
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        label = Label(text=text, halign="left", valign="top")
        label.bind(size=lambda w, s: setattr(w, "text_size", s))
        box.add_widget(label)
        popup = self.fit_popup(Popup(title=title, content=box, size_hint=(None, None)),
                               dp(520), dp(240))
        close = Button(text="Close", size_hint_y=None, height=dp(40))
        close.bind(on_release=popup.dismiss)
        box.add_widget(close)
        popup.open()

    def open_url(self, url: str) -> None:
        """Open a website link in the default browser (background thread)."""
        if url:
            _run_in_background(lambda: webbrowser.open(url))

    # -- analyze / stop --------------------------------------------------------

    def on_analyze(self) -> None:
        """Parse, deduplicate and start the background pipeline."""
        if self.running:
            return
        emails = parse_email_input(self.root.ids.email_input.text)
        if not emails:
            self.set_status("Nothing to analyze: paste emails or load a file first.")
            return
        existing = {email_key(r.get("email", "")) for r in self.table.data}
        unique, duplicates, already = dedupe_emails(emails, existing)

        parts = []
        if duplicates:
            parts.append(f"{duplicates} duplicate(s) skipped")
        if already:
            parts.append(f"{already} already in table skipped")
        self._run_summary = (" | " + ", ".join(parts)) if parts else ""

        if not unique:
            self.set_status("No new emails to analyze" + self._run_summary + ".")
            return

        self._replace_targets = {}
        self.start_worker(unique)

    def start_worker(self, emails: List[str]) -> None:
        """Start the two-phase background pipeline for ``emails``."""
        self.hide_banner()
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._run_started = time.monotonic()
        self._paused_total = 0.0
        self._pause_started = None
        self._ai_phase_started = None
        self._progress_state = (0, len(emails), 0, 0)
        self._set_running(True)
        self.render_run_status()
        if self._timer_event is not None:
            self._timer_event.cancel()
        self._timer_event = Clock.schedule_interval(lambda _dt: self.render_run_status(), 1.0)
        callbacks = RunCallbacks(
            on_row=self.on_row_ready, on_update=self.on_row_update,
            on_progress=self.on_progress, on_phase=self.on_phase,
            on_banner=self.show_banner, on_done=self.on_run_finished)
        self._worker = PipelineRunner(
            emails, Settings(**vars(self.settings)), callbacks, _post_to_ui,
            self._stop_event, self._pause_event, dictionary_store=self.dictionary_store)
        self._worker.start()

    # -- timing ------------------------------------------------------------------

    def active_seconds(self) -> float:
        """Seconds since the run started, excluding paused time."""
        now = time.monotonic()
        paused = self._paused_total + ((now - self._pause_started)
                                       if self._pause_started is not None else 0.0)
        return max(0.0, now - self._run_started - paused)

    def render_run_status(self) -> None:
        """Show progress, elapsed time and time left in the status line."""
        if not self.running:
            return
        processed, total, ai_done, ai_total = self._progress_state
        parts = [f"Processed {processed} / {total}{self._run_summary}"]
        elapsed = self.active_seconds()
        if ai_total:
            parts.append(f"AI {ai_done} / {ai_total}")
        parts.append(f"elapsed {format_duration(elapsed)}")
        if ai_total and self._ai_phase_started is not None:
            if ai_done:
                per_email = (elapsed - self._ai_phase_started) / ai_done
                left = per_email * (ai_total - ai_done)
                parts.append(f"about {format_duration(left)} left "
                             f"({per_email:.1f}s per AI email)")
            else:
                parts.append("estimating time left...")
        if self._pause_started is not None:
            parts.append("PAUSED")
        if self._stop_event.is_set():
            parts.append("stopping after the current email...")
        self.set_status(" | ".join(parts))

    # -- run callbacks (main thread) --------------------------------------------------

    def reindex_rows(self) -> None:
        """Rebuild the email_key -> row map after the table data is replaced."""
        self._rows_by_key = {email_key(r.get("email", "")): r for r in self.table.data}

    def reanalyze_candidates(self) -> List[Dict[str, Any]]:
        """Rows worth re-running: unresolved, valid email, not marked by the user.

        NOTE: Rows the user marked "not a person" and invalid addresses are
        left alone; re-running them could only undo the user's decision or
        repeat "invalid email".
        """
        return [r for r in self.table.data
                if not is_resolved(r)
                and r.get("reason") != USER_NOT_PERSON_REASON
                and is_valid_email(str(r.get("email") or ""))]

    def on_reanalyze_unresolved(self) -> None:
        """Re-run the pipeline for unresolved rows and update them in place.

        Useful after Ollama comes back, after teaching or importing names, or
        after switching model. Resolved rows and user edits are untouched.
        """
        if self.running:
            return
        rows = self.reanalyze_candidates()
        if not rows:
            self.set_status("Nothing to re-analyze: every row is resolved.")
            return
        self._replace_targets = {email_key(r["email"]): r for r in rows}
        self._run_summary = " | re-analyzing unresolved rows"
        self.dictionary_store.invalidate()  # pick up names taught since the last run
        self.start_worker([r["email"] for r in rows])

    def on_stop_clicked(self) -> None:
        """Ask the runner to stop after the current email(s)."""
        if self.running:
            self._stop_event.set()
            self.root.ids.stop_btn.disabled = True
            self.root.ids.pause_btn.disabled = True
            self.render_run_status()

    def on_pause_clicked(self) -> None:
        """Pause or resume the run (emails already sent to the AI still finish)."""
        if not self.running:
            return
        btn = self.root.ids.pause_btn
        if self._pause_event.is_set():
            self._pause_event.clear()
            if self._pause_started is not None:
                self._paused_total += time.monotonic() - self._pause_started
            self._pause_started = None
            btn.text = "Pause"
        else:
            self._pause_event.set()
            self._pause_started = time.monotonic()
            btn.text = "Resume"
        self.render_run_status()

    def on_row_ready(self, row: Dict[str, Any]) -> None:
        """Add one row from the fast phase (main thread).

        Re-analysed rows replace their existing row in place; new rows are
        appended.
        """
        key = email_key(row.get("email", ""))
        target = self._replace_targets.pop(key, None)
        if target is not None:
            target.update(row)
            self._refresh_trigger()
        else:
            self.table.data.append(row)
            self._rows_by_key[key] = row
        self.mark_dirty()

    def on_row_update(self, key: str, changes: Dict[str, Any]) -> None:
        """Apply an AI verdict or website result to an existing row (main thread).

        A Final Name the user typed while the row was waiting for the AI is
        kept.
        """
        row = self._rows_by_key.get(key)
        if row is None:
            return
        changes = dict(changes)
        if "reason" in changes:
            if row.pop("_pending", False) and (row.get("final_name") or "").strip():
                changes.pop("final_name", None)
        row.update(changes)
        self._refresh_trigger()
        self.mark_dirty()

    def on_phase(self, name: str, count: int) -> None:
        """Note when the AI phase starts (for the time-left estimate)."""
        if name == "ai":
            self._ai_phase_started = self.active_seconds()

    def on_progress(self, processed: int, total: int, ai_done: int = 0,
                    ai_total: int = 0) -> None:
        """Update the progress line (main thread)."""
        self._progress_state = (processed, total, ai_done, ai_total)
        if self.running:
            self.render_run_status()
        else:
            self.set_status(f"Processed {processed} / {total}{self._run_summary}")

    def on_run_finished(self, stats: RunStats) -> None:
        """Re-enable controls, report timing and auto-save (main thread)."""
        elapsed = self.active_seconds()
        if self._timer_event is not None:
            self._timer_event.cancel()
            self._timer_event = None
        self._set_running(False)
        self._worker = None
        self._replace_targets = {}
        self._pause_started = None
        for row in self.table.data:
            row.pop("_pending", None)
            if row.get("email_status") == STATUS_CHECKING:   # stopped mid-lookup
                row["email_status"] = STATUS_NOT_CHECKED
                row["mail_provider"] = "Unknown"
        self._refresh_trigger()
        self.update_counts()

        verb = "Stopped after" if stats.stopped else "Done in"
        parts = [f"{verb} {format_duration(elapsed)}",
                 f"{stats.processed} / {stats.total} processed{self._run_summary}"]
        if stats.dictionary_hits:
            parts.append(f"{stats.dictionary_hits} by dictionary (instant)")
        if stats.ai_done:
            # Active AI time (paused time excluded), from the app's own clock.
            ai_seconds = elapsed - (self._ai_phase_started or 0.0)
            parts.append(f"{stats.ai_done} by AI in {format_duration(ai_seconds)}"
                         f" ({ai_seconds / stats.ai_done:.1f}s each)")
        self.set_status(" | ".join(parts))
        rows = self.snapshot_rows()
        version = self._change_count

        def saved(_path: Any) -> None:
            self._saved_count = version

        _run_in_background(lambda: save_session(rows), saved,
                           lambda exc: self.set_status(f"Could not save session: {exc}"))

    # -- load file -------------------------------------------------------------

    def on_load_file(self) -> None:
        """Pick a file in the background and add its unique emails to the input."""
        if self.dialog_open:
            return
        self.dialog_open = True
        box_text = self.root.ids.email_input.text

        def work() -> Optional[Tuple[str, List[str], ExtractResult]]:
            path = FileDialogs.ask_open_path()
            if not path:
                return None
            current = extract_emails(box_text).emails
            return path, current, read_emails_from_file(path, existing=current)

        def done(result: Optional[Tuple[str, List[str], ExtractResult]]) -> None:
            self.dialog_open = False
            if not result:
                return
            path, current, found = result
            self.set_input_emails(current + found.emails)
            self.set_status(found.summary(Path(path).name) + self.table_overlap_note(found.emails)
                            + ". Press Analyze to process them.")

        def failed(exc: Exception) -> None:
            self.dialog_open = False
            self.message_popup("Load failed", f"Could not read the file:\n{exc}")

        _run_in_background(work, done, failed)

    def on_paste_emails(self, pasted: str) -> None:
        """Clean, deduplicate and merge pasted text into the input box.

        Called by :class:`EmailInput` when the pasted text contains ``@``.
        Extraction runs in the background so huge pastes never freeze the UI.

        Args:
            pasted: Raw clipboard text.
        """
        box_text = self.root.ids.email_input.text

        def work() -> Tuple[List[str], ExtractResult]:
            current = extract_emails(box_text).emails
            return current, extract_emails(pasted, existing=current)

        def done(result: Tuple[List[str], ExtractResult]) -> None:
            current, found = result
            self.set_input_emails(current + found.emails)
            self.set_status(found.summary("Pasted") + self.table_overlap_note(found.emails))

        _run_in_background(work, done,
                           lambda exc: self.set_status(f"Could not read pasted text: {exc}"))

    def set_input_emails(self, emails: List[str]) -> None:
        """Replace the input box with one email per line."""
        box = self.root.ids.email_input
        box.text = "\n".join(emails) + ("\n" if emails else "")
        box.cursor = (0, len(emails))

    def table_overlap_note(self, emails: List[str]) -> str:
        """Note how many of ``emails`` are already in the results table."""
        if not self.table.data or not emails:
            return ""
        in_table = {email_key(r.get("email", "")) for r in self.table.data}
        overlap = sum(1 for e in emails if email_key(e) in in_table)
        return f" ({overlap} already in the results table)" if overlap else ""

    # -- export ------------------------------------------------------------------

    def on_export(self) -> None:
        """Show export options (format, providers, filters, files), then save."""
        if self.dialog_open:
            return
        rows = self.snapshot_rows()
        if not rows:
            self.message_popup("Export CSV", "There are no results to export yet.")
            return
        resolved, unresolved = count_resolved(rows)
        undeliverable = sum(1 for r in rows if is_undeliverable(r))
        providers = provider_counts(rows)

        outer = BoxLayout(orientation="vertical", padding=dp(10), spacing=dp(8))
        box = BoxLayout(orientation="vertical", spacing=dp(6), size_hint_y=None,
                        padding=(dp(4), dp(4), dp(14), dp(4)))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(Label(
            text=f"[b]{len(rows)}[/b] rows: {resolved} resolved, {unresolved} not resolved, "
                 f"{undeliverable} undeliverable.", markup=True, size_hint_y=None,
            height=dp(26)))

        def heading(text: str) -> None:
            label = Label(text=f"[b]{text}[/b]", markup=True, size_hint_y=None,
                          height=dp(24), halign="left", valign="middle")
            label.bind(size=lambda w, s: setattr(w, "text_size", s))
            box.add_widget(label)

        def option_row(widget: Any, text: str, hint: str = "", height: float = 46) -> BoxLayout:
            row = BoxLayout(size_hint_y=None, height=dp(height), spacing=dp(6))
            widget.size_hint_x = None
            widget.width = dp(40)
            row.add_widget(widget)
            extra = f"\n[size=12sp][color=a0a0a0]{hint}[/color][/size]" if hint else ""
            label = Label(text=f"{text}{extra}", markup=True, halign="left", valign="middle")
            label.bind(size=lambda w, s: setattr(w, "text_size", s))
            row.add_widget(label)
            return row

        heading("Format")
        mailing_cb = CheckBox(group="export_format", allow_no_selection=False,
                              active=self.settings.export_format == FORMAT_MAILING)
        full_cb = CheckBox(group="export_format", allow_no_selection=False,
                           active=self.settings.export_format != FORMAT_MAILING)
        box.add_widget(option_row(mailing_cb, "Email sender list",
                                  "Email Address, First/Last/Full Name, Company Website, "
                                  "Mail Provider, Email Status"))
        box.add_widget(option_row(full_cb, "Full analysis", "every column"))

        heading("Mail providers to include")
        provider_boxes: Dict[str, CheckBox] = {}
        grid = GridLayout(cols=2, size_hint_y=None, row_default_height=dp(34),
                          row_force_default=True, spacing=(dp(8), 0))
        grid.bind(minimum_height=grid.setter("height"))
        for label, count in providers:
            cb = CheckBox(active=True)
            provider_boxes[label] = cb
            grid.add_widget(option_row(cb, f"{label}  ({count})", height=34))
        scroll = ScrollView(size_hint_y=None, height=min(dp(34) * ((len(providers) + 1) // 2),
                                                        dp(150)))
        scroll.add_widget(grid)
        box.add_widget(scroll)
        quick = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
        for text, value in (("Select all", True), ("Select none", False)):
            btn = Button(text=text, size_hint_x=None, width=dp(120))
            btn.bind(on_release=lambda _b, v=value: [setattr(c, "active", v)
                                                      for c in provider_boxes.values()])
            quick.add_widget(btn)
        quick.add_widget(Label())
        box.add_widget(quick)

        heading("Rows and files")
        only_cb = CheckBox(active=self.settings.export_only_resolved)
        skip_cb = CheckBox(active=True)
        sort_cb = CheckBox(active=True)
        split_cb = CheckBox(active=False)
        box.add_widget(option_row(only_cb, "Remove unresolved names",
                                  f"skip rows without a Final Name ({unresolved})"))
        box.add_widget(option_row(skip_cb, "Remove undeliverable emails",
                                  f"invalid, domain doesn't exist, no mail server, "
                                  f"disposable ({undeliverable})"))
        box.add_widget(option_row(sort_cb, "Sort by mail provider and domain"))
        box.add_widget(option_row(split_cb, "One file per mail provider (as a .zip)",
                                  "a ZIP containing email_list_gmail.csv, "
                                  "email_list_microsoft_365.csv, ..."))
        body = ScrollView(bar_width=dp(8), scroll_type=["bars", "content"])
        body.add_widget(box)
        outer.add_widget(body)

        buttons = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        popup = self.fit_popup(Popup(title="Export CSV", content=outer,
                                     size_hint=(None, 0.94)), dp(720))

        def go(*_: Any) -> None:
            chosen = {label for label, cb in provider_boxes.items() if cb.active}
            if not chosen:
                self.message_popup("Export CSV", "Select at least one mail provider.")
                return
            export_format = FORMAT_MAILING if mailing_cb.active else FORMAT_FULL
            selection = ExportSelection(
                only_resolved=bool(only_cb.active),
                skip_undeliverable=bool(skip_cb.active),
                providers=None if len(chosen) == len(provider_boxes) else chosen,
                sort_by_domain=bool(sort_cb.active))
            popup.dismiss()
            self.settings.export_format = export_format
            self.settings.export_only_resolved = selection.only_resolved
            snapshot = Settings(**vars(self.settings))
            _run_in_background(lambda: save_settings(snapshot))
            self.run_export(rows, export_format, selection, bool(split_cb.active))

        export_btn = Button(text="Export...", bold=True)
        export_btn.bind(on_release=go)
        cancel_btn = Button(text="Cancel")
        cancel_btn.bind(on_release=popup.dismiss)
        buttons.add_widget(export_btn)
        buttons.add_widget(cancel_btn)
        outer.add_widget(buttons)
        popup.open()

    def run_export(self, rows: List[Dict[str, Any]], export_format: str,
                   selection: ExportSelection, per_provider: bool = False) -> None:
        """Filter rows, ask for a path and write the CSV file(s) in the background.

        Args:
            rows: Snapshot of the table rows.
            export_format: ``"mailing"`` or ``"full"``.
            selection: Which rows to include and how to sort them.
            per_provider: Write one CSV per mail provider, packed into a
                single ``.zip``, instead of one combined CSV.
        """
        kept, skipped = select_rows(rows, selection)
        if not kept:
            self.message_popup("Export CSV", "No rows left to export with these options.")
            return
        self.dialog_open = True
        default_name = default_export_filename(export_format=export_format)
        writer = export_mailing_csv if export_format == FORMAT_MAILING else export_rows_to_csv

        def work() -> Optional[Tuple[str, int, List[str]]]:
            if not per_provider:
                path = FileDialogs.ask_save_path(default_name)
                return (path, writer(kept, path), []) if path else None
            # One CSV per provider, packed into a single .zip.
            path = FileDialogs.ask_save_path(str(Path(default_name).with_suffix(".zip")), ".zip")
            if not path:
                return None
            contents = export_provider_zip(group_by_provider(kept), path, export_format)
            return path, sum(contents.values()), [f"{name}: {n} email(s)"
                                                  for name, n in contents.items()]

        def done(result: Optional[Tuple[str, int, List[str]]]) -> None:
            self.dialog_open = False
            if not result:
                return
            path, count, files = result
            notes = [f"{n} {label} skipped" for label, n in (
                ("unresolved", skipped["unresolved"]),
                ("undeliverable", skipped["undeliverable"]),
                ("other-provider", skipped["provider"])) if n]
            self.export_success_popup(path, count, "; ".join(notes), files)

        def failed(exc: Exception) -> None:
            self.dialog_open = False
            self.message_popup("Export failed", f"Could not write the CSV:\n{exc}")

        _run_in_background(work, done, failed)

    def export_success_popup(self, path: str, count: int, notes: str = "",
                             contents: Optional[List[str]] = None) -> None:
        """Show what was exported and where, with an 'Open folder' button.

        Args:
            path: The file written (CSV or ZIP).
            count: Rows written in total.
            notes: Skipped-row summary.
            contents: For a ZIP, one line per file inside it.
        """
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        lines = [f"[b]Exported {count} email(s)[/b] to:", escape_markup(path)]
        if contents:
            lines += ["", f"[b]Inside the ZIP ({len(contents)} files):[/b]"]
            lines += [f"  {escape_markup(c)}" for c in contents]
        if notes:
            lines += ["", f"[color=a0a0a0]{escape_markup(notes)}[/color]"]
        label = Label(text="\n".join(lines), markup=True, halign="left", valign="top",
                      size_hint_y=None)
        label.bind(width=lambda w, width: setattr(w, "text_size", (width, None)),
                   texture_size=lambda w, ts: setattr(w, "height", ts[1]))
        scroll = ScrollView(bar_width=dp(8))
        scroll.add_widget(label)
        box.add_widget(scroll)
        buttons = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        popup = self.fit_popup(Popup(title="Export complete", content=box,
                                     size_hint=(None, None)),
                               dp(680), dp(420) if contents else dp(260))
        target = Path(path).resolve()
        folder = str(target if target.is_dir() else target.parent)
        open_btn = Button(text="Open folder")
        open_btn.bind(on_release=lambda *_: _run_in_background(lambda: _open_folder(folder)))
        close_btn = Button(text="Close")
        close_btn.bind(on_release=popup.dismiss)
        buttons.add_widget(open_btn)
        buttons.add_widget(close_btn)
        box.add_widget(buttons)
        popup.open()

    # -- clear ---------------------------------------------------------------------

    def on_clear(self) -> None:
        """Ask for confirmation, then wipe the table and the saved session."""
        if self.running:
            return
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        box.add_widget(Label(text="Clear all results and delete the saved session?"))
        buttons = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        popup = self.fit_popup(Popup(title="Confirm clear", content=box,
                                     size_hint=(None, None), auto_dismiss=False),
                               dp(460), dp(180))

        def confirm(*_: Any) -> None:
            popup.dismiss()
            self.table.data = []
            self.reindex_rows()
            self._change_count = self._saved_count = 0
            self.update_counts()
            self.set_status("Cleared.")
            _run_in_background(clear_session, None,
                               lambda exc: self.set_status(f"Could not delete session: {exc}"))

        yes = Button(text="Clear", background_color=(0.9, 0.4, 0.4, 1))
        yes.bind(on_release=confirm)
        no = Button(text="Cancel")
        no.bind(on_release=popup.dismiss)
        buttons.add_widget(yes)
        buttons.add_widget(no)
        box.add_widget(buttons)
        popup.open()

    # -- details -------------------------------------------------------------------

    def show_row_details(self, row: Dict[str, Any]) -> None:
        """Show the full reason and other details for one row."""
        website = row.get("company_website") or "(none)"
        if row.get("company_website") and not row.get("website_verified"):
            website += " (unverified)"
        text = (
            f"[b]Email:[/b] {escape_markup(row.get('email', ''))}\n"
            f"[b]Is real name:[/b] {'yes' if row.get('is_real_name') else 'no'}"
            f"    [b]Confidence:[/b] {row.get('confidence', 0)}\n"
            f"[b]Final name:[/b] {escape_markup(row.get('final_name', '') or '(empty)')}\n"
            f"[b]Name parts:[/b] {escape_markup(' | '.join(row.get('name_parts') or []))}\n"
            f"[b]Website:[/b] {escape_markup(website)}\n"
            f"[b]Mail provider:[/b] {escape_markup(row.get('mail_provider') or '(not checked)')}"
            f"    [b]Email status:[/b] {escape_markup(row.get('email_status') or '(not checked)')}\n\n"
            f"[b]Reason:[/b]\n{escape_markup(row.get('reason', ''))}"
        )
        label = Label(text=text, markup=True, halign="left", valign="top",
                      size_hint_y=None)
        label.bind(width=lambda w, width: setattr(w, "text_size", (width, None)),
                   texture_size=lambda w, ts: setattr(w, "height", ts[1]))
        scroll = ScrollView()
        scroll.add_widget(label)
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        box.add_widget(scroll)
        popup = self.fit_popup(Popup(title="Row details", content=box,
                                     size_hint=(None, None)), dp(680), dp(420))
        buttons = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        teach_yes = Button(text="Teach: correct name")
        teach_yes.bind(on_release=lambda *_: (popup.dismiss(), self.teach_name(row)))
        teach_no = Button(text="Teach: not a person")
        teach_no.bind(on_release=lambda *_: (popup.dismiss(), self.teach_not_a_name(row)))
        close = Button(text="Close")
        close.bind(on_release=popup.dismiss)
        for btn in (teach_yes, teach_no, close):
            buttons.add_widget(btn)
        box.add_widget(buttons)
        popup.open()

    # -- teaching the dictionary -------------------------------------------------------

    def _apply_row_update(self, row: Dict[str, Any], **changes: Any) -> None:
        """Update one row in place and redraw the table."""
        row.update(changes)
        self.table.refresh_from_data()
        self.mark_dirty()

    def teach_name(self, row: Dict[str, Any]) -> None:
        """Add the row's Final Name to the user's dictionary and confirm the row.

        The first part goes to first names and the last part to surnames.
        Initials and particles are skipped.
        """
        raw_parts = (row.get("final_name") or "").split()
        if not raw_parts:
            self.message_popup("Teach: correct name",
                               "Type the correct name in the Final Name column first, "
                               "then use this button again.")
            return
        parts = [p.strip(".,") for p in raw_parts]
        first = clean_name(parts[0])
        last = clean_name(parts[-1]) if len(parts) > 1 else ""
        if last in PARTICLES:
            last = ""
        store = self.dictionary_store

        def work() -> int:
            added = 0
            if first:
                added += add_user_words(store.user_dir, KIND_FIRST, [first])
            if last:
                added += add_user_words(store.user_dir, KIND_SURNAME, [last])
            store.invalidate()
            store.get()
            return added

        def done(added: int) -> None:
            self._apply_row_update(
                row, is_real_name=True, confidence=100,
                name_parts=[p for p in parts if p],
                reason="confirmed by user (saved to your dictionary)")
            learned = ", ".join(x for x in (first and f"first name '{first}'",
                                             last and f"surname '{last}'") if x)
            self.set_status(f"Saved to your dictionary: {learned}." if added
                            else "Row confirmed. These names were already in your dictionary.")

        _run_in_background(work, done,
                           lambda exc: self.message_popup("Teach failed", str(exc)))

    def teach_not_a_name(self, row: Dict[str, Any]) -> None:
        """Mark the row as not a person and block its unknown words.

        Only words that are not known first names or surnames are added to the
        "not a name" list, so a real name is never blocked by accident.
        """
        store = self.dictionary_store
        tokens = split_email_local(row.get("email", "")).get("tokens") or []

        def work() -> List[str]:
            dictionary = store.get()
            words = [t for t in tokens
                     if t not in dictionary.first and t not in dictionary.surnames]
            if words:
                add_user_words(store.user_dir, KIND_ROLE, words)
                store.invalidate()
                store.get()
            return words

        def done(words: List[str]) -> None:
            self._apply_row_update(row, is_real_name=False, confidence=0, final_name="",
                                   name_parts=[], reason=USER_NOT_PERSON_REASON)
            if words:
                self.set_status(f"Added to your 'not a name' list: {', '.join(words)}.")
            else:
                self.set_status("Row marked as not a person. Nothing added to the list, because "
                                "its words are known names (blocking them would hide real people).")

        _run_in_background(work, done,
                           lambda exc: self.message_popup("Teach failed", str(exc)))

    def import_names(self, kind: str) -> None:
        """Pick a .txt/.csv of names and add them to one of the user's lists.

        Args:
            kind: ``"first"``, ``"surname"`` or ``"role"``.
        """
        if self.dialog_open:
            return
        self.dialog_open = True
        store = self.dictionary_store
        labels = {KIND_FIRST: "first names", KIND_SURNAME: "surnames",
                  KIND_ROLE: "'not a name' words"}

        def work() -> Optional[Tuple[int, int]]:
            path = FileDialogs.ask_open_path()
            if not path:
                return None
            text = read_file_text(path)
            names = list(parse_word_lines(text.splitlines()))
            added = add_user_words(store.user_dir, kind, names)
            store.invalidate()
            store.get()
            return len(names), added

        def done(result: Optional[Tuple[int, int]]) -> None:
            self.dialog_open = False
            if result is None:
                return
            found, added = result
            self.message_popup("Import names",
                               f"Read {found} {labels[kind]} from the file.\n"
                               f"Added {added} new ones to your dictionary "
                               f"({found - added} were already there).\n\n"
                               "They are used from the next Analyze.")

        def failed(exc: Exception) -> None:
            self.dialog_open = False
            self.message_popup("Import failed", f"Could not import the file:\n{exc}")

        _run_in_background(work, done, failed)

    def choose_import_kind(self) -> None:
        """Ask which list an imported file should be added to."""
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(8))
        box.add_widget(Label(text="Add names from a .txt or .csv file (one per line,\n"
                                  "or first column) to which list?", halign="center"))
        popup = self.fit_popup(Popup(title="Import names", content=box,
                                     size_hint=(None, None)), dp(460), dp(320))
        for text, kind in (("First names", KIND_FIRST), ("Surnames", KIND_SURNAME),
                           ("Not-a-name words (e.g. info, coolguy)", KIND_ROLE)):
            btn = Button(text=text, size_hint_y=None, height=dp(40))
            btn.bind(on_release=lambda _b, k=kind: (popup.dismiss(), self.import_names(k)))
            box.add_widget(btn)
        cancel = Button(text="Cancel", size_hint_y=None, height=dp(40))
        cancel.bind(on_release=popup.dismiss)
        box.add_widget(cancel)
        popup.open()

    def open_dictionary_folder(self) -> None:
        """Open the user's dictionary folder in Explorer."""
        store = self.dictionary_store
        _run_in_background(lambda: _open_folder(str(store.user_dir)), None,
                           lambda exc: self.message_popup("Open folder failed", str(exc)))

    # -- settings ------------------------------------------------------------------

    def open_setup_guide(self) -> None:
        """Open the step-by-step Ollama install and model setup guide."""
        open_setup_guide(self, logo=asset_path("logo.png"))

    def test_ollama_connection(self, report: Callable[[str], None], url: str = "",
                               model: str = "") -> None:
        """Check Ollama and the model in the background and report a message.

        Args:
            report: Receives a human-readable result on the main thread.
            url: Base URL to test (defaults to the saved setting).
            model: Model name to look for (defaults to the saved setting).
        """
        url = (url or self.settings.base_url).rstrip("/")
        model = model or self.settings.model

        def message(models: Optional[List[str]]) -> None:
            if models is None:
                report(f"Ollama not reachable at {url}. Is Ollama installed and running? "
                       "(see Setup guide, Step 2)")
            elif model_is_pulled(model, models):
                report(f"Connected. Model '{model}' is available.")
            elif models:
                report(f"Connected, but '{model}' is not downloaded. Run: ollama pull {model}"
                       f"  (installed: {', '.join(models[:3])}{'...' if len(models) > 3 else ''})")
            else:
                report(f"Connected, but no models are downloaded yet. Run: ollama pull {model}")

        _run_in_background(lambda: list_local_models(url), message)

    def open_settings(self) -> None:
        """Open the settings popup (model, URL, toggles, name-list tools)."""
        def note(text: str) -> Label:
            """Grey helper text that shortens instead of overflowing."""
            label = Label(text=text, color=(0.7, 0.7, 0.7, 1), halign="left",
                          valign="middle", shorten=True, shorten_from="right")
            label.bind(size=lambda w, s: setattr(w, "text_size", s))
            return label

        def with_note(widget: Any, text: str) -> BoxLayout:
            row = BoxLayout(spacing=dp(8))
            row.add_widget(widget)
            row.add_widget(note(text))
            return row

        grid = GridLayout(cols=2, spacing=dp(8), size_hint_y=None, row_default_height=dp(40),
                          row_force_default=True, padding=(0, 0, dp(12), 0))
        grid.bind(minimum_height=grid.setter("height"))
        model_in = TextInput(text=self.settings.model, multiline=False, write_tab=False)
        url_in = TextInput(text=self.settings.base_url, multiline=False, write_tab=False)
        website_cb = CheckBox(active=self.settings.resolve_website, size_hint_x=None,
                              width=dp(40))
        website_row = with_note(website_cb, "(slower: one web request per new domain)")
        dict_cb = CheckBox(active=self.settings.use_dictionary, size_hint_x=None,
                           width=dp(40))
        dict_row = with_note(dict_cb, "(confident matches skip the AI)")
        domains_cb = CheckBox(active=self.settings.check_domains, size_hint_x=None, width=dp(40))
        domains_row = with_note(domains_cb,
                                "(Google Workspace / Microsoft 365 / no mail server...)")
        dns_names = {"cloudflare": "Cloudflare (1.1.1.1)", "google": "Google (8.8.8.8)"}
        dns_in = Spinner(text=dns_names[self.settings.dns_service],
                         values=list(dns_names.values()), size_hint_x=None, width=dp(200))
        dns_row = with_note(dns_in, "(only domain names are sent)")
        parallel_in = Spinner(text=str(self.settings.ai_parallel),
                              values=["1", "2", "3", "4"], size_hint_x=None, width=dp(70))
        parallel_row = with_note(parallel_in,
                                 "(2-4 is faster with a graphics card; keep 1 on CPU)")
        scale_in = Spinner(text=self.settings.ui_scale, values=list(UI_SCALE_CHOICES),
                           size_hint_x=None, width=dp(100))
        scale_row = with_note(scale_in, "(smaller = more fits on screen; applies after restart)")
        dict_buttons = BoxLayout(spacing=dp(8))
        import_btn = Button(text="Import names...")
        import_btn.bind(on_release=lambda *_: self.choose_import_kind())
        folder_btn = Button(text="Open dictionary folder")
        folder_btn.bind(on_release=lambda *_: self.open_dictionary_folder())
        dict_buttons.add_widget(import_btn)
        dict_buttons.add_widget(folder_btn)
        for text, widget in (("Ollama model", model_in), ("Ollama base URL", url_in),
                             ("Resolve company website", website_row),
                             ("Use name dictionary", dict_row),
                             ("AI emails at once", parallel_row),
                             ("Detect provider & verify", domains_row),
                             ("DNS lookup service", dns_row),
                             ("Interface size", scale_row),
                             ("Your name lists", dict_buttons)):
            label = Label(text=text, size_hint_x=None, width=dp(180),
                          halign="left", valign="middle")
            label.bind(size=lambda w, s: setattr(w, "text_size", s))
            grid.add_widget(label)
            grid.add_widget(widget)

        scroll = ScrollView(bar_width=dp(8), scroll_type=["bars", "content"])
        scroll.add_widget(grid)
        info = Label(text="", size_hint_y=None, height=dp(30), color=(0.8, 0.85, 1, 1),
                     halign="left", valign="middle", shorten=True)
        info.bind(size=lambda w, s: setattr(w, "text_size", s))
        box = BoxLayout(orientation="vertical", padding=dp(12), spacing=dp(10))
        box.add_widget(scroll)
        box.add_widget(info)
        buttons = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        popup = self.fit_popup(Popup(title="Settings", content=box, size_hint=(None, None)),
                               dp(820), dp(560))

        def test_connection(*_: Any) -> None:
            info.text = "Checking..."
            self.test_ollama_connection(lambda msg: setattr(info, "text", msg),
                                        url=url_in.text.strip(), model=model_in.text.strip())

        def show_guide(*_: Any) -> None:
            popup.dismiss()
            self.open_setup_guide()

        old_scale = self.settings.ui_scale

        def scale_changed() -> bool:
            return scale_in.text != old_scale

        def save(*_: Any) -> None:
            url = url_in.text.strip().rstrip("/")
            if not url.startswith(("http://", "https://")):
                info.text = "Base URL must start with http:// or https://"
                return
            self.settings = Settings(model=model_in.text.strip() or Settings.model,
                                     base_url=url,
                                     resolve_website=bool(website_cb.active),
                                     use_dictionary=bool(dict_cb.active),
                                     ai_parallel=int(parallel_in.text),
                                     check_domains=bool(domains_cb.active),
                                     dns_service=next(k for k, v in dns_names.items()
                                                      if v == dns_in.text),
                                     export_format=self.settings.export_format,
                                     export_only_resolved=self.settings.export_only_resolved,
                                     ui_scale=scale_in.text)
            snapshot = Settings(**vars(self.settings))
            popup.dismiss()
            saved = ("Settings saved. Restart the app to apply the new interface size."
                     if scale_changed() else "Settings saved.")
            _run_in_background(lambda: save_settings(snapshot),
                               lambda _p: self.set_status(saved),
                               lambda exc: self.set_status(f"Could not save settings: {exc}"))

        for text, handler in (("Setup guide", show_guide),
                              ("Test connection", test_connection), ("Save", save),
                              ("Cancel", lambda *_: popup.dismiss())):
            btn = Button(text=text)
            btn.bind(on_release=handler)
            buttons.add_widget(btn)
        box.add_widget(buttons)
        popup.open()


def main() -> None:
    """Run the application."""
    EmailNameResolverApp().run()


if __name__ == "__main__":
    main()
