"""Small, exact voice controls handled before an Agent is invoked."""
import re
from voice_config import WAKE_PHRASE


def dismiss_command(text):
    text = re.sub('^(?:' + re.escape(WAKE_PHRASE) + r'|小七)[，,。！!：:\s]*', '', text.strip())
    return text.rstrip('，,。！!？?：: ') in {'退下', '退下吧', '你退下', '你退下吧'}
