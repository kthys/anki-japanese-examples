from . import GUI, japanese_examples, config_ui
from aqt import mw

mw.addonManager.setConfigAction(__name__, config_ui.on_config)
