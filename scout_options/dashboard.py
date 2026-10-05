"""Generate the same read-only options dashboard beside a local status file."""
import json
from pathlib import Path


def dashboard_html(status_name='options-status.json'):
    if Path(status_name).name != status_name or not status_name.endswith('.json'):
        raise ValueError('Status must be a local JSON basename.')
    template = Path(__file__).resolve().parent.parent/'options.html'
    # JSON string escaping avoids inserting filename characters as JavaScript.
    name = json.dumps(status_name).replace('<', '\\u003c').replace('>', '\\u003e')
    return template.read_text().replace("const STATUS_FILE='options-status.json';", 'const STATUS_FILE='+name+';')


def ensure_dashboard(status_path):
    status_path = Path(status_path)
    path = status_path.parent/'options.html'
    if not path.exists():
        temp = path.with_suffix('.html.tmp')
        temp.write_text(dashboard_html(status_path.name))
        temp.replace(path)
    return path
