"""Очистка окружения, которое терминал VS Code из snap передаёт пульту."""

from ak80_gui.snap_env import restore_host_env

HOST_DATA_DIRS = "/usr/share/ubuntu:/usr/share/gnome:/usr/local/share/:/usr/share/"


def vscode_snap_env() -> dict[str, str]:
    """Окружение терминала VS Code 268 из snap на стенде (сокращено)."""
    return {
        "HOME": "/home/maxat",
        "DISPLAY": ":0",
        "XDG_DATA_DIRS": "/home/maxat/snap/code/268/.local/share:/snap/code/268/usr/share:"
                         + HOST_DATA_DIRS,
        "XDG_DATA_DIRS_VSCODE_SNAP_ORIG": HOST_DATA_DIRS,
        "XDG_CONFIG_DIRS": "/snap/code/268/etc/xdg:/etc/xdg",
        "XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG": "/etc/xdg/xdg-ubuntu:/etc/xdg",
        "GSETTINGS_SCHEMA_DIR": "/home/maxat/snap/code/268/.local/share/glib-2.0/schemas",
        "GTK_PATH": "/snap/code/268/usr/lib/x86_64-linux-gnu/gtk-3.0",
        "GIO_MODULE_DIR": "/home/maxat/snap/code/common/.cache/gio-modules",
        "LOCPATH": "/snap/code/268/usr/lib/locale",
    }


def test_snap_paths_removed():
    env = vscode_snap_env()
    restore_host_env(env)
    assert env["XDG_DATA_DIRS"] == HOST_DATA_DIRS
    assert env["XDG_CONFIG_DIRS"] == "/etc/xdg/xdg-ubuntu:/etc/xdg"
    for name in ("GSETTINGS_SCHEMA_DIR", "GTK_PATH", "GIO_MODULE_DIR", "LOCPATH"):
        assert name not in env
    assert not any(k.endswith("_VSCODE_SNAP_ORIG") for k in env)
    assert env["HOME"] == "/home/maxat" and env["DISPLAY"] == ":0"


def test_empty_original_means_unset():
    env = {"XDG_CONFIG_DIRS": "/snap/code/268/etc/xdg", "XDG_CONFIG_DIRS_VSCODE_SNAP_ORIG": ""}
    restore_host_env(env)
    assert env == {}


def test_plain_terminal_untouched():
    env = {
        "XDG_DATA_DIRS": HOST_DATA_DIRS,
        "GTK_PATH": "/usr/lib/x86_64-linux-gnu/gtk-3.0",
        "GSETTINGS_SCHEMA_DIR": "/home/maxat/schemas",
    }
    before = dict(env)
    assert restore_host_env(env) == []
    assert env == before
