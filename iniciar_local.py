"""Inicia o ProspectaX localmente, preservando o login inicial da ferramenta."""

import os
from pathlib import Path
import runpy
import sys


def main():
    root = Path(__file__).resolve().parent
    site = Path(os.environ.get('PROSPECTAX_SITE_DIR', os.environ.get('PROSPECTOR_SITE_DIR', str(root / 'site'))))
    if not (site / 'index.html').is_file():
        raise SystemExit('A pasta site/ com as telas ainda nao foi adicionada. Consulte o README.md.')
    sys.path.insert(0, str(root))
    runpy.run_path(str(root / 'prospector_api_parquet.py'), run_name='__main__')


if __name__ == '__main__':
    main()
