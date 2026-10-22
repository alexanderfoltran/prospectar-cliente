"""Consultas publicas, sem inferir score ou ausencia geral de restricoes."""
import csv
import io
import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError
from prospectax_discovery import DiscoveryCache, json_request, normalize_cnpj, norm


def cnpj_checked(value):
    cnpj = normalize_cnpj(value)
    if not re.fullmatch(r'[A-Z0-9]{12}[0-9]{2}', cnpj):
        raise ValueError('CNPJ invalido.')
    return cnpj


def public_links(cnpj):
    cnpj = cnpj_checked(cnpj)
    return [
        {'label': 'Processos judiciais — TJPR / Projudi',
         'url': 'https://consulta.tjpr.jus.br/projudi_consulta/processo/consultaPublica.do?' + urlencode({'actionType': 'iniciar', 'cpfCnpj': cnpj, 'opcaoConsultaPublica': '1'}),
         'prefilled': True, 'scope': 'Consulta judicial manual. Confira a instancia, resolva o CAPTCHA e clique em Pesquisar no portal oficial.'},
        {'label': 'Divida Aberta — PGFN', 'url': 'https://www.dividaaberta.pgfn.gov.br/', 'prefilled': False,
         'scope': 'Divida ativa da Uniao e do FGTS publicada pela PGFN.'},
        {'label': 'Emitir certidao federal', 'url': 'https://servicos.receitafederal.gov.br/servico/certidoes', 'prefilled': False,
         'scope': 'Certidao de regularidade fiscal; o portal pode exigir CAPTCHA.'},
        {'label': 'Sancoes publicas — CGU', 'url': 'https://portaldatransparencia.gov.br/sancoes/consulta?' + urlencode({'cpfCnpj': cnpj, 'cadastro': '1,2'}), 'prefilled': True,
         'scope': 'CEIS/CNEP: sancoes administrativas, nao restricoes de credito.'},
        {'label': 'Consulta indicativa de protesto', 'url': 'https://protestosp.com.br/consulta/consulta-gratuita-de-protesto', 'prefilled': False, 'scope': 'Consulta gratuita de protestos nos cartorios participantes; nao substitui certidao.'},
    ]


class PublicChecks:
    def __init__(self, directory, request_json=json_request):
        self.directory = Path(directory)
        self.config_file = self.directory / 'public_checks_config.json'
        self.debt_db = self.directory / 'pgfn.sqlite'
        self.cache = DiscoveryCache(self.directory / 'public_checks_cache')
        self.request_json = request_json
        self.lock = threading.Lock()

    def _config(self):
        try:
            return json.loads(self.config_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def status(self):
        return {'cgu_configured': bool(self._config().get('cgu_key')), 'pgfn_base_installed': self.debt_db.exists(),
                'cgu_registration_url': 'https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email'}

    def configure(self, data):
        key = str(data.get('cgu_key') or '').strip()
        if len(key) > 500:
            raise ValueError('Chave invalida.')
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.lock:
            current = self._config()
            if data.get('remove'):
                current = {}
            elif key:
                current['cgu_key'] = key
            temp = self.config_file.with_suffix('.tmp')
            temp.write_text(json.dumps(current), encoding='utf-8')
            temp.replace(self.config_file)
        return self.status()

    def debt(self, cnpj):
        cnpj = cnpj_checked(cnpj)
        if not self.debt_db.exists():
            return {'status': 'not_configured', 'found': None, 'source': 'PGFN — dados abertos',
                    'message': 'Base publica PGFN ainda nao importada. Use o portal oficial ou importe os CSVs para consultar dentro da ferramenta.'}
        try:
            con = sqlite3.connect(self.debt_db.resolve().as_uri() + '?mode=ro', uri=True)
            try:
                meta = dict(con.execute('SELECT key,value FROM metadata').fetchall())
                entries = con.execute('SELECT registration, amount, situation, source_file FROM debts WHERE cnpj=? LIMIT 101', (cnpj,)).fetchall()
                count, amount = con.execute('SELECT count(*),sum(amount) FROM debts WHERE cnpj=?', (cnpj,)).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            return {'status': 'unavailable', 'found': None, 'source': 'PGFN — dados abertos', 'message': 'A base local de pendencias nao pode ser lida. Nenhuma conclusao sobre debitos foi emitida.'}
        return {'status': 'found' if count else 'not_found_in_dataset', 'found': bool(count), 'source': 'PGFN — dados abertos importados',
                'dataset_date': meta.get('competence', ''), 'imported_at': meta.get('imported_at', ''), 'count': count,
                'amount': amount if count else None, 'limited': count > 100,
                'entries': [{'registration': x[0], 'amount': x[1], 'situation': x[2], 'source_file': x[3]} for x in entries[:100]],
                'message': 'Registros encontrados na base importada; confira a situacao atual no portal.' if count else 'CNPJ nao localizado nos arquivos importados. Isso nao comprova ausencia de dividas ou restricoes.',
                'coverage': meta.get('coverage', 'Arquivos selecionados pelo administrador.')}

    def sanctions(self, cnpj):
        cnpj = cnpj_checked(cnpj)
        key = self._config().get('cgu_key')
        if not key:
            return {'status': 'not_configured', 'found': None, 'source': 'CGU — CEIS/CNEP', 'message': 'O administrador pode cadastrar a chave gratuita do Portal da Transparencia para consultar dentro da ferramenta.'}
        cache_key = 'cgu-v1:' + cnpj
        cached = self.cache.get(cache_key, ttl=3600)
        if cached is not None:
            return {**cached, 'from_cache': True}
        entries, sources = [], []
        with self.lock:
            for kind in ('ceis', 'cnep'):
                try:
                    # Consulta pontual; limite de 10 paginas, indicando truncamento.
                    limited = False
                    for page in range(1, 11):
                        url = 'https://api.portaldatransparencia.gov.br/api-de-dados/' + kind + '?' + urlencode({'codigoSancionado': cnpj, 'pagina': page})
                        raw = self.request_json(url, headers={'chave-api-dados': key}, timeout=12)
                        if not isinstance(raw, list):
                            raise ValueError('Formato de resposta inesperado.')
                        if not raw:
                            break
                        for record in raw:
                            sanctioned = record.get('sancionado') or {}
                            if normalize_cnpj(sanctioned.get('codigoFormatado') or sanctioned.get('codigo')) != cnpj:
                                continue
                            sanction = record.get('tipoSancao') or {}
                            org = record.get('orgaoSancionador') or {}
                            entries.append({'registry': kind.upper(), 'name': sanctioned.get('nome', ''),
                                'description': sanction.get('descricaoResumida') or sanction.get('descricaoPortal') or '',
                                'authority': org.get('nome', ''), 'start': record.get('dataInicioSancao', ''),
                                'end': record.get('dataFimSancao', ''), 'reference': record.get('dataReferencia', ''),
                                'process': record.get('numeroProcesso', '')})
                        limited = page == 10
                    sources.append({'registry': kind.upper(), 'status': 'ok', 'limited': limited})
                except Exception:
                    # Falha de API jamais se transforma em "nada encontrado".
                    sources.append({'registry': kind.upper(), 'status': 'unavailable', 'limited': False})
        partial = any(s['status'] != 'ok' or s['limited'] for s in sources)
        result = {'status': 'partial' if partial else ('found' if entries else 'not_found'), 'found': True if entries else (None if partial else False),
                  'entries': entries, 'sources': sources, 'source': 'CGU — CEIS/CNEP',
                  'consulted_at': datetime.now(timezone.utc).isoformat(),
                  'message': 'Sancoes administrativas publicadas; consulte o periodo e a situacao no registro oficial. Esta consulta nao avalia credito.'}
        if not partial:
            self.cache.put(cache_key, result)
        return result

    def lookup(self, cnpj):
        cnpj = cnpj_checked(cnpj)
        return {'cnpj': cnpj, 'debt': self.debt(cnpj), 'sanctions': self.sanctions(cnpj), 'links': public_links(cnpj),
                'consulted_at': datetime.now(timezone.utc).isoformat(),
                'scope': 'Divida ativa e sancoes publicas. Nao inclui Serasa/SPC, todos os protestos ou todas as dividas estaduais/municipais. Nao emite parecer de credito nem certidao.'}


def import_pgfn_csvs(paths, target, competence, coverage, progress=None):
    """Construcao separada: so substitui indice anterior depois de concluir e validar."""
    import os
    import uuid
    from decimal import Decimal, InvalidOperation
    paths = [Path(p) for p in paths]
    if not paths:
        raise ValueError('Selecione os CSVs oficiais da PGFN.')
    if not re.fullmatch(r'\d{4}-\d{2}', competence):
        raise ValueError('Informe a competencia como AAAA-MM.')
    datetime.strptime(competence, '%Y-%m')
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
    con = sqlite3.connect(temp)
    imported = rejected = duplicates = 0
    try:
        con.executescript('CREATE TABLE debts(cnpj TEXT NOT NULL, registration TEXT NOT NULL, amount REAL, situation TEXT, source_file TEXT, UNIQUE(cnpj,registration)); CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);')
        for path in paths:
            if progress:
                progress('Lendo ' + path.name)
            # Os dados abertos PGFN podem ser UTF-8 ou Latin-1.
            with path.open('rb') as probe:
                sample = probe.read(32768)
            try:
                sample.decode('utf-8-sig')
                encoding = 'utf-8-sig'
            except UnicodeDecodeError:
                encoding = 'latin-1'
            with path.open('r', encoding=encoding, newline='') as file:
                first = file.readline()
                delimiter = ';' if first.count(';') >= first.count(',') else ','
                file.seek(0)
                reader = csv.DictReader(file, delimiter=delimiter)
                headers = {re.sub(r'[^A-Z0-9]', '_', (k or '').strip().upper()).strip('_'): k for k in (reader.fieldnames or [])}
                def column(*names):
                    return next((headers[n] for n in names if n in headers), None)
                ccol = column('CPF_CNPJ', 'CNPJ', 'NR_CPF_CNPJ', 'CPF_CNPJ_DEVEDOR')
                rcol = column('NUMERO_INSCRICAO', 'NR_INSCRICAO', 'INSCRICAO')
                acol = column('VALOR_CONSOLIDADO', 'VALOR_TOTAL_CONSOLIDADO', 'VALOR_DIVIDA')
                scol = column('DESCRICAO_TIPO_SITUACAO_INSCRICAO', 'TIPO_SITUACAO_INSCRICAO', 'SITUACAO_INSCRICAO')
                if not ccol or not rcol or not acol:
                    raise ValueError('CSV sem colunas oficiais de CNPJ, inscricao e valor: ' + path.name)
                for row in reader:
                    cnpj = normalize_cnpj(row.get(ccol))
                    registration = str(row.get(rcol) or '').strip()
                    if not re.fullmatch(r'[A-Z0-9]{12}[0-9]{2}', cnpj):
                        # Pessoas fisicas ficam fora desta ferramenta empresarial.
                        continue
                    if not registration:
                        rejected += 1
                        continue
                    amount_raw = str(row.get(acol) or '').replace('R$', '').strip()
                    if ',' in amount_raw:
                        amount_raw = amount_raw.replace('.', '').replace(',', '.')
                    try:
                        value = Decimal(amount_raw)
                        if not value.is_finite() or value < 0:
                            raise InvalidOperation()
                    except InvalidOperation:
                        rejected += 1
                        continue
                    cur = con.execute('INSERT OR IGNORE INTO debts VALUES (?,?,?,?,?)', (cnpj, registration, float(value), str(row.get(scol) or '') if scol else '', path.name))
                    if cur.rowcount:
                        imported += 1
                    else:
                        duplicates += 1
                    if (imported + duplicates) % 10000 == 0:
                        con.commit()
        if not imported:
            raise ValueError('Nenhuma inscricao empresarial valida encontrada. Base anterior preservada.')
        if rejected:
            raise ValueError(f'{rejected} registro(s) empresarial(is) invalido(s). Corrija o arquivo antes de substituir a base; nada foi publicado.')
        metadata = {'competence': competence, 'coverage': str(coverage or 'Arquivos selecionados pelo administrador')[:500],
                    'imported_at': datetime.now(timezone.utc).isoformat(), 'records': str(imported), 'duplicates_ignored': str(duplicates)}
        con.executemany('INSERT INTO metadata VALUES (?,?)', metadata.items())
        con.commit()
        if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Falha de integridade na importacao.')
        con.close()
        # Copia anterior e troca recuperavel; nao remove arquivos da Receita.
        if target.exists():
            import shutil
            backup = target.with_name(target.name + '.backup-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
            shutil.copy2(target, backup)
        os.replace(temp, target)
        return metadata
    finally:
        con.close()
        if temp.exists():
            temp.unlink()
