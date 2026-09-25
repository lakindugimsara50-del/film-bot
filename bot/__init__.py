"""
bot package initialization.
Ensures Pyrogram 64-bit channel limits are monkeypatched immediately
when any bot module is loaded.
"""

import os
import sys

_bot_dir = os.path.dirname(os.path.abspath(__file__))
if _bot_dir not in sys.path:
    sys.path.insert(0, _bot_dir)

try:
    import pyrogram.utils
    pyrogram.utils.MIN_CHANNEL_ID = -1009999999999999
    pyrogram.utils.MIN_CHAT_ID = -999999999999
except Exception:
    pass
