"""Rede de parceiros: cadastro privado, vitrine aprovada e métricas agregadas."""
import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, parse_qs

CATEGORIES = {
    'produtos': 'Produtos para revenda', 'acessorios': 'Acessórios e complementos',
    'embalagens': 'Embalagens e apresentação', 'insumos': 'Insumos e equipamentos',
    'tecnologia': 'Tecnologia e gestão', 'marketing': 'Marketing e vendas',
    'logistica': 'Entrega e logística', 'servicos': 'Serviços para empresas',
}
GOALS = {'ampliar': 'Ampliar produtos e serviços', 'custos': 'Reduzir custos', 'operacao': 'Melhorar a operação'}
STATES = {'rascunho', 'aguardando', 'aprovado', 'pausado'}
UFS = set('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split())
SEGMENTS = [
    (('roupa', 'moda', 'vestuario', 'boutique'), ['acessorios', 'embalagens', 'marketing', 'tecnologia']),
    (('restaurante', 'lanchonete', 'padaria', 'aliment', 'mercado'), ['insumos', 'embalagens', 'logistica', 'tecnologia']),
    (('oficina', 'autopec', 'automotiv', 'mecanica'), ['insumos', 'acessorios', 'servicos', 'tecnologia']),
    (('beleza', 'salao', 'estetica', 'barbearia'), ['produtos', 'insumos', 'marketing', 'tecnologia']),
    (('construcao', 'material', 'engenharia'), ['insumos', 'produtos', 'logistica', 'servicos']),
]


def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFD', str(value).lower()) if unicodedata.category(c) != 'Mn')


def text(value, maximum=500):
    if not isinstance(value, str):
        raise ValueError('Preencha os campos de texto corretamente.')
    if len(value.strip()) > maximum:
        raise ValueError(f'Um campo excedeu o limite de {maximum} caracteres.')
    return value.strip()


def url(value):
    value = text(value, 1000)
    if not value:
        return ''
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or any(c.isspace() for c in value):
        raise ValueError('Use um endereço de site completo, começando com https:// ou http://.')
    return value


def string_list(value, allowed=None, maximum=30):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('Lista de opções inválida.')
    result = list(dict.fromkeys(text(item, 120) for item in value if item))
    if allowed is not None and any(item not in allowed for item in result):
        raise ValueError('Uma opção selecionada não é válida.')
    return result


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Cadastro inválido.')
    item = {key: text(data.get(key, ''), limit) for key, limit in {
        'name': 120, 'description': 1800, 'products': 1800, 'city': 100, 'benefit': 1200,
        'conditions': 1200, 'minimum_order': 120, 'contact_name': 100, 'email': 180,
        'partnership_notes': 2000,
    }.items()}
    if not item['name'] or not item['description']:
        raise ValueError('Informe o nome e a descrição do fornecedor.')
    item['category'] = text(data.get('category', ''), 40)
    item['status'] = text(data.get('status', 'rascunho'), 30)
    if item['category'] not in CATEGORIES or item['status'] not in STATES:
        raise ValueError('Categoria ou situação inválida.')
    item['website'] = url(data.get('website', ''))
    item['logo'] = url(data.get('logo', ''))
    item['uf'] = text(data.get('uf', ''), 2).upper()
    if item['uf'] and item['uf'] not in UFS:
        raise ValueError('Informe uma UF válida.')
    item['cnpj'] = re.sub(r'[^A-Z0-9]', '', text(data.get('cnpj', ''), 25).upper())
    if item['cnpj'] and not re.fullmatch(r'[A-Z0-9]{12}[0-9]{2}', item['cnpj']):
        raise ValueError('Informe um CNPJ completo ou deixe o campo vazio.')
    item['whatsapp'] = re.sub(r'\D', '', text(data.get('whatsapp', ''), 35))
    if item['whatsapp'] and not 10 <= len(item['whatsapp']) <= 15:
        raise ValueError('Informe o WhatsApp com DDD e, se necessário, código do país.')
    if len(item['whatsapp']) in {10, 11}:
        item['whatsapp'] = '55' + item['whatsapp']
    if item['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', item['email']):
        raise ValueError('Informe um e-mail válido.')
    item['coverage'] = string_list(data.get('coverage', []), UFS)
    item['segments'] = string_list(data.get('segments', []))
    item['goals'] = string_list(data.get('goals', []), GOALS)
    for key in ('national', 'wholesale', 'small_business', 'sponsored'):
        if not isinstance(data.get(key, False), bool):
            raise ValueError('Marcação inválida.')
        item[key] = data.get(key, False)
    item['sponsor_scope'] = text(data.get('sponsor_scope', 'categoria'), 20)
    if item['sponsor_scope'] not in {'categoria', 'regional', 'geral'}:
        raise ValueError('Tipo de patrocínio inválido.')
    item['sponsor_ufs'] = string_list(data.get('sponsor_ufs', []), UFS)
    if item['sponsored'] and item['sponsor_scope'] == 'regional' and not item['sponsor_ufs']:
        raise ValueError('Selecione ao menos uma UF para o patrocínio regional.')
    for key in ('sponsor_start', 'sponsor_end', 'benefit_until'):
        item[key] = text(data.get(key, ''), 10)
        if item[key]:
            try:
                date.fromisoformat(item[key])
            except ValueError:
                raise ValueError('Data do patrocínio inválida.') from None
    if item['sponsor_start'] and item['sponsor_end'] and item['sponsor_start'] > item['sponsor_end']:
        raise ValueError('O término deve ser igual ou posterior ao início do patrocínio.')
    return item


def suggestions(segment, goal):
    categories = next((cats[:] for terms, cats in SEGMENTS if any(term in norm(segment) for term in terms)), [])
    if goal == 'ampliar':
        categories = ['produtos', 'acessorios'] + categories
    elif goal == 'custos':
        categories = ['insumos', 'logistica', 'embalagens'] + categories
    else:
        categories += ['tecnologia', 'marketing', 'servicos']
    return list(dict.fromkeys(categories))[:6]


class Conflict(ValueError):
    pass


class PartnerStore:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10)
        con.execute('PRAGMA foreign_keys=ON')
        con.executescript('''
            CREATE TABLE IF NOT EXISTS partners (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS profiles (owner TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS favorites (owner TEXT NOT NULL, partner TEXT NOT NULL REFERENCES partners(id), PRIMARY KEY(owner,partner));
            CREATE TABLE IF NOT EXISTS metrics (partner TEXT NOT NULL REFERENCES partners(id), kind TEXT NOT NULL, owner TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(partner,kind,owner,day));
        ''')
        try:
            with con:
                yield con
        finally:
            con.close()

    def all(self):
        with self.connect() as con:
            return [dict(json.loads(p), id=i, revision=r) for i, r, p in con.execute('SELECT id,revision,payload FROM partners')]

    def save(self, data):
        item = validate(data)
        identifier = text(data.get('id', ''), 40) or uuid.uuid4().hex
        if not re.fullmatch('[a-f0-9]{32}', identifier):
            raise ValueError('Identificador inválido.')
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT revision,payload FROM partners WHERE id=?', (identifier,)).fetchone()
            if row and data.get('revision') != row[0]:
                raise Conflict('O cadastro foi alterado em outra tela. Atualize a lista antes de editar.')
            if not row and data.get('id'):
                raise ValueError('Fornecedor não encontrado.')
            revision = row[0] + 1 if row else 1
            previous = json.loads(row[1]) if row else {}
            item['created_at'] = previous.get('created_at', now)
            item['updated_at'] = now
            con.execute('INSERT INTO partners VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload', (identifier, revision, json.dumps(item, ensure_ascii=False)))
        return dict(item, id=identifier, revision=revision)

    def profile(self, owner, data=None):
        with self.connect() as con:
            if data is not None:
                item = {'segment': text(data.get('segment', ''), 120), 'goal': text(data.get('goal', 'operacao'), 20), 'uf': text(data.get('uf', ''), 2).upper()}
                if item['goal'] not in GOALS or item['uf'] and item['uf'] not in UFS:
                    raise ValueError('Objetivo ou UF inválidos.')
                con.execute('INSERT INTO profiles VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET payload=excluded.payload', (owner, json.dumps(item)))
            row = con.execute('SELECT payload FROM profiles WHERE owner=?', (owner,)).fetchone()
            return json.loads(row[0]) if row else {'segment': '', 'goal': 'operacao', 'uf': ''}

    def public(self, owner, qs):
        profile = self.profile(owner)
        recommended = suggestions(profile['segment'], profile['goal'])
        one = lambda key: str(qs.get(key, [''])[0])
        selected_uf = one('uf').upper()
        with self.connect() as con:
            favorites = {row[0] for row in con.execute('SELECT partner FROM favorites WHERE owner=?', (owner,))}
        result = []
        all_partners = self.all()
        total = sum(p['status'] == 'aprovado' for p in all_partners)
        for original in all_partners:
            if original['status'] != 'aprovado':
                continue
            p = {k: v for k, v in original.items() if k != 'partnership_notes'}
            if selected_uf and not (p['national'] or selected_uf in p['coverage'] or selected_uf == p['uf']):
                continue
            if one('category') and p['category'] != one('category'):
                continue
            if one('q') and norm(one('q')) not in norm(' '.join([p['name'], p['description'], p['products'], *p['segments']])):
                continue
            if any(one(k) == '1' and not p[k] for k in ('wholesale', 'small_business')):
                continue
            if one('favorites') == '1' and p['id'] not in favorites:
                continue
            score = 0
            reasons = []
            segment = norm(profile['segment'])
            if segment and any(norm(tag) in segment or segment in norm(tag) for tag in p['segments']):
                score += 10
                reasons.append('Atende ao ramo informado no seu perfil')
            if p['category'] in recommended:
                score += 3
                reasons.append('Categoria sugerida para complementar seu negócio')
            if profile['goal'] in p['goals']:
                score += 5
                reasons.append('Pode contribuir com o objetivo escolhido')
            if profile['uf'] and not (p['national'] or profile['uf'] in p['coverage'] or profile['uf'] == p['uf']):
                score = 0
                reasons = []
            p['score'], p['reasons'] = score, reasons
            p['favorite'] = p['id'] in favorites
            today = date.today().isoformat()
            p['sponsored'] = bool(p['sponsored'] and (not p['sponsor_start'] or p['sponsor_start'] <= today) and (not p['sponsor_end'] or p['sponsor_end'] >= today))
            if p['sponsor_scope'] == 'regional':
                p['sponsored'] = bool(p['sponsored'] and (selected_uf or profile['uf']) in p['sponsor_ufs'])
            result.append(p)
        result.sort(key=lambda p: (-int(p['sponsored']), -p['score'], norm(p['name'])))
        return {'partners': result, 'total': total, 'profile': profile, 'suggestions': [{'id': c, 'label': CATEGORIES[c]} for c in recommended], 'categories': CATEGORIES, 'goals': GOALS}

    def approved(self, identifier):
        with self.connect() as con:
            row = con.execute('SELECT payload FROM partners WHERE id=?', (identifier,)).fetchone()
        if not row or json.loads(row[0])['status'] != 'aprovado':
            raise ValueError('Fornecedor indisponível.')
        return json.loads(row[0])

    def favorite(self, owner, identifier, enabled):
        self.approved(identifier)
        if not isinstance(enabled, bool):
            raise ValueError('Favorito inválido.')
        with self.connect() as con:
            if enabled:
                con.execute('INSERT OR IGNORE INTO favorites VALUES (?,?)', (owner, identifier))
            else:
                con.execute('DELETE FROM favorites WHERE owner=? AND partner=?', (owner, identifier))
        return {'ok': True, 'favorite': enabled}

    def track(self, owner, identifier, kind):
        partner = self.approved(identifier)
        if kind not in {'view', 'site', 'contact'}:
            raise ValueError('Evento inválido.')
        if kind == 'site' and not partner['website'] or kind == 'contact' and not (partner['whatsapp'] or partner['email']):
            raise ValueError('Contato indisponível.')
        with self.connect() as con:
            con.execute('INSERT INTO metrics VALUES (?,?,?,?,1) ON CONFLICT(partner,kind,owner,day) DO UPDATE SET count=CASE WHEN excluded.kind="view" THEN 1 ELSE MIN(metrics.count+1,100) END', (identifier, kind, owner, date.today().isoformat()))
        return {'ok': True}

    def report(self, days=30):
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        with self.connect() as con:
            stats = {(i, k): n for i, k, n in con.execute('SELECT partner,kind,SUM(count) FROM metrics WHERE day>=? GROUP BY partner,kind', (cutoff,))}
        return [{'id': p['id'], 'name': p['name'], 'status': p['status'], 'sponsored': p['sponsored'], 'views': stats.get((p['id'], 'view'), 0), 'site_clicks': stats.get((p['id'], 'site'), 0), 'contact_clicks': stats.get((p['id'], 'contact'), 0)} for p in self.all()]


def owner(session):
    return str(session.get('company_id') or 'local') + ':' + str(session.get('usuario') or '')


def get_route(handler, path, qs, session, ctx):
    if path not in {'/api/partners', '/api/partners/admin', '/api/partners/metrics'}:
        return False
    try:
        if path != '/api/partners' and not ctx['_is_global_admin'](session):
            ctx['_write_json'](handler, {'error': 'Acesso restrito ao administrador geral.'}, 403)
            return True
        store = ctx['_PARTNER_STORE']
        if path == '/api/partners/admin':
            result = {'partners': store.all(), 'categories': CATEGORIES, 'goals': GOALS}
        elif path == '/api/partners/metrics':
            days = int(qs.get('days', ['30'])[0])
            if days not in {7, 30, 90}:
                raise ValueError('Período inválido.')
            result = {'days': days, 'metrics': store.report(days)}
        else:
            result = store.public(owner(session), qs)
        ctx['_write_json'](handler, result)
    except (ValueError, TypeError) as exc:
        ctx['_write_json'](handler, {'error': str(exc)}, 400)
    except Exception:
        ctx['_write_json'](handler, {'error': 'Não foi possível consultar a rede de parceiros.'}, 500)
    return True


def post_route(handler, path, session, ctx):
    if path not in {'/api/partners/save', '/api/partners/profile', '/api/partners/favorite', '/api/partners/event'}:
        return False
    try:
        if path == '/api/partners/save' and not ctx['_is_global_admin'](session):
            ctx['_write_json'](handler, {'error': 'Acesso restrito ao administrador geral.'}, 403)
            return True
        length = int(handler.headers.get('Content-Length', '0'))
        if not 0 <= length <= 40000:
            raise ValueError('Cadastro muito grande.')
        data = json.loads(handler.rfile.read(length).decode('utf-8') or '{}')
        if not isinstance(data, dict):
            raise ValueError('Envie um objeto JSON.')
        store = ctx['_PARTNER_STORE']
        if path == '/api/partners/save':
            result = {'ok': True, 'partner': store.save(data)}
        elif path == '/api/partners/profile':
            result = {'ok': True, 'profile': store.profile(owner(session), data)}
        elif path == '/api/partners/favorite':
            result = store.favorite(owner(session), text(data.get('id', ''), 40), data.get('enabled'))
        else:
            result = store.track(owner(session), text(data.get('id', ''), 40), text(data.get('kind', ''), 20))
        ctx['_write_json'](handler, result)
    except Conflict as exc:
        ctx['_write_json'](handler, {'error': str(exc)}, 409)
    except (ValueError, TypeError) as exc:
        ctx['_write_json'](handler, {'error': str(exc)}, 400)
    except Exception:
        ctx['_write_json'](handler, {'error': 'Não foi possível salvar esta operação.'}, 500)
    return True
