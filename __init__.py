try:
    from .src.ui import GUI, config_ui, batch_ui
    from .src.core import japanese_examples, tatoeba_data
    from .src.utils import changelog
    from aqt import mw, gui_hooks

    mw.addonManager.setConfigAction(__name__, config_ui.on_config)

    gui_hooks.main_window_did_init.append(lambda: changelog.check_and_show_changelog(__name__))
    gui_hooks.main_window_did_init.append(batch_ui.register_batch_menu)
    # Reclaim the pairs TSV that versions up to 1.5.0 left in user_files/.
    # Users who never re-download would otherwise keep it forever.
    gui_hooks.main_window_did_init.append(tatoeba_data.remove_legacy_pairs_files)
except ImportError as e:
    if "attempted relative import" in str(e):
        pass
    else:
        raise
