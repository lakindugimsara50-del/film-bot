"""
bot package initialization.
Ensures Pyrogram 64-bit channel limits are monkeypatched immediately
when any bot module is loaded.
"""

try:
    import pyrogram.utils
    pyrogram.utils.MIN_CHANNEL_ID = -1009999999999999
    pyrogram.utils.MIN_CHAT_ID = -999999999999
except Exception:
    pass
