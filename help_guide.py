"""In-app "AI setup guide": how to install Ollama and connect a model.

The guide is plain data (:data:`GUIDE`) rendered by :func:`open_setup_guide`
into a scrollable popup with Copy buttons for commands, link buttons and
action buttons (Test connection, Open Settings).

Facts checked against Ollama's documentation (docs.ollama.com/windows and
the project README), October 2026.
"""

from __future__ import annotations

import os
import threading
import webbrowser
from typing import Any, List, Tuple

from kivy.core.clipboard import Clipboard
from kivy.metrics import dp

import version
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView

DOWNLOAD_URL = "https://ollama.com/download"
LIBRARY_URL = "https://ollama.com/library"

# Block types:
#   ("h", title)                   section heading
#   ("p", text)                    paragraph (Kivy markup allowed)
#   ("cmd", command)               command with a Copy button
#   ("link", label, url)           button that opens a web page
#   ("action", label, name)        button calling a named guide action
Block = Tuple[str, ...]

GUIDE: List[Block] = [
    ("p", "This guide sets up the [b]AI judge[/b] the app uses for emails the name "
          "dictionary can't resolve on its own (for example non-US names). Everything "
          "runs on your own computer: no account, no API key, no cost, and your email "
          "lists never leave the machine.\n\n"
          "[b]Without the AI the app still works[/b]: dictionary matches and generic "
          "mailboxes are still resolved, and the rest get a rule-based split you can "
          "edit by hand."),

    ("h", "Step 1. Check whether Ollama is already installed"),
    ("p", "Press [b]Windows key + R[/b], type [b]cmd[/b] and press Enter to open "
          "Command Prompt. Then type this and press Enter:"),
    ("cmd", "ollama --version"),
    ("p", "[b]If you see a version number[/b] (e.g. \"ollama version is 0.x.x\"), Ollama "
          "is installed: skip to Step 3.\n"
          "[b]If you see[/b] \"'ollama' is not recognized as an internal or external "
          "command\", it isn't installed (or the window was opened before installing): "
          "go to Step 2."),

    ("h", "Step 2. Install Ollama (about 5 minutes)"),
    ("p", "[b]You need:[/b]\n"
          "  - Windows 10 (version 22H2) or newer, Home or Pro\n"
          "  - At least 8 GB of RAM for the recommended model (4 GB can run a smaller one)\n"
          "  - About 10 GB of free disk space (Ollama itself ~4 GB, plus 2 to 5 GB per model)\n"
          "  - Optional: a graphics card makes it much faster. NVIDIA cards need "
          "driver 551.61 or newer. Without one it runs on the processor, just slower."),
    ("p", "[b]2.1[/b]  Open the download page and click [b]Download for Windows[/b]:"),
    ("link", "Open ollama.com/download", DOWNLOAD_URL),
    ("p", "[b]2.2[/b]  Run the downloaded file [b]OllamaSetup.exe[/b] and click "
          "[b]Install[/b]. It does not need administrator rights.\n"
          "[b]2.3[/b]  When it finishes, Ollama runs in the background. You'll see a "
          "small [b]llama icon[/b] in the system tray (bottom-right, next to the clock; "
          "click the ^ arrow if it's hidden). It starts automatically with Windows.\n"
          "[b]2.4[/b]  Close any Command Prompt windows, open a [b]new[/b] one "
          "(Windows key + R, cmd) and check it worked:"),
    ("cmd", "ollama --version"),

    ("h", "Step 3. Download a model"),
    ("p", "A \"model\" is the AI brain Ollama runs. Download it once; it stays on "
          "your computer. In Command Prompt run the line for the model you want "
          "(the download shows a progress bar and ends with [b]success[/b]):"),
    ("p", "[b]Recommended[/b]: Llama 3.1, 8B, 4.7 GB download, needs ~8 GB RAM. "
          "Good accuracy for this job."),
    ("cmd", "ollama pull llama3.1"),
    ("p", "[b]Lighter / older computers[/b]: Llama 3.2, 3B, 2.0 GB download, ~4 GB "
          "RAM. Faster, slightly less accurate."),
    ("cmd", "ollama pull llama3.2"),
    ("p", "[b]Alternative[/b]: Mistral, 7B, 4.1 GB download, ~8 GB RAM."),
    ("cmd", "ollama pull mistral"),
    ("p", "To see every model you've downloaded and its exact name:"),
    ("cmd", "ollama list"),
    ("p", "Example output:\n"
          "[font=RobotoMono-Regular]NAME               ID              SIZE      MODIFIED\n"
          "llama3.1:latest    (an ID)         4.9 GB    2 minutes ago[/font]\n\n"
          "If you already had another model listed there, you can use it instead of "
          "downloading a new one: just use its full name in Step 4. Any instruction-"
          "following model of 3B or more works; browse more at the Ollama library:"),
    ("link", "Open the Ollama model library", LIBRARY_URL),

    ("h", "Step 4. Connect the model to this app"),
    ("p", "[b]4.1[/b]  Click [b]Settings[/b] (top-right of the main window).\n"
          "[b]4.2[/b]  In [b]Ollama model[/b], type the model name exactly as "
          "[b]ollama list[/b] shows it. For the defaults: [b]llama3.1[/b] "
          "(\":latest\" can be left off). For other models use the full name, e.g. "
          "[b]huihui_ai/dolphin3-abliterated:8b-llama3.1-q4_K_M[/b].\n"
          "[b]4.3[/b]  Leave [b]Ollama base URL[/b] as [b]http://localhost:11434[/b] "
          "(only change it if Ollama runs on another computer, see Troubleshooting).\n"
          "[b]4.4[/b]  Click [b]Test connection[/b]. You should see: "
          "\"Connected. Model '...' is available.\"\n"
          "[b]4.5[/b]  Click [b]Save[/b]."),
    ("action", "Open Settings now", "settings"),
    ("action", "Test the connection with my current settings", "test"),

    ("h", "Step 5. Check it's working"),
    ("p", "Paste a few emails such as [b]oluwaseun.adeyemi@example.com[/b] and "
          "[b]coolguy123@gmail.com[/b] and click [b]Analyze[/b]. If the AI is working:\n"
          "  - the orange \"Ollama not reachable\" banner does [b]not[/b] appear;\n"
          "  - those rows show an AI reason (not \"ollama unavailable\").\n"
          "The first email can take 10 to 30 seconds while the model loads into memory; "
          "after that each one takes about 1 to 5 seconds (faster with a graphics card). "
          "If you analysed while Ollama was off, use [b]Re-analyze unresolved[/b] to "
          "retry just those rows."),

    ("h", "Troubleshooting"),
    ("p", "[b]\"'ollama' is not recognized...\"[/b]\n"
          "Open a new Command Prompt after installing. If it still fails, run it by "
          "its full path:"),
    ("cmd", "\"%LOCALAPPDATA%\\Programs\\Ollama\\ollama.exe\" list"),
    ("p", "[b]Orange banner \"Ollama not reachable\" / Test connection fails[/b]\n"
          "Ollama isn't running. Start [b]Ollama[/b] from the Start menu (the llama "
          "icon appears in the tray), wait a few seconds and test again. Or start it "
          "by hand in a Command Prompt and leave that window open:"),
    ("cmd", "ollama serve"),
    ("p", "[b]\"Model not found\" banner[/b]\n"
          "The name in Settings doesn't match a downloaded model. Run "
          "[b]ollama list[/b] and copy the name exactly (spelling, tag after the "
          "colon), or download it with [b]ollama pull <name>[/b].\n\n"
          "[b]Very slow, or the computer freezes[/b]\n"
          "The model is too big for the computer's memory. Switch to the lighter "
          "model ([b]ollama pull llama3.2[/b], then set the model to "
          "[b]llama3.2[/b] in Settings). Closing other heavy programs helps too. "
          "On a computer with a graphics card, setting [b]AI emails at once[/b] to 2 to 4 "
          "in Settings speeds things up; without one, keep it at 1.\n\n"
          "[b]Not enough disk space on C:[/b]\n"
          "Models are stored in your user folder ([b]%USERPROFILE%\\.ollama[/b]). To "
          "use another drive, search Windows for \"environment variables\", choose "
          "[b]Edit environment variables for your account[/b], add a variable named "
          "[b]OLLAMA_MODELS[/b] with a folder such as [b]D:\\ollama-models[/b], then "
          "quit Ollama from the tray icon and start it again.\n\n"
          "[b]Using Ollama on another computer (e.g. a server)[/b]\n"
          "Simplest and safest: install and run this app on that same computer, so "
          "the address stays http://localhost:11434. If it must be remote: on that "
          "computer set the environment variable [b]OLLAMA_HOST[/b] to "
          "[b]0.0.0.0[/b], restart Ollama, allow port 11434 in Windows Firewall, then "
          "in this app's Settings use [b]http://<that computer's IP>:11434[/b]. "
          "[color=f0a060]Ollama has no password: only allow your own IP address in "
          "the firewall, never the whole internet.[/color]\n\n"
          "[b]Uninstalling[/b]\n"
          "Windows Settings > Apps > Installed apps > Ollama > Uninstall. To also "
          "free the model space, delete the [b]%USERPROFILE%\\.ollama[/b] folder."),
]


def _wrap_label(text: str, **kwargs: Any) -> Label:
    """Create a markup Label that wraps to its width and grows to fit."""
    label = Label(text=text, markup=True, halign="left", valign="top",
                  size_hint_y=None, **kwargs)
    label.bind(width=lambda w, width: setattr(w, "text_size", (width, None)),
               texture_size=lambda w, ts: setattr(w, "height", ts[1] + dp(4)))
    return label


def _command_row(command: str, on_copied: Any) -> BoxLayout:
    """A monospace command with a Copy button."""
    row = BoxLayout(size_hint_y=None, height=dp(38), spacing=dp(8), padding=(dp(12), 0))
    cmd = Label(text=command, font_name="RobotoMono-Regular", halign="left",
                valign="middle", color=(0.6, 0.95, 0.6, 1), font_size="14sp")
    cmd.bind(size=lambda w, s: setattr(w, "text_size", s))
    copy = Button(text="Copy", size_hint_x=None, width=dp(80))

    def do_copy(*_: Any) -> None:
        Clipboard.copy(command)
        on_copied(command)

    copy.bind(on_release=do_copy)
    row.add_widget(cmd)
    row.add_widget(copy)
    return row


def open_setup_guide(app: Any, logo: str = "") -> None:
    """Open the AI setup guide popup.

    Args:
        app: The running app; provides ``open_settings``, ``set_status`` and
            ``test_ollama_connection(callback)``.
        logo: Optional path to the app logo shown at the top of the guide.
    """
    content = BoxLayout(orientation="vertical", spacing=dp(6), padding=dp(10))
    status = Label(text="", size_hint_y=None, height=dp(24), color=(0.8, 0.85, 1, 1))

    body = BoxLayout(orientation="vertical", spacing=dp(8), size_hint_y=None,
                     padding=(dp(6), dp(6), dp(16), dp(6)))
    body.bind(minimum_height=body.setter("height"))
    popup = Popup(title="AI setup guide: install Ollama and connect a model",
                  content=content, size_hint=(0.9, 0.92))

    def copied(command: str) -> None:
        status.text = f"Copied: {command}  (paste in Command Prompt with Ctrl+V)"

    def run_action(name: str) -> None:
        if name == "settings":
            popup.dismiss()
            app.open_settings()
        elif name == "test":
            status.text = "Checking the connection..."
            app.test_ollama_connection(lambda message: setattr(status, "text", message))

    if logo and os.path.exists(logo):
        header = BoxLayout(size_hint_y=None, height=dp(96), spacing=dp(14))
        header.add_widget(Image(source=logo, size_hint_x=None, width=dp(96),
                                fit_mode="contain"))
        title = Label(text=f"[size=22sp][b]{version.APP_NAME}[/b][/size]  "
                           f"[color=a0a0a0]{version.about_text()}[/color]\n"
                           "[color=8be28b]AI setup guide[/color]",
                      markup=True, halign="left", valign="middle")
        title.bind(size=lambda w, s: setattr(w, "text_size", s))
        header.add_widget(title)
        body.add_widget(header)

    for block in GUIDE:
        kind = block[0]
        if kind == "h":
            body.add_widget(_wrap_label(f"[size=18sp][b]{block[1]}[/b][/size]",
                                        color=(0.55, 0.8, 1, 1)))
        elif kind == "p":
            body.add_widget(_wrap_label(block[1]))
        elif kind == "cmd":
            body.add_widget(_command_row(block[1], copied))
        elif kind == "link":
            url = block[2]
            btn = Button(text=block[1], size_hint=(None, None), height=dp(38), width=dp(320))
            btn.bind(on_release=lambda *_, u=url: threading.Thread(
                target=webbrowser.open, args=(u,), daemon=True).start())
            body.add_widget(btn)
        elif kind == "action":
            btn = Button(text=block[1], size_hint=(None, None), height=dp(38), width=dp(420))
            btn.bind(on_release=lambda *_, n=block[2]: run_action(n))
            body.add_widget(btn)

    scroll = ScrollView(bar_width=dp(10), scroll_type=["bars", "content"])
    scroll.add_widget(body)
    content.add_widget(scroll)
    content.add_widget(status)
    close = Button(text="Close", size_hint_y=None, height=dp(42))
    close.bind(on_release=popup.dismiss)
    content.add_widget(close)
    popup.open()


def guide_as_text() -> str:
    """Return the guide as plain text (markup stripped), e.g. for docs/tests."""
    import re

    lines: List[str] = []
    for block in GUIDE:
        kind = block[0]
        if kind == "h":
            lines += ["", block[1].upper(), ""]
        elif kind == "p":
            lines.append(re.sub(r"\[/?[a-z]+(=[^\]]*)?\]", "", block[1]))
        elif kind == "cmd":
            lines.append(f"    > {block[1]}")
        elif kind == "link":
            lines.append(f"    {block[1]}: {block[2]}")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    print(guide_as_text())
