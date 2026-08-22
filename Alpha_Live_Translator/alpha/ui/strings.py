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

THE KILL SWITCH
---------------
`DEFAULT_UI_LANGUAGE = "en"` makes `t()` an identity function -- it returns its
argument unchanged, for every input, with no dictionary consulted. The app then
behaves exactly as it did before this module existed. Switching the whole
feature off is that one word, and it needs no other edit anywhere.

The environment variable `ALPHA_UI_LANGUAGE` overrides it, so a machine that has
already been handed over can be put back to English without touching the code:

    set ALPHA_UI_LANGUAGE=en

An unrecognised value falls back to English rather than raising.

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

import os

# Change this one word to turn the feature off completely. See the module
# docstring: with "en" the table below is never consulted at all.
DEFAULT_UI_LANGUAGE = "ja"

ENV_VAR = "ALPHA_UI_LANGUAGE"


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


def _resolve_language() -> str:
    """The active language code, environment first.

    Never raises and never returns something `_TABLES` cannot handle: an unknown
    or misspelled value simply means English.
    """
    try:
        value = os.environ.get(ENV_VAR) or DEFAULT_UI_LANGUAGE
        code = str(value).strip().lower()
    except Exception:
        return "en"
    return code if code in _TABLES else "en"


_active = _resolve_language()


def get_language() -> str:
    """The language code in force right now."""
    return _active


def set_language(code: str) -> None:
    """Switch language. Intended for tests and for a future settings control.

    Text already drawn on screen is not re-rendered; every call site reads
    through `t()` when it paints, so this takes effect for anything drawn
    afterwards. The app applies the language once, at startup.
    """
    global _active
    normalized = str(code).strip().lower() if code else "en"
    _active = normalized if normalized in _TABLES else "en"


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
