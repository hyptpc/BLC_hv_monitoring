"""Read private CAEN credentials beside the existing monitor configuration."""

import os
from pathlib import Path
import stat

import yaml


class CredentialsError(Exception):
    """Credential errors intentionally exclude file contents and secret values."""


def load_credentials(config_file):
    path = Path(config_file).expanduser().absolute().parent / 'credentials.local.yml'
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
    flags |= getattr(os, 'O_NONBLOCK', 0)
    if path.is_symlink():
        raise CredentialsError('credentials.local.yml must be a regular private file, not a symlink')
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise CredentialsError(f'Cannot read {path}; place credentials.local.yml beside the config file') from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise CredentialsError('credentials.local.yml must be a regular private file')
        if info.st_uid != os.geteuid():
            raise CredentialsError('credentials.local.yml must be owned by the user running the monitor')
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise CredentialsError(f'Credentials are not private; run chmod 600 {path}')
        stream = os.fdopen(descriptor, 'r', encoding='utf-8')
        descriptor = None
        with stream:
            try:
                values = yaml.safe_load(stream)
            except (yaml.YAMLError, UnicodeError):
                raise CredentialsError('Invalid YAML in credentials.local.yml; expected username and password') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)

    if not isinstance(values, dict) or set(values) != {'username', 'password'}:
        raise CredentialsError('credentials.local.yml must contain only username and password')
    username, password = values['username'], values['password']
    if not isinstance(username, str) or not username.strip():
        raise CredentialsError('credentials.local.yml requires a nonempty username string')
    if not isinstance(password, str) or not password:
        raise CredentialsError('credentials.local.yml requires a nonempty password string')
    return username, password
