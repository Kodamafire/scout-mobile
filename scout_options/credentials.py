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


if __name__ == '__main__': main()
