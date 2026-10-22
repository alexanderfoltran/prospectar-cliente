"""Quatro fontes de oportunidades. Consultas sob demanda, com origem e cobertura explícitas."""
import hashlib
import io
import json
import math
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
import zipfile
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin
from xml.etree import ElementTree as ET

from prospectax_research_net import request, public_url, SourceUnavailable
from prospectax_routes import read_body, owner_key

PNCP = 'https://pncp.gov.br'
OBRAS = 'https://api-publica.obrasgov.gestao.gov.br/obras'
INPI = 'https://revistas.inpi.gov.br'
COMEX = 'https://api-comexstat.mdic.gov.br'
UFS = {'AC':12,'AL':27,'AP':16,'AM':13,'BA':29,'CE':23,'DF':53,'ES':32,'GO':52,'MA':21,'MT':51,'MS':50,'MG':31,'PA':15,'PB':25,'PR':41,'PE':26,'PI':22,'RJ':33,'RN':24,'RS':43,'RO':11,'RR':14,'SC':42,'SP':35,'SE':28,'TO':17}
KINDS = {'pncp', 'obras', 'expansao', 'inpi', 'comex', 'web'}


@contextmanager
def database(path, **options):
    connection = sqlite3.connect(path, timeout=10, **options)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def now():
    return datetime.now(timezone.utc).isoformat()


def text(value, limit=300):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).casefold()) if not unicodedata.combining(c))


def integer(value, low, high, label):
    try:
        number = int(str(value))
    except (ValueError, TypeError):
        raise ValueError(label + ' inválido.') from None
    if not low <= number <= high:
        raise ValueError(label + ' fora do intervalo permitido.')
    return number


def state(value):
    value = text(value, 2).upper()
    if value and value not in UFS:
        raise ValueError('UF inválida.')
    return value


def link(value):
    try:
        return public_url(value)
    except ValueError:
        return ''


def number(value):
    if value is None or value == '':
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


class Editions(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.current = [], None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr':
            self.current = {'content': [], 'links': []}
        if tag == 'a' and self.current is not None:
            self.current['links'].append(urljoin(INPI, attrs.get('href', '')))

    def handle_data(self, value):
        if self.current is not None:
            self.current['content'].append(value)

    def handle_endtag(self, tag):
        if tag == 'tr' and self.current is not None:
            content = ' '.join(self.current['content'])
            xml = next((u for u in self.current['links'] if re.fullmatch(INPI + r'/txt/RM\d{4,5}\.zip', u)), '')
            if xml:
                edition = re.search(r'RM(\d+)\.zip', xml).group(1)
                published = re.search(r'\b(?:\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b', content)
                publication_date = published.group(0) if published else ''
                if '-' in publication_date:
                    publication_date = '/'.join(reversed(publication_date.split('-')))
                self.rows.append({'edition': edition, 'date': publication_date,
                                  'xml': xml, 'pdf': [u for u in self.current['links'] if re.fullmatch(INPI + r'/pdf/Marcas' + edition + r'(?:_[IVX]+)?\.pdf', u)]})
            self.current = None


class Radar:
    def __init__(self, directory, transport=request):
        self.directory = Path(directory) / 'radar'
        self.transport = transport
        self._init_lock = threading.Lock()
        self._locks = [threading.Lock() for _ in range(32)]
        self._ready = False

    def db(self):
        with self._init_lock:
            if not self._ready:
                self.directory.mkdir(parents=True, exist_ok=True)
                with database(self.directory / 'radar.sqlite') as con:
                    con.executescript('''CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL);
                        CREATE TABLE IF NOT EXISTS saved(owner TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(owner,id));''')
                self._ready = True
        return database(self.directory / 'radar.sqlite')

    def cached(self, key, ttl, build):
        key = hashlib.sha256(key.encode()).hexdigest()
        lock = self._locks[int(key[:4], 16) % len(self._locks)]
        with lock:
            with self.db() as con:
                row = con.execute('SELECT payload,expires FROM cache WHERE key=?', (key,)).fetchone()
            if row and row[1] > time.time():
                return {**json.loads(row[0]), 'from_cache': True}
            result = {**build(), 'consulted_at': now()}
            with self.db() as con:
                con.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, json.dumps(result, ensure_ascii=False), time.time() + ttl))
                con.execute('DELETE FROM cache WHERE key NOT IN (SELECT key FROM cache ORDER BY expires DESC LIMIT 180)')
            return {**result, 'from_cache': False}

    def raw(self, url, *, data=None, limit=4_000_000, timeout=22):
        status, raw, _, _ = self.transport(url, method='POST' if data is not None else 'GET',
            headers={'Accept': 'application/json, application/xml, text/html, */*', **({'Content-Type':'application/json'} if data is not None else {})},
            body=json.dumps(data).encode() if data is not None else None, timeout=timeout, limit=limit, redirects=2)
        if status == 429:
            raise SourceUnavailable('A fonte limitou as consultas (HTTP 429). Aguarde alguns minutos antes de tentar novamente.')
        if status not in (200, 204):
            raise SourceUnavailable(f'A fonte respondeu HTTP {status}. Nenhum resultado foi presumido.')
        return raw

    def api(self, url, data=None):
        raw = self.raw(url, data=data)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise SourceUnavailable('A fonte retornou um formato inesperado. Tente novamente mais tarde.') from None

    def pncp(self, data):
        query = text(data.get('q'), 120)
        uf = state(data.get('uf'))
        page = integer(data.get('page', 1), 1, 1000, 'Página')
        mode = data.get('mode', 'edital')
        if mode not in {'edital', 'contrato'}:
            raise ValueError('Tipo de oportunidade inválido.')
        params = {'q': query, 'tipos_documento':mode, 'ordenacao':'-data', 'pagina':page, 'tam_pagina':20}
        if mode == 'edital':
            params['status'] = 'recebendo_proposta'
        if uf:
            params['ufs'] = uf
        url = PNCP + '/api/search/?' + urlencode(params)

        def build():
            payload = self.api(url)
            if not isinstance(payload, dict) or not isinstance(payload.get('items'), list) or not isinstance(payload.get('total'), int):
                raise SourceUnavailable('O PNCP mudou o formato da consulta.')
            rows = []
            for r in payload['items']:
                source_url = link(urljoin(PNCP + '/app/', str(r.get('item_url') or '').lstrip('/')))
                supplier_cnpj = re.sub(r'[^A-Z0-9]', '', text(r.get('fornecedor_ni'), 30).upper())
                supplier_cnpj = supplier_cnpj if re.fullmatch(r'[A-Z0-9]{12}\d{2}', supplier_cnpj) else ''
                rows.append({'kind':'pncp', 'id':text(r.get('numero_controle_pncp') or r.get('id')), 'title':text(r.get('title')),
                    'description':text(r.get('description'), 2600), 'organization':text(r.get('orgao_nome')),
                    'cnpj':text(r.get('orgao_cnpj'), 14), 'city':text(r.get('municipio_nome')), 'uf':text(r.get('uf'), 2),
                    'modality':text(r.get('modalidade_licitacao_nome')), 'status':text(r.get('situacao_nome')),
                    'published':text(r.get('data_publicacao_pncp')), 'deadline':text(r.get('data_fim_recebimento_propostas') or (r.get('data_fim_vigencia') if mode == 'edital' else '')),
                    'contract_end':text(r.get('data_fim_vigencia')) if mode == 'contrato' else '',
                    'value':number(r.get('valor_total_estimado') if mode == 'edital' else r.get('valor_global')),
                    'value_label':'Valor estimado' if mode == 'edital' else 'Valor do contrato',
                    'supplier':text(r.get('fornecedor_nome')), 'supplier_cnpj':supplier_cnpj, 'url':source_url, 'source':'PNCP', 'mode':mode})
            return {'items':rows, 'total':payload['total'], 'page':page, 'has_more':page * 20 < payload['total'],
                'source':'PNCP · índice público de pesquisa', 'source_url':PNCP + '/app/editais',
                'coverage':'Editais com recebimento de propostas aberto no índice consultado.' if mode == 'edital' else 'Contratos publicados no PNCP. O fornecedor contratado não é indicação de interesse em comprar.'}
        return self.cached('pncp-v1:' + url, 600, build)

    def obras(self, data):
        page = integer(data.get('page', 1), 1, 20000, 'Página')
        uf = state(data.get('uf'))
        situation = text(data.get('situation'))
        if situation and situation not in {'Cadastrada','Cancelada','Concluída','Em execução','Inacabada','Paralisada'}:
            raise ValueError('Situação de obra inválida.')
        params = {'pagina':page, 'tamanho_da_pagina':20}
        for key, value in [('uf_principal', uf), ('situacao', situation), ('desc_nome', text(data.get('q'), 120)), ('especie_intervencao', text(data.get('intervention')))]:
            if value:
                params[key] = value
        if params.get('especie_intervencao') not in {None,'Máquinas e Equipamentos','Recuperação','Construção','Reforma','Ampliação','Fabricação'}:
            raise ValueError('Tipo de intervenção inválido.')
        url = OBRAS + '/projeto-investimento?' + urlencode(params)

        def build():
            payload = self.api(url)
            if not isinstance(payload, dict) or not isinstance(payload.get('data'), list) or 'total_items' not in payload:
                raise SourceUnavailable('O Obrasgov mudou o formato da consulta.')
            rows = []
            for r in payload['data']:
                executors = [text(e.get('organizacao_executor') or e.get('nome_executor') or e.get('nome_razao_social')) for e in (r.get('executores') or []) if isinstance(e, dict)]
                rows.append({'kind':'obras', 'id':text(r.get('id_projeto_investimento')), 'title':text(r.get('desc_nome')),
                    'description':text(r.get('desc_projeto'), 2600), 'address':text(r.get('desc_endereco')), 'uf':text(r.get('uf_principal')),
                    'organization':text(r.get('organizacao_resp')), 'cnpj':text(r.get('cnpj_organizacao_resp')),
                    'status':text(r.get('situacao')), 'intervention':text(r.get('especie_intervencao')),
                    'start':text(r.get('dt_inicial_prevista')), 'end':text(r.get('dt_final_prevista')), 'published':text(r.get('dt_cadastro')),
                    'executors':[e for e in executors if e], 'source':'Obrasgov.br',
                    'url':OBRAS+'/projeto-investimento?'+urlencode({'id_projeto_investimento':text(r.get('id_projeto_investimento')),'pagina':1,'tamanho_da_pagina':1})})
            return {'items':rows, 'total':int(payload['total_items']), 'page':page, 'has_more':page < int(payload['total_pages']),
                'source':'Obrasgov.br · dados oficiais', 'source_url':'https://www.gov.br/obrasgov/',
                'coverage':'Projetos cadastrados no Obrasgov, com financiamento federal. A situação é a informada pelo gestor; o cadastro não confirma uma compra em andamento.'}
        return self.cached('obras-v1:' + url, 1800, build)

    def news(self, data):
        query = text(data.get('q'), 100)
        region = text(data.get('region'), 100)
        days = integer(data.get('days', 30), 7, 90, 'Período')
        # User words are literals, never GDELT query operators.
        words = re.findall(r'[^\W_]+', query + ' ' + region, flags=re.UNICODE)[:12]
        literal = ' '.join('"' + norm(word) + '"' for word in words if len(word) > 2)
        # GDELT DOC searches English translations of the Portuguese coverage.
        expression = '("new unit" OR "new factory" OR "new store" OR "new plant" OR "new branch" OR "inaugurates") sourcelang:portuguese sourcecountry:brazil'
        if literal:
            expression += ' ' + literal
        url = 'https://api.gdeltproject.org/api/v2/doc/doc?' + urlencode({'query':expression,'mode':'artlist','format':'json','maxrecords':50,'timespan':str(days)+'d','sort':'datedesc'})

        def build():
            payload = self.api(url)
            # GDELT also returns an empty JSON object for a successful no-match query.
            if payload == {}:
                payload = {'articles':[]}
            if not isinstance(payload, dict) or not isinstance(payload.get('articles'), list):
                raise SourceUnavailable('O índice de notícias não retornou uma lista válida. Tente reduzir o filtro.')
            seen, rows = set(), []
            for r in payload['articles']:
                source_url = link(r.get('url'))
                if not source_url or source_url in seen:
                    continue
                seen.add(source_url)
                captured = text(r.get('seendate'))
                try:
                    captured = datetime.strptime(captured, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).isoformat()
                except ValueError:
                    captured = ''
                rows.append({'kind':'expansao', 'id':hashlib.sha256(source_url.encode()).hexdigest()[:24], 'title':text(r.get('title')),
                    'url':source_url, 'source':text(r.get('domain')), 'captured':captured,
                    'description':'Menção na imprensa. Abra a publicação para confirmar a empresa, a localização e o estágio do investimento.'})
            return {'items':rows, 'total':len(rows), 'page':1, 'has_more':False, 'source':'The GDELT Project · metadados de notícias',
                'source_url':'https://www.gdeltproject.org/', 'coverage':f'Até 50 notícias dos últimos {days} dias, conforme a cobertura do GDELT. Data de detecção no índice; não é necessariamente a data de publicação. Menções não comprovam demanda de compra.'}
        return self.cached('news-v1:' + url, 1800, build)

    def editions(self):
        def build():
            parser = Editions()
            parser.feed(self.raw(INPI + '/rpi/', limit=500_000).decode('utf-8', 'replace'))
            editions = sorted({r['edition']:r for r in parser.rows}.values(), key=lambda r:int(r['edition']), reverse=True)[:8]
            if not editions:
                raise SourceUnavailable('Não foi possível identificar as edições de marcas no portal do INPI.')
            return {'editions':editions, 'source':'INPI · Revista da Propriedade Industrial', 'source_url':INPI + '/rpi/'}
        return self.cached('inpi-editions-v1', 21600, build)

    def _inpi_index(self, edition):
        selected = next((r for r in self.editions()['editions'] if r['edition'] == edition), None)
        if not selected:
            raise ValueError('Selecione uma edição disponível no catálogo do INPI.')
        path = self.directory / ('inpi-v2-' + edition + '.sqlite')
        lock = self._locks[int(edition) % len(self._locks)]
        with lock:
            if path.exists():
                return path, selected
            raw = self.raw(selected['xml'], limit=25_000_000, timeout=30)
            temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    files = [f for f in archive.infolist() if f.filename.lower().endswith('.xml')]
                    if len(files) != 1 or files[0].file_size > 150_000_000:
                        raise SourceUnavailable('A edição excede o limite de importação desta consulta.')
                    with archive.open(files[0]) as source, database(temp) as con:
                        con.execute('CREATE TABLE marks(id INTEGER PRIMARY KEY, number TEXT, search TEXT, ufs TEXT, classes TEXT, codes TEXT, payload TEXT)')
                        first = source.read(2048)
                        if b'<!DOCTYPE' in first.upper() or b'<!ENTITY' in first.upper():
                            raise SourceUnavailable('Formato XML não permitido.')
                        # XMLPullParser avoids extracting files or holding the edition in memory.
                        parser = ET.XMLPullParser(events=('start','end'))
                        root = None
                        count = 0
                        block = first
                        while block:
                            parser.feed(block)
                            for event, element in parser.read_events():
                                if event == 'start' and root is None:
                                    root = element
                                    if element.tag != 'revista' or element.get('numero') != edition:
                                        raise SourceUnavailable('A edição recebida não corresponde à selecionada.')
                                if event != 'end' or element.tag != 'processo':
                                    continue
                                owners = [text(e.get('nome-razao-social'), 400) for e in element.findall('./titulares/titular')]
                                ufs = sorted({text(e.get('uf'), 2) for e in element.findall('./titulares/titular') if e.get('uf')})
                                classes = sorted({str(int(e.get('codigo'))) for e in element.findall('.//classe-nice')
                                                  if str(e.get('codigo','')).isdigit() and 1 <= int(e.get('codigo')) <= 45}, key=int)
                                dispatches = [{'code':text(e.get('codigo')), 'name':text(e.get('nome'))} for e in element.findall('./despachos/despacho')]
                                item = {'kind':'inpi', 'id':edition + ':' + str(count), 'process':text(element.get('numero')),
                                    'title':text(element.findtext('./marca/nome') or 'Marca sem nome textual'), 'owners':owners, 'ufs':ufs, 'classes':classes,
                                    'description':text(' '.join(e.text or '' for e in element.findall('.//especificacao')), 2400),
                                    'dispatches':dispatches, 'deposited':text(element.get('data-deposito')), 'edition':edition,
                                    'published':selected['date'], 'source':'INPI · RPI ' + edition, 'url':selected['pdf'][0] if selected['pdf'] else INPI + '/rpi/'}
                                con.execute('INSERT INTO marks(number,search,ufs,classes,codes,payload) VALUES(?,?,?,?,?,?)',
                                    (item['process'],norm(' '.join([item['title'],*owners,item['description'],item['process']])),
                                     '|'+'|'.join(ufs)+'|','|'+'|'.join(classes)+'|','|'+'|'.join(d['code'] for d in dispatches)+'|',json.dumps(item,ensure_ascii=False)))
                                count += 1
                                element.clear()
                                root.clear()
                            block = source.read(65536)
                        parser.close()
                        if not count:
                            raise SourceUnavailable('A edição não contém processos de marcas reconhecíveis.')
                        con.execute('CREATE INDEX by_number ON marks(number)')
                temp.replace(path)
            except Exception:
                temp.unlink(missing_ok=True)
                raise
        return path, selected

    def inpi(self, data):
        edition = text(data.get('edition'), 5)
        if not re.fullmatch(r'\d{4,5}', edition):
            raise ValueError('Selecione a edição da RPI.')
        uf = state(data.get('uf'))
        page = integer(data.get('page', 1), 1, 10000, 'Página')
        code = data.get('dispatch', 'IPAS009')
        if code not in {'', 'IPAS009', 'IPAS158'}:
            raise ValueError('Tipo de publicação inválido.')
        nice = text(data.get('nice'), 2)
        if nice:
            nice = str(integer(nice, 1, 45, 'Classe Nice'))
        path, selected = self._inpi_index(edition)
        conditions, values = [], []
        for token in norm(text(data.get('q'), 120)).split()[:8]:
            conditions.append('instr(search,?)>0');values.append(token)
        for column, value in [('ufs', uf), ('classes', nice), ('codes', code)]:
            if value:
                conditions.append('instr('+column+',?)>0');values.append('|'+value+'|')
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with database(path.resolve().as_uri() + '?mode=ro', uri=True) as con:
            total = con.execute('SELECT count(*) FROM marks' + where, values).fetchone()[0]
            rows = [json.loads(r[0]) for r in con.execute('SELECT payload FROM marks' + where + ' ORDER BY id LIMIT 20 OFFSET ?', [*values,(page-1)*20])]
        return {'items':rows, 'total':total, 'page':page, 'has_more':page*20 < total, 'consulted_at':now(), 'source':'INPI · RPI '+edition,
            'source_url':INPI+'/rpi/', 'pdfs':selected['pdf'], 'coverage':'Pesquisa na edição selecionada, com dados simplificados do XML oficial. Confira o ato completo no PDF. Um pedido publicado não equivale a registro concedido nem confirma lançamento de produto.'}

    def comex_meta(self):
        def build():
            payload = self.api(COMEX + '/general/dates/updated')
            raw = payload.get('data') if isinstance(payload,dict) else None
            if not isinstance(raw,dict) or not raw.get('year') or not raw.get('monthNumber'):
                raise SourceUnavailable('O Comex Stat não informou o período disponível.')
            year = integer(raw['year'], 1997, 2200, 'Ano da fonte')
            month = integer(raw['monthNumber'], 1, 12, 'Mês da fonte')
            end = f'{year:04d}-{month:02d}'
            start_index = year*12+month-1-11
            start = f'{start_index//12:04d}-{start_index%12+1:02d}'
            return {'latest':end, 'start':start, 'updated':text(raw.get('updated')), 'source':'MDIC · Comex Stat', 'source_url':'https://comexstat.mdic.gov.br/pt/home'}
        return self.cached('comex-meta-v1', 21600, build)

    def comex(self, data):
        flow = data.get('flow','export')
        if flow not in {'import','export'}:
            raise ValueError('Fluxo comercial inválido.')
        ncm = text(data.get('ncm'), 12)
        if not re.fullmatch(r'\d{8}', ncm):
            raise ValueError('Informe os oito dígitos da NCM, sem pontuação.')
        start, end = text(data.get('from'),7), text(data.get('to'),7)
        try:
            a, b = date.fromisoformat(start+'-01'), date.fromisoformat(end+'-01')
        except ValueError:
            raise ValueError('Informe o período no formato ano-mês.') from None
        span = (b.year-a.year)*12 + b.month-a.month
        meta = self.comex_meta()
        if a.year < 1997 or not 0 <= span <= 23 or end > meta['latest']:
            raise ValueError('Use até 24 meses, entre janeiro de 1997 e o último mês disponível: '+meta['latest']+'.')
        uf = state(data.get('uf'))
        filters = [{'filter':'ncm', 'values':[ncm]}]
        if uf:
            filters.append({'filter':'state', 'values':[UFS[uf]]})
        body = {'flow':flow,'monthDetail':True,'period':{'from':start,'to':end},'filters':filters,'details':['country'],'metrics':['metricFOB','metricKG']}

        def build():
            payload = self.api(COMEX+'/general?language=pt', data=body)
            raw = payload.get('data') if isinstance(payload,dict) else None
            if not isinstance(payload,dict) or payload.get('success') is False:
                raise SourceUnavailable('O Comex Stat não concluiu a consulta. Reduza o período ou tente mais tarde.')
            records = raw.get('list') if isinstance(raw,dict) else raw
            if not isinstance(records,list):
                raise SourceUnavailable('O Comex Stat mudou o formato da resposta.')
            countries, months = defaultdict(lambda:{'value':0.,'kg':0.}), defaultdict(float)
            for r in records:
                value, kg = number(r.get('metricFOB')), number(r.get('metricKG'))
                country = text(r.get('country'))
                if value is None or kg is None or not country:
                    raise SourceUnavailable('A fonte retornou linhas sem país ou métricas válidas. Nenhum total foi calculado.')
                countries[country]['value'] += value;countries[country]['kg'] += kg
                year, month = text(r.get('year')), text(r.get('monthNumber') or r.get('month'))
                if year.isdigit() and month.isdigit() and 1 <= int(month) <= 12:
                    months[f'{int(year):04d}-{int(month):02d}'] += value
            ranked = sorted(countries.items(), key=lambda r:r[1]['value'], reverse=True)
            rows = [{'kind':'comex','id':flow+':'+ncm+':'+start+':'+end+':'+uf+':'+country,'title':country,'country':country,
                'value':v['value'],'kg':v['kg'],'ncm':ncm,'flow':flow,'period':start+' a '+end,'source':'MDIC · Comex Stat',
                'url':'https://comexstat.mdic.gov.br/pt/geral','description':('Exportação' if flow=='export' else 'Importação')+' · NCM '+ncm+' · '+start+' a '+end} for country,v in ranked]
            return {'items':rows,'total':len(rows),'page':1,'has_more':False,'total_fob':sum(r['value'] for r in rows),'total_kg':sum(r['kg'] for r in rows),
                'months':[{'month':m,'value':v} for m,v in sorted(months.items())], 'source':'MDIC · Comex Stat', 'source_url':'https://comexstat.mdic.gov.br/pt/home',
                'coverage':'Estatísticas agregadas por país e produto. Valores em US$ FOB e peso líquido em kg. Não identifica compradores ou fornecedores individuais. UF segue o critério da base oficial.',
                'updated':meta['updated'], 'latest':meta['latest'], 'ncm':ncm,'flow':flow, 'period':{'from':start,'to':end}}
        return self.cached('comex-v1:'+json.dumps(body,sort_keys=True),3600,build)

    def saved(self, owner):
        with self.db() as con:
            rows = con.execute('SELECT id,payload,created,updated FROM saved WHERE owner=? ORDER BY updated DESC LIMIT 300',(owner,)).fetchall()
        return {'items':[dict(json.loads(r[1]), saved_id=r[0], saved_at=r[2], updated_at=r[3]) for r in rows]}

    def save(self, owner, data):
        item = data.get('item')
        if not isinstance(item,dict) or item.get('kind') not in KINDS:
            raise ValueError('Oportunidade inválida.')
        url = link(item.get('url'))
        title = text(item.get('title'))
        if not url or not title:
            raise ValueError('A oportunidade precisa de título e fonte pública.')
        # Only public, bounded display fields are persisted from the browser.
        allowed = {'kind','id','title','description','url','source','organization','supplier','supplier_cnpj','cnpj','city','uf','status','published','deadline','value','kg','value_label','country','ncm','period','edition','process','captured','consulted_at','modality','contract_end','intervention','start','end','address','deposited'}
        clean = {k:(number(v) if k in {'value','kg'} else text(v,2600 if k=='description' else 350)) for k,v in item.items() if k in allowed}
        for key in ['owners','ufs','classes','executors']:
            if isinstance(item.get(key),list):
                clean[key] = [text(v,400) for v in item[key][:25]]
        if isinstance(item.get('dispatches'),list):
            clean['dispatches'] = [{'code':text(v.get('code')), 'name':text(v.get('name'))} for v in item['dispatches'][:10] if isinstance(v,dict)]
        clean['url'] = url
        clean['note'] = str(data.get('note') or '').strip()[:2000]
        identifier = hashlib.sha256((clean['kind']+'\0'+text(item.get('id'))+'\0'+url).encode()).hexdigest()[:32]
        stamp = now()
        with self.db() as con:
            previous = con.execute('SELECT payload FROM saved WHERE owner=? AND id=?',(owner,identifier)).fetchone()
            if previous and 'note' not in data:
                clean['note'] = json.loads(previous[0]).get('note','')
            if not previous and con.execute('SELECT count(*) FROM saved WHERE owner=?',(owner,)).fetchone()[0] >= 300:
                raise ValueError('Limite de 300 oportunidades salvas. Remova uma antes de salvar outra.')
            con.execute('INSERT INTO saved VALUES(?,?,?,?,?) ON CONFLICT(owner,id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated',
                (owner,identifier,json.dumps(clean,ensure_ascii=False),stamp,stamp))
        return {'ok':True,'id':identifier}

    def remove(self, owner, data):
        identifier = str(data.get('id') or '')
        if not re.fullmatch(r'[a-f0-9]{32}',identifier):
            raise ValueError('Identificador inválido.')
        with self.db() as con:
            con.execute('DELETE FROM saved WHERE owner=? AND id=?',(owner,identifier))
        return {'ok':True}


def get_route(handler, path, qs, session, ctx):
    if path.startswith('/api/radar/auto/'):
        from prospectax_radar_auto import get_route as automatic_get
        return automatic_get(handler,path,qs,session,ctx)
    if not path.startswith('/api/radar/'):
        return False
    if not session:
        ctx['_write_json'](handler, {'error':'Não autenticado'}, 401);return True
    service = ctx['_RADAR']
    data = {k:v[0] for k,v in qs.items() if v}
    routes = {'pncp':service.pncp, 'obras':service.obras, 'expansao':service.news, 'inpi/search':service.inpi, 'comex/search':service.comex}
    name = path.removeprefix('/api/radar/')
    try:
        if name in routes:
            result = routes[name](data)
        elif name == 'inpi/editions':
            result = service.editions()
        elif name == 'comex/meta':
            result = service.comex_meta()
        elif name == 'saved':
            result = service.saved(owner_key(session))
        else:
            ctx['_write_json'](handler, {'error':'Rota não encontrada'},404);return True
        ctx['_write_json'](handler,result,200)
    except SourceUnavailable as exc:
        ctx['_write_json'](handler,{'error':str(exc),'source_unavailable':True},502)
    except (ValueError,TypeError) as exc:
        ctx['_write_json'](handler,{'error':str(exc)},400)
    except Exception:
        ctx['_write_json'](handler,{'error':'Não foi possível concluir esta consulta. A fonte pode estar indisponível; nenhum resultado foi presumido.'},502)
    return True


def post_route(handler,path,session,ctx):
    if path.startswith('/api/radar/auto/'):
        from prospectax_radar_auto import post_route as automatic_post
        return automatic_post(handler,path,session,ctx)
    if path not in {'/api/radar/save','/api/radar/remove'}:
        return False
    if not session:
        ctx['_write_json'](handler,{'error':'Não autenticado'},401);return True
    try:
        data = read_body(handler,limit=24000)
        service = ctx['_RADAR']
        result = (service.save if path.endswith('/save') else service.remove)(owner_key(session),data)
        ctx['_write_json'](handler,result,200)
    except (ValueError,TypeError) as exc:
        ctx['_write_json'](handler,{'error':str(exc)},400)
    except Exception:
        ctx['_write_json'](handler,{'error':'Não foi possível salvar a oportunidade. Tente novamente.'},500)
    return True
