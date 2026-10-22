"""Inicia o ProspectaX localmente sem gravar uma senha padrao no codigo."""

from getpass import getpass
import os
from pathlib import Path
import runpy
import sys


def main():
    root = Path(__file__).resolve().parent
    site = Path(os.environ.get('PROSPECTAX_SITE_DIR', os.environ.get('PROSPECTOR_SITE_DIR', str(root / 'site'))))
    if not (site / 'index.html').is_file():
        raise SystemExit('A pasta site/ com as telas ainda nao foi adicionada. Consulte o README.md.')
    if not (site / 'usuarios.json').exists() and not os.environ.get('PROSPECTAX_ADMIN_PASSWORD'):
        password = getpass('Crie a senha inicial do admin (minimo 16 caracteres): ')
        if len(password) < 16:
            raise SystemExit('Senha curta: use pelo menos 16 caracteres.')
        if getpass('Confirme a senha: ') != password:
            raise SystemExit('As senhas nao coincidem.')
        os.environ['PROSPECTAX_ADMIN_PASSWORD'] = password
    sys.path.insert(0, str(root))
    runpy.run_path(str(root / 'prospector_api_parquet.py'), run_name='__main__')


if __name__ == '__main__':
    main()
