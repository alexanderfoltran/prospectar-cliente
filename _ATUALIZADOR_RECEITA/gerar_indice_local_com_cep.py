from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import sys
import time
import traceback

import duckdb

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "dados_parquet"
LOG_PATH = BASE_DIR / "logs" / "gerar_indice_local_com_cep.log"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from atualizar_base_receita import generate_fast_index, validate_output, log  # noqa: E402


def main():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fp, redirect_stdout(fp), redirect_stderr(fp):
        print()
        print("=" * 70)
        print(time.strftime("[%Y-%m-%d %H:%M:%S] INICIO GERAR INDICE LOCAL COM CEP"))
        print(f"BASE_DIR={BASE_DIR}")
        print(f"DATA_DIR={DATA_DIR}")
        try:
            generate_fast_index(DATA_DIR, duckdb, threads=4, memory_limit="6GB")
            validate_output(DATA_DIR, duckdb)
            print(time.strftime("[%Y-%m-%d %H:%M:%S] FIM OK"))
            return 0
        except Exception:
            print(time.strftime("[%Y-%m-%d %H:%M:%S] ERRO"))
            print(traceback.format_exc())
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
