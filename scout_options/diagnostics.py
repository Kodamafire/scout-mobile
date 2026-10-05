"""Read-only local connection check. Credential values and remote bodies never print."""
import os
from pathlib import Path
from .credentials import load_credentials


def running_credentials(proc_root='/proc'):
    """Reuse only this user's Scout service environment; no credential files created."""
    found = []
    for process in Path(proc_root).iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            argv = (process/'cmdline').read_bytes().split(b'\0')
            if not any(argv[i:i+2] == [b'-m', b'scout_options.service'] for i in range(len(argv)-1)):
                continue
            values = {}
            for entry in (process/'environ').read_bytes().split(b'\0'):
                name, separator, value = entry.partition(b'=')
                if separator and name in {b'ALPACA_API_KEY', b'ALPACA_SECRET_KEY'}:
                    values[name.decode()] = value.decode()
            found.append(load_credentials(values))
        except (OSError, UnicodeError, ValueError):
            continue
    if len(found) != 1:
        raise RuntimeError('Expected exactly one running Scout options service with valid credential formatting')
    return found[0]


def check(client):
    passed = True
    for label, action in [('Paper account',client.get_account), ('Exchange clock',client.get_clock),
                          ('Positions read',client.get_all_positions)]:
        try:
            action()
            print(label+': OK')
        except Exception as exc:
            status = getattr(exc,'status_code',None)
            safe_status = str(status) if type(status) is int and 100 <= status <= 599 else 'unavailable'
            print(label+': FAILED (HTTP '+safe_status+')')
            if status == 401:
                print('Alpaca did not accept the paper key/secret pair. Check that both come from the same regeneration.')
            elif status == 403:
                print('Access denied. Account permissions need checking.')
            passed = False
    return passed


def main():
    import argparse
    from alpaca.trading.client import TradingClient
    parser=argparse.ArgumentParser(description='Private read-only paper connection diagnostics')
    parser.add_argument('--running',action='store_true')
    args=parser.parse_args()
    try:
        key,secret=running_credentials() if args.running else load_credentials()
    except (ValueError,RuntimeError):
        print('No unique service with usable credential formatting found. Leave the options service running.')
        return
    print('Checking locally; credentials and account values remain hidden. No broker orders.')
    check(TradingClient(key,secret,paper=True))


if __name__ == '__main__': main()
