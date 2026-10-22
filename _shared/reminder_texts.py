"""Read canonical hook guidance shared by installers and native runners."""
import json
from pathlib import Path


def reminder_text(name):
    text = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))[name]
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f'Missing hook instruction: {name}')
    return text
