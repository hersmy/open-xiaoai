"""Fixed prompts synthesized once by this speaker's own Xiaomi TTS voice."""
import json
import shlex

PROMPTS = {
    'ready': '哎。',
    'received': '收到。',
    'done': '已完成。',
    'no_input': '没听到指令，请再唤醒我一次。',
    'unclear': '没听清指令，请再唤醒我一次。',
    'not_ready': '暂时没能开始收音，请再试一次。',
}
DIRECTORY = '/data/open-xiaoai/feedback-native-v1'


def prepare(directory=None):
    return dict(PROMPTS)


def install_script(prompts):
    commands = ['mkdir -p ' + DIRECTORY,
                '[ -x ' + DIRECTORY + '/feedback-player ] || cp /usr/bin/miplayer ' + DIRECTORY + '/feedback-player']
    for name, text in prompts.items():
        if name not in PROMPTS or text != PROMPTS[name]:
            raise ValueError('Only fixed native prompts can be synthesized')
        destination = DIRECTORY + '/' + name + '.mp3'
        request = shlex.quote(json.dumps({'text': text, 'save': 1, 'play': 0}, ensure_ascii=False))
        commands.append(
            '[ -s ' + destination + ' ] || { '
            '. /usr/share/libubox/jshn.sh; '
            'result=$(ubus call mibrain text_to_speech ' + request + '); '
            'json_init; json_load "$result" || exit 1; json_get_var info info; '
            'json_init; json_load "$info" || exit 1; json_get_var path path; '
            '[ -s "$path" ] || exit 1; cp "$path" ' + destination + ' || exit 1; '
            'rm -f "$path"; }; [ -s ' + destination + ' ]')
    return commands


def play_script(name):
    if name not in PROMPTS:
        raise ValueError('Unknown fixed feedback')
    return DIRECTORY + '/feedback-player -f ' + DIRECTORY + '/' + name + '.mp3'


def open_asr_script():
    # A successful ubus call alone did not prove capture started on this LX06.
    # AIVS may truncate/rotate its log between turns. Counts can stay equal or
    # decrease even after a new Recognize; compare the latest event (unique ID).
    latest = "grep '\"name\" *: *\"Recognize\"' /tmp/mico_aivs_lab/event.log 2>/dev/null | tail -n 1"
    return ("before=$(" + latest + "); "
            "ubus call pnshelper event_notify '{\"src\":1,\"event\":0}' >/dev/null || exit 1; "
            "i=0; while [ $i -lt 25 ]; do after=$(" + latest + "); "
            '[ -n "$after" ] && [ "$after" != "$before" ] && { echo ASR_READY; exit 0; }; '
            'sleep 0.1; i=$((i+1)); done; echo ASR_NOT_CONFIRMED; exit 2')
