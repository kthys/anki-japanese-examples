from . import GUI, japanese_examples, config_ui, changelog
from aqt import mw, gui_hooks

mw.addonManager.setConfigAction(__name__, config_ui.on_config)

gui_hooks.main_window_did_init.append(lambda: changelog.check_and_show_changelog(__name__))
