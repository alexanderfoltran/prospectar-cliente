"""Ferramentas de descoberta: filtros locais, segmentos e dados publicos.

Nao inicia rede, nao le credenciais e nao abre a base ao importar.
"""
import json
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def normalize_cnpj(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def clean_cnpjs(values):
    return sorted({v for v in (normalize_cnpj(x) for x in values) if re.fullmatch(r'[A-Z0-9]{12}[0-9]{2}', v)})


def norm(value):
    value = unicodedata.normalize('NFKD', str(value or '').casefold())
    return ''.join(c for c in value if not unicodedata.combining(c)).strip()


BUYER_SEGMENTS = [
    {'id': 'beleza', 'name': 'Estetica, saloes e barbearias',
     'terms': 'estetica beleza barbearia barbeiro salao cabelo capilar cosmetico descartavel depilacao manicure esmalte navalha shampoo utilidades estetica',
     'cnaes': [('9602502', 'Atividades de estetica e outros servicos de cuidados com a beleza'), ('9602501', 'Cabeleireiros, manicure e pedicure')],
     'why': 'Estabelecimentos que podem consumir produtos e materiais de beleza. Confirme a linha utilizada no atendimento.', 'map_tags': ['shop=beauty', 'shop=hairdresser']},
    {'id': 'revenda_cosmeticos', 'name': 'Lojas de cosmeticos e perfumaria',
     'terms': 'cosmetico maquiagem perfumaria perfume beleza revenda higiene sabonete shampoo',
     'cnaes': [('4772500', 'Comercio varejista de cosmeticos, produtos de perfumaria e de higiene pessoal')],
     'why': 'Possiveis pontos de revenda de cosmeticos e higiene pessoal.', 'map_tags': ['shop=cosmetics']},
    {'id': 'alimentacao', 'name': 'Restaurantes e lanchonetes',
     'terms': 'alimenticio alimento bebida restaurante lanchonete embalagem descartavel cozinha limpeza copo prato guardanapo',
     'cnaes': [('5611201', 'Restaurantes e similares'), ('5611203', 'Lanchonetes, casas de cha, de sucos e similares')],
     'why': 'Operacoes de alimentacao que podem comprar insumos, embalagens e materiais de consumo.', 'map_tags': ['amenity=restaurant', 'amenity=fast_food', 'amenity=cafe']},
    {'id': 'mercados', 'name': 'Mercados e mercearias',
     'terms': 'mercado supermercado mercearia alimento bebida limpeza utilidades revenda',
     'cnaes': [('4711302', 'Comercio varejista de mercadorias em geral, com predominancia de produtos alimenticios - supermercados'), ('4712100', 'Comercio varejista de mercadorias em geral, com predominancia de produtos alimenticios - minimercados, mercearias e armazens')],
     'why': 'Pontos de revenda de alimentos e produtos de consumo.', 'map_tags': ['shop=supermarket', 'shop=convenience']},
    {'id': 'pet', 'name': 'Pet shops', 'terms': 'pet animal racao veterinario cachorro gato banho tosa',
     'cnaes': [('4789004', 'Comercio varejista de animais vivos e de artigos e alimentos para animais de estimacao')],
     'why': 'Possiveis revendedores de alimentos, higiene e acessorios para animais.', 'map_tags': ['shop=pet']},
    {'id': 'construcao', 'name': 'Lojas de materiais de construcao',
     'terms': 'construcao ferramenta eletrico hidraulico ferragem tinta obra material',
     'cnaes': [('4744099', 'Comercio varejista de materiais de construcao em geral'), ('4744001', 'Comercio varejista de ferragens e ferramentas')],
     'why': 'Revenda de materiais e ferramentas; selecione os codigos compativeis com seu catalogo.', 'map_tags': ['shop=hardware', 'shop=doityourself']},
    {'id': 'auto', 'name': 'Autopecas e oficinas',
     'terms': 'automotivo autopeca oficina carro veiculo lubrificante ferramenta mecanico',
     'cnaes': [('4530703', 'Comercio a varejo de pecas e acessorios novos para veiculos automotores'), ('4520001', 'Servicos de manutencao e reparacao mecanica de veiculos automotores')],
     'why': 'Possiveis compradores de pecas, ferramentas e materiais automotivos.', 'map_tags': ['shop=car_parts', 'shop=car_repair']},
]


def buyer_suggestions(query='', catalog=None):
    words = [w for w in re.findall(r'[a-z0-9]+', norm(query)) if len(w) > 2 and w not in {'para', 'com', 'uma', 'distribuidora', 'produtos', 'produto', 'vendo'}]
    official = {str(x['code']): x['desc'] for x in (catalog or [])}
    result = []
    for seg in BUYER_SEGMENTS:
        score = sum(any(term.startswith(w) or w.startswith(term) for term in seg['terms'].split()) for w in words)
        if words and not score:
            continue
        result.append({'id': seg['id'], 'name': seg['name'], 'reason': seg['why'], 'score': score,
                       'cnaes': [{'code': code, 'desc': official.get(code, desc)} for code, desc in seg['cnaes']]})
    return sorted(result, key=lambda x: (-x['score'], x['name']))


FILTER_KEYS = ('discovery_contact', 'discovery_porte', 'discovery_cep', 'discovery_tipo',
               'discovery_exclude', 'discovery_bairro', 'discovery_abertura_de', 'discovery_abertura_ate')


def discovery_filters(qs):
    def one(key):
        value = qs.get(key, '')
        return str(value[0] if isinstance(value, list) and value else value or '').strip()
    f = {key.removeprefix('discovery_'): one(key) for key in FILTER_KEYS}
    if f['contact'] not in ('', 'telefone', 'email', 'ambos', 'qualquer', 'sem_contato'):
        raise ValueError('Filtro de contato invalido.')
    if f['porte'] not in ('', '01', '03', '05'):
        raise ValueError('Porte invalido.')
    if f['tipo'] not in ('', 'matriz', 'filial'):
        raise ValueError('Tipo de estabelecimento invalido.')
    f['cep'] = re.sub(r'[^0-9]', '', f['cep'])
    if len(f['cep']) > 8:
        raise ValueError('Informe ate 8 digitos de CEP.')
    if len(f['exclude']) > 250 or len(f['bairro']) > 100:
        raise ValueError('Filtro de texto muito longo.')
    for key in ('abertura_de', 'abertura_ate'):
        if f[key]:
            try:
                f[key] = date.fromisoformat(f[key]).isoformat()
            except ValueError:
                raise ValueError('Informe uma data de abertura valida.') from None
    if f['abertura_de'] and f['abertura_ate'] and f['abertura_de'] > f['abertura_ate']:
        raise ValueError('A data inicial precisa ser anterior a data final.')
    return {key: value for key, value in f.items() if value}


def filter_sql(filters, available_columns):
    f = filters or {}
    q = lambda v: "'" + str(v).replace("'", "''") + "'"
    clauses = []
    tel_parts = ["length(regexp_replace(coalesce(p.telefone,''), '[^0-9]', '', 'g')) >= 10"]
    if 'telefone_2' in available_columns:
        tel_parts.append("length(regexp_replace(coalesce(p.telefone_2,''), '[^0-9]', '', 'g')) >= 10")
    tel = '(' + ' OR '.join(tel_parts) + ')'
    email = "regexp_matches(trim(coalesce(p.email,'')), '^[^ @]+@[^ @]+[.][^ @]+$')"
    contact = f.get('contact')
    if contact:
        clauses.append({'telefone': tel, 'email': email, 'ambos': f'({tel} AND {email})',
                        'qualquer': f'({tel} OR {email})', 'sem_contato': f'(NOT {tel} AND NOT {email})'}[contact])
    if f.get('porte'):
        clauses.append(f"lpad(trim(coalesce(p.porte_empresa,'')),2,'0') = {q(f['porte'])}")
    if f.get('tipo'):
        if 'matriz_filial' in available_columns:
            clauses.append(f"trim(p.matriz_filial) = {'1' if f['tipo'] == 'matriz' else '2'}::VARCHAR")
        else:
            # Ordem 0001 nao comprova matriz: exigimos o campo oficial para nao excluir compradores.
            raise ValueError('Sua base ainda nao possui o indicador oficial de matriz/filial. Atualize o indice para usar esse filtro.')
    for column, key in [('cep', 'cep'), ('bairro', 'bairro')]:
        if f.get(key):
            if column not in available_columns:
                raise ValueError(f'Sua base ainda nao possui {column} no indice. Atualize o indice para usar esse filtro.')
            if column == 'cep':
                clauses.append(f"starts_with(regexp_replace(coalesce(p.cep,''), '[^0-9]', '', 'g'), {q(f[key])})")
            else:
                clauses.append(f"contains(lower(strip_accents(coalesce(p.bairro,''))), {q(norm(f[key]))})")
    if f.get('abertura_de') or f.get('abertura_ate'):
        if 'abertura' not in available_columns:
            raise ValueError('Sua base ainda nao possui data de abertura no indice. Atualize o indice para usar esse filtro.')
        expr = "coalesce(try_cast(p.abertura AS DATE), cast(try_strptime(p.abertura, '%Y%m%d') AS DATE))"
        for key, op in [('abertura_de', '>='), ('abertura_ate', '<=')]:
            if f.get(key):
                clauses.append(f'{expr} {op} DATE {q(f[key])}')
    for word in [norm(v) for v in f.get('exclude', '').split(',') if norm(v)][:10]:
        clauses.append(f"NOT contains(lower(strip_accents(coalesce(p.nome,''))), {q(word)})")
    return clauses


def free_pitch(data):
    name = str(data.get('nome') or 'sua empresa').strip()[:150]
    offer = str(data.get('oferta') or 'produtos para o seu segmento').strip()[:300]
    city = str(data.get('cidade') or '').strip()[:100]
    place = f' na regiao de {city}' if city else ''
    return {'pitch': f'Ola! Trabalho com distribuicao de {offer}{place}. Gostaria de apresentar nosso catalogo para a {name}. Quem e a pessoa responsavel pelas compras? Posso encaminhar as opcoes para avaliacao?',
            'source': 'roteiro_local', 'cost': 0}


def json_request(url, *, timeout=20, data=None, headers=None, max_bytes=4_000_000):
    request = Request(url, data=data, headers={'User-Agent': 'ProspectaX-Local/3.0', 'Accept': 'application/json', **(headers or {})})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError('A resposta excedeu o limite desta consulta. Reduza a area ou o termo.')
    return json.loads(raw.decode('utf-8'))


class DiscoveryCache:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()

    def get(self, key, ttl=86400):
        import hashlib
        path = self.directory / (hashlib.sha256(key.encode()).hexdigest() + '.json')
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            if time.time() - payload['ts'] < ttl:
                return payload['data']
        except (OSError, ValueError, KeyError):
            pass
        return None

    def put(self, key, data):
        import hashlib
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / (hashlib.sha256(key.encode()).hexdigest() + '.json')
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'ts': time.time(), 'data': data}, ensure_ascii=False), encoding='utf-8')
            tmp.replace(path)


class DiscoveryConflict(ValueError):
    def __init__(self, state, version):
        super().__init__('Os dados foram alterados em outra janela. Recarregue antes de substituir.')
        self.state, self.version = state, version


from contextlib import contextmanager


class PersonalDiscoveryStore:
    """Estado de busca/favoritos por usuario com transacoes locais, fora dos assets."""
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _connect(self):
        import sqlite3
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=15)
        con.execute('CREATE TABLE IF NOT EXISTS discovery_state (owner TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)')
        if 'version' not in {r[1] for r in con.execute('PRAGMA table_info(discovery_state)')}:
            con.execute('ALTER TABLE discovery_state ADD COLUMN version INTEGER NOT NULL DEFAULT 0')
        try:
            with con:
                yield con
        finally:
            con.close()

    def load(self, owner):
        return self.load_record(owner)['state']

    def load_record(self, owner):
        with self._connect() as con:
            row = con.execute('SELECT payload,version FROM discovery_state WHERE owner=?', (owner,)).fetchone()
        return {'state': json.loads(row[0]), 'version': row[1]} if row else {'state': {}, 'version': 0}

    def save(self, owner, payload, expected_version=None):
        if not owner or not isinstance(payload, dict):
            raise ValueError('Estado de prospeccao invalido.')
        # Novos recursos guardam somente o estado explicitamente conhecido.
        payload = {k: payload[k] for k in ('leadTools', 'savedSearches', 'discovery', 'productSelection') if k in payload}
        if 'leadTools' in payload and (not isinstance(payload['leadTools'], dict) or len(payload['leadTools']) > 20000):
            raise ValueError('Lista de favoritos invalida ou muito grande.')
        if 'savedSearches' in payload and (not isinstance(payload['savedSearches'], list) or len(payload['savedSearches']) > 100):
            raise ValueError('Lista de buscas invalida.')
        raw = json.dumps(payload, ensure_ascii=False)
        if len(raw.encode('utf-8')) > 4_000_000:
            raise ValueError('Estado de prospeccao excedeu 4 MB.')
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            current = con.execute('SELECT payload,version FROM discovery_state WHERE owner=?', (owner,)).fetchone()
            version = current[1] if current else 0
            if expected_version is None or expected_version != version:
                raise DiscoveryConflict(json.loads(current[0]) if current else {}, version)
            version += 1
            con.execute('INSERT INTO discovery_state(owner,payload,updated_at,version) VALUES (?,?,?,?) ON CONFLICT(owner) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at, version=excluded.version',
                        (owner, raw, datetime.now(timezone.utc).isoformat(), version))
        return {'ok': True, 'version': version}


UF_NAMES = dict(zip('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split(),
    ['Acre','Alagoas','Amapa','Amazonas','Bahia','Ceara','Distrito Federal','Espirito Santo','Goias','Maranhao','Mato Grosso','Mato Grosso do Sul','Minas Gerais','Para','Paraiba','Parana','Pernambuco','Piaui','Rio de Janeiro','Rio Grande do Norte','Rio Grande do Sul','Rondonia','Roraima','Santa Catarina','Sao Paulo','Sergipe','Tocantins']))


class PublicPlaces:
    """Consultas pontuais, cache 24h e teto por instalacao. Sem busca em segundo plano."""
    def __init__(self, directory, request_json=json_request):
        self.cache = DiscoveryCache(Path(directory) / 'maps_cache')
        self.budget_file = Path(directory) / 'maps_budget.json'
        self.request_json = request_json
        self.lock = threading.Lock()
        self.last_request = 0
        self.cooldown_until = 0

    def _request(self, url, **kwargs):
        from urllib.error import HTTPError
        with self.lock:
            if time.time() < self.cooldown_until:
                raise ValueError('A fonte pediu uma pausa. Aguarde antes de consultar novamente.')
            day = datetime.now(timezone.utc).date().isoformat()
            try:
                budget = json.loads(self.budget_file.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                budget = {}
            used = int(budget.get('count', 0)) if budget.get('day') == day else 0
            if used >= 40:
                raise ValueError('Limite de 40 consultas publicas por dia nesta instalacao atingido. Resultados em cache continuam disponiveis.')
            self.budget_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.budget_file.with_suffix('.tmp')
            tmp.write_text(json.dumps({'day': day, 'count': used + 1}), encoding='utf-8')
            tmp.replace(self.budget_file)
            time.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                return self.request_json(url, **kwargs)
            except HTTPError as exc:
                if exc.code in (406, 429):
                    from email.utils import parsedate_to_datetime
                    wait = 60
                    value = exc.headers.get('Retry-After', '') if exc.headers else ''
                    try:
                        wait = max(wait, int(value))
                    except ValueError:
                        try:
                            wait = max(wait, parsedate_to_datetime(value).timestamp() - time.time())
                        except (TypeError, ValueError):
                            pass
                    self.cooldown_until = time.time() + min(wait, 86400)
                raise

    def cities(self, city, uf):
        city, uf = str(city or '').strip()[:100], str(uf or '').upper()
        if len(city) < 3 or uf not in UF_NAMES:
            raise ValueError('Informe cidade e UF para localizar o centro da busca.')
        key = 'city-v1:' + norm(city) + ':' + uf
        cached = self.cache.get(key, ttl=30*86400)
        if cached is not None:
            return {'cities': cached, 'from_cache': True}
        raw = self._request('https://photon.komoot.io/api/?' + urlencode({'q': f'{city}, {UF_NAMES[uf]}, Brasil', 'limit': 8}), timeout=15)
        cities = []
        for item in raw.get('features', []):
            p = item.get('properties', {})
            if p.get('countrycode', '').upper() != 'BR' or norm(p.get('state')) != norm(UF_NAMES[uf]):
                continue
            if p.get('osm_value') not in ('city', 'town', 'village', 'municipality'):
                continue
            name = p.get('name', '')
            if norm(city) not in norm(name) and norm(name) not in norm(city):
                continue
            coords = item.get('geometry', {}).get('coordinates', [])
            if len(coords) != 2:
                continue
            cities.append({'name': name, 'uf': uf, 'lat': coords[1], 'lon': coords[0]})
        self.cache.put(key, cities)
        return {'cities': cities, 'from_cache': False}

    def search(self, segment, lat, lon, radius=3000):
        import math
        seg = next((s for s in BUYER_SEGMENTS if s['id'] == segment), None)
        if not seg:
            raise ValueError('Selecione um segmento disponivel.')
        lat, lon, radius = float(lat), float(lon), int(radius)
        if not (math.isfinite(lat) and math.isfinite(lon) and -34 <= lat <= 6 and -74 <= lon <= -28 and 500 <= radius <= 15000):
            raise ValueError('Informe um ponto no Brasil e raio entre 0,5 e 15 km.')
        key = f'places-v1:{segment}:{lat:.5f}:{lon:.5f}:{radius}'
        cached = self.cache.get(key)
        if cached is not None:
            return {**cached, 'from_cache': True}
        parts = []
        for tag in seg['map_tags']:
            key_tag, val = tag.split('=')
            parts.append(f'nwr(around:{radius},{lat:.5f},{lon:.5f})["{key_tag}"="{val}"];')
        query = '[out:json][timeout:20];(' + ''.join(parts) + ');out center tags 201;'
        raw = self._request('https://overpass.private.coffee/api/interpreter', data=urlencode({'data': query}).encode(),
                            headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=28)
        if raw.get('remark'):
            raise ValueError('A fonte nao concluiu a consulta. Tente um raio menor.')
        if not isinstance(raw.get('elements'), list):
            raise ValueError('Resposta inesperada do mapa publico.')
        rows, seen = [], set()
        for item in raw['elements'][:200]:
            tags = item.get('tags', {})
            name = tags.get('name') or tags.get('brand')
            obj = f"{item.get('type')}/{item.get('id')}"
            if not name or obj in seen:
                continue
            seen.add(obj)
            cnpj = normalize_cnpj(tags.get('ref:cnpj'))
            rows.append({'id': obj, 'nome': name, 'telefone': tags.get('contact:phone') or tags.get('phone') or '',
                         'email': tags.get('contact:email') or tags.get('email') or '',
                         'website': tags.get('contact:website') or tags.get('website') or '',
                         'endereco': ', '.join(str(tags[k]) for k in ('addr:street','addr:housenumber','addr:suburb','addr:city') if tags.get(k)),
                         'cnpj': cnpj if re.fullmatch(r'[A-Z0-9]{12}[0-9]{2}', cnpj) else '',
                         'source_url': 'https://www.openstreetmap.org/' + obj, 'source': 'OpenStreetMap'})
        result = {'rows': rows, 'total': len(rows), 'limited': len(raw['elements']) > 200,
                  'consulted_at': datetime.now(timezone.utc).isoformat(), 'radius_m': radius,
                  'attribution': '© OpenStreetMap contributors — ODbL', 'source_url': 'https://www.openstreetmap.org/copyright'}
        self.cache.put(key, result)
        return {**result, 'from_cache': False}
