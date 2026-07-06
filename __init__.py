try:
    from .src.ui import GUI, config_ui, batch_ui
    from .src.core import japanese_examples
    from .src.utils import changelog
    from aqt import mw, gui_hooks

    mw.addonManager.setConfigAction(__name__, config_ui.on_config)

    gui_hooks.main_window_did_init.append(lambda: changelog.check_and_show_changelog(__name__))
    gui_hooks.main_window_did_init.append(batch_ui.register_batch_menu)
except ImportError as e:
    if "attempted relative import" in str(e):
        pass
    else:
        raise
