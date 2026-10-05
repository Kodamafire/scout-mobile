"""Local format diagnostics; never prints or transmits credentials."""
import os

NAMES = ('ALPACA_API_KEY', 'ALPACA_SECRET_KEY')


def credential_status(value):
    if not value:
        return 'EMPTY'
    if any(c.isspace() for c in value):
        return 'CONTAINS WHITESPACE'
    if not value.isascii() or not value.isalnum():
        return 'CONTAINS UNEXPECTED CHARACTERS'
    return 'FORMAT CHECK PASSED'


def load_credentials(environ=None):
    environ = os.environ if environ is None else environ
    values = tuple(environ.get(name, '') for name in NAMES)
    failures = [name+': '+credential_status(value) for name, value in zip(NAMES, values)
                if credential_status(value) != 'FORMAT CHECK PASSED']
    if failures:
        raise ValueError('Paper credential input needs correction: '+'; '.join(failures))
    return values


def main():
    for name in NAMES:
        print(name+': '+credential_status(os.environ.get(name, '')))
    print('Format checks do not verify authentication or data access. Values remain hidden.')


def connect():
    """Hidden local prompts replace prior malformed shell values; nothing is saved."""
    import getpass
    import sys
    environ = dict(os.environ)
    for name, label in zip(NAMES, ('Paper API key: ', 'Paper API secret: ')):
        while True:
            value = getpass.getpass(label)
            # Remove terminal paste delimiters and accidental surrounding whitespace.
            value = value.replace('\x1b[200~', '').replace('\x1b[201~', '').strip()
            status = credential_status(value)
            if status == 'FORMAT CHECK PASSED':
                environ[name] = value
                break
            print(status+'. Copy only the credential value and try again.')
    environ['SCOUT_OPTIONS_FEED'] = 'indicative'
    print('Starting read-only reference service with the free indicative feed. No broker orders.')
    os.execve(sys.executable, [sys.executable, '-m', 'scout_options.service',
        '--capital', '10000', '--ledger', '.scout-options/research.sqlite',
        '--status', '.scout-options/options-status.json'], environ)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Private local credential checks and connection setup')
    parser.add_argument('--connect', action='store_true', help='Prompt privately then run the read-only indicative service')
    args = parser.parse_args()
    try:
        connect() if args.connect else main()
    except (KeyboardInterrupt, EOFError):
        print('Local setup canceled.')
