"""Importador local de CSVs oficiais PGFN. Nao faz downloads automaticos."""
import argparse
from pathlib import Path
from prospectax_public_checks import import_pgfn_csvs


def main():
    parser = argparse.ArgumentParser(description='Importar arquivos CSV oficiais da divida ativa PGFN.')
    parser.add_argument('files', nargs='*')
    parser.add_argument('--competencia')
    parser.add_argument('--cobertura')
    args = parser.parse_args()
    if not args.files:
        import tkinter as tk
        from tkinter import filedialog, simpledialog, messagebox
        window = tk.Tk()
        window.withdraw()
        files = filedialog.askopenfilenames(title='Selecione os CSVs oficiais PGFN descompactados', filetypes=[('Dados PGFN', '*.csv *.CSV')])
        if not files:
            return
        competence = simpledialog.askstring('Referencia da base', 'Mes de referencia dos arquivos (AAAA-MM). Para um trimestre, informe o ultimo mes:', parent=window)
        if not competence:
            return
        coverage = simpledialog.askstring('Cobertura dos dados', 'Descreva os arquivos selecionados, por exemplo: somente PR, nao previdenciario. Isso aparecera na consulta:', parent=window)
        if not coverage:
            return
        window.destroy()
    else:
        files, competence, coverage = args.files, args.competencia, args.cobertura
    if not competence or not coverage:
        parser.error('Informe --competencia AAAA-MM e --cobertura com o alcance dos arquivos.')
    target = Path(__file__).resolve().parent / 'private_data' / 'pgfn.sqlite'
    print('Importando a base publica. Arquivos grandes podem demorar. A base anterior sera preservada ate a conclusao.', flush=True)
    result = import_pgfn_csvs(files, target, competence, coverage, progress=lambda message: print(message, flush=True))
    print('Concluido:', result['records'], 'inscricoes empresariais. Referencia:', result['competence'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Importacao nao concluida:', exc)
        raise SystemExit(1)
