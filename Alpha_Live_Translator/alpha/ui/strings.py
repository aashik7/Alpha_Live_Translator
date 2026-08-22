"""User-interface text, and its Japanese rendering (sprint item 88).

WHAT THIS IS
------------
A lookup table and one function, `t()`. Every piece of chrome text -- button
labels, section titles, dialog wording, status lines -- is written in English at
its call site exactly as before, and passed through `t()` on its way to the
widget. `t()` either finds a translation or hands the English straight back.

    self.listen_button.configure(text=t("Start Listening"))

The English string is therefore still the single authority: it is the key, it is
what the code reads, and it is what appears if anything at all goes wrong.

WHERE THE ACTIVE LANGUAGE COMES FROM
------------------------------------
Three sources, checked in this order, first usable one wins:

    1. the ALPHA_UI_LANGUAGE environment variable
    2. `ui_language` in <app root>/user_settings.json, written by `save_language`
    3. DEFAULT_UI_LANGUAGE below

The environment variable is deliberately first. It is how a machine that has
already been handed over is forced back to English without editing code and
without clicking through a UI that the person looking at it may not be able to
read:

    set ALPHA_UI_LANGUAGE=en

An unusable value at any level falls through to the next rather than raising, so
a missing, unreadable or corrupt settings file is simply "nobody has chosen yet".

THE KILL SWITCH
---------------
`DEFAULT_UI_LANGUAGE = "en"` makes `t()` an identity function -- it returns its
argument unchanged, for every input, with no dictionary consulted. The app then
behaves exactly as it did before this module existed. Switching the whole
feature off is that one word, and it needs no other edit anywhere. The
environment variable above does the same on a machine already delivered.

APPLIES AT STARTUP, NOT INSTANTLY
---------------------------------
`t()` is read when a widget is painted, and most of the widgets that carry
translated text are local variables inside `main_window.py`'s `create_*`
methods rather than attributes on the app. There is therefore no handle to
re-render them through, and `set_language()` affects only what is drawn after
it. Any control built on top of this has to say the change applies next time
the app starts -- and must not pretend otherwise.

WHAT IS DELIBERATELY *NOT* TRANSLATED, AND WHY
----------------------------------------------
1. **`SOURCE_LANGUAGES` / `TARGET_LANGUAGES`** (`alpha/constants.py`). Those two
   lists read "English" and "Japanese", and those words are not only shown in
   the dropdowns -- they are the *keys* the rest of the app switches on. They
   are read in more than a dozen places across `main_window.py`,
   `deepgram_client.py` and `duplicate_protection.py`, and they feed
   `_resolve_deepgram_language()`. Translating them would silently break speech
   recognition routing and the DeepL target, with no error to show for it. A
   string that is also a key stays in English.

2. **Everything written to a log, and every `print()`.** The whole point of
   `collect_logs.py` is that a problem on a machine we cannot reach can still be
   diagnosed here. Japanese log lines would make that harder for no user-visible
   gain.

3. **Transcript and translation pane content.** That is the user's own data, and
   those two widgets carry the Tk marks the pane bookkeeping depends on. This
   module never goes near them.

4. **Status lines assembled with an f-string** (for example the reconnect line
   that carries a second count). They have no fixed literal to key on, so they
   pass through as English. Adding them means giving `t()` format arguments,
   which is a bigger change than this one and is not needed to read the UI.

ADDING A TRANSLATION
--------------------
Add the exact English literal as a key. `test_item88_ui_strings.py` fails if a
key here does not appear verbatim in the source, so a typo cannot sit unnoticed
and do nothing.
"""

from __future__ import annotations

import json
import os

# What a fresh install starts in, before anyone has chosen anything. With "en"
# the table below is never consulted at all, so this doubles as the switch that
# turns the whole feature off. See the module docstring.
DEFAULT_UI_LANGUAGE = "en"

ENV_VAR = "ALPHA_UI_LANGUAGE"

# Where the user's own choice is remembered between runs.
SETTINGS_FILENAME = "user_settings.json"
SETTINGS_KEY = "ui_language"

# alpha/ui/strings.py -> alpha/ui -> alpha -> the app root: the same directory
# `.env` sits in, and what alpha/config.py calls PROJECT_ROOT. Worked out here
# rather than imported, because this module has to stay free of dependencies --
# main.py starts it before anything that could fail.
APP_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# What the language is called in its own language, which is the only way a
# reader who cannot read the current one can find their way out.
LANGUAGE_NAMES = {"en": "English", "ja": "日本語"}


# Keys are the exact English literal used in the code. Anything absent from this
# table renders in English, which is a normal outcome and not a failure.
_JA: dict[str, str] = {
    # -- Primary action ----------------------------------------------------
    "Start Listening": "認識を開始",
    "Stop Listening": "認識を停止",
    "Starting…": "開始しています…",
    # -- Footer and menu actions -------------------------------------------
    "Clear": "クリア",
    "Export": "エクスポート",
    "Copy Translation": "翻訳をコピー",
    "Copy Transcript": "文字起こしをコピー",
    "Summary": "要約",
    "Microphone": "マイク",
    "Mic": "マイク",
    "Always on Top": "常に最前面",
    # -- Header and section titles -----------------------------------------
    "Meeting Assistant": "会議アシスタント",
    "Alpha Meeting Assistant": "Alpha 会議アシスタント",
    "Live Transcript": "ライブ文字起こし",
    "Translation": "翻訳",
    "Meeting Summary": "会議の要約",
    "Meeting Summary ▲": "会議の要約 ▲",
    "Meeting Summary ▼": "会議の要約 ▼",
    "Listening to:": "認識する言語:",
    "Translate to:": "翻訳先:",
    # -- Status strip ------------------------------------------------------
    "● LIVE": "● ライブ",
    "○ IDLE": "○ 待機",
    "● Standby": "● 待機中",
    "Ready": "準備完了",
    "Ready to listen": "開始できます",
    "Listening — capturing audio": "認識中 — 音声を取得しています",
    "Finalising…": "確定処理中…",
    "Finalizing...": "確定処理中…",
    "Stopped": "停止しました",
    "Stopped. Diagnostics may still be saving.": (
        "停止しました。診断データを保存している場合があります。"
    ),
    # -- Connection indicator (_CONNECTION_INDICATOR_TEXT in main_window.py) -
    "● Signal OK": "● 信号良好",
    "● Reconnecting": "● 再接続中",
    "● Translation degraded": "● 翻訳機能が低下",
    "● Key rejected": "● キーが拒否されました",
    "● Audio device changed": "● 音声デバイスが変更されました",
    # -- Connection status (rendered from alpha/utils/service_status.py) ----
    "Idle.": "待機中。",
    "Connected.": "接続済み。",
    "Translation degraded.": "翻訳機能が低下しています。",
    "Credentials OK.": "認証情報は正常です。",
    "Reconnecting to Deepgram…": "Deepgram に再接続しています…",
    # -- Empty states ------------------------------------------------------
    "Live meeting transcript will appear here...": (
        "会議の文字起こしがここに表示されます..."
    ),
    "This feature is coming soon.": "この機能は近日公開予定です。",
    "Meeting summary will appear here after summarization is enabled in a later version.": (
        "要約機能が有効になると、会議の要約がここに表示されます。"
    ),
    # -- Dialog titles -----------------------------------------------------
    "Error": "エラー",
    "Language Profile": "言語プロファイル",
    "Language Routing": "言語ルーティング",
    # -- Dialog messages ---------------------------------------------------
    "No transcript store available.": "文字起こしデータがありません。",
    "No transcript text to copy.": "コピーする文字起こしがありません。",
    "No transcript text to export.": "エクスポートする文字起こしがありません。",
    "Live transcript copied to clipboard.": (
        "文字起こしをクリップボードにコピーしました。"
    ),
    "No translated transcript to copy.": "コピーする翻訳がありません。",
    "Translated transcript copied to clipboard.": (
        "翻訳をクリップボードにコピーしました。"
    ),
}

_TABLES: dict[str, dict[str, str]] = {"ja": _JA}


def settings_path() -> str:
    """The file the user's choice is remembered in."""
    return os.path.join(APP_ROOT, SETTINGS_FILENAME)


def available_languages() -> tuple[str, ...]:
    """Every code a caller may offer, English first."""
    return ("en",) + tuple(_TABLES)


def _normalize(code) -> str:
    """A code reduced to something comparable, or "" if it is unusable."""
    try:
        return str(code).strip().lower() if code else ""
    except Exception:
        return ""


def _saved_language() -> str:
    """The choice from a previous run, or "" if there isn't a usable one.

    A missing, unreadable, or corrupt settings file is an ordinary state, not
    an error: it just means nobody has chosen yet.
    """
    try:
        with open(settings_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return _normalize(data.get(SETTINGS_KEY)) if isinstance(data, dict) else ""
    except Exception:
        return ""


def _resolve_language() -> str:
    """Environment variable, then the saved choice, then the shipped default.

    The environment variable comes first deliberately: it is the way to force a
    machine that has already been handed over back to English without editing
    anything or clicking through a UI that may be unreadable to whoever is
    looking at it. An unusable value at any level falls through to the next one
    rather than raising or forcing English.
    """
    for candidate in (os.environ.get(ENV_VAR), _saved_language(), DEFAULT_UI_LANGUAGE):
        code = _normalize(candidate)
        if code in available_languages():
            return code
    return "en"


_active = _resolve_language()


def get_language() -> str:
    """The language code in force right now."""
    return _active


def save_language(code: str) -> bool:
    """Remember `code` for future runs, and apply it to anything drawn next.

    Returns False if the choice could not be written to disk, so the caller can
    say so rather than let someone believe a setting stuck when it did not. The
    install directory is writable by design, but a locked-down machine is
    exactly where that would surprise them.

    The write lands complete or not at all: it goes to a temporary file in the
    same directory and is then renamed over the target, because `os.replace` is
    atomic on Windows for a same-volume rename. Writing in place would mean that
    a crash between truncate and flush leaves a half-written file -- and since
    any other preference in that file is read, merged and written back here,
    that would lose settings this module knows nothing about.

    The language is applied only once the write has succeeded, so memory and
    disk cannot disagree about what was chosen.
    """
    normalized = _normalize(code)
    if normalized not in available_languages():
        normalized = "en"

    data = {}
    try:
        with open(settings_path(), "r", encoding="utf-8") as handle:
            existing = json.load(handle)
        if isinstance(existing, dict):
            data = existing
    except Exception:
        data = {}
    data[SETTINGS_KEY] = normalized

    target = settings_path()
    temp = target + ".tmp"
    try:
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    except Exception:
        try:
            os.remove(temp)
        except Exception:
            pass
        return False

    set_language(normalized)
    return True


def set_language(code: str) -> None:
    """Switch the active language without remembering it.

    `save_language` is what a settings control should call; this is the
    in-memory half of it, and what tests use to pin a language.

    Text already on screen is not re-rendered -- see "APPLIES AT STARTUP" in the
    module docstring. This takes effect for whatever is drawn next.
    """
    global _active
    normalized = _normalize(code)
    _active = normalized if normalized in available_languages() else "en"


def t(text):
    """Return `text` in the active language, or `text` itself.

    Deliberately total: a missing key, an unknown language, or a value that is
    not a string all give back exactly what was passed in. A label can never end
    up blank because of this function.
    """
    if _active == "en":
        return text
    if not isinstance(text, str):
        return text
    return _TABLES[_active].get(text, text)


def translate_all(items):
    """`t()` over a sequence, keeping the sequence's own type where it matters.

    `LISTEN_BUTTON_LABELS` is a tuple that gets measured to size the Start/Stop
    button, so the widest *rendered* label is what the width has to come from.
    """
    return type(items)(t(item) for item in items) if isinstance(items, tuple) else [t(i) for i in items]
