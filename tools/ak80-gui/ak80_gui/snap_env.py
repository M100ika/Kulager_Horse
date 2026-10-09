"""Окружение, которое терминал VS Code из snap передаёт запущенным программам.

VS Code из snap оставляет своему терминалу переменные, указывающие на схемы
GSettings и модули GTK внутри snap. Системная GTK3, которую Qt подгружает для
темы оформления под Wayland, берёт оттуда схемы другой версии GNOME и
аварийно завершает процесс:

    GLib-GIO-ERROR: Settings schema 'org.gnome.settings-daemon.plugins.xsettings'
    does not contain a key named 'antialiasing'

Пульт не работает внутри snap, поэтому эти переменные ему не нужны.
"""

from __future__ import annotations

from collections.abc import MutableMapping

# Под этим суффиксом VS Code сохраняет значение, бывшее до запуска snap.
_ORIG_SUFFIX = "_VSCODE_SNAP_ORIG"

# Переменные GLib, GTK и glibc, которые snap направляет в свои каталоги.
_SNAP_VARS = (
    "GSETTINGS_SCHEMA_DIR",
    "GTK_PATH",
    "GTK_EXE_PREFIX",
    "GTK_IM_MODULE_FILE",
    "GIO_MODULE_DIR",
    "GDK_PIXBUF_MODULEDIR",
    "GDK_PIXBUF_MODULE_FILE",
    "LOCPATH",
)


def restore_host_env(env: MutableMapping[str, str]) -> list[str]:
    """Возвращает системные значения переменных; отдаёт имена изменённых."""
    changed = []
    for key in [k for k in env if k.endswith(_ORIG_SUFFIX)]:
        name = key[: -len(_ORIG_SUFFIX)]
        original = env.pop(key)
        if original:
            env[name] = original
        else:
            env.pop(name, None)  # пустое значение: до snap переменной не было
        changed.append(name)
    for name in _SNAP_VARS:
        if "/snap/" in env.get(name, ""):
            del env[name]
            changed.append(name)
    return changed
