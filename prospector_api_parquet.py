import json
import gzip
import hashlib
import os
import re
import sys
import time
import webbrowser
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from prospectax_intelligence import Intelligence, get_route as _intelligence_get, post_route as _intelligence_post
from prospectax_radar import Radar, get_route as _radar_get, post_route as _radar_post
from prospectax_business import BusinessStore, get_route as _business_get, post_route as _business_post
from prospectax_partners import PartnerStore, get_route as _partners_get, post_route as _partners_post
from prospectax_live_tunnel import remember_tunnel, adopt_existing_tunnel
from prospectax_discovery import (normalize_cnpj, clean_cnpjs, discovery_filters, filter_sql, free_pitch as _free_pitch, PersonalDiscoveryStore, PublicPlaces)
from prospectax_products import ProductSearch
from prospectax_public_checks import PublicChecks
from prospectax_routes import get_route as _discovery_get, post_route as _discovery_post
from prospectax_services import FreeCompanyAPIs, QueryCache, database_revision, flag as _api_flag

_FREE_COMPANY_APIS = FreeCompanyAPIs()
_EXACT_COUNT_CACHE = QueryCache(128)
_PAGE_QUERY_CACHE = QueryCache(64)
_API_LOOKUP_LOCKS = [threading.Lock() for _ in range(32)]

try:
    import duckdb
except Exception:
    print("DuckDB não encontrado. Instalando...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "duckdb"])
    import duckdb


# ===== CNAE — carregado da API oficial do IBGE (1.332 subclasses) =====
_cnae_cache = []  # lista de dicts {code, desc}
_cnae_loaded = False

def _normalizar(s):
    import unicodedata
    s = s.lower().strip()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s

def load_cnae_ibge():
    global _cnae_cache, _cnae_loaded
    if _cnae_loaded:
        return
    try:
        print('Carregando tabela CNAE do IBGE...')
        req = Request(
            'https://servicodados.ibge.gov.br/api/v2/cnae/subclasses',
            headers={'User-Agent': 'ProspectorBramais/1.0'}
        )
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        # data = lista de {id, descricao, classe:{...}}
        _cnae_cache = []
        for item in data:
            code = re.sub(r'[^0-9]', '', str(item.get('id', '')))
            desc = str(item.get('descricao', '')).strip()
            if code and desc:
                _cnae_cache.append({'code': code, 'desc': desc, 'norm': _normalizar(desc)})
        _cnae_loaded = True
        print(f'CNAE carregado: {len(_cnae_cache)} subclasses')
    except Exception as e:
        print(f'Aviso: nao foi possivel carregar CNAE do IBGE: {e}')
        print('Usando tabela local de fallback...')
        _load_cnae_fallback()
        _cnae_loaded = True

def _load_cnae_fallback():
    FALLBACK = [
        ("4530701","Comercio varejista de autopecas e acessorios para veiculos automotores"),
        ("4530702","Comercio varejista de pneumaticos e camaras-de-ar"),
        ("4530703","Servicos de borracharia"),
        ("4711301","Supermercado"),("4711302","Hipermercado"),
        ("4721102","Padaria e confeitaria"),("4731800","Farmacia"),
        ("4771701","Farmacia sem manipulacao"),("4771702","Farmacia com manipulacao"),
        ("5611201","Restaurante e similares"),("5611203","Lanchonete"),
        ("9313100","Academia de condicionamento fisico"),
        ("6201501","Desenvolvimento de software"),("6920601","Contabilidade"),
        ("6911701","Advocacia"),("7111100","Arquitetura"),("7112000","Engenharia"),
        ("8630501","Clinica medica"),("8630503","Clinica odontologica"),
        ("4744003","Comercio varejista de materiais hidraulicos"),
    ]
    for code, desc in FALLBACK:
        _cnae_cache.append({'code': code, 'desc': desc, 'norm': _normalizar(desc)})

def search_cnae(query):
    load_cnae_ibge()
    q = query.strip()
    if not q:
        return []
    results = []
    # Busca por código
    qd = re.sub(r'[^0-9]', '', q)
    if qd:
        for item in _cnae_cache:
            if item['code'].startswith(qd):
                results.append({'code': item['code'], 'desc': item['desc'], 'score': 10})
        if results:
            return sorted(results, key=lambda x: x['code'])[:15]
    # Busca por descrição
    qn = _normalizar(q)
    words = [w for w in qn.split() if len(w) >= 3]
    if not words:
        return []
    for item in _cnae_cache:
        score = sum(1 for w in words if w in item['norm'])
        if score > 0:
            results.append({'code': item['code'], 'desc': item['desc'], 'score': score})
    results.sort(key=lambda x: -x['score'])
    return results[:15]

BASE_DIR = Path(__file__).resolve().parent

# ===== CHAVES DE API (configuradas via tela de Configurações) =====
ANTHROPIC_API_KEY  = os.environ.get('ANTHROPIC_API_KEY', '')
GOOGLE_API_KEY     = os.environ.get('GOOGLE_API_KEY', '')
GOOGLE_CX          = os.environ.get('GOOGLE_CX', '')
INFOSIMPLES_TOKEN  = os.environ.get('INFOSIMPLES_TOKEN', '')
APIFY_TOKEN        = os.environ.get('APIFY_TOKEN', '')
GEOAPIFY_API_KEY   = os.environ.get('GEOAPIFY_API_KEY', '')
CEPABERTO_TOKEN    = os.environ.get('CEPABERTO_TOKEN', '')
_CEPABERTO_LAST_CALL = 0.0
# Dica: configure pelas telas de Configurações no sistema
# Use variaveis de ambiente; nunca grave chaves no codigo:
# ANTHROPIC_API_KEY = 'sk-ant-...'
# INFOSIMPLES_TOKEN = 'seu-token...'
# APIFY_TOKEN = 'apify_api_...'
# GEOAPIFY_API_KEY = 'sua-chave...'
# CEPABERTO_TOKEN = 'seu-token...'
def _resolve_data_dir():
    env = os.environ.get('PROSPECTAX_DATA_DIR') or os.environ.get('PROSPECTOR_DATA_DIR')
    if env:
        return Path(env.strip())
    for candidate in [
        BASE_DIR / 'dados_parquet',
        Path(os.path.expanduser('~')) / 'Desktop' / 'prospector_local' / 'dados_parquet',
    ]:
        if candidate.exists():
            return candidate
    return BASE_DIR / 'dados_parquet'

DATA_DIR = _resolve_data_dir()
SITE_DIR = Path(os.environ.get("PROSPECTAX_SITE_DIR", os.environ.get("PROSPECTOR_SITE_DIR", BASE_DIR / "site")))
PRIVATE_DATA_DIR = Path(os.environ.get('PROSPECTAX_PRIVATE_DIR', str(BASE_DIR / 'private_data')))
_PARTNER_STORE = PartnerStore(PRIVATE_DATA_DIR / 'partners.sqlite')
_INTELLIGENCE = Intelligence(PRIVATE_DATA_DIR)
_RADAR = Radar(PRIVATE_DATA_DIR)
_BUSINESS_STORE = BusinessStore(PRIVATE_DATA_DIR / 'business.sqlite')
_DISCOVERY_STORE = PersonalDiscoveryStore(PRIVATE_DATA_DIR / 'discovery.sqlite')
_PUBLIC_PLACES = PublicPlaces(PRIVATE_DATA_DIR)
_PRODUCT_SEARCH = ProductSearch(PRIVATE_DATA_DIR)
_PUBLIC_CHECKS = PublicChecks(PRIVATE_DATA_DIR)
_COMMERCIAL_WRITE_LOCK = threading.RLock()

if not (SITE_DIR / "index.html").exists() and (BASE_DIR / "index.html").exists():
    SITE_DIR = BASE_DIR

# ===== AUTH =====
import hashlib as _hl, secrets as _sec
_USUARIOS_FILE = SITE_DIR / "usuarios.json"
_COMPANIES_FILE = SITE_DIR / "empresas.json"
_tokens_mem    = {}
_TOKEN_TTL     = 86400


def _phash(s):
    return _hl.sha256(s.encode()).hexdigest()


def _slug_company_id(value):
    value = re.sub(r'[^a-zA-Z0-9_-]+', '_', (value or '').strip().lower())
    value = re.sub(r'_+', '_', value).strip('_')
    return value or 'empresa'


def _sanitize_limit(value, default=1):
    try:
        n = int(value)
    except Exception:
        n = default
    return max(1, n)


def _carregar_empresas():
    if not _COMPANIES_FILE.exists():
        SITE_DIR.mkdir(parents=True, exist_ok=True)
        _COMPANIES_FILE.write_text(json.dumps({}, indent=2, ensure_ascii=False), encoding='utf-8')
        return {}
    try:
        data = json.loads(_COMPANIES_FILE.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _salvar_empresas(empresas):
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    _COMPANIES_FILE.write_text(json.dumps(empresas, indent=2, ensure_ascii=False), encoding='utf-8')


def _garantir_empresa(company_id, nome=None, limite_subcontas=1, admin_user=None, ativa=True, criado_por='system'):
    if not company_id:
        return None
    company_id = _slug_company_id(company_id)
    empresas = _carregar_empresas()
    atual = empresas.get(company_id, {})
    atual.update({
        'id': company_id,
        'nome': (nome or atual.get('nome') or company_id).strip(),
        'limite_subcontas': _sanitize_limit(limite_subcontas, atual.get('limite_subcontas', 1) or 1),
        'ativo': bool(atual.get('ativo', ativa)),
        'admin_user': admin_user or atual.get('admin_user', ''),
        'criado_por': atual.get('criado_por') or criado_por,
        'criado_em': atual.get('criado_em') or time.strftime('%Y-%m-%d %H:%M:%S'),
    })
    empresas[company_id] = atual
    _salvar_empresas(empresas)
    return atual


def _sincronizar_empresas_com_usuarios(usuarios):
    empresas = _carregar_empresas()
    changed = False
    for login, data in usuarios.items():
        cid = data.get('company_id')
        if not cid:
            continue
        cid = _slug_company_id(cid)
        if data.get('company_id') != cid:
            data['company_id'] = cid
            changed = True
        emp = empresas.get(cid, {})
        emp.setdefault('id', cid)
        emp.setdefault('nome', data.get('company_name') or cid)
        emp.setdefault('limite_subcontas', _sanitize_limit(data.get('subcontas_limite', 1), 1))
        emp.setdefault('ativo', True)
        emp.setdefault('criado_por', 'migration')
        emp.setdefault('criado_em', time.strftime('%Y-%m-%d %H:%M:%S'))
        if data.get('scope') == 'company_admin' and not emp.get('admin_user'):
            emp['admin_user'] = login
        empresas[cid] = emp
    if empresas:
        _salvar_empresas(empresas)
    return changed


def _carregar_usuarios():
    if not _USUARIOS_FILE.exists():
        password = os.environ.get('PROSPECTAX_ADMIN_PASSWORD') or 'admin123'
        if password != 'admin123' and len(password) < 16:
            raise RuntimeError(
                'Defina PROSPECTAX_ADMIN_PASSWORD com pelo menos 16 caracteres '
                'para substituir a senha inicial padrao da ferramenta.'
            )
        SITE_DIR.mkdir(parents=True, exist_ok=True)
        u = {
            "admin": {
                "nome": "Administrador",
                "senha": _phash(password),
                "role": "admin",
                "scope": "global_admin",
                "ativo": True,
            },
        }
        _USUARIOS_FILE.write_text(json.dumps(u, indent=2, ensure_ascii=False), encoding="utf-8")
        print("  Conta administrativa inicial criada.")
        _sincronizar_empresas_com_usuarios(u)
        return u
    try:
        usuarios = json.loads(_USUARIOS_FILE.read_text(encoding="utf-8"))
        if not isinstance(usuarios, dict):
            return {}
    except Exception:
        return {}

    changed = False
    for login, data in usuarios.items():
        if not isinstance(data, dict):
            usuarios[login] = {
                'nome': login,
                'senha': '',
                'role': 'cliente',
                'scope': 'seller',
                'ativo': True,
            }
            changed = True
            continue
        if 'role' not in data:
            data['role'] = data.get('perfil', 'cliente')
            changed = True
        if 'ativo' not in data:
            data['ativo'] = True
            changed = True
        if 'scope' not in data:
            if login == 'admin':
                data['scope'] = 'global_admin'
            elif data.get('role') == 'admin' and data.get('company_id'):
                data['scope'] = 'company_admin'
            elif data.get('role') == 'admin':
                data['scope'] = 'admin_legacy'
            else:
                data['scope'] = 'seller'
            changed = True
        if data.get('company_id'):
            cid = _slug_company_id(data.get('company_id'))
            if data.get('company_id') != cid:
                data['company_id'] = cid
                changed = True
    if _sincronizar_empresas_com_usuarios(usuarios):
        changed = True
    if changed:
        _salvar_usuarios(usuarios)
    return usuarios


def _salvar_usuarios(u):
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    _USUARIOS_FILE.write_text(json.dumps(u, indent=2, ensure_ascii=False), encoding="utf-8")
    _sincronizar_empresas_com_usuarios(u)


def _montar_sessao(usuario, data):
    company_id = data.get('company_id') or None
    empresas = _carregar_empresas()
    empresa = empresas.get(company_id, {}) if company_id else {}
    return {
        'usuario': usuario,
        'nome': data.get('nome', usuario),
        'role': data.get('role', data.get('perfil', 'cliente')),
        'scope': data.get('scope', 'seller'),
        'company_id': company_id,
        'company_name': empresa.get('nome') or data.get('company_name') or '',
        'subcontas_limite': empresa.get('limite_subcontas', _sanitize_limit(data.get('subcontas_limite', 1), 1)) if company_id else 0,
        'company_active': empresa.get('ativo', True) if company_id else True,
        'is_global_admin': data.get('scope') == 'global_admin',
    }


def _autenticar(usuario, senha):
    usuarios = _carregar_usuarios()
    u = usuarios.get(usuario)
    if not u or not u.get("ativo", True):
        return None
    hash_salvo = u.get("senha") or u.get("senha_hash") or ""
    if hash_salvo != _phash(senha):
        return None
    sess = _montar_sessao(usuario, u)
    if sess.get('company_id') and not sess.get('company_active', True):
        return None
    return sess


def _validar_token(token):
    if not token:
        return None
    e = _tokens_mem.get(token)
    if not e:
        return None
    if time.time() - e["ts"] > _TOKEN_TTL:
        del _tokens_mem[token]
        return None
    e["ts"] = time.time()
    return e


def _get_token(handler):
    c = handler.headers.get("Cookie", "")
    for p in c.split(";"):
        p = p.strip()
        if p.startswith("prospectax_token="):
            return p.split("=", 1)[1].strip()
    a = handler.headers.get("X-Auth-Token", "")
    if a:
        return a.strip()
    a = handler.headers.get("Authorization", "")
    if a.startswith("Bearer "):
        return a[7:].strip()
    return None


def _is_global_admin(sess):
    return bool(sess and sess.get('role') == 'admin' and sess.get('scope') == 'global_admin')


def _is_company_admin(sess):
    return bool(sess and sess.get('role') == 'admin' and sess.get('company_id'))


def _contar_subcontas_empresa(company_id, usuarios=None):
    company_id = _slug_company_id(company_id or '')
    if not company_id:
        return 0
    usuarios = usuarios or _carregar_usuarios()
    total = 0
    for login, data in usuarios.items():
        if data.get('company_id') != company_id:
            continue
        if data.get('role') == 'admin':
            continue
        if data.get('ativo', True):
            total += 1
    return total


def _pode_gerir_usuario(sess, login_alvo, dados_alvo=None):
    if _is_global_admin(sess):
        return True
    if not _is_company_admin(sess):
        return False
    if login_alvo == sess.get('usuario'):
        return False
    if not dados_alvo:
        dados_alvo = _carregar_usuarios().get(login_alvo)
    if not dados_alvo:
        return False
    return dados_alvo.get('company_id') == sess.get('company_id') and dados_alvo.get('role') != 'admin'


def _purge_tokens_for_user(login):
    for token, sess in list(_tokens_mem.items()):
        if isinstance(sess, dict) and sess.get('usuario') == login:
            _tokens_mem.pop(token, None)


def _cleanup_company_after_user_delete(login_del, user_data, usuarios):
    cid = _slug_company_id(user_data.get('company_id') or '')
    if not cid:
        return
    empresas = _carregar_empresas()
    emp = dict(empresas.get(cid, {}) or {})
    remaining = [d for u, d in usuarios.items() if u != login_del and d.get('company_id') == cid]
    if user_data.get('role') == 'admin':
        if remaining:
            # Promote first company admin if one exists, otherwise clear admin binding
            next_admin = ''
            for data in remaining:
                if data.get('role') == 'admin':
                    next_admin = next((u for u, d in usuarios.items() if d is data), '')
                    break
            emp['admin_user'] = next_admin
            empresas[cid] = emp
        else:
            empresas.pop(cid, None)
    else:
        # If company became empty after seller deletion, remove orphan company too
        if not remaining:
            empresas.pop(cid, None)
    _salvar_empresas(empresas)


def _delete_user(login_del):
    usuarios = _carregar_usuarios()
    if login_del not in usuarios:
        raise ValueError('Usuário não encontrado')
    user_data = dict(usuarios[login_del] or {})
    del usuarios[login_del]
    _cleanup_company_after_user_delete(login_del, user_data, usuarios)
    _salvar_usuarios(usuarios)
    _purge_tokens_for_user(login_del)
    return user_data
# ===== FIM AUTH =====
CONTACT_STATUS_FILE = SITE_DIR / "contact_status.json"
NOTES_FILE = SITE_DIR / "notes.json"
HISTORY_FILE = SITE_DIR / "history.json"
TEAM_RULES_FILE = SITE_DIR / "team_status_rules.json"
USER_PREFS_FILE = SITE_DIR / "user_prefs.json"
SAVED_CLIENTS_FILE = SITE_DIR / "saved_clients.json"
SEARCH_CACHE_DIR = SITE_DIR / "cache_buscas"
SEARCH_CACHE_TTL = int(os.environ.get("PROSPECTAX_SEARCH_CACHE_TTL", str(7 * 24 * 3600)))


# ===== PROGRESSO DA BUSCA / CACHE =====
_SEARCH_PROGRESS = {}
_SEARCH_PROGRESS_LOCK = threading.Lock()


def _search_progress_update(job_id, **fields):
    """Atualiza progresso consultável pelo frontend durante uma busca pesada."""
    job_id = str(job_id or '').strip()
    if not job_id:
        return
    now = time.time()
    with _SEARCH_PROGRESS_LOCK:
        current = dict(_SEARCH_PROGRESS.get(job_id, {}) or {})
        current.setdefault('started_at', now)
        current.update(fields)
        current['updated_at'] = now
        started = float(current.get('started_at') or now)
        current['elapsed'] = max(0.0, now - started)
        try:
            percent = float(current.get('percent') or 0)
            if percent > 0 and percent < 100:
                current['eta'] = max(0.0, (current['elapsed'] * (100.0 - percent)) / max(percent, 1.0))
            else:
                current['eta'] = 0.0
        except Exception:
            current['eta'] = 0.0
        _SEARCH_PROGRESS[job_id] = current

        # Limpeza leve de jobs antigos para não acumular memória.
        cutoff = now - 6 * 3600
        for key, value in list(_SEARCH_PROGRESS.items()):
            if float((value or {}).get('updated_at') or 0) < cutoff:
                _SEARCH_PROGRESS.pop(key, None)


def _search_progress_get(job_id):
    job_id = str(job_id or '').strip()
    if not job_id:
        return {'status': 'missing', 'percent': 0, 'phase': 'Sem busca em andamento.'}
    with _SEARCH_PROGRESS_LOCK:
        data = dict(_SEARCH_PROGRESS.get(job_id, {}) or {})
    if not data:
        return {'status': 'unknown', 'percent': 0, 'phase': 'Aguardando início da busca.'}
    now = time.time()
    started = float(data.get('started_at') or now)
    data['elapsed'] = max(0.0, now - started)
    try:
        percent = float(data.get('percent') or 0)
        data['eta'] = max(0.0, (data['elapsed'] * (100.0 - percent)) / max(percent, 1.0)) if 0 < percent < 100 else 0.0
    except Exception:
        data['eta'] = 0.0
    return data
# ===== FIM PROGRESSO DA BUSCA / CACHE =====
HOST = os.environ.get("PROSPECTAX_HOST", "127.0.0.1")
PORT = int(os.environ.get("PROSPECTAX_PORT", "8765"))
ALL_UFS = ['AC','AL','AP','AM','BA','CE','DF','ES','GO','MA','MT','MS','MG','PA','PB','PR','PE','PI','RJ','RN','RS','RO','RR','SC','SP','SE','TO']
try:
    SEARCH_MAX_LIMIT = int(os.environ.get('PROSPECTAX_SEARCH_MAX_LIMIT', '500000') or '500000')
except Exception:
    SEARCH_MAX_LIMIT = 500000


# ===== LICENÇA LOCAL / BLOQUEIO DE CLIENTE =====
def _bool_env(name, default='1'):
    return str(os.environ.get(name, default)).strip().lower() not in {'0', 'false', 'no', 'nao', 'não', 'off'}

APP_ROOT_DIR = Path(os.environ.get(
    'PROSPECTAX_APP_ROOT',
    str(BASE_DIR.parent if BASE_DIR.name.lower() == '_prospectax_app' else BASE_DIR)
)).resolve()
LICENSE_REQUIRED = _bool_env('PROSPECTAX_LICENSE_REQUIRED', '0')
LICENSE_API_URL = os.environ.get(
    'PROSPECTAX_LICENSE_API',
    ''
).strip()
LICENSE_FILE = Path(os.environ.get(
    'PROSPECTAX_LICENSE_FILE',
    str(APP_ROOT_DIR / '.prospectax_licenca_local.json')
)).resolve()
LICENSE_SECRET = os.environ.get('PROSPECTAX_LICENSE_SECRET', '')
LICENSE_REQUIRE_SIGNATURE = _bool_env('PROSPECTAX_LICENSE_REQUIRE_SIGNATURE', '0')
LICENSE_REMOTE_VALIDATE = _bool_env('PROSPECTAX_LICENSE_REMOTE_VALIDATE', '1')
LICENSE_REMOTE_FAIL_OPEN = _bool_env('PROSPECTAX_LICENSE_REMOTE_FAIL_OPEN', '0')
LICENSE_VALIDATE_API_URL = os.environ.get('PROSPECTAX_LICENSE_VALIDATE_API', '').strip()
try:
    LICENSE_REMOTE_CACHE_TTL = int(os.environ.get('PROSPECTAX_LICENSE_REMOTE_CACHE_TTL', '60') or '60')
except Exception:
    LICENSE_REMOTE_CACHE_TTL = 60
_LICENSE_CACHE = {'ts': 0.0, 'ok': False, 'data': {}, 'reason': ''}
_LICENSE_CACHE_TTL = 2.0
_LICENSE_REMOTE_CACHE = {'ts': 0.0, 'ok': None, 'data': {}, 'reason': ''}


def _machine_id():
    """Identificador estável da máquina para prender a licença ao computador."""
    try:
        import uuid
        import platform
        parts = [
            'ProspectaX',
            platform.node() or '',
            os.environ.get('COMPUTERNAME', ''),
            str(uuid.getnode() or ''),
            os.environ.get('PROCESSOR_IDENTIFIER', ''),
            os.environ.get('PROCESSOR_ARCHITECTURE', ''),
            os.environ.get('SystemDrive', ''),
        ]
        raw = '|'.join(str(p).strip() for p in parts if str(p).strip())
        if not raw:
            raw = str(APP_ROOT_DIR)
        return hashlib.sha256(raw.encode('utf-8', errors='ignore')).hexdigest()
    except Exception:
        return hashlib.sha256((os.environ.get('COMPUTERNAME', '') + str(APP_ROOT_DIR)).encode('utf-8', errors='ignore')).hexdigest()


def _license_candidate_paths():
    paths = [LICENSE_FILE]
    for p in [
        APP_ROOT_DIR / '.prospectax_licenca_local.json',
        BASE_DIR / '.prospectax_licenca_local.json',
        BASE_DIR.parent / '.prospectax_licenca_local.json',
        SITE_DIR.parent / '.prospectax_licenca_local.json',
    ]:
        try:
            paths.append(Path(p).resolve())
        except Exception:
            pass
    out, seen = [], set()
    for p in paths:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _assinatura_licenca(dados):
    campos = [
        dados.get('produto', 'ProspectaX'),
        dados.get('tipo', 'vitalicia'),
        dados.get('status', 'ativa'),
        dados.get('email', ''),
        dados.get('cnpj', ''),
        dados.get('maquina_id', ''),
        dados.get('ativada_em', ''),
    ]
    raw = '::'.join(str(x or '').strip().lower() for x in campos) + '::' + LICENSE_SECRET
    return hashlib.sha256(raw.encode('utf-8', errors='ignore')).hexdigest()


def _read_local_license():
    for path in _license_candidate_paths():
        try:
            if path.exists() and path.is_file():
                data = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    data['_license_file'] = str(path)
                    return data
        except Exception:
            continue
    return None



def _license_api_base():
    base = (LICENSE_API_URL or '').strip().rstrip('/')
    if not base:
        return ''
    lower = base.lower()
    if lower.endswith('/ativar') or lower.endswith('/validar'):
        base = base.rsplit('/', 1)[0]
    return base.rstrip('/')


def _license_validate_url():
    if LICENSE_VALIDATE_API_URL:
        return LICENSE_VALIDATE_API_URL.rstrip('/')
    base = _license_api_base()
    return (base + '/validar') if base else ''

def _sanitize_license_public(data):
    """Remove campos sensiveis antes de expor status de licenca no navegador."""
    data = data if isinstance(data, dict) else {}
    blocked = {
        'admin_empresa_senha', 'admin_senha', 'senha_admin_empresa', 'senha_admin',
        'senha', 'password', 'pass', 'token', 'admin_token', 'secret',
        'assinatura_local', 'api_url'
    }
    out = {}
    for k, v in data.items():
        key = str(k)
        low = key.lower()
        if low in blocked or 'senha' in low or 'password' in low or 'token' in low or 'secret' in low:
            continue
        if isinstance(v, dict):
            out[key] = _sanitize_license_public(v)
        elif isinstance(v, list):
            out[key] = [_sanitize_license_public(x) if isinstance(x, dict) else x for x in v]
        else:
            out[key] = v
    return out

def _merge_remote_license_controls(local_data, remote_data):
    local_data = dict(local_data or {})
    remote_data = remote_data if isinstance(remote_data, dict) else {}
    if not remote_data:
        return local_data
    merged = dict(local_data)
    for key in (
        'plano', 'tipo_plano', 'status', 'cliente', 'email', 'cnpj',
        'empresa_id', 'company_id', 'empresa_nome', 'company_name',
        'max_usuarios', 'usuarios_permitidos', 'limite_subcontas',
        'permitir_tunel', 'cloudflare_tunnel', 'maquinas_permitidas',
        'licenca_id', 'license_id', 'chave'
    ):
        if remote_data.get(key) not in (None, ''):
            merged[key] = remote_data.get(key)
    return merged


def _validar_licenca_remota(local_data, force=False):
    """
    Valida na Cloudflare se a licença local continua ativa.
    Isso é o que permite bloquear/liberar a licença de longe.
    """
    if not LICENSE_REMOTE_VALIDATE:
        return True, {'remote_skipped': True, 'motivo': 'validacao_remota_desativada'}, ''

    url = _license_validate_url()
    if not url:
        if LICENSE_REMOTE_FAIL_OPEN:
            return True, {'remote_skipped': True, 'motivo': 'api_nao_configurada'}, ''
        return False, {'remote_error': 'api_nao_configurada'}, 'API de validação remota não configurada.'

    now = time.time()
    if not force and (now - float(_LICENSE_REMOTE_CACHE.get('ts') or 0)) < max(5, LICENSE_REMOTE_CACHE_TTL):
        cached_ok = _LICENSE_REMOTE_CACHE.get('ok')
        if cached_ok is not None:
            return bool(cached_ok), dict(_LICENSE_REMOTE_CACHE.get('data') or {}), str(_LICENSE_REMOTE_CACHE.get('reason') or '')

    body = json.dumps({
        'produto': 'ProspectaX',
        'email': local_data.get('email', ''),
        'cnpj': local_data.get('cnpj', ''),
        'licenca_id': local_data.get('licenca_id', local_data.get('license_id', '')),
        'chave': local_data.get('chave', ''),
        'maquina_id': _machine_id(),
        'machine_id': _machine_id(),
        'app': 'ProspectaX',
    }).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': 'ProspectaX/4.0 RemoteLicenseCheck',
    }

    try:
        req = Request(url, data=body, headers=headers, method='POST')
        with urlopen(req, timeout=12) as resp:
            raw = resp.read().decode('utf-8', errors='ignore')
            status_code = getattr(resp, 'status', 200)
    except HTTPError as e:
        try:
            raw = e.read().decode('utf-8', errors='ignore')
        except Exception:
            raw = ''
        status_code = e.code
    except Exception as e:
        motivo = f'Não foi possível validar a licença online: {str(e)[:120]}'
        data = {'remote_error': str(e)[:200], 'remote_url': url}
        if LICENSE_REMOTE_FAIL_OPEN:
            _LICENSE_REMOTE_CACHE.update({'ts': now, 'ok': True, 'data': data, 'reason': ''})
            return True, data, ''
        _LICENSE_REMOTE_CACHE.update({'ts': now, 'ok': False, 'data': data, 'reason': motivo})
        return False, data, motivo

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {'raw': raw}

    active_status = str((data or {}).get('status') or '').strip().lower()
    aprovado = bool(
        200 <= int(status_code) < 300 and (
            data.get('ok') is True or
            data.get('success') is True or
            data.get('valid') is True or
            data.get('licenciada') is True or
            data.get('ativa') is True or
            active_status in {'ativa', 'ativo', 'active', 'ok', 'aprovada', 'validada'}
        )
    )
    if aprovado:
        _LICENSE_REMOTE_CACHE.update({'ts': now, 'ok': True, 'data': data, 'reason': ''})
        return True, data, ''

    motivo = data.get('erro') or data.get('error') or data.get('message') or f'Licença não aprovada na validação online. HTTP {status_code}'
    _LICENSE_REMOTE_CACHE.update({'ts': now, 'ok': False, 'data': data, 'reason': str(motivo)})
    return False, data, str(motivo)


def _licenca_status(force=False):
    """Retorna (ativa, dados, motivo)."""
    now = time.time()
    if not force and (now - float(_LICENSE_CACHE.get('ts') or 0)) < _LICENSE_CACHE_TTL:
        return bool(_LICENSE_CACHE.get('ok')), dict(_LICENSE_CACHE.get('data') or {}), str(_LICENSE_CACHE.get('reason') or '')

    mid = _machine_id()
    if not LICENSE_REQUIRED:
        data = {'licenciada': True, 'status': 'desativada_por_ambiente', 'maquina_id_final': mid}
        _LICENSE_CACHE.update({'ts': now, 'ok': True, 'data': data, 'reason': ''})
        return True, data, ''

    data = _read_local_license()
    if not data:
        reason = 'Licença local não encontrada.'
        payload = {'licenciada': False, 'maquina_id_final': mid}
        _LICENSE_CACHE.update({'ts': now, 'ok': False, 'data': payload, 'reason': reason})
        return False, payload, reason

    status = str(data.get('status') or '').strip().lower()
    if status not in {'ativa', 'ativo', 'active', 'ok', 'aprovada', 'validada'}:
        reason = 'Licença local não está ativa.'
        payload = {**data, 'licenciada': False, 'maquina_id_final': mid}
        _LICENSE_CACHE.update({'ts': now, 'ok': False, 'data': payload, 'reason': reason})
        return False, payload, reason

    lic_mid = str(data.get('maquina_id') or data.get('maquina_id_final') or '').strip()
    if lic_mid and lic_mid != mid:
        reason = 'Licença pertence a outra máquina.'
        payload = {**data, 'licenciada': False, 'maquina_id_final': mid}
        _LICENSE_CACHE.update({'ts': now, 'ok': False, 'data': payload, 'reason': reason})
        return False, payload, reason
    if not lic_mid:
        reason = 'Licença local sem vínculo de máquina.'
        payload = {**data, 'licenciada': False, 'maquina_id_final': mid}
        _LICENSE_CACHE.update({'ts': now, 'ok': False, 'data': payload, 'reason': reason})
        return False, payload, reason

    produto = str(data.get('produto') or 'ProspectaX').strip().lower()
    if produto and produto != 'prospectax':
        reason = 'Licença não pertence ao ProspectaX.'
        payload = {**data, 'licenciada': False, 'maquina_id_final': mid}
        _LICENSE_CACHE.update({'ts': now, 'ok': False, 'data': payload, 'reason': reason})
        return False, payload, reason

    vencimento = str(data.get('vence_em') or data.get('expira_em') or data.get('validade') or '').strip()
    if vencimento:
        try:
            import datetime as _dt
            base = vencimento[:10]
            dt = _dt.datetime.strptime(base, '%Y-%m-%d').date()
            if _dt.date.today() > dt:
                reason = 'Licença expirada.'
                payload = {**data, 'licenciada': False, 'maquina_id_final': mid}
                _LICENSE_CACHE.update({'ts': now, 'ok': False, 'data': payload, 'reason': reason})
                return False, payload, reason
        except Exception:
            pass

    sig = str(data.get('assinatura_local') or '').strip()
    if LICENSE_REQUIRE_SIGNATURE and (not LICENSE_SECRET or sig != _assinatura_licenca(data)):
        reason = 'Assinatura local da licença inválida.'
        payload = {**data, 'licenciada': False, 'maquina_id_final': mid}
        _LICENSE_CACHE.update({'ts': now, 'ok': False, 'data': payload, 'reason': reason})
        return False, payload, reason

    remote_ok, remote_data, remote_reason = _validar_licenca_remota(data, force=force)
    if not remote_ok:
        reason = remote_reason or 'Licença não aprovada na validação online.'
        payload = {**data, 'licenciada': False, 'maquina_id_final': mid, 'validacao_remota': remote_data}
        _LICENSE_CACHE.update({'ts': now, 'ok': False, 'data': payload, 'reason': reason})
        return False, payload, reason

    effective = _merge_remote_license_controls(data, remote_data)
    try:
        provision_source = dict(effective)
        for k in ('admin_empresa_senha', 'admin_senha', 'senha_admin_empresa', 'senha_admin'):
            provision_source.pop(k, None)
        _aplicar_licenca_empresa(provision_source)
    except Exception:
        pass
    payload = {**effective, 'licenciada': True, 'maquina_id_final': mid, 'validacao_remota': _sanitize_license_public(remote_data)}
    _LICENSE_CACHE.update({'ts': now, 'ok': True, 'data': payload, 'reason': ''})
    return True, payload, ''



def _license_first(source, names, default=''):
    """Pega o primeiro campo preenchido vindo da API de licença."""
    source = source if isinstance(source, dict) else {}
    for name in names:
        value = source.get(name)
        if value is not None and str(value).strip() != '':
            return value
    return default


def _license_bool(source, names, default=False):
    value = _license_first(source, names, None)
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    txt = str(value).strip().lower()
    return txt in {'1', 'true', 'sim', 's', 'yes', 'y', 'on', 'liberado', 'ativo', 'permitido'}


def _license_int(source, names, default=1, min_value=1, max_value=1000):
    value = _license_first(source, names, default)
    try:
        n = int(value)
    except Exception:
        n = default
    n = max(min_value, n)
    if max_value is not None:
        n = min(max_value, n)
    return n


def _license_plan(source):
    raw = str(_license_first(source, ['plano', 'license_plan', 'tipo_plano', 'tipo_acesso', 'tipo'], 'local') or 'local').strip().lower()
    raw = raw.replace('ú', 'u').replace('ã', 'a').replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ç', 'c')
    if 'tunel' in raw or 'tunnel' in raw or raw in {'empresa_tunel', 'empresa+tunel', 'empresa-tunel'}:
        return 'empresa_tunel'
    if 'empresa' in raw or 'equipe' in raw or raw in {'team', 'business', 'company'}:
        return 'empresa'
    return 'local'


def _license_company_id(source):
    cid = _license_first(source, ['company_id', 'empresa_id', 'empresa_slug', 'id_empresa'], '')
    if cid:
        return _slug_company_id(str(cid))
    cnpj = normalize_cnpj(str(source.get('cnpj') or ''))
    if cnpj:
        return _slug_company_id('empresa_' + cnpj[-8:])
    cliente = str(_license_first(source, ['empresa_nome', 'company_name', 'cliente', 'empresa', 'nome'], '') or '')
    if cliente:
        return _slug_company_id(cliente)
    email = str(source.get('email') or '').strip().lower()
    if '@' in email:
        return _slug_company_id(email.split('@', 1)[0])
    return 'empresa'


def _license_admin_login(source, company_id):
    login = str(_license_first(source, ['admin_empresa_login', 'admin_login', 'login_admin_empresa', 'usuario_admin', 'admin_user'], '') or '').strip()
    if not login:
        login = f'{company_id}_admin'
    login = _slug_company_id(login)
    if login in {'admin', 'administrador', 'global_admin'}:
        login = f'{company_id}_admin'
    return login


def _aplicar_licenca_empresa(source):
    """
    Aplica localmente os dados comerciais da licença:
    - cria/atualiza a empresa;
    - cria/atualiza o admin da empresa;
    - grava limite de usuários/vendedores;
    - grava se o túnel Cloudflare está liberado.
    """
    source = source if isinstance(source, dict) else {}
    plano = _license_plan(source)
    permitir_tunel = _license_bool(source, ['permitir_tunel', 'cloudflare_tunnel', 'tunel_cloudflare', 'tunnel_enabled'], plano == 'empresa_tunel')
    if plano == 'local':
        return {'aplicado': False, 'motivo': 'plano_local', 'plano': plano, 'permitir_tunel': permitir_tunel}

    company_id = _license_company_id(source)
    empresa_nome = str(_license_first(source, ['empresa_nome', 'company_name', 'empresa', 'cliente', 'nome'], company_id) or company_id).strip()
    limite = _license_int(source, ['max_usuarios', 'usuarios_permitidos', 'limite_usuarios', 'limite_subcontas', 'subcontas_limite'], 1)
    admin_login = _license_admin_login(source, company_id)
    admin_nome = str(_license_first(source, ['admin_empresa_nome', 'admin_nome', 'nome_admin_empresa'], empresa_nome or admin_login) or admin_login).strip()
    admin_senha = str(_license_first(source, ['admin_empresa_senha', 'admin_senha', 'senha_admin_empresa', 'senha_admin'], '') or '')

    usuarios = _carregar_usuarios()
    if admin_login in usuarios and usuarios.get(admin_login, {}).get('scope') == 'global_admin':
        return {'aplicado': False, 'erro': 'login_admin_reservado', 'admin_empresa_login': admin_login, 'company_id': company_id}

    empresa = _garantir_empresa(
        company_id,
        nome=empresa_nome,
        limite_subcontas=limite,
        admin_user=admin_login,
        ativa=True,
        criado_por='licenca'
    )

    empresas = _carregar_empresas()
    emp = dict(empresas.get(company_id, empresa) or {})
    emp.update({
        'id': company_id,
        'nome': empresa_nome or emp.get('nome') or company_id,
        'limite_subcontas': limite,
        'ativo': True,
        'admin_user': admin_login,
        'plano': plano,
        'permitir_tunel': bool(permitir_tunel),
        'licenca_email': source.get('email', ''),
        'licenca_cnpj': normalize_cnpj(str(source.get('cnpj') or '')),
        'licenca_atualizada_em': time.strftime('%Y-%m-%dT%H:%M:%S'),
    })
    empresas[company_id] = emp
    _salvar_empresas(empresas)

    atual = usuarios.get(admin_login)
    if atual and not isinstance(atual, dict):
        atual = None
    if atual is None:
        if not admin_senha:
            return {
                'aplicado': False,
                'erro': 'senha_admin_empresa_ausente',
                'company_id': company_id,
                'admin_empresa_login': admin_login,
                'limite_subcontas': limite,
                'permitir_tunel': bool(permitir_tunel),
            }
        usuarios[admin_login] = {
            'nome': admin_nome,
            'senha': _phash(admin_senha),
            'role': 'admin',
            'scope': 'company_admin',
            'company_id': company_id,
            'company_name': empresa_nome,
            'ativo': True,
            'criado_por': 'licenca',
            'subcontas_limite': limite,
        }
        acao_admin = 'criado'
    else:
        atual['nome'] = atual.get('nome') or admin_nome
        atual['role'] = 'admin'
        atual['scope'] = 'company_admin'
        atual['company_id'] = company_id
        atual['company_name'] = empresa_nome
        atual['ativo'] = True
        atual['subcontas_limite'] = limite
        atual['criado_por'] = atual.get('criado_por') or 'licenca'
        if admin_senha:
            atual['senha'] = _phash(admin_senha)
        usuarios[admin_login] = atual
        acao_admin = 'atualizado'

    _salvar_usuarios(usuarios)
    return {
        'aplicado': True,
        'plano': plano,
        'company_id': company_id,
        'empresa_nome': empresa_nome,
        'admin_empresa_login': admin_login,
        'admin_empresa': acao_admin,
        'limite_subcontas': limite,
        'permitir_tunel': bool(permitir_tunel),
    }


def _save_local_license(api_data, email='', cnpj=''):
    if not LICENSE_SECRET:
        raise ValueError('Configure PROSPECTAX_LICENSE_SECRET para assinar licencas locais.')
    now_iso = time.strftime('%Y-%m-%dT%H:%M:%S')
    mid = _machine_id()
    source = api_data if isinstance(api_data, dict) else {}
    plano = _license_plan(source)
    permitir_tunel = _license_bool(source, ['permitir_tunel', 'cloudflare_tunnel', 'tunel_cloudflare', 'tunnel_enabled'], plano == 'empresa_tunel')
    limite = _license_int(source, ['max_usuarios', 'usuarios_permitidos', 'limite_usuarios', 'limite_subcontas', 'subcontas_limite'], 1)
    company_id = _license_company_id(source) if plano != 'local' else ''
    empresa_nome = str(_license_first(source, ['empresa_nome', 'company_name', 'empresa', 'cliente', 'nome'], '') or '').strip()
    admin_login = _license_admin_login(source, company_id) if plano != 'local' else ''

    data = {
        'produto': 'ProspectaX',
        'tipo': source.get('tipo') or 'vitalicia',
        'plano': plano,
        'status': source.get('status') or 'ativa',
        'cliente': source.get('cliente') or source.get('nome') or source.get('empresa') or '',
        'email': source.get('email') or email or '',
        'cnpj': normalize_cnpj(str(source.get('cnpj') or cnpj or '')),
        'maquina_id': mid,
        'ativada_em': source.get('ativada_em') or source.get('activated_at') or now_iso,
        'validada_em': now_iso,
        'api_url': LICENSE_API_URL,
        'max_usuarios': limite,
        'usuarios_permitidos': limite,
        'limite_subcontas': limite,
        'permitir_tunel': bool(permitir_tunel),
    }
    if company_id:
        data['company_id'] = company_id
        data['empresa_id'] = company_id
    if empresa_nome:
        data['company_name'] = empresa_nome
        data['empresa_nome'] = empresa_nome
    if admin_login:
        data['admin_empresa_login'] = admin_login

    for k in ('licenca_id', 'license_id', 'pedido_id', 'transaction_id', 'chave'):
        if source.get(k):
            data[k] = source.get(k)

    provisionamento = _aplicar_licenca_empresa({**source, **data})
    if plano != 'local' and not provisionamento.get('aplicado'):
        detalhe = provisionamento.get('erro') or provisionamento.get('motivo') or 'provisionamento_nao_aplicado'
        raise ValueError(f'Licença aprovada, mas não foi possível criar o admin da empresa: {detalhe}')
    data['provisionamento_local'] = {k: v for k, v in provisionamento.items() if k not in {'erro'}}
    data['assinatura_local'] = _assinatura_licenca(data)
    LICENSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LICENSE_FILE.with_suffix(LICENSE_FILE.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(LICENSE_FILE)
    _LICENSE_CACHE.update({'ts': 0.0, 'ok': False, 'data': {}, 'reason': ''})
    out = dict(data)
    out['_provisionamento'] = provisionamento
    return out

def _ativar_licenca_remota(email='', cnpj=''):
    email = (email or '').strip()
    cnpj = normalize_cnpj(str(cnpj or ''))
    if not email and not cnpj:
        return {'ok': False, 'erro': 'Informe o e-mail da compra ou o CNPJ.'}
    if not LICENSE_API_URL:
        return {'ok': False, 'erro': 'API de licença não configurada.'}

    body = json.dumps({
        'produto': 'ProspectaX',
        'email': email,
        'cnpj': cnpj,
        'maquina_id': _machine_id(),
        'machine_id': _machine_id(),
        'app': 'ProspectaX',
    }).encode('utf-8')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'ProspectaX/4.0'}
    try:
        req = Request(LICENSE_API_URL, data=body, headers=headers, method='POST')
        with urlopen(req, timeout=25) as resp:
            raw = resp.read().decode('utf-8', errors='ignore')
            status_code = getattr(resp, 'status', 200)
    except HTTPError as e:
        try:
            raw = e.read().decode('utf-8', errors='ignore')
        except Exception:
            raw = ''
        status_code = e.code
    except Exception as e:
        return {'ok': False, 'erro': f'Não foi possível conectar ao servidor de licença: {str(e)[:120]}'}

    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {'raw': raw}

    active_status = str((data or {}).get('status') or '').strip().lower()
    aprovado = bool(
        200 <= int(status_code) < 300 and (
            data.get('ok') is True or
            data.get('success') is True or
            data.get('valid') is True or
            data.get('licenciada') is True or
            data.get('ativa') is True or
            active_status in {'ativa', 'ativo', 'active', 'ok', 'aprovada', 'validada'}
        )
    )
    if not aprovado:
        erro = data.get('erro') or data.get('error') or data.get('message') or f'Licença não aprovada. HTTP {status_code}'
        return {'ok': False, 'erro': str(erro)}

    try:
        saved = _save_local_license(data, email=email, cnpj=cnpj)
    except Exception as e:
        return {'ok': False, 'erro': str(e)}
    return {
        'ok': True,
        'licenciada': True,
        'cliente': saved.get('cliente'),
        'email': saved.get('email'),
        'cnpj': saved.get('cnpj'),
        'plano': saved.get('plano', ''),
        'max_usuarios': saved.get('max_usuarios', 1),
        'limite_subcontas': saved.get('limite_subcontas', saved.get('max_usuarios', 1)),
        'permitir_tunel': bool(saved.get('permitir_tunel', False)),
        'company_id': saved.get('company_id', ''),
        'empresa_nome': saved.get('empresa_nome', saved.get('company_name', '')),
        'admin_empresa_login': saved.get('admin_empresa_login', ''),
        'provisionamento': saved.get('_provisionamento', {}),
        'maquina_id_final': saved.get('maquina_id'),
        'mensagem': data.get('mensagem') or data.get('message') or 'Licença ativada com sucesso.',
    }
# ===== FIM LICENÇA LOCAL / BLOQUEIO DE CLIENTE =====

EST_COLS = {
    'cnpj_basico': 'VARCHAR',
    'cnpj_ordem': 'VARCHAR',
    'cnpj_dv': 'VARCHAR',
    'matriz_filial': 'VARCHAR',
    'nome_fantasia': 'VARCHAR',
    'situacao_cadastral': 'VARCHAR',
    'data_situacao_cadastral': 'VARCHAR',
    'motivo_situacao_cadastral': 'VARCHAR',
    'nome_cidade_exterior': 'VARCHAR',
    'pais': 'VARCHAR',
    'data_inicio_atividade': 'VARCHAR',
    'cnae_principal': 'VARCHAR',
    'cnae_secundaria': 'VARCHAR',
    'tipo_logradouro': 'VARCHAR',
    'logradouro': 'VARCHAR',
    'numero': 'VARCHAR',
    'complemento': 'VARCHAR',
    'bairro': 'VARCHAR',
    'cep': 'VARCHAR',
    'uf': 'VARCHAR',
    'municipio': 'VARCHAR',
    'ddd_1': 'VARCHAR',
    'telefone_1': 'VARCHAR',
    'ddd_2': 'VARCHAR',
    'telefone_2': 'VARCHAR',
    'ddd_fax': 'VARCHAR',
    'fax': 'VARCHAR',
    'correio_eletronico': 'VARCHAR',
    'situacao_especial': 'VARCHAR',
    'data_situacao_especial': 'VARCHAR'
}
EMP_COLS = {
    'cnpj_basico': 'VARCHAR',
    'razao_social': 'VARCHAR',
    'natureza_juridica': 'VARCHAR',
    'qualificacao_responsavel': 'VARCHAR',
    'capital_social': 'VARCHAR',
    'porte_empresa': 'VARCHAR',
    'ente_federativo_responsavel': 'VARCHAR'
}
MUNI_COLS = {
    'codigo': 'VARCHAR',
    'descricao': 'VARCHAR'
}

MEI_NATUREZAS = {'2135'}

def _pattern(data_dir, prefix_part, nome_unico):
    partes = sorted(data_dir.glob(f'{prefix_part}_*.parquet'))
    if partes:
        return str(data_dir / f'{prefix_part}_*.parquet')
    return str(data_dir / nome_unico)

EST_PATTERN  = _pattern(DATA_DIR, 'estabelecimentos_part', 'estabelecimentos.parquet')
EMP_PATTERN  = _pattern(DATA_DIR, 'empresas_part',         'empresas.parquet')
SOC_PATTERN  = _pattern(DATA_DIR, 'socios_part',           'socios.parquet')
MUNI_PATTERN = str(DATA_DIR / 'municipios.parquet')
QUAL_PATTERN = str(DATA_DIR / 'qualificacoes.parquet')

# ===== ÍNDICE RÁPIDO CNAE / REGIÃO =====
# Arquivo gerado por 02_CRIAR_INDICE_RAPIDO.py dentro da pasta dados_parquet.
# Se ele existir, a busca principal usa este banco pronto. Se não existir,
# o sistema continua funcionando pelo modo antigo com os Parquets.
INDEX_DB_PATH = Path(os.environ.get('PROSPECTAX_INDEX_DB', str(DATA_DIR / 'prospectax_index.duckdb')))

# ===== BANCO RÁPIDO COMPLETO (prospectax_fast.duckdb) =====
# Criado por setup_banco_rapido.py — contém px_estabelecimentos, px_socios, px_busca, etc.
# Usado para fetch_cnpj_local, fetch_socios_local, buscar_concorrentes e contar_filiais.
FAST_DB_PATH = Path(os.environ.get(
    'PROSPECTAX_FAST_DB',
    str(BASE_DIR / 'prospectax_fast.duckdb')
))
_fastdb_local = threading.local()

def fast_db_available():
    try:
        return FAST_DB_PATH.exists() and FAST_DB_PATH.stat().st_size > 1024 * 1024
    except Exception:
        return False

def get_fast_db():
    """Conexão thread-local ao prospectax_fast.duckdb (read-only)."""
    conn = getattr(_fastdb_local, 'conn', None)
    if conn is not None:
        try:
            conn.execute('SELECT 1')
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            _fastdb_local.conn = None
    conn = duckdb.connect(database=str(FAST_DB_PATH), read_only=True)
    try:
        conn.execute("PRAGMA threads=4;")
        conn.execute("PRAGMA enable_progress_bar=false;")
    except Exception:
        pass
    _fastdb_local.conn = conn
    return conn


_indexdb_local = threading.local()

def _sql_quote(value):
    return "'" + str(value).replace("'", "''") + "'"

def fast_index_available():
    try:
        return INDEX_DB_PATH.exists() and INDEX_DB_PATH.stat().st_size > 1024
    except Exception:
        return False

def index_table_exists(table_name):
    if not fast_index_available():
        return False
    table_name = str(table_name or '').strip()
    if not table_name:
        return False
    try:
        conn = get_index_db()
        row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='main' AND table_name = ?",
            [table_name]
        ).fetchone()
        return bool(row and int(row[0] or 0) > 0)
    except Exception:
        return False

def get_index_db():
    conn = getattr(_indexdb_local, 'conn', None)
    if conn is not None:
        try:
            conn.execute('SELECT 1')
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            _indexdb_local.conn = None
    conn = duckdb.connect(database=str(INDEX_DB_PATH), read_only=True)
    try:
        conn.execute("PRAGMA threads=4;")
        conn.execute("PRAGMA enable_progress_bar=false;")
    except Exception:
        pass
    _indexdb_local.conn = conn
    return conn

def fetch_cnpj_index_local(cnpj_limpo, include_socios=False):
    """Resposta instantanea usando o indice principal, sem varrer Parquet bruto."""
    cnpj_limpo = normalize_cnpj(str(cnpj_limpo or ''))
    if len(cnpj_limpo) != 14 or not fast_index_available():
        return {}
    try:
        conn = get_index_db()
        cur = conn.execute("SELECT * FROM prospects_idx WHERE cnpj = ? LIMIT 1", [cnpj_limpo])
        cols = [col[0] for col in cur.description]
        row = cur.fetchone()
        if not row:
            return {}
        d = dict(zip(cols, row))
        cnae_code = str(d.get('cnae') or '').strip()
        porte_map = {'00': 'Nao informado', '01': 'Micro empresa', '03': 'Empresa de pequeno porte', '05': 'Demais'}
        porte = str(d.get('porte_empresa') or '').strip()
        socios = fetch_socios_local(cnpj_limpo[:8], limit=20) if include_socios else []
        result = {
            'cnpj': cnpj_limpo,
            'razao_social': str(d.get('razao_social') or d.get('nome') or '').strip(),
            'nome_fantasia': str(d.get('nome_fantasia') or '').strip(),
            'abertura': str(d.get('abertura') or '').strip(),
            'capital_social': _fmt_brl_api(d.get('capital_social')) if d.get('capital_social') else '',
            'situacao': str(d.get('situacao') or '').strip(),
            'porte': porte_map.get(porte, porte),
            'natureza_juridica': str(d.get('natureza_juridica') or '').strip(),
            'cnae_principal_codigo': cnae_code,
            'cnae_principal_descricao': _cnae_desc_from_code(cnae_code),
            'telefone_1': str(d.get('telefone') or '').strip(),
            'telefone_2': str(d.get('telefone_2') or '').strip(),
            'email': str(d.get('email') or '').strip(),
            'logradouro': str(d.get('end') or '').strip(),
            'bairro': str(d.get('bairro') or '').strip(),
            'cep': str(d.get('cep') or '').strip(),
            'municipio': str(d.get('cidade') or '').strip(),
            'uf': str(d.get('uf') or '').strip(),
            'mei': _api_flag(d.get('mei')) if d.get('mei_fonte') == 'simples_nacional' else None,
            'socios': socios,
            '_socios_pending': not include_socios,
            '_enrich_version': 2,
            '_fonte': 'base rapida local',
            '_fonte_local': 'prospectax_index',
            '_modo_rapido': True,
            '_detalhes_parciais': True
        }
        result['faturamento_estimado'] = estimar_faturamento('', result.get('porte',''), result.get('cnae_principal_descricao',''), '')
        result['funcionarios_estimados'] = estimar_funcionarios(result.get('porte',''), '', '')
        return result
    except Exception:
        return {}

def warmup_index_db_async():
    def _warm():
        try:
            if fast_index_available():
                get_index_db().execute("SELECT cnpj FROM prospects_idx LIMIT 1").fetchone()
        except Exception:
            pass
    try:
        threading.Thread(target=_warm, daemon=True).start()
    except Exception:
        pass


_INDEX_SCHEMA_CACHE = QueryCache(4)

def _index_columns():
    if not fast_index_available():
        return set()
    columns, _ = _INDEX_SCHEMA_CACHE.get(database_revision(INDEX_DB_PATH), lambda: tuple(row[0] for row in get_index_db().execute('DESCRIBE prospects_idx').fetchall()))
    return set(columns)


def build_search_sql_indexed(cnaes, ufs, situacoes, excluir_mei=False, palavra='', limit=200000, offset=0, cnae_modo='todos', count_only=False, order_results=True, cidades=None, count_by_uf=False, cnpjs=None, excluir_cnpjs=None, paginate=True, discovery=None):
    """Busca rápida usando prospectax_index.duckdb, sem varrer Parquet bruto."""
    cnae_vals = ",".join(_sql_quote(v) for v in cnaes)
    multi_cnae = len(cnaes or []) > 1
    cnae_modo = 'primario' if str(cnae_modo or '').strip().lower() in {'primario', 'principal', 'primary'} else 'todos'
    where = []

    if cnae_modo == 'primario':
        where.append(f"p.cnae IN ({cnae_vals})")

    if ufs:
        uf_vals = ",".join(_sql_quote(v) for v in ufs)
        where.append(f"p.uf IN ({uf_vals})")

    if situacoes:
        sit_vals = ",".join(_sql_quote(v) for v in situacoes)
        where.append(f"p.situacao IN ({sit_vals})")

    cidades = [str(c or '').strip() for c in (cidades or []) if str(c or '').strip()]
    if cidades:
        city_vals = ",".join(_sql_quote(_normalizar(c)) for c in cidades)
        where.append(f"lower(strip_accents(coalesce(p.cidade, ''))) IN ({city_vals})")

    if excluir_mei:
        where.append("coalesce(p.mei, 'NAO') <> 'SIM'")

    if palavra:
        termo_busca = _normalizar(palavra)
        if re.fullmatch(r'[\d. /-]+', termo_busca):
            termo_busca = re.sub(r'\D', '', termo_busca)
        termos = [t for t in re.split(r'\s+', termo_busca) if len(t) >= 2][:6]
        if termos:
            hay = "lower(strip_accents(coalesce(p.nome,'') || ' ' || coalesce(p.cidade,'') || ' ' || coalesce(p.email,'') || ' ' || coalesce(p.telefone,'') || ' ' || coalesce(p.cnae,'') || ' ' || p.cnpj))"
            where.append("(" + " AND ".join(f"contains({hay}, {_sql_quote(t)})" for t in termos) + ")")

    if cnpjs is not None:
        where.append("p.cnpj IN (" + ",".join(_sql_quote(v) for v in cnpjs) + ")" if cnpjs else "1=0")
    if excluir_cnpjs:
        where.append("p.cnpj NOT IN (" + ",".join(_sql_quote(v) for v in excluir_cnpjs) + ")")
    available = _index_columns()
    where.extend(filter_sql(discovery, available))
    where_sql = " AND ".join(where) if where else "1=1"
    limit = _sanitize_int(limit, default=200000, min_value=1, max_value=SEARCH_MAX_LIMIT)
    offset = _sanitize_int(offset, default=0, min_value=0, max_value=5_000_000)

    # JOIN direto deixa o DuckDB iniciar pelo mapeamento de CNAE, que é bem mais rápido
    # para o modo "primário ou secundário" do que SEMI JOIN nesta base.
    cnae_join_sql = ''
    if cnae_modo != 'primario':
        cnae_join_sql = f"""
    JOIN (SELECT DISTINCT cnpj FROM cnae_empresa_idx WHERE cnae IN ({cnae_vals})) m
      ON p.cnpj = m.cnpj"""

    order_sql = "p.uf, p.cidade, p.nome, p.cnpj"
    if palavra:
        p_ord = palavra.replace("'", "''").strip().lower()
        if p_ord:
            order_sql = f"""
        CASE
            WHEN lower(coalesce(p.nome,'')) = '{p_ord}' THEN 0
            WHEN lower(coalesce(p.nome,'')) LIKE '{p_ord}%' THEN 1
            WHEN lower(coalesce(p.nome,'')) LIKE '%{p_ord}%' THEN 2
            WHEN lower(coalesce(p.cidade,'')) LIKE '%{p_ord}%' THEN 3
            ELSE 4
        END,
        p.nome, p.uf, p.cidade, p.cnpj"""

    if count_only:
        count_expr = "COUNT(*)"
        if count_by_uf:
            return f"""
    SELECT p.uf, {count_expr} AS total
    FROM prospects_idx p
    {cnae_join_sql}
    WHERE {where_sql}
    GROUP BY p.uf
    """
        return f"""
    SELECT {count_expr} AS total
    FROM prospects_idx p
    {cnae_join_sql}
    WHERE {where_sql}
    """

    order_clause = f"ORDER BY {order_sql}" if order_results else ""
    select_word = "SELECT"
    pagination = f"LIMIT {limit} OFFSET {offset}" if paginate else ""

    extra_select = ''.join(', p.' + col for col in ('cep', 'bairro', 'abertura', 'telefone_2', 'matriz_filial') if col in available)
    return f"""
    {select_word}
        p.cnpj,
        p.nome,
        p.situacao,
        p.cnae,
        p.uf,
        p.cidade,
        p.municipio_cod,
        p.telefone,
        p.email,
        p.end,
        p.natureza_juridica,
        p.porte_empresa,
        p.mei{extra_select}
    FROM prospects_idx p
    {cnae_join_sql}
    WHERE {where_sql}
    {order_clause}
    {pagination}
    """


_duckdb_local = threading.local()

def get_db():
    conn = getattr(_duckdb_local, 'conn', None)
    if conn is not None:
        try:
            conn.execute('SELECT 1')
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            _duckdb_local.conn = None
    conn = duckdb.connect(database=':memory:', read_only=False)
    conn.execute("PRAGMA threads=4;")
    conn.execute("PRAGMA enable_progress_bar=false;")
    _duckdb_local.conn = conn
    return conn

def run_query(sql):
    conn = get_db()
    return conn.execute(sql)

# Cache em memoria para enriquecimento (evita bater na API externa repetidamente)
# Formato: { cnpj: { 'data': {...}, 'ts': timestamp } }
_enrich_cache = {}
CACHE_TTL = 3600  # 1 hora em segundos


def _json_safe(value):
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        import math
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def jdump(obj):
    return json.dumps(_json_safe(obj), ensure_ascii=False, allow_nan=False).encode('utf-8')


CHINA_SUPPLIERS_CACHE_DIR = SITE_DIR / "cache_fornecedores_china"
APIFY_MADE_IN_CHINA_ACTOR = "jungle_intertwining~made-in-china-scraper"
APIFY_MADE_IN_CHINA_PRIMARY_ACTOR = "parseforge~made-in-china-scraper"


def _china_supplier_cache_key(keyword, cnae='', hs_code='', max_pages=1):
    raw = json.dumps({
        'keyword': (keyword or '').strip().lower(),
        'cnae': re.sub(r'\D', '', cnae or ''),
        'hs_code': re.sub(r'[^0-9.]', '', hs_code or ''),
        'max_pages': int(max_pages or 1),
        'actor': APIFY_MADE_IN_CHINA_PRIMARY_ACTOR,
        'v': 3,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _read_china_supplier_cache(cache_key, max_age_days=30):
    try:
        fp = CHINA_SUPPLIERS_CACHE_DIR / f"{cache_key}.json"
        if not fp.exists():
            return None
        data = json.loads(fp.read_text(encoding='utf-8'))
        if not data.get('suppliers'):
            return None
        ts = float(data.get('fetched_at_ts') or 0)
        if max_age_days and ts and (time.time() - ts) > max_age_days * 86400:
            return None
        return data
    except Exception:
        return None


def _write_china_supplier_cache(cache_key, payload):
    try:
        CHINA_SUPPLIERS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = dict(payload or {})
        data['cache_key'] = cache_key
        data['fetched_at_ts'] = time.time()
        (CHINA_SUPPLIERS_CACHE_DIR / f"{cache_key}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
    except Exception:
        pass


def _apify_request_json(api_path, timeout=15, method='GET', payload=None):
    if not APIFY_TOKEN:
        return {'configured': False, 'error': 'Token do Apify nao configurado'}
    sep = '&' if '?' in api_path else '?'
    url = f"https://api.apify.com{api_path}{sep}token={urllib.parse.quote(APIFY_TOKEN)}"
    data = None
    headers = {'User-Agent': 'ProspectaX/1.0'}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _apify_usage_status():
    if not APIFY_TOKEN:
        return {'configured': False, 'error': 'Token do Apify nao configurado'}
    try:
        limits = _apify_request_json('/v2/users/me/limits', timeout=15)
        data = limits.get('data') or {}
        lim = data.get('limits') or {}
        cur = data.get('current') or {}
        max_usd = float(lim.get('maxMonthlyUsageUsd') or 0)
        used_usd = float(cur.get('monthlyUsageUsd') or 0)
        remaining = max(0.0, max_usd - used_usd) if max_usd else None
        return {
            'configured': True,
            'ok': True,
            'cycle': data.get('monthlyUsageCycle') or {},
            'max_monthly_usage_usd': max_usd,
            'monthly_usage_usd': used_usd,
            'remaining_usage_usd': remaining,
            'monthly_actor_compute_units': cur.get('monthlyActorComputeUnits'),
            'max_monthly_actor_compute_units': lim.get('maxMonthlyActorComputeUnits'),
        }
    except HTTPError as e:
        msg = e.read().decode('utf-8', errors='ignore')[:300] if hasattr(e, 'read') else str(e)
        return {'configured': True, 'ok': False, 'error': f'Apify HTTP {e.code}: {msg}'}
    except Exception as e:
        return {'configured': True, 'ok': False, 'error': str(e)[:300]}


def _keyword_from_cnae(cnae_code):
    code = re.sub(r'\D', '', cnae_code or '')
    if not code:
        return ''
    try:
        load_cnae_ibge()
        item = next((x for x in _cnae_cache if x.get('code') == code), None)
        desc = (item or {}).get('desc') or ''
        desc = re.sub(r'\b(com[eé]rcio|varejista|atacadista|servi[cç]os?|atividade|fabrica[cç][aã]o|manuten[cç][aã]o|repara[cç][aã]o)\b', ' ', desc, flags=re.I)
        words = [w for w in re.split(r'[^A-Za-zÀ-ÿ0-9]+', desc) if len(w) >= 4]
        return ' '.join(words[:5]).strip()
    except Exception:
        return ''


CHINA_KEYWORD_ALIASES = {
    'esmalte': ['nail polish', 'gel polish', 'uv gel polish'],
    'esmalte de unha': ['nail polish', 'gel polish', 'uv gel polish'],
    'esmaltes': ['nail polish', 'gel polish', 'uv gel polish'],
    'unha': ['nail products', 'manicure tools', 'nail art supplies'],
    'unhas': ['nail products', 'manicure tools', 'nail art supplies'],
    'manicure': ['manicure tools', 'nail polish', 'nail lamp'],
    'pedicure': ['pedicure tools', 'foot file', 'nail care tools'],
    'cabeleireiro': ['hair salon equipment', 'hair dryer', 'hair dye'],
    'cabeleireiros': ['hair salon equipment', 'hair dryer', 'hair dye'],
    'shampoo': ['shampoo', 'hair conditioner', 'hair care products'],
    'tintura de cabelo': ['hair dye', 'hair color cream'],
    'secador': ['hair dryer', 'professional hair dryer'],
    'chapinha': ['hair straightener', 'flat iron hair straightener'],
    'painel solar': ['solar panel', 'photovoltaic panel', 'solar module'],
    'paineis solares': ['solar panel', 'photovoltaic panel', 'solar module'],
    'placa solar': ['solar panel', 'photovoltaic panel', 'solar module'],
    'energia solar': ['solar panel', 'solar inverter', 'solar mounting system'],
    'inversor solar': ['solar inverter'],
    'bateria solar': ['solar battery', 'lithium solar battery'],
    'auto pecas': ['auto parts', 'car parts'],
    'autopeças': ['auto parts', 'car parts'],
    'pecas automotivas': ['auto parts', 'car parts'],
    'embalagem': ['packaging', 'packaging machine'],
    'maquina de embalagem': ['packaging machine'],
    'maquina industrial': ['industrial machine', 'machinery'],
    'led': ['led light', 'led lamp'],
    'lampada led': ['led lamp', 'led light'],
}


CHINA_CNAE_PRODUCT_SUGGESTIONS = {
    '9602501': [
        {'label': 'Esmalte de unha', 'term': 'nail polish'},
        {'label': 'Esmalte em gel', 'term': 'gel polish'},
        {'label': 'Cabine UV/LED para unhas', 'term': 'nail lamp'},
        {'label': 'Kit manicure', 'term': 'manicure tools'},
        {'label': 'Produtos para nail art', 'term': 'nail art supplies'},
        {'label': 'Alicate e cortador de unha', 'term': 'nail clipper'},
        {'label': 'Secador de cabelo profissional', 'term': 'professional hair dryer'},
        {'label': 'Chapinha / prancha', 'term': 'hair straightener'},
        {'label': 'Tintura de cabelo', 'term': 'hair dye'},
        {'label': 'Shampoo e condicionador', 'term': 'hair care products'},
        {'label': 'Cadeira de salao', 'term': 'salon chair'},
    ],
    '4755502': [
        {'label': 'Tecidos', 'term': 'fabric'},
        {'label': 'Algodao', 'term': 'cotton fabric'},
        {'label': 'Malha', 'term': 'knitted fabric'},
        {'label': 'Poliester', 'term': 'polyester fabric'},
    ],
    '4530703': [
        {'label': 'Autopecas', 'term': 'auto parts'},
        {'label': 'Acessorios automotivos', 'term': 'car accessories'},
        {'label': 'Lampada automotiva LED', 'term': 'car led light'},
        {'label': 'Sensor automotivo', 'term': 'car sensor'},
    ],
    '4742300': [
        {'label': 'Material eletrico', 'term': 'electrical supplies'},
        {'label': 'Disjuntor', 'term': 'circuit breaker'},
        {'label': 'Cabo eletrico', 'term': 'electric cable'},
        {'label': 'Interruptor', 'term': 'wall switch'},
    ],
    '4753900': [
        {'label': 'Eletrodomesticos', 'term': 'home appliance'},
        {'label': 'Liquidificador', 'term': 'blender'},
        {'label': 'Air fryer', 'term': 'air fryer'},
        {'label': 'Caixa de som', 'term': 'bluetooth speaker'},
    ],
    '4321500': [
        {'label': 'Painel solar', 'term': 'solar panel'},
        {'label': 'Inversor solar', 'term': 'solar inverter'},
        {'label': 'Cabo solar', 'term': 'solar cable'},
        {'label': 'Kit energia solar', 'term': 'solar power system'},
    ],
}


def _china_product_suggestions_for_cnae(cnae_code):
    code = re.sub(r'\D', '', cnae_code or '')
    if not code:
        return []
    if code in CHINA_CNAE_PRODUCT_SUGGESTIONS:
        return CHINA_CNAE_PRODUCT_SUGGESTIONS[code]
    try:
        load_cnae_ibge()
        item = next((x for x in _cnae_cache if x.get('code') == code), None)
        desc_norm = _normalizar((item or {}).get('desc') or '')
        suggestions = []
        rules = [
            (('manicure', 'pedicure', 'cabeleireiro', 'beleza'), [
                {'label': 'Esmalte de unha', 'term': 'nail polish'},
                {'label': 'Kit manicure', 'term': 'manicure tools'},
                {'label': 'Secador profissional', 'term': 'professional hair dryer'},
            ]),
            (('solar', 'eletrica', 'instalacao eletrica'), [
                {'label': 'Painel solar', 'term': 'solar panel'},
                {'label': 'Inversor solar', 'term': 'solar inverter'},
                {'label': 'Material eletrico', 'term': 'electrical supplies'},
            ]),
            (('auto', 'veiculo', 'automotor'), [
                {'label': 'Autopecas', 'term': 'auto parts'},
                {'label': 'Acessorios automotivos', 'term': 'car accessories'},
                {'label': 'Ferramentas automotivas', 'term': 'automotive tools'},
            ]),
            (('construcao', 'hidraulico', 'material'), [
                {'label': 'Ferramentas', 'term': 'hand tools'},
                {'label': 'Material hidraulico', 'term': 'plumbing supplies'},
                {'label': 'Maquina de construcao', 'term': 'construction machinery'},
            ]),
        ]
        for keys, values in rules:
            if any(k in desc_norm for k in keys):
                suggestions.extend(values)
        return suggestions[:12]
    except Exception:
        return []


def _china_search_keywords(keyword):
    kw = (keyword or '').strip()
    if not kw:
        return []
    norm = _normalizar(kw)
    terms = []
    for alias in CHINA_KEYWORD_ALIASES.get(norm, []):
        if alias not in terms:
            terms.append(alias)
    if kw not in terms:
        terms.append(kw)
    return terms[:4]


def _normalize_apify_supplier_item(item, fallback_keyword='', hs_code=''):
    if not isinstance(item, dict):
        item = {}
    member_level = item.get('memberLevel') or item.get('certifications') or ''
    if isinstance(member_level, list):
        member_level = ', '.join(str(x) for x in member_level if x)
    elif isinstance(member_level, dict):
        member_level = json.dumps(member_level, ensure_ascii=False)
    return {
        'product_name': item.get('productName') or item.get('title') or item.get('name') or '',
        'product_url': item.get('productUrl') or item.get('url') or item.get('listingUrl') or '',
        'price': item.get('price') or '',
        'moq': item.get('moq') or item.get('minimumOrderQuantity') or '',
        'supplier_name': item.get('supplierName') or item.get('companyName') or '',
        'supplier_url': item.get('supplierUrl') or item.get('companyUrl') or '',
        'supplier_location': item.get('supplierLocation') or item.get('location') or '',
        'business_type': item.get('businessType') or '',
        'member_level': member_level,
        'audited_supplier': bool(item.get('auditedSupplier') or item.get('verified')),
        'image_url': item.get('imageUrl') or '',
        'search_keyword': item.get('searchKeyword') or fallback_keyword,
        'hs_code': hs_code or '',
        'page_num': item.get('pageNum') or '',
        'position': item.get('position') or '',
        'source': 'apify-made-in-china',
    }


def _search_china_items_parseforge(keyword, max_pages=1):
    max_items = min(100, max(10, int(max_pages or 1) * 25))
    payload = {
        'searchQuery': keyword,
        'maxItems': max_items,
    }
    api_path = f'/v2/acts/{APIFY_MADE_IN_CHINA_PRIMARY_ACTOR}/run-sync-get-dataset-items'
    items = _apify_request_json(api_path, timeout=120, method='POST', payload=payload)
    return items if isinstance(items, list) else []


def _search_china_items_jungle(search_terms, max_pages=1):
    payload = {
        'searchKeywords': search_terms,
        'maxPages': max_pages,
    }
    api_path = f'/v2/acts/{APIFY_MADE_IN_CHINA_ACTOR}/run-sync-get-dataset-items'
    items = _apify_request_json(api_path, timeout=90, method='POST', payload=payload)
    return items if isinstance(items, list) else []


def _search_china_suppliers_apify(keyword, cnae='', hs_code='', max_pages=1, use_cache=True):
    keyword = (keyword or '').strip()
    if not keyword and cnae:
        suggestions = _china_product_suggestions_for_cnae(cnae)
        keyword = (suggestions[0] or {}).get('term') if suggestions else _keyword_from_cnae(cnae)
    if not keyword:
        return {'ok': False, 'error': 'Informe uma palavra-chave de produto ou um CNAE.', 'suppliers': []}

    max_pages = _sanitize_int(max_pages, default=1, min_value=1, max_value=5)
    search_terms = _china_search_keywords(keyword)
    cache_key = _china_supplier_cache_key(keyword, cnae, hs_code, max_pages)
    if use_cache:
        cached = _read_china_supplier_cache(cache_key)
        if cached:
            cached['from_cache'] = True
            return cached

    usage_before = _apify_usage_status()
    if not usage_before.get('configured'):
        return {'ok': False, 'configured': False, 'error': usage_before.get('error'), 'suppliers': []}
    if usage_before.get('ok') is False:
        return {'ok': False, 'configured': True, 'error': usage_before.get('error'), 'suppliers': []}
    remaining = usage_before.get('remaining_usage_usd')
    if remaining is not None and remaining <= 0:
        return {'ok': False, 'configured': True, 'error': 'Credito mensal do Apify esgotado nesta conta.', 'usage_before': usage_before, 'suppliers': []}

    try:
        actor_used = APIFY_MADE_IN_CHINA_PRIMARY_ACTOR
        items = _search_china_items_parseforge((search_terms or [keyword])[0], max_pages=max_pages)
        if not items:
            actor_used = APIFY_MADE_IN_CHINA_ACTOR
            items = _search_china_items_jungle(search_terms or [keyword], max_pages=max_pages)
        suppliers = [_normalize_apify_supplier_item(x, keyword, hs_code) for x in items]
        suppliers = [x for x in suppliers if x.get('supplier_name') or x.get('product_name')]
        usage_after = _apify_usage_status()
        result = {
            'ok': True,
            'configured': True,
            'from_cache': False,
            'keyword': keyword,
            'search_terms': search_terms,
            'cnae': re.sub(r'\D', '', cnae or ''),
            'hs_code': hs_code or '',
            'max_pages': max_pages,
            'actor_used': actor_used,
            'total': len(suppliers),
            'suppliers': suppliers,
            'usage_before': usage_before,
            'usage_after': usage_after,
            'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        if suppliers:
            _write_china_supplier_cache(cache_key, result)
        return result
    except HTTPError as e:
        msg = e.read().decode('utf-8', errors='ignore')[:500] if hasattr(e, 'read') else str(e)
        return {'ok': False, 'configured': True, 'error': f'Apify HTTP {e.code}: {msg}', 'usage_before': usage_before, 'suppliers': []}
    except Exception as e:
        return {'ok': False, 'configured': True, 'error': str(e)[:300], 'usage_before': usage_before, 'suppliers': []}


def clean_digits_list(values):
    out = []
    for v in values:
        d = re.sub(r'\D', '', v or '')
        if d:
            out.append(d)
    return out


def clean_uf_list(values):
    out = []
    for v in values:
        v = re.sub(r'[^A-Za-z]', '', v or '').upper()
        if len(v) == 2:
            out.append(v)
    return out


def clean_situ_list(values):
    allowed = {"ATIVA", "SUSPENSA", "INAPTA", "BAIXADA", "NULA"}
    out = []
    for v in values:
        t = (v or '').strip().upper()
        if t in allowed:
            out.append(t)
    return out


LOCALITY_CITY_COORDS = {
    'PR|CURITIBA': (-25.4284, -49.2733), 'PR|LONDRINA': (-23.3045, -51.1696), 'PR|MARINGA': (-23.4205, -51.9331),
    'PR|PONTA GROSSA': (-25.0950, -50.1619), 'PR|CASCAVEL': (-24.9555, -53.4552), 'PR|SAO JOSE DOS PINHAIS': (-25.5313, -49.2031),
    'PR|FOZ DO IGUACU': (-25.5163, -54.5854), 'PR|COLOMBO': (-25.2925, -49.2262), 'PR|GUARAPUAVA': (-25.3902, -51.4623),
    'PR|PARANAGUA': (-25.5205, -48.5095), 'PR|ARAUCARIA': (-25.5931, -49.4103), 'PR|TOLEDO': (-24.7246, -53.7412),
    'PR|APUCARANA': (-23.5508, -51.4636), 'PR|CAMPO MOURAO': (-24.0437, -52.3781), 'PR|ARAPONGAS': (-23.4153, -51.4259),
    'PR|PINHAIS': (-25.4447, -49.1927), 'PR|UMUARAMA': (-23.7656, -53.3201), 'PR|CAMBE': (-23.2758, -51.2784),
    'PR|FAZENDA RIO GRANDE': (-25.6624, -49.3073), 'PR|SARANDI': (-23.4439, -51.8733), 'PR|ALMIRANTE TAMANDARE': (-25.3247, -49.3100),
    'PR|PARANAVAI': (-23.0736, -52.4653), 'PR|PATO BRANCO': (-26.2292, -52.6706), 'PR|FRANCISCO BELTRAO': (-26.0817, -53.0535),
    'PR|CIANORTE': (-23.6633, -52.6050), 'PR|TELEMACO BORBA': (-24.3245, -50.6176), 'PR|ROLANDIA': (-23.3095, -51.3691),
    'PR|IBIPORA': (-23.2692, -51.0486), 'PR|IRATI': (-25.4677, -50.6511), 'PR|MARECHAL CANDIDO RONDON': (-24.5570, -54.0571),
    'PR|CASTRO': (-24.7891, -50.0108), 'PR|CORNELIO PROCOPIO': (-23.1813, -50.6469), 'PR|JACAREZINHO': (-23.1591, -49.9739),
    'PR|UNIAO DA VITORIA': (-26.2273, -51.0873), 'PR|PAICANDU': (-23.4575, -52.0486), 'PR|MANDAGUARI': (-23.5474, -51.6709),
    'PR|MARIALVA': (-23.4850, -51.7917), 'PR|ASTORGA': (-23.2325, -51.6656), 'PR|NOVA ESPERANCA': (-23.1820, -52.2031),
    'PR|MANDAGUACU': (-23.3472, -52.0953), 'PR|FLORESTA': (-23.6031, -52.0807), 'PR|IGUARACU': (-23.1940, -51.8252),
    'PR|JANDAIA DO SUL': (-23.6011, -51.6440), 'PR|COLORADO': (-22.8374, -51.9743), 'PR|IVAIPORA': (-24.2480, -51.6756),
    'PR|CAMPO LARGO': (-25.4596, -49.5304), 'PR|GUARATUBA': (-25.8817, -48.5752), 'PR|LAPA': (-25.7697, -49.7158),
    'PR|PALMAS': (-26.4842, -51.9906), 'PR|MEDIANEIRA': (-25.2977, -54.0943), 'PR|MATELANDIA': (-25.2408, -53.9964),
    'PR|ASSIS CHATEAUBRIAND': (-24.4168, -53.5213), 'PR|PALOTINA': (-24.2839, -53.8403), 'PR|GOIOERE': (-24.1838, -53.0248),
    'PR|LARANJEIRAS DO SUL': (-25.4077, -52.4109), 'PR|PARANACITY': (-22.9308, -52.1545),
}


def _locality_city_key(city, uf='PR'):
    city_norm = _normalizar(str(city or '')).upper().replace('-', ' ')
    city_norm = re.sub(r'\s+', ' ', city_norm).strip()
    return f"{str(uf or 'PR').strip().upper()}|{city_norm}"


def _haversine_km(a, b):
    import math
    r = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _locality_regional_payload(cnaes, ufs, situacoes, excluir_mei=False, cnae_modo='todos', centro='Maringa', raio_km=50):
    if not fast_index_available():
        return {'ok': False, 'error': 'Indice rapido nao encontrado.', 'cities': [], 'total': 0}
    ufs = ufs or ['PR']
    centro_key = None
    centro_coord = None
    for uf in ufs:
        key = _locality_city_key(centro, uf)
        if key in LOCALITY_CITY_COORDS:
            centro_key = key
            centro_coord = LOCALITY_CITY_COORDS[key]
            break
    if centro_coord is None:
        return {'ok': False, 'error': 'Cidade central sem coordenada local nesta versao.', 'cities': [], 'total': 0}

    raio_km = _sanitize_int(raio_km, default=50, min_value=1, max_value=500)
    candidates = []
    for key, coord in LOCALITY_CITY_COORDS.items():
        uf, city = key.split('|', 1)
        if ufs and uf not in ufs:
            continue
        dist = _haversine_km(centro_coord, coord)
        if dist <= raio_km:
            candidates.append({'key': key, 'uf': uf, 'city': city, 'coord': coord, 'dist': dist})
    if not candidates:
        return {'ok': True, 'center': centro, 'center_key': centro_key, 'radius_km': raio_km, 'cities': [], 'total': 0}

    cnae_modo = _normalize_cnae_modo(cnae_modo)
    cnaes = clean_digits_list(cnaes or [])
    situacoes = situacoes or ['ATIVA']
    where = []
    join = ''
    distinct = False
    if cnaes:
        vals = ",".join(_sql_quote(v) for v in cnaes)
        if cnae_modo == 'primario':
            where.append(f"p.cnae IN ({vals})")
        else:
            join = f"""
        JOIN cnae_empresa_idx m
          ON p.cnpj = m.cnpj
         AND m.cnae IN ({vals})"""
            distinct = len(cnaes) > 1
    if ufs:
        where.append("p.uf IN (" + ",".join(_sql_quote(v) for v in ufs) + ")")
    if situacoes:
        where.append("p.situacao IN (" + ",".join(_sql_quote(v) for v in situacoes) + ")")
    if excluir_mei:
        where.append("coalesce(p.mei, 'NAO') <> 'SIM'")
    city_norms = [_normalizar(c['city']) for c in candidates]
    where.append("lower(strip_accents(coalesce(p.cidade, ''))) IN (" + ",".join(_sql_quote(v) for v in city_norms) + ")")
    count_expr = "COUNT(DISTINCT p.cnpj)" if distinct else "COUNT(*)"
    sql = f"""
        SELECT p.uf, p.cidade, lower(strip_accents(coalesce(p.cidade, ''))) AS cidade_norm, {count_expr} AS total
        FROM prospects_idx p
        {join}
        WHERE {' AND '.join(where)}
        GROUP BY p.uf, p.cidade, cidade_norm
    """
    rows = get_index_db().execute(sql).fetchall()
    counts = {}
    labels = {}
    for uf, cidade, cidade_norm, total in rows:
        k = f"{str(uf or '').upper()}|{str(cidade_norm or '').upper()}"
        counts[k] = counts.get(k, 0) + int(total or 0)
        labels[k] = str(cidade or '').strip()
    out = []
    total_all = 0
    for c in candidates:
        city_norm_key = f"{c['uf']}|{_normalizar(c['city']).upper()}"
        total = counts.get(city_norm_key, 0)
        if total <= 0:
            continue
        total_all += total
        out.append({
            'city': labels.get(city_norm_key) or c['city'].title(),
            'uf': c['uf'],
            'lat': c['coord'][0],
            'lng': c['coord'][1],
            'dist_km': round(c['dist'], 2),
            'count': total,
        })
    out.sort(key=lambda x: (x['dist_km'], -x['count'], x['city']))
    return {
        'ok': True,
        'center': centro,
        'center_key': centro_key,
        'center_lat': centro_coord[0],
        'center_lng': centro_coord[1],
        'radius_km': raio_km,
        'cities': out,
        'total': total_all,
        'candidate_cities': len(candidates),
        'filters': {
            'cnaes': cnaes,
            'ufs': ufs,
            'situacoes': situacoes,
            'excluir_mei': bool(excluir_mei),
            'cnae_modo': cnae_modo,
        }
    }


def _geocode_cache_dir():
    d = SITE_DIR / 'cache_geocoding'
    d.mkdir(parents=True, exist_ok=True)
    return d


def _geocode_cache_key(provider, query):
    raw = json.dumps({
        'provider': str(provider or '').strip().lower(),
        'query': _normalizar(str(query or '')).strip(),
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _geocode_cache_get(provider, query):
    try:
        fp = _geocode_cache_dir() / f'{_geocode_cache_key(provider, query)}.json'
        if fp.exists():
            data = json.loads(fp.read_text(encoding='utf-8'))
            if isinstance(data, dict) and data.get('lat') is not None and data.get('lng') is not None:
                data['_from_cache'] = True
                return data
    except Exception:
        pass
    return None


def _geocode_cache_put(provider, query, payload):
    try:
        data = dict(payload or {})
        data['_provider'] = provider
        data['_query'] = query
        data['_cached_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
        fp = _geocode_cache_dir() / f'{_geocode_cache_key(provider, query)}.json'
        fp.write_text(json.dumps(_json_safe(data), ensure_ascii=False, allow_nan=False), encoding='utf-8')
    except Exception:
        pass


def _format_geocode_payload(feature, query=''):
    props = (feature or {}).get('properties') or {}
    lat = props.get('lat')
    lng = props.get('lon')
    if lat is None or lng is None:
        lat = (feature or {}).get('lat')
        lng = (feature or {}).get('lon') or (feature or {}).get('lng')
    try:
        lat = float(lat)
        lng = float(lng)
    except Exception:
        return None
    return {
        'lat': lat,
        'lng': lng,
        'formatted': props.get('formatted') or props.get('address_line1') or query,
        'city': props.get('city') or props.get('town') or props.get('village') or props.get('municipality') or '',
        'state': props.get('state') or '',
        'state_code': str(props.get('state_code') or props.get('state_code_iso3166_2') or '').split('-')[-1].upper(),
        'postcode': props.get('postcode') or '',
        'country': props.get('country') or '',
        'rank_confidence': props.get('rank', {}).get('confidence') if isinstance(props.get('rank'), dict) else None,
    }


def _cepaberto_geocode(cep):
    global _CEPABERTO_LAST_CALL
    cep = re.sub(r'\D', '', str(cep or ''))
    if len(cep) != 8:
        return {'ok': False, 'configured': bool(CEPABERTO_TOKEN), 'error': 'CEP invalido.'}
    cached = _geocode_cache_get('cepaberto', cep)
    if cached:
        return {'ok': True, 'configured': bool(CEPABERTO_TOKEN), 'data': cached, 'from_cache': True, 'provider': 'cepaberto'}
    if not CEPABERTO_TOKEN:
        return {'ok': False, 'configured': False, 'error': 'CEP Aberto nao configurado.'}
    url = 'https://www.cepaberto.com/api/v3/cep?' + urllib.parse.urlencode({'cep': cep})
    headers = {
        'Authorization': f'Token token={CEPABERTO_TOKEN}',
        'Accept': 'application/json',
        'User-Agent': 'ProspectaX/1.0',
    }
    try:
        wait = 1.05 - (time.time() - float(_CEPABERTO_LAST_CALL or 0))
        if wait > 0:
            time.sleep(wait)
        _CEPABERTO_LAST_CALL = time.time()
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        lat = payload.get('latitude')
        lng = payload.get('longitude')
        if lat is None or lng is None:
            return {'ok': False, 'configured': True, 'error': 'CEP Aberto retornou CEP sem coordenadas.'}
        cidade = payload.get('cidade') if isinstance(payload.get('cidade'), dict) else {}
        estado = payload.get('estado') if isinstance(payload.get('estado'), dict) else {}
        data = {
            'lat': float(lat),
            'lng': float(lng),
            'formatted': ', '.join(str(x).strip() for x in [payload.get('logradouro'), payload.get('bairro'), cep, cidade.get('nome'), estado.get('sigla'), 'Brasil'] if str(x or '').strip()),
            'city': cidade.get('nome') or '',
            'state': estado.get('nome') or '',
            'state_code': str(estado.get('sigla') or '').upper(),
            'postcode': cep,
            'country': 'Brasil',
        }
        _geocode_cache_put('cepaberto', cep, data)
        return {'ok': True, 'configured': True, 'data': data, 'from_cache': False, 'provider': 'cepaberto'}
    except HTTPError as e:
        return {'ok': False, 'configured': True, 'error': f'CEP Aberto HTTP {e.code}'}
    except Exception as e:
        return {'ok': False, 'configured': True, 'error': str(e)[:300]}


def _cepaberto_nearest(lat, lng):
    if not CEPABERTO_TOKEN:
        return {'ok': False, 'configured': False, 'error': 'CEP Aberto nao configurado.'}
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except Exception:
        return {'ok': False, 'configured': True, 'error': 'Coordenada invalida.'}
    query = f'{lat_f:.6f},{lng_f:.6f}'
    cached = _geocode_cache_get('cepaberto_nearest', query)
    if cached:
        return {'ok': True, 'configured': True, 'data': cached, 'from_cache': True, 'provider': 'cepaberto_nearest'}
    url = 'https://www.cepaberto.com/api/v3/nearest?' + urllib.parse.urlencode({'lat': lat_f, 'lng': lng_f})
    headers = {'Authorization': f'Token token={CEPABERTO_TOKEN}', 'Accept': 'application/json', 'User-Agent': 'ProspectaX/1.0'}
    try:
        global _CEPABERTO_LAST_CALL
        wait = 1.05 - (time.time() - float(_CEPABERTO_LAST_CALL or 0))
        if wait > 0:
            time.sleep(wait)
        _CEPABERTO_LAST_CALL = time.time()
        req = Request(url, headers=headers)
        with urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        cep = re.sub(r'\D', '', str(payload.get('cep') or ''))
        lat2 = payload.get('latitude')
        lng2 = payload.get('longitude')
        if not cep or lat2 is None or lng2 is None:
            return {'ok': False, 'configured': True, 'error': 'CEP Aberto nearest nao retornou CEP.'}
        cidade = payload.get('cidade') if isinstance(payload.get('cidade'), dict) else {}
        estado = payload.get('estado') if isinstance(payload.get('estado'), dict) else {}
        data = {'lat': float(lat2), 'lng': float(lng2), 'formatted': ', '.join(str(x).strip() for x in [payload.get('logradouro'), payload.get('bairro'), cep, cidade.get('nome'), estado.get('sigla'), 'Brasil'] if str(x or '').strip()), 'city': cidade.get('nome') or '', 'state_code': str(estado.get('sigla') or '').upper(), 'postcode': cep, 'country': 'Brasil'}
        _geocode_cache_put('cepaberto_nearest', query, data)
        return {'ok': True, 'configured': True, 'data': data, 'from_cache': False, 'provider': 'cepaberto_nearest'}
    except HTTPError as e:
        return {'ok': False, 'configured': True, 'error': f'CEP Aberto nearest HTTP {e.code}'}
    except Exception as e:
        return {'ok': False, 'configured': True, 'error': str(e)[:300]}

def _geocode_prefer_cep(query, cep=''):
    cep = re.sub(r'\D', '', str(cep or ''))
    if len(cep) != 8:
        digits = re.sub(r'\D', '', str(query or ''))
        text = _normalizar(str(query or ''))
        if len(digits) == 8 and re.sub(r'[0-9\s,.-]', '', text).replace('brasil', '').strip() == '':
            cep = digits
    if len(cep) == 8:
        via_cep = _cepaberto_geocode(cep)
        if via_cep.get('ok'):
            return via_cep
    return _geoapify_geocode(query)
def _geoapify_geocode(query):
    query = str(query or '').strip()
    if not query:
        return {'ok': False, 'error': 'Informe um endereço, CEP ou CNPJ.'}
    cached = _geocode_cache_get('geoapify', query)
    if cached:
        return {'ok': True, 'configured': bool(GEOAPIFY_API_KEY), 'data': cached, 'from_cache': True}
    if not GEOAPIFY_API_KEY:
        return {'ok': False, 'configured': False, 'error': 'Geoapify nao configurado.'}
    url = 'https://api.geoapify.com/v1/geocode/search?' + urllib.parse.urlencode({
        'text': query,
        'format': 'geojson',
        'limit': '1',
        'filter': 'countrycode:br',
        'apiKey': GEOAPIFY_API_KEY,
    })
    try:
        req = Request(url, headers={'User-Agent': 'ProspectaX/1.0'})
        with urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        features = payload.get('features') or []
        if not features:
            return {'ok': False, 'configured': True, 'error': 'Endereco nao localizado pela Geoapify.'}
        data = _format_geocode_payload(features[0], query=query)
        if not data:
            return {'ok': False, 'configured': True, 'error': 'Geoapify retornou endereco sem coordenadas.'}
        _geocode_cache_put('geoapify', query, data)
        return {'ok': True, 'configured': True, 'data': data, 'from_cache': False}
    except HTTPError as e:
        try:
            msg = e.read().decode('utf-8')[:300]
        except Exception:
            msg = str(e)
        return {'ok': False, 'configured': True, 'error': f'Geoapify HTTP {e.code}: {msg}'}
    except Exception as e:
        return {'ok': False, 'configured': True, 'error': str(e)[:300]}


def _address_from_cnpj_payload(d, fallback=None):
    d = d or {}
    fallback = fallback or {}
    parts = [
        d.get('logradouro') or d.get('endereco') or fallback.get('end') or fallback.get('endereco'),
        d.get('bairro'),
        d.get('cep') or fallback.get('cep'),
        d.get('municipio') or d.get('cidade') or fallback.get('cidade'),
        d.get('uf') or fallback.get('uf'),
        'Brasil',
    ]
    return ', '.join(str(x).strip() for x in parts if str(x or '').strip())


def _proximity_address_terms(text, max_terms=7):
    stop = {
        'rua', 'r', 'avenida', 'av', 'travessa', 'alameda', 'rodovia', 'estrada',
        'praca', 'pc', 'largo', 'via', 'brasil', 'bairro', 'centro', 'cep',
        'numero', 'num', 'n', 'sn', 's', 'km', 'bloco', 'sala', 'andar',
    }
    words = re.findall(r'[a-z0-9]+', _normalizar(text or ''))
    out = []
    for w in words:
        if len(w) < 3 or w in stop or w.isdigit():
            continue
        if w not in out:
            out.append(w)
        if len(out) >= max_terms:
            break
    return out


def _cep_prefixes_for_radius(center_cep, raio_km):
    cep = re.sub(r'\D', '', str(center_cep or ''))
    if len(cep) != 8:
        return []
    try:
        raio = int(float(raio_km or 5))
    except Exception:
        raio = 5
    base3 = int(cep[:3])
    if raio <= 5:
        spread = 2
    elif raio <= 10:
        spread = 4
    elif raio <= 25:
        spread = 10
    else:
        spread = 25
    start = max(0, base3 - spread)
    end = min(999, base3 + spread)
    return [f'{n:03d}' for n in range(start, end + 1)]


def _query_proximity_candidates(cnaes, ufs, situacoes, excluir_mei=False, cnae_modo='todos', cidade='', limit=120, center_address='', center_cep='', raio_km=5):
    if not fast_index_available():
        return []
    cnaes = clean_digits_list(cnaes or [])
    ufs = clean_uf_list(ufs or [])
    situacoes = situacoes or ['ATIVA']
    cnae_modo = _normalize_cnae_modo(cnae_modo)
    no_limit = limit is None or str(limit).strip() in {'', '0', '-1', 'all', 'todos'}
    if not no_limit:
        limit = _sanitize_int(limit, default=120, min_value=1, max_value=200000)
    where = []
    join = ''
    if cnaes:
        vals = ",".join(_sql_quote(v) for v in cnaes)
        if cnae_modo == 'primario':
            where.append(f"p.cnae IN ({vals})")
        else:
            join = f"""
        JOIN cnae_empresa_idx m
          ON p.cnpj = m.cnpj
         AND m.cnae IN ({vals})"""
    if ufs:
        where.append("p.uf IN (" + ",".join(_sql_quote(v) for v in ufs) + ")")
    if cidade:
        where.append("lower(strip_accents(coalesce(p.cidade, ''))) = " + _sql_quote(_normalizar(cidade)))
    cep_prefixes = _cep_prefixes_for_radius(center_cep, raio_km)
    if cep_prefixes:
        where.append("substr(regexp_replace(coalesce(p.cep, ''), '[^0-9]', '', 'g'), 1, 3) IN (" + ",".join(_sql_quote(x) for x in cep_prefixes) + ")")
    if situacoes:
        where.append("p.situacao IN (" + ",".join(_sql_quote(v) for v in situacoes) + ")")
    if excluir_mei:
        where.append("coalesce(p.mei, 'NAO') <> 'SIM'")
    where_sql = ' AND '.join(where) if where else '1=1'
    distinct = 'DISTINCT' if join else ''
    score_parts = []
    for term in _proximity_address_terms(center_address):
        score_parts.append("CASE WHEN lower(strip_accents(coalesce(p.\"end\", ''))) LIKE " + _sql_quote('%' + term + '%') + " THEN 1 ELSE 0 END")
    score_expr = " + ".join(score_parts) if score_parts else "0"
    sql = f"""
        SELECT {distinct}
            p.cnpj, p.nome, p.situacao, p.cnae, p.uf, p.cidade,
            p.telefone, p.email, p.\"end\" AS endereco, p.cep, p.porte_empresa, p.mei,
            ({score_expr}) AS endereco_score
        FROM prospects_idx p
        {join}
        WHERE {where_sql}
          AND coalesce(p."end", '') <> ''
        ORDER BY endereco_score DESC, length(coalesce(p."end", '')) DESC, p.nome
        {'' if no_limit else f'LIMIT {limit}'}
    """
    rows = get_index_db().execute(sql).fetchall()
    cols = ['cnpj','nome','situacao','cnae','uf','cidade','telefone','email','endereco','cep','porte_empresa','mei','endereco_score']
    return [dict(zip(cols, r)) for r in rows]


LOCALITY_ACTIVITY_SYNONYMS = {
    'barbearia': ['9602501'],
    'barbeiro': ['9602501'],
    'cabeleireiro': ['9602501'],
    'cabeleireiros': ['9602501'],
    'salao': ['9602501'],
    'salao de beleza': ['9602501'],
    'manicure': ['9602501'],
    'pedicure': ['9602501'],
    'estetica': ['9602502'],
    'clinica estetica': ['9602502'],
    'academia': ['9313100'],
    'restaurante': ['5611201'],
    'lanchonete': ['5611203'],
    'bar': ['5611204'],
    'padaria': ['1091102', '4721102'],
    'mercado': ['4711302', '4712100'],
    'supermercado': ['4711301'],
    'oficina': ['4520001'],
    'oficina mecanica': ['4520001'],
    'mecanica': ['4520001'],
    'funilaria': ['4520002'],
    'autopecas': ['4530703'],
    'auto pecas': ['4530703'],
    'farmacia': ['4771701'],
    'drogaria': ['4771701'],
    'pet shop': ['4789004'],
    'veterinaria': ['7500100'],
    'dentista': ['8630504'],
    'odontologia': ['8630504'],
    'contabilidade': ['6920601'],
    'advocacia': ['6911701'],
    'imobiliaria': ['6821801'],
    'construtora': ['4120400'],
    'escola': ['8512100', '8513900', '8599699'],
}


def _cnaes_from_activity_term(term, max_items=8):
    term = str(term or '').strip()
    if not term:
        return [], []
    direct = clean_digits_list(re.split(r'[,;\s]+', term))
    if direct:
        return direct[:max_items], [{'code': c, 'desc': _cnae_desc_from_code(c), 'source': 'digitado'} for c in direct[:max_items]]
    norm = _normalizar(term)
    norm_words = set(re.findall(r'[a-z0-9]+', norm))
    found = []
    for key, codes in LOCALITY_ACTIVITY_SYNONYMS.items():
        if len(key) <= 4:
            matched = norm == key or key in norm_words
        else:
            matched = key in norm or norm in key
        if matched:
            found.extend(codes)
    if found:
        unique = []
        for c in found:
            if c not in unique:
                unique.append(c)
        return unique[:max_items], [{'code': c, 'desc': _cnae_desc_from_code(c), 'source': 'sinonimo'} for c in unique[:max_items]]
    matches = search_cnae(term)[:max_items]
    codes = [m.get('code') for m in matches if m.get('code')]
    return codes, [{'code': m.get('code'), 'desc': m.get('desc'), 'source': 'busca_cnae'} for m in matches]


def _locality_proximity_payload(mode='address', value='', cnaes=None, ufs=None, situacoes=None, excluir_mei=False, cnae_modo='todos', raio_km=5, limit=100, atividade='', todas=False, force_all=False):
    mode = str(mode or 'address').strip().lower()
    value = str(value or '').strip()
    raio_km = _sanitize_int(raio_km, default=5, min_value=1, max_value=100)
    requested_limit = limit
    limit = _sanitize_int(limit, default=100, min_value=10, max_value=200000)
    situacoes = situacoes or ['ATIVA']
    cnaes = clean_digits_list(cnaes or [])
    ufs = clean_uf_list(ufs or [])
    activity_cnaes, activity_matches = _cnaes_from_activity_term(atividade)
    if todas:
        cnaes = []
        activity_matches = []
    elif activity_cnaes:
        cnaes = activity_cnaes

    center_source = {'mode': mode, 'value': value}
    center_address = value
    center_cep = ''
    cnpj_data = None
    if mode == 'cnpj':
        cnpj = normalize_cnpj(value)
        if len(cnpj) != 14:
            return {'ok': False, 'error': 'CNPJ deve ter 14 digitos.'}
        cnpj_data = fetch_cnpj_index_local(cnpj) or {}
        if not cnpj_data:
            cnpj_data = fetch_cnpj_local(cnpj) or {}
        if not cnpj_data:
            return {'ok': False, 'error': 'CNPJ nao encontrado na base local.'}
        center_cep = normalize_cnpj(str(cnpj_data.get('cep') or ''))
        center_address = _address_from_cnpj_payload(cnpj_data)
        center_source.update({
            'cnpj': cnpj,
            'nome': cnpj_data.get('razao_social') or cnpj_data.get('nome_fantasia') or '',
            'address': center_address,
            'cep': center_cep,
        })
        if not cnaes and not todas:
            cc = normalize_cnpj(str(cnpj_data.get('cnae_principal_codigo') or ''))
            if cc:
                cnaes = [cc]
        if cnpj_data.get('uf'):
            # Na busca por CNPJ, a origem manda na região. Evita filtros antigos de UF
            # (ex.: PR selecionado) zerarem uma origem real em outro estado.
            ufs = [str(cnpj_data.get('uf')).upper()]
    elif mode == 'cep':
        cep = normalize_cnpj(value)
        if len(cep) != 8:
            return {'ok': False, 'error': 'CEP deve ter 8 digitos.'}
        center_address = f'{cep}, Brasil'
        center_cep = cep
        center_source['cep'] = cep
    elif not value:
        return {'ok': False, 'error': 'Informe CNPJ, CEP ou endereco.'}

    center_geo = _geocode_prefer_cep(center_address, center_cep)
    if not center_geo.get('ok'):
        return {'ok': False, 'error': center_geo.get('error') or 'Nao foi possivel localizar o ponto central.', 'configured': center_geo.get('configured', False)}
    center = center_geo.get('data') or {}
    center_city = cnpj_data.get('municipio') if cnpj_data else ''
    center_city = center_city or center.get('city') or ''
    center_uf = (cnpj_data.get('uf') if cnpj_data else '') or center.get('state_code') or ''
    if not center_cep:
        center_cep = re.sub(r'\D', '', str(center.get('postcode') or ''))
    if not center_cep and center.get('lat') is not None and center.get('lng') is not None:
        nearest_cep = _cepaberto_nearest(center.get('lat'), center.get('lng'))
        if nearest_cep.get('ok'):
            nearest_data = nearest_cep.get('data') or {}
            center_cep = re.sub(r'\D', '', str(nearest_data.get('postcode') or ''))
            if not center_city:
                center_city = nearest_data.get('city') or center_city
            if not center_uf:
                center_uf = nearest_data.get('state_code') or center_uf
            center_source['nearest_cep'] = center_cep
            center_source['nearest_provider'] = nearest_cep.get('provider') or 'cepaberto_nearest'
    if center_uf and (not ufs or center_uf not in ufs):
        ufs = [center_uf]
    if not center_city:
        return {
            'ok': False,
            'configured': True,
            'error': 'Nao consegui identificar a cidade do ponto central. Informe o endereco com cidade e UF, exemplo: Av Brasil, 1000, Maringa PR.',
            'center': {
                'lat': center.get('lat'),
                'lng': center.get('lng'),
                'formatted': center.get('formatted') or center_address,
                'postcode': center_cep,
                'city': center_city,
                'uf': center_uf,
                'from_cache': bool(center_geo.get('from_cache')),
            },
            'source': center_source,
            'results': [],
            'total': 0,
            'candidates': 0,
        }
    query_limit = None if todas else limit
    candidates = _query_proximity_candidates(cnaes, ufs, situacoes, excluir_mei, cnae_modo, center_city, query_limit, center_address, center_cep, raio_km)

    candidate_addresses = []
    unique_candidate_addresses = set()
    for item in candidates:
        item_cep = re.sub(r'\D', '', str(item.get('cep') or ''))
        cep_addr = f'{item_cep}, Brasil' if len(item_cep) == 8 else ''
        full_addr = _address_from_cnpj_payload({
            'logradouro': item.get('endereco'),
            'cep': item.get('cep'),
            'municipio': item.get('cidade'),
            'uf': item.get('uf'),
        })
        addr = full_addr or cep_addr
        if not addr:
            continue
        candidate_addresses.append((item, cep_addr or addr, full_addr or addr))
        unique_candidate_addresses.add(_normalizar(full_addr or addr))
    street_matches = sum(1 for x in candidates if int(x.get('endereco_score') or 0) > 0)
    max_without_confirm = 3000
    if todas and not force_all and len(unique_candidate_addresses) > max_without_confirm:
        return {
            'ok': True,
            'configured': True,
            'requires_force_all': True,
            'message': 'A busca completa tem muitos enderecos unicos e precisa de confirmacao antes de consumir creditos.',
            'center': {
                'lat': center.get('lat'),
                'lng': center.get('lng'),
                'formatted': center.get('formatted') or center_address,
                'postcode': center_cep,
                'city': center_city,
                'uf': center_uf,
                'from_cache': bool(center_geo.get('from_cache')),
            },
            'source': center_source,
            'radius_km': raio_km,
            'results': [],
            'total': 0,
            'candidates': len(candidates),
            'street_matches': street_matches,
            'closest_outside': [],
            'geocoded_new': 0,
            'geocoded_cache': 0,
            'geocoded_reused': 0,
            'geocoded_unique_addresses': len(unique_candidate_addresses),
            'geocode_errors': 0,
            'limit': query_limit,
            'requested_limit': requested_limit,
            'filters': {
                'cnaes': cnaes,
                'atividade': atividade,
                'atividade_matches': activity_matches,
                'todas': bool(todas),
                'force_all': bool(force_all),
                'ufs': ufs,
                'situacoes': situacoes,
                'excluir_mei': bool(excluir_mei),
                'cnae_modo': _normalize_cnae_modo(cnae_modo),
            }
        }

    results = []
    closest_outside = []
    geocoded = 0
    from_cache = 0
    reused_in_run = 0
    errors = 0
    address_geo = {}
    exact_geocoded = 0
    exact_from_cache = 0
    exact_skipped_by_cep = 0
    rough_buffer_km = max(1.5, min(5.0, float(raio_km) * 0.45))
    for item, cep_addr, full_addr in candidate_addresses:
        rough_key = 'rough:' + _normalizar(cep_addr or full_addr)
        reused_addr = False
        if rough_key in address_geo:
            rough_g = address_geo[rough_key]
            reused_in_run += 1
            reused_addr = True
        else:
            rough_g = _geocode_prefer_cep(cep_addr or full_addr, item.get('cep'))
            address_geo[rough_key] = rough_g
        if not rough_g.get('ok'):
            errors += 1
            continue
        if not reused_addr:
            if rough_g.get('from_cache'):
                from_cache += 1
            else:
                geocoded += 1
        rough_geo = rough_g.get('data') or {}
        rough_dist = _haversine_km((center['lat'], center['lng']), (rough_geo['lat'], rough_geo['lng']))
        if rough_dist > (float(raio_km) + rough_buffer_km):
            exact_skipped_by_cep += 1
            row = dict(item)
            row.update({
                'lat': rough_geo['lat'],
                'lng': rough_geo['lng'],
                'dist_km': round(rough_dist, 3),
                'endereco': item.get('endereco') or '',
                'geocode_label': rough_geo.get('formatted') or cep_addr or full_addr,
                'precision': 'cep_pre_filtro',
            })
            closest_outside.append(row)
            continue

        exact_key = 'exact:' + _normalizar(full_addr)
        exact_reused = False
        if exact_key in address_geo:
            g = address_geo[exact_key]
            reused_in_run += 1
            exact_reused = True
        else:
            g = _geoapify_geocode(full_addr)
            address_geo[exact_key] = g
        if not g.get('ok'):
            errors += 1
            continue
        if not exact_reused:
            if g.get('from_cache'):
                exact_from_cache += 1
            else:
                exact_geocoded += 1
        geo = g.get('data') or {}
        dist = _haversine_km((center['lat'], center['lng']), (geo['lat'], geo['lng']))
        row = dict(item)
        row.update({
            'lat': geo['lat'],
            'lng': geo['lng'],
            'dist_km': round(dist, 3),
            'endereco': item.get('endereco') or '',
            'geocode_label': geo.get('formatted') or full_addr,
            'precision': 'endereco_completo',
            'rough_dist_km': round(rough_dist, 3),
        })
        if dist <= raio_km:
            results.append(row)
        else:
            closest_outside.append(row)
    results.sort(key=lambda x: (x.get('dist_km', 999), x.get('nome') or ''))
    closest_outside.sort(key=lambda x: (x.get('dist_km', 999), x.get('nome') or ''))
    return {
        'ok': True,
        'configured': True,
        'center': {
            'lat': center.get('lat'),
            'lng': center.get('lng'),
            'formatted': center.get('formatted') or center_address,
            'postcode': center_cep,
            'city': center_city,
            'uf': center_uf,
            'from_cache': bool(center_geo.get('from_cache')),
        },
        'source': center_source,
        'radius_km': raio_km,
        'results': results,
        'total': len(results),
        'candidates': len(candidates),
        'street_matches': street_matches,
        'closest_outside': closest_outside[:12],
        'geocoded_new': geocoded,
        'geocoded_cache': from_cache,
        'geocoded_reused': reused_in_run,
        'geocoded_unique_addresses': len(address_geo),
        'geocode_errors': errors,
        'exact_geocoded_new': exact_geocoded,
        'exact_geocoded_cache': exact_from_cache,
        'skipped_by_cep_prefilter': exact_skipped_by_cep,
        'precision': 'endereco_completo_quando_possivel',
        'limit': query_limit,
        'requested_limit': requested_limit,
        'filters': {
            'cnaes': cnaes,
            'atividade': atividade,
            'atividade_matches': activity_matches,
            'todas': bool(todas),
            'force_all': bool(force_all),
            'ufs': ufs,
            'situacoes': situacoes,
            'excluir_mei': bool(excluir_mei),
            'cnae_modo': _normalize_cnae_modo(cnae_modo),
        }
    }


def _sanitize_int(value, default=0, min_value=0, max_value=None):
    try:
        n = int(value)
    except Exception:
        n = default
    if n < min_value:
        n = min_value
    if max_value is not None and n > max_value:
        n = max_value
    return n


def _accepts_gzip(handler):
    enc = handler.headers.get('Accept-Encoding', '') or ''
    return 'gzip' in enc.lower()


def _write_json(handler, payload, code=200):
    raw = json.dumps(_json_safe(payload), ensure_ascii=False, allow_nan=False).encode('utf-8')
    use_gzip = len(raw) >= 1024 and _accepts_gzip(handler)
    handler.send_response(code)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('Access-Control-Allow-Origin', '*')
    if use_gzip:
        raw = gzip.compress(raw, compresslevel=6)
        handler.send_header('Content-Encoding', 'gzip')
    handler.send_header('Content-Length', str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _ensure_search_cache_dir():
    SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _search_cache_key(cnaes, ufs, situacoes, excluir_mei, palavra, cnae_modo='todos', limit=None, offset=0, tipo='search'):
    payload = {
        'cnaes': sorted(cnaes),
        'ufs': sorted(ufs),
        'situacoes': sorted(situacoes),
        'excluir_mei': bool(excluir_mei),
        'palavra': (palavra or '').strip().lower(),
        'cnae_modo': 'primario' if str(cnae_modo or '').lower() in {'primario','principal','primary'} else 'todos',
        'tipo': tipo,
        'data_dir': str(DATA_DIR),
        'limit': int(limit) if limit is not None else None,
        'offset': max(0, int(offset or 0)),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _search_cache_paths(cache_key):
    _ensure_search_cache_dir()
    return (SEARCH_CACHE_DIR / f'{cache_key}.json.gz', SEARCH_CACHE_DIR / f'{cache_key}.meta.json')


def load_search_cache(cache_key):
    data_path, meta_path = _search_cache_paths(cache_key)
    if not data_path.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        ts = float(meta.get('ts', 0))
        if SEARCH_CACHE_TTL > 0 and (time.time() - ts) > SEARCH_CACHE_TTL:
            try:
                data_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        with gzip.open(data_path, 'rt', encoding='utf-8') as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            payload['_cache_meta'] = meta
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def save_search_cache(cache_key, payload, params):
    data_path, meta_path = _search_cache_paths(cache_key)
    temp_data = data_path.with_suffix(data_path.suffix + '.tmp')
    temp_meta = meta_path.with_suffix(meta_path.suffix + '.tmp')
    with gzip.open(temp_data, 'wt', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    meta = {
        'ts': time.time(),
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'rows': len(payload.get('rows', [])),
        'params': params,
    }
    temp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    temp_data.replace(data_path)
    temp_meta.replace(meta_path)


def clear_search_cache():
    _EXACT_COUNT_CACHE.clear()
    _PAGE_QUERY_CACHE.clear()
    if not SEARCH_CACHE_DIR.exists():
        return 0
    total = 0
    for fp in SEARCH_CACHE_DIR.glob('*'):
        if fp.is_file():
            try:
                fp.unlink()
                total += 1
            except Exception:
                pass
    return total


def _load_user_scoped_file(path, leaf_key, owner=None):
    if not path.exists():
        return {} if owner else {leaf_key: {}, 'users': {}}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {} if owner else {leaf_key: {}, 'users': {}}
    if not isinstance(data, dict):
        return {} if owner else {leaf_key: {}, 'users': {}}
    if owner:
        users = data.get('users', {}) if isinstance(data.get('users', {}), dict) else {}
        owner_bucket = users.get(owner, {}) if isinstance(users.get(owner, {}), dict) else {}
        scoped = owner_bucket.get(leaf_key, {}) if isinstance(owner_bucket.get(leaf_key, {}), dict) else {}
        if scoped:
            return scoped
        legacy = data.get(leaf_key, {}) if isinstance(data.get(leaf_key, {}), dict) else {}
        if legacy:
            users[owner] = dict(owner_bucket)
            users[owner][leaf_key] = legacy
            data['users'] = users
            data[leaf_key] = {}
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            return legacy
        return {}
    if 'users' not in data or not isinstance(data.get('users', {}), dict):
        data['users'] = {}
    if leaf_key not in data or not isinstance(data.get(leaf_key, {}), dict):
        data[leaf_key] = {}
    return data


def _save_user_scoped_file(path, leaf_key, values, owner=None):
    data = _load_user_scoped_file(path, leaf_key, owner=None)
    if owner:
        users = data.get('users', {}) if isinstance(data.get('users', {}), dict) else {}
        owner_bucket = users.get(owner, {}) if isinstance(users.get(owner, {}), dict) else {}
        owner_bucket[leaf_key] = values
        users[owner] = owner_bucket
        data['users'] = users
    else:
        data[leaf_key] = values
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def load_contact_statuses(owner=None):
    return _load_user_scoped_file(CONTACT_STATUS_FILE, 'statuses', owner)


def save_contact_statuses(statuses, owner=None):
    _save_user_scoped_file(CONTACT_STATUS_FILE, 'statuses', statuses, owner)


def load_notes(owner=None):
    return _load_user_scoped_file(NOTES_FILE, 'notes', owner)


def save_notes(notes, owner=None):
    _save_user_scoped_file(NOTES_FILE, 'notes', notes, owner)


def load_history(owner=None):
    return _load_user_scoped_file(HISTORY_FILE, 'history', owner)


def save_history(history, owner=None):
    _save_user_scoped_file(HISTORY_FILE, 'history', history, owner)


def _scope_owner(sess, shared=False):
    sess = sess or {}
    if shared and sess.get('company_id'):
        return f"team:{sess.get('company_id')}"
    return sess.get('usuario')


def _normalize_status_record(value):
    if isinstance(value, dict):
        status = (value.get('status') or 'novo').strip() or 'novo'
        rec = dict(value)
        rec['status'] = status
        return rec
    return {'status': (str(value or 'novo').strip() or 'novo')}


def _load_json_dict(path, root_key):
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            if root_key and isinstance(data.get(root_key), dict):
                return data.get(root_key)
            return data
    except Exception:
        return {}
    return {}


def _save_json_dict(path, root_key, payload):
    if root_key:
        path.write_text(json.dumps({root_key: payload}, ensure_ascii=False, indent=2), encoding='utf-8')
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def load_user_prefs(owner):
    owner = owner or ''
    data = _load_json_dict(USER_PREFS_FILE, 'users')
    prefs = data.get(owner, {}) if isinstance(data.get(owner), dict) else {}
    if 'cnaes' not in prefs or not isinstance(prefs.get('cnaes'), list):
        prefs['cnaes'] = []
    return prefs


def save_user_prefs(owner, prefs):
    owner = owner or ''
    if not owner:
        return
    data = _load_json_dict(USER_PREFS_FILE, 'users')
    data[owner] = prefs
    _save_json_dict(USER_PREFS_FILE, 'users', data)


def load_team_status_rules(company_id):
    company_id = _slug_company_id(company_id or '')
    if not company_id:
        return {}
    data = _load_json_dict(TEAM_RULES_FILE, 'teams')
    rules = data.get(company_id, {}) if isinstance(data.get(company_id), dict) else {}
    cleaned = {}
    for k, v in rules.items():
        try:
            iv = int(v or 0)
        except Exception:
            iv = 0
        if iv > 0:
            cleaned[k] = iv
    return cleaned


def save_team_status_rules(company_id, rules):
    company_id = _slug_company_id(company_id or '')
    if not company_id:
        return
    data = _load_json_dict(TEAM_RULES_FILE, 'teams')
    cleaned = {}
    for k, v in (rules or {}).items():
        try:
            iv = int(v or 0)
        except Exception:
            iv = 0
        if iv > 0:
            cleaned[k] = iv
    data[company_id] = cleaned
    _save_json_dict(TEAM_RULES_FILE, 'teams', data)


def load_contact_status_records(sess=None):
    owner = _scope_owner(sess, shared=True) if isinstance(sess, dict) else sess
    raw = load_contact_statuses(owner)
    records = {}
    changed = False
    rules = load_team_status_rules((sess or {}).get('company_id')) if isinstance(sess, dict) and (sess or {}).get('company_id') else {}
    for cnpj, value in (raw or {}).items():
        rec = _normalize_status_record(value)
        status = rec.get('status', 'novo')
        if status != 'novo':
            days = int(rules.get(status, 0) or 0)
            ts_source = rec.get('updated_at') or rec.get('ts') or rec.get('created_at')
            if days > 0 and ts_source:
                try:
                    ref = time.mktime(time.strptime(ts_source[:19], '%Y-%m-%dT%H:%M:%S'))
                    if time.time() >= ref + (days * 86400):
                        rec = {'status': 'novo', 'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'reset_from': status, 'reset_reason': 'auto_rule'}
                        changed = True
                except Exception:
                    pass
        records[cnpj] = rec
    if changed:
        save_contact_statuses(records, owner)
    return records


def save_contact_status_records(sess, records):
    save_contact_statuses(records, _scope_owner(sess, shared=True))


def _status_map_from_records(records):
    return {cnpj: _normalize_status_record(v).get('status', 'novo') for cnpj, v in (records or {}).items()}


def _team_history_owner(sess):
    return _scope_owner(sess, shared=True)


def _append_team_history(sess, cnpj, new_status, prev_status, nota=''):
    owner = _team_history_owner(sess)
    history = load_history(owner)
    if not isinstance(history, dict):
        history = {}
    if not isinstance(history.get(cnpj), list):
        history[cnpj] = []
    item = {
        'status': new_status,
        'prev': prev_status,
        'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'nota': (nota or '').strip(),
        'owner_login': (sess or {}).get('usuario', ''),
        'owner_nome': (sess or {}).get('nome', (sess or {}).get('usuario', '')),
    }
    history[cnpj].insert(0, item)
    save_history(history, owner)
    return item


def _compute_team_dashboard(company_id):
    company_id = _slug_company_id(company_id or '')
    usuarios = _carregar_usuarios()
    sellers = []
    idx = {}
    for login, data in usuarios.items():
        if data.get('company_id') != company_id or data.get('role') == 'admin':
            continue
        row = {'login': login, 'nome': data.get('nome', login), 'ativo': data.get('ativo', True), 'locked_clients': 0, 'status_changes': 0, 'by_status': {}, 'last_activity': ''}
        sellers.append(row)
        idx[login] = row
    records = load_contact_status_records({'company_id': company_id})
    for rec in records.values():
        rec = _normalize_status_record(rec)
        owner_login = rec.get('owner_login') or ''
        if owner_login in idx and rec.get('status') != 'novo':
            idx[owner_login]['locked_clients'] += 1
    history = load_history(f'team:{company_id}')
    for items in (history or {}).values():
        if not isinstance(items, list):
            continue
        for h in items:
            owner_login = h.get('owner_login') or ''
            if owner_login not in idx:
                continue
            row = idx[owner_login]
            row['status_changes'] += 1
            st = h.get('status') or 'novo'
            row['by_status'][st] = row['by_status'].get(st, 0) + 1
            ts = h.get('ts') or ''
            if ts and (not row['last_activity'] or ts > row['last_activity']):
                row['last_activity'] = ts
    sellers.sort(key=lambda r: ((not r['ativo']), r['nome'].lower()))
    return sellers


def _reset_team_statuses(company_id, statuses=None):
    sess = {'company_id': company_id}
    records = load_contact_status_records(sess)
    statuses = set(statuses or [])
    changed = 0
    for cnpj, rec in list(records.items()):
        rec = _normalize_status_record(rec)
        st = rec.get('status', 'novo')
        if st == 'novo':
            continue
        if statuses and st not in statuses:
            continue
        records[cnpj] = {'status': 'novo', 'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'reset_reason': 'manual'}
        changed += 1
    save_contact_status_records(sess, records)
    return changed

def load_saved_clients(sess=None):
    if not sess:
        return {}
    try:
        data = json.loads(SAVED_CLIENTS_FILE.read_text(encoding='utf-8'))
        from prospectax_routes import owner_key
        clients = dict((data.get('owners') or {}).get(owner_key(sess), {}))
        if _is_global_admin(sess):
            clients = {**(data.get('clients') or {}), **clients}
        return clients
    except (OSError, ValueError):
        return {}


def save_saved_clients(clients, sess=None):
    if not sess:
        raise ValueError('Sessao necessaria para salvar clientes.')
    from prospectax_routes import owner_key
    with _COMMERCIAL_WRITE_LOCK:
        try:
            data = json.loads(SAVED_CLIENTS_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = {}
        data.setdefault('owners', {})[owner_key(sess)] = clients
        temp = SAVED_CLIENTS_FILE.with_suffix('.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(SAVED_CLIENTS_FILE)


def upsert_saved_client(cnpj, status=None, nota_marker=None, nota_valor='', touched_from='manual', sess=None):
    cnpj = normalize_cnpj(cnpj or '')
    if not cnpj:
        return None
    clients = load_saved_clients(sess)
    now_iso = time.strftime('%Y-%m-%dT%H:%M:%S')
    item = clients.get(cnpj, {}) if isinstance(clients.get(cnpj, {}), dict) else {}
    if 'created_at' not in item:
        item['created_at'] = now_iso
    item['updated_at'] = now_iso
    item['cnpj'] = cnpj
    if status is not None:
        item['status'] = (status or 'novo').strip() or 'novo'
    if nota_marker is not None:
        if nota_valor:
            item['nota'] = nota_valor
        else:
            item.pop('nota', None)
    item['touched_from'] = touched_from
    clients[cnpj] = item
    save_saved_clients(clients, sess)
    return item



def merge_live_client_state(rows, owner=None):
    sess = owner if isinstance(owner, dict) else None
    status_records = load_contact_status_records(sess) if sess else {}
    statuses = _status_map_from_records(status_records) if sess else load_contact_statuses(owner)
    notes = load_notes((sess or {}).get('usuario') if sess else owner)
    saved_clients = load_saved_clients(sess)
    merged = []
    for item in rows:
        row = dict(item)
        cnpj = normalize_cnpj(str(row.get('cnpj', '') or ''))
        saved = saved_clients.get(cnpj, {}) if cnpj else {}
        rec = status_records.get(cnpj, {}) if cnpj else {}
        row['contato_status'] = statuses.get(cnpj) or saved.get('status') or row.get('contato_status') or 'novo'
        row['status_owner_login'] = rec.get('owner_login', '') if isinstance(rec, dict) else ''
        row['status_owner_nome'] = rec.get('owner_nome', '') if isinstance(rec, dict) else ''
        if cnpj and cnpj in notes:
            row['nota_salva'] = notes.get(cnpj, '')
        elif sess and saved.get('nota'):
            row['nota_salva'] = saved.get('nota', '')
        merged.append(row)
    return merged

# Cache para dívida ativa (separado do enriquecimento)
_divida_cache = {}
DIVIDA_CACHE_TTL = 3600  # 1 hora

def fetch_divida_ativa(cnpj_limpo):
    return _PUBLIC_CHECKS.debt(cnpj_limpo)


def estimar_faturamento(capital_social_str, porte_str, cnae_str, abertura_str):
    """
    Estima faturamento anual com base em capital social, porte e CNAE.
    Retorna string formatada ex: 'R$ 500K - R$ 2M/ano (estimado)'
    """
    # Extrair valor numérico do capital social
    capital = 0
    try:
        cs = str(capital_social_str).replace('R$','').replace('.','').replace(',','.').strip()
        capital = float(cs)
    except:
        pass

    # Calcular anos de operação
    anos = 1
    try:
        from datetime import datetime
        if abertura_str:
            # Formato pode ser AAAA-MM-DD ou DD/MM/AAAA
            if '-' in str(abertura_str):
                ano_abertura = int(str(abertura_str)[:4])
            else:
                ano_abertura = int(str(abertura_str)[-4:])
            anos = max(1, datetime.now().year - ano_abertura)
    except:
        pass

    # Multiplicadores por porte
    porte = str(porte_str).upper()
    if 'GRANDE' in porte or '05' in porte:
        mult_min, mult_max = 50, 300
    elif 'PEQUENO' in porte or 'EPP' in porte or '03' in porte:
        mult_min, mult_max = 5, 30
    elif 'MICRO' in porte or 'ME' in porte or '01' in porte:
        mult_min, mult_max = 2, 10
    else:
        mult_min, mult_max = 2, 15

    # Ajuste por capital social
    if capital > 0:
        base = capital * mult_min
        topo = capital * mult_max
    else:
        # Sem capital, usa faixas por porte
        if 'GRANDE' in porte:
            base, topo = 5_000_000, 50_000_000
        elif 'PEQUENO' in porte or 'EPP' in porte:
            base, topo = 360_000, 4_800_000
        elif 'MICRO' in porte or 'ME' in porte:
            base, topo = 60_000, 360_000
        else:
            base, topo = 60_000, 1_000_000

    # Ajuste por tempo de operação (empresas mais antigas tendem a faturar mais)
    if anos > 10:
        base *= 1.5; topo *= 1.5
    elif anos > 5:
        base *= 1.2; topo *= 1.2

    def fmt_val(v):
        if v >= 1_000_000:
            return f"R$ {v/1_000_000:.1f}M"
        elif v >= 1_000:
            return f"R$ {v/1_000:.0f}K"
        else:
            return f"R$ {v:.0f}"

    return f"{fmt_val(base)} — {fmt_val(topo)}/ano (estimado)"


def estimar_funcionarios(porte_str, capital_social_str, abertura_str):
    """
    Estima quantidade de funcionários com base no porte e capital social.
    """
    porte = str(porte_str).upper()
    capital = 0
    try:
        cs = str(capital_social_str).replace('R$','').replace('.','').replace(',','.').strip()
        capital = float(cs)
    except:
        pass

    if 'GRANDE' in porte or '05' in porte:
        if capital > 10_000_000:
            return '500 — 5.000+ funcionários (estimado)'
        return '100 — 500 funcionários (estimado)'
    elif 'PEQUENO' in porte or 'EPP' in porte or '03' in porte:
        if capital > 500_000:
            return '20 — 99 funcionários (estimado)'
        return '10 — 49 funcionários (estimado)'
    elif 'MICRO' in porte or 'ME' in porte or '01' in porte:
        if capital > 100_000:
            return '5 — 19 funcionários (estimado)'
        return '1 — 9 funcionários (estimado)'
    else:
        return '1 — 49 funcionários (estimado)'



def _fmt_brl(valor):
    try:
        n = float(str(valor).replace('.', '').replace(',', '.'))
        return f"R$ {n:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        return str(valor or '').strip()


def _fmt_data_br(valor):
    s = re.sub(r'\D', '', str(valor or ''))
    if len(s) == 8:
        return f"{s[6:8]}/{s[4:6]}/{s[0:4]}"
    return str(valor or '').strip()



def _cnae_desc_from_code(code):
    code = re.sub(r'\D', '', str(code or ''))
    if not code:
        return ''

    cache = getattr(_cnae_desc_from_code, '_cache', None)
    if cache is None:
        cache = {}
        setattr(_cnae_desc_from_code, '_cache', cache)
    if code in cache:
        return cache.get(code, '')

    try:
        cnae_path = DATA_DIR / 'cnaes.parquet'
        if cnae_path.exists():
            conn = get_db()
            q = f"""
            SELECT trim(coalesce(descricao, '')) AS descricao
            FROM read_parquet('{str(cnae_path)}')
            WHERE regexp_replace(coalesce(codigo, ''), '[^0-9]', '', 'g') = '{code}'
            LIMIT 1
            """
            row = conn.execute(q).fetchone()
            if row and row[0]:
                cache[code] = row[0]
                return row[0]
    except Exception:
        pass

    if os.environ.get('PROSPECTAX_LOAD_CNAE_IBGE', '0') == '1':
        try:
            load_cnae_ibge()
            for item in _cnae_cache:
                if item.get('code') == code:
                    cache[code] = item.get('desc', '')
                    return cache[code]
        except Exception:
            pass

    cache[code] = ''
    return ''

SOCIO_QUALIFICACAO_MAP = {
    '05': 'Administrador',
    '08': 'Conselheiro de administração',
    '10': 'Diretor',
    '16': 'Presidente',
    '17': 'Procurador',
    '22': 'Sócio',
    '23': 'Sócio capitalista',
    '24': 'Sócio comanditado',
    '28': 'Sócio comanditário',
    '29': 'Sócio ostensivo',
    '31': 'Sócio pessoa física residente no exterior',
    '37': 'Sócio pessoa jurídica domiciliado no exterior',
    '49': 'Sócio-administrador',
    '52': 'Sócio com capital',
    '53': 'Sócio sem capital',
    '54': 'Fundador',
    '59': 'Produtor rural',
    '65': 'Titular pessoa física residente ou domiciliado no Brasil',
    '66': 'Titular pessoa física residente ou domiciliado no exterior',
}

def _qualificacao_socio_desc(value):
    raw = str(value or '').strip()
    code = re.sub(r'\D', '', raw).zfill(2) if re.sub(r'\D', '', raw) else ''
    return SOCIO_QUALIFICACAO_MAP.get(code) or raw


def fetch_socios_local(cnpj_basico, limit=20):
    cnpj_basico = normalize_cnpj(str(cnpj_basico or ''))[:8]
    if len(cnpj_basico) != 8:
        return []

    cache = getattr(fetch_socios_local, '_cache', None)
    if cache is None:
        cache = {}
        setattr(fetch_socios_local, '_cache', cache)
    if cnpj_basico in cache:
        return list(cache.get(cnpj_basico) or [])

    try:
        # ── Caminho rápido: usa banco pré-indexado ──────────────────────────
        if fast_db_available():
            conn = get_fast_db()
            q = f"""
            SELECT
                trim(coalesce(s.nome_socio, '')) AS nome,
                trim(coalesce(q.descricao, s.qualificacao_socio, '')) AS qualificacao,
                trim(coalesce(s.faixa_etaria, '')) AS faixa_etaria,
                trim(coalesce(s.data_entrada_sociedade, '')) AS entrada
            FROM px_socios s
            LEFT JOIN px_qualificacoes q ON s.qualificacao_socio = q.codigo
            WHERE s.cnpj_basico = '{cnpj_basico}'
              AND coalesce(s.nome_socio, '') <> ''
            LIMIT {int(limit)}
            """
            rows = conn.execute(q).fetchall()
            socios = []
            for nome, qualificacao, faixa, entrada in rows:
                item = {
                    'nome': str(nome or '').strip(),
                    'qualificacao': _qualificacao_socio_desc(qualificacao),
                }
                if faixa:
                    item['faixa_etaria'] = str(faixa).strip()
                if entrada:
                    item['entrada'] = _fmt_data_br(entrada)
                if item['nome']:
                    socios.append(item)
            cache[cnpj_basico] = socios
            return list(socios)

        # Indice leve criado dentro do prospectax_index.duckdb para exibir donos/socios rapido.
        if index_table_exists('socios_idx'):
            conn = get_index_db()
            rows = conn.execute("""
                SELECT
                    nome,
                    qualificacao,
                    faixa_etaria,
                    entrada
                FROM socios_idx
                WHERE cnpj_basico = ?
                  AND coalesce(nome, '') <> ''
                ORDER BY ordem, nome
                LIMIT ?
            """, [cnpj_basico, int(limit)]).fetchall()
            socios = []
            for nome, qualificacao, faixa, entrada in rows:
                item = {
                    'nome': str(nome or '').strip(),
                    'qualificacao': _qualificacao_socio_desc(qualificacao),
                }
                if faixa:
                    item['faixa_etaria'] = str(faixa).strip()
                if entrada:
                    item['entrada'] = _fmt_data_br(entrada)
                if item['nome']:
                    socios.append(item)
            cache[cnpj_basico] = socios
            return list(socios)

        # ── Fallback: parquet bruto ─────────────────────────────────────────
        conn = get_db()
        qual_path = DATA_DIR / 'qualificacoes.parquet'
        if qual_path.exists():
            q = f"""
            SELECT
                trim(coalesce(s.nome_socio, '')) AS nome,
                trim(coalesce(q.descricao, s.qualificacao_socio, '')) AS qualificacao,
                trim(coalesce(s.faixa_etaria, '')) AS faixa_etaria,
                trim(coalesce(s.data_entrada_sociedade, '')) AS entrada
            FROM read_parquet('{SOC_PATTERN}') s
            LEFT JOIN read_parquet('{str(qual_path)}') q
              ON trim(s.qualificacao_socio) = trim(q.codigo)
            WHERE trim(s.cnpj_basico) = '{cnpj_basico}'
              AND trim(coalesce(s.nome_socio, '')) <> ''
            LIMIT {int(limit)}
            """
        else:
            q = f"""
            SELECT
                trim(coalesce(nome_socio, '')) AS nome,
                trim(coalesce(qualificacao_socio, '')) AS qualificacao,
                trim(coalesce(faixa_etaria, '')) AS faixa_etaria,
                trim(coalesce(data_entrada_sociedade, '')) AS entrada
            FROM read_parquet('{SOC_PATTERN}')
            WHERE trim(cnpj_basico) = '{cnpj_basico}'
              AND trim(coalesce(nome_socio, '')) <> ''
            LIMIT {int(limit)}
            """
        rows = conn.execute(q).fetchall()
        socios = []
        for nome, qualificacao, faixa, entrada in rows:
            item = {
                'nome': str(nome or '').strip(),
                'qualificacao': _qualificacao_socio_desc(qualificacao),
            }
            if faixa:
                item['faixa_etaria'] = str(faixa).strip()
            if entrada:
                item['entrada'] = _fmt_data_br(entrada)
            if item['nome']:
                socios.append(item)
        cache[cnpj_basico] = socios
        return list(socios)
    except Exception:
        return []


def fetch_cnpj_local(cnpj_limpo):
    cnpj_limpo = normalize_cnpj(str(cnpj_limpo or ''))
    if len(cnpj_limpo) != 14:
        return {}

    cache = getattr(fetch_cnpj_local, '_cache', None)
    if cache is None:
        cache = {}
        setattr(fetch_cnpj_local, '_cache', cache)

    cached = cache.get(cnpj_limpo)
    if cached and (time.time() - cached.get('ts', 0)) < 3600:
        return dict(cached.get('data') or {})

    cache_dir = SITE_DIR / 'cache_enriquecimento'
    cache_file = None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f'{cnpj_limpo}.json'
        if cache_file.exists():
            try:
                payload = json.loads(cache_file.read_text(encoding='utf-8'))
                cache[cnpj_limpo] = {'data': payload, 'ts': time.time()}
                return dict(payload)
            except Exception:
                pass
    except Exception:
        pass

    basico = cnpj_limpo[:8]
    ordem  = cnpj_limpo[8:12]
    dv     = cnpj_limpo[12:14]

    try:
        # ── Caminho rápido: banco pré-indexado ─────────────────────────────
        if fast_db_available():
            conn = get_fast_db()
            q = f"""
            SELECT
                e.cnpj,
                coalesce(nullif(emp.razao_social,''),'') AS razao_social,
                e.nome_fantasia,
                e.situacao_cadastral,
                e.data_inicio_atividade,
                e.cnae_principal,
                e.tipo_logradouro,
                e.logradouro,
                e.numero,
                e.complemento,
                e.bairro,
                e.cep,
                e.uf,
                coalesce(nullif(m.descricao,''), e.municipio, '') AS municipio,
                e.ddd_1,
                e.telefone_1,
                e.ddd_2,
                e.telefone_2,
                e.correio_eletronico,
                coalesce(emp.natureza_juridica,'') AS natureza_juridica,
                coalesce(emp.capital_social,'') AS capital_social,
                coalesce(emp.porte_empresa,'') AS porte_empresa,
                e.matriz_filial
            FROM px_estabelecimentos e
            LEFT JOIN px_empresas emp ON e.cnpj_basico = emp.cnpj_basico
            LEFT JOIN px_municipios m ON e.municipio = m.codigo
            WHERE e.cnpj_basico = '{basico}'
              AND e.cnpj_ordem  = '{ordem}'
              AND e.cnpj_dv     = '{dv}'
            LIMIT 1
            """
            row = conn.execute(q).fetchone()
        else:
            # ── Fallback: parquet bruto ─────────────────────────────────────
            conn = get_db()
            if Path(MUNI_PATTERN).exists():
                muni_join_sql = f"LEFT JOIN read_parquet('{MUNI_PATTERN}') m ON trim(e.municipio)=trim(m.codigo)"
                muni_desc_expr = "trim(coalesce(m.descricao, e.municipio, ''))"
            else:
                muni_join_sql = ""
                muni_desc_expr = "trim(coalesce(e.municipio, ''))"
            q = f"""
            WITH est AS (
                SELECT
                    cnpj_basico, cnpj_ordem, cnpj_dv, matriz_filial, nome_fantasia,
                    situacao_cadastral, data_inicio_atividade, cnae_principal,
                    tipo_logradouro, logradouro, numero, complemento, bairro, cep, uf,
                    municipio, ddd_1, telefone_1, ddd_2, telefone_2, correio_eletronico
                FROM read_parquet('{EST_PATTERN}')
                WHERE trim(cnpj_basico) = '{basico}'
                  AND trim(cnpj_ordem)  = '{ordem}'
                  AND trim(cnpj_dv)     = '{dv}'
                LIMIT 1
            ), emp AS (
                SELECT cnpj_basico, razao_social, natureza_juridica, capital_social, porte_empresa
                FROM read_parquet('{EMP_PATTERN}')
                WHERE trim(cnpj_basico) = '{basico}'
                LIMIT 1
            )
            SELECT
                trim(e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) AS cnpj,
                trim(coalesce(emp.razao_social, '')) AS razao_social,
                trim(coalesce(e.nome_fantasia, '')) AS nome_fantasia,
                trim(coalesce(e.situacao_cadastral, '')) AS situacao_cadastral,
                trim(coalesce(e.data_inicio_atividade, '')) AS data_inicio_atividade,
                regexp_replace(coalesce(e.cnae_principal, ''), '[^0-9]', '', 'g') AS cnae_principal,
                trim(coalesce(e.tipo_logradouro, '')) AS tipo_logradouro,
                trim(coalesce(e.logradouro, '')) AS logradouro,
                trim(coalesce(e.numero, '')) AS numero,
                trim(coalesce(e.complemento, '')) AS complemento,
                trim(coalesce(e.bairro, '')) AS bairro,
                trim(coalesce(e.cep, '')) AS cep,
                trim(upper(coalesce(e.uf, ''))) AS uf,
                {muni_desc_expr} AS municipio,
                trim(coalesce(e.ddd_1, '')) AS ddd_1,
                trim(coalesce(e.telefone_1, '')) AS telefone_1,
                trim(coalesce(e.ddd_2, '')) AS ddd_2,
                trim(coalesce(e.telefone_2, '')) AS telefone_2,
                lower(trim(coalesce(e.correio_eletronico, ''))) AS email,
                trim(coalesce(emp.natureza_juridica, '')) AS natureza_juridica,
                trim(coalesce(emp.capital_social, '')) AS capital_social,
                trim(coalesce(emp.porte_empresa, '')) AS porte_empresa,
                trim(coalesce(e.matriz_filial, '')) AS matriz_filial
            FROM est e
            LEFT JOIN emp ON trim(e.cnpj_basico)=trim(emp.cnpj_basico)
            {muni_join_sql}
            LIMIT 1
            """
            row = conn.execute(q).fetchone()

        if not row:
            socios_only = fetch_socios_local(basico)
            out = {'socios': socios_only, '_fonte_local': 'base_receita_local'} if socios_only else {}
            cache[cnpj_limpo] = {'data': out, 'ts': time.time()}
            if cache_file and out:
                try:
                    cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
                except Exception:
                    pass
            return dict(out)

        cols = [
            'cnpj','razao_social','nome_fantasia','situacao_cadastral','data_inicio_atividade','cnae_principal',
            'tipo_logradouro','logradouro','numero','complemento','bairro','cep','uf','municipio',
            'ddd_1','telefone_1','ddd_2','telefone_2','email','natureza_juridica','capital_social','porte_empresa','matriz_filial'
        ]
        d = dict(zip(cols, row))
        situacao_map = {'01':'NULA','02':'ATIVA','03':'SUSPENSA','04':'INAPTA','05':'ATIVA','08':'BAIXADA'}
        porte_map = {'00': 'Não informado', '01': 'Micro empresa', '03': 'Empresa de pequeno porte', '05': 'Demais'}
        tipo_map = {'1':'Matriz','2':'Filial'}

        tel1 = ''
        if d.get('ddd_1') and d.get('telefone_1'):
            tel1 = f"({d['ddd_1']}) {d['telefone_1']}"
        elif d.get('telefone_1'):
            tel1 = d['telefone_1']

        tel2 = ''
        if d.get('ddd_2') and d.get('telefone_2'):
            tel2 = f"({d['ddd_2']}) {d['telefone_2']}"
        elif d.get('telefone_2'):
            tel2 = d['telefone_2']

        logr = ' '.join(v for v in [d.get('tipo_logradouro',''), d.get('logradouro',''), d.get('numero','')] if v)
        cnae_code = str(d.get('cnae_principal') or '').strip()
        socios = fetch_socios_local(basico)

        result = {
            'cnpj': cnpj_limpo,
            'razao_social': str(d.get('razao_social') or '').strip(),
            'nome_fantasia': str(d.get('nome_fantasia') or '').strip(),
            'situacao': situacao_map.get(str(d.get('situacao_cadastral') or '').strip(), str(d.get('situacao_cadastral') or '').strip()),
            'abertura': _fmt_data_br(d.get('data_inicio_atividade')),
            'tipo': tipo_map.get(str(d.get('matriz_filial') or '').strip(), ''),
            'capital_social': _fmt_brl(d.get('capital_social')) if str(d.get('capital_social') or '').strip() else '',
            'porte': porte_map.get(str(d.get('porte_empresa') or '').strip(), str(d.get('porte_empresa') or '').strip()),
            'natureza_juridica': str(d.get('natureza_juridica') or '').strip(),
            'cnae_principal_codigo': cnae_code,
            'cnae_principal_descricao': _cnae_desc_from_code(cnae_code),
            'telefone_1': tel1,
            'telefone_2': tel2,
            'email': str(d.get('email') or '').strip(),
            'logradouro': logr.strip(),
            'complemento': str(d.get('complemento') or '').strip(),
            'bairro': str(d.get('bairro') or '').strip(),
            'cep': str(d.get('cep') or '').strip(),
            'municipio': str(d.get('municipio') or '').strip(),
            'uf': str(d.get('uf') or '').strip(),
            'socios': socios,
            '_fonte_local': 'base_receita_local',
            '_modo_rapido': True
        }
        if result.get('abertura'):
            result['tempo_vida'] = calcular_tempo_vida(result.get('abertura')) or ''
        result['faturamento_estimado'] = estimar_faturamento(result.get('capital_social',''), result.get('porte',''), result.get('cnae_principal_descricao',''), result.get('abertura',''))
        result['funcionarios_estimados'] = estimar_funcionarios(result.get('porte',''), result.get('capital_social',''), result.get('abertura',''))

        cache[cnpj_limpo] = {'data': result, 'ts': time.time()}
        if cache_file:
            try:
                cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass
        return dict(result)
    except Exception as e:
        return {'error': f'Erro na consulta local: {str(e)[:120]}'}


def _merge_company_data(primary, fallback):
    if not isinstance(primary, dict):
        primary = {}
    if not isinstance(fallback, dict):
        fallback = {}
    merged = dict(primary)
    fields = [
        'razao_social','nome_fantasia','abertura','situacao','capital_social','porte','natureza_juridica',
        'cnae_principal_codigo','cnae_principal_descricao','telefone_1','telefone_2','email','logradouro',
        'complemento','bairro','cep','municipio','uf','tipo','simples_data','tempo_vida',
        'faturamento_estimado','funcionarios_estimados','mei','simples'
    ]
    for k in fields:
        v = merged.get(k)
        if v in (None, '', '—', 'None'):
            fb = fallback.get(k)
            if fb not in (None, '', '—', 'None'):
                merged[k] = fb

    if not merged.get('socios') and fallback.get('socios'):
        merged['socios'] = fallback.get('socios')

    if not merged.get('tempo_vida') and merged.get('abertura'):
        merged['tempo_vida'] = calcular_tempo_vida(merged.get('abertura')) or ''

    if not merged.get('cnae_principal_descricao') and fallback.get('cnae_principal_codigo'):
        merged['cnae_principal_codigo'] = fallback.get('cnae_principal_codigo', '')
        merged['cnae_principal_descricao'] = fallback.get('cnae_principal_descricao', '')

    merged['_fontes'] = list(dict.fromkeys([x for x in [primary.get('_fonte'), fallback.get('_fonte_local')] if x]))
    return merged

# ===== CORRECAO: SOCIOS/DONO NOS DADOS ENRIQUECIDOS =====
def _normalize_socios_payload(items):
    out = []
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        nome = str(item.get('nome') or item.get('nome_socio') or item.get('name') or item.get('razao_social') or '').strip()
        qual = str(item.get('qualificacao') or item.get('qualificacao_socio') or item.get('role') or item.get('cargo') or '').strip()
        if nome:
            rec = {'nome': nome, 'qualificacao': qual}
            if item.get('faixa_etaria'): rec['faixa_etaria'] = str(item.get('faixa_etaria')).strip()
            if item.get('entrada'): rec['entrada'] = str(item.get('entrada')).strip()
            if item.get('pais'): rec['pais'] = str(item.get('pais')).strip()
            out.append(rec)
    return out

def _anexar_socios_donos(cnpj_limpo, payload):
    if not isinstance(payload, dict):
        payload = {}
    cnpj_limpo = normalize_cnpj(str(cnpj_limpo or ''))
    socios = _normalize_socios_payload(payload.get('socios') or payload.get('qsa') or [])
    if not socios and len(cnpj_limpo) == 14:
        try:
            socios = fetch_socios_local(cnpj_limpo[:8], limit=20)
            socios = _normalize_socios_payload(socios)
        except Exception:
            socios = []
    payload['socios'] = socios
    payload['qsa'] = socios
    payload['donos'] = ', '.join([s.get('nome', '') for s in socios if s.get('nome')]) if socios else ''
    payload['_socios_checked'] = True
    payload['_socios_v2'] = True
    return payload
# ===== FIM CORRECAO: SOCIOS/DONO NOS DADOS ENRIQUECIDOS =====

# ===== API GRATUITA MELHORADA - CNPJ / DADOS ENRIQUECIDOS =====
def _api_cache_dir():
    return SITE_DIR / "cache_enriquecimento_api"

def _load_api_enrich_cache(cnpj_limpo):
    try:
        fp = _api_cache_dir() / f"{cnpj_limpo}.json"
        if not fp.exists():
            return None
        data = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        ts = float(data.get("_cache_ts", 0) or 0)
        if data.get('_enrich_version') == 2 and ts and (time.time() - ts) <= (30 * 86400):
            return data
    except Exception:
        return None
    return None

def _save_api_enrich_cache(cnpj_limpo, payload):
    try:
        _api_cache_dir().mkdir(parents=True, exist_ok=True)
        data = dict(payload or {})
        data["_cache_ts"] = time.time()
        data["_api_combo_v1"] = True
        (_api_cache_dir() / f"{cnpj_limpo}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _s_api(v):
    return str(v).strip() if v not in (None, "") else ""

def _fmt_brl_api(v):
    s = _s_api(v)
    if not s:
        return ""
    try:
        n = float(str(s).replace("R$", "").replace(".", "").replace(",", ".").strip())
        return f"R$ {n:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return s

def _phone_api(ddd, numero):
    ddd = re.sub(r"\D", "", _s_api(ddd))
    numero = re.sub(r"\D", "", _s_api(numero))
    if ddd and numero:
        return f"({ddd}) {numero}"
    return numero

def _normalize_socios_api(items):
    out = []
    if not isinstance(items, list):
        return out
    for q in items:
        if not isinstance(q, dict):
            continue
        pessoa = q.get("person") if isinstance(q.get("person"), dict) else {}
        role = q.get("role") if isinstance(q.get("role"), dict) else {}
        nome = _s_api(q.get("nome") or q.get("nome_socio") or q.get("name") or q.get("razao_social") or pessoa.get("name") or q.get("nome_representante_legal") or "")
        qual_raw = q.get("qualificacao") or q.get("qualificacao_socio") or q.get("qual") or q.get("cargo") or role.get("text") or ""
        if isinstance(qual_raw, dict):
            qual = _s_api(qual_raw.get("descricao") or qual_raw.get("nome") or qual_raw.get("text") or "")
        else:
            qual = _s_api(qual_raw)
        if nome:
            item = {"nome": nome, "qualificacao": qual}
            if q.get("faixa_etaria"):
                item["faixa_etaria"] = _s_api(q.get("faixa_etaria"))
            if q.get("data_entrada_sociedade") or q.get("entrada"):
                item["entrada"] = _fmt_data_br(q.get("data_entrada_sociedade") or q.get("entrada"))
            out.append(item)
    return out

def _merge_api_payload(base, novo):
    base = dict(base or {})
    novo = dict(novo or {})
    for k, v in novo.items():
        if k.startswith("_"):
            continue
        if k in ("socios", "qsa"):
            atuais = base.get("socios") or base.get("qsa") or []
            novos = _normalize_socios_api(v)
            if len(novos) > len(_normalize_socios_api(atuais)):
                base["socios"] = novos
                base["qsa"] = novos
            continue
        if k == "cnaes_secundarios":
            if not base.get(k) and v:
                base[k] = v
            elif isinstance(v, list) and isinstance(base.get(k), list) and len(v) > len(base.get(k)):
                base[k] = v
            continue
        if base.get(k) in (None, "", "—", "None", [], {}):
            if v not in (None, "", "—", "None", [], {}):
                base[k] = v
    fontes = []
    for p in (base, novo):
        f = p.get("_fonte")
        if f:
            for x in str(f).split("+"):
                x = x.strip()
                if x and x not in fontes:
                    fontes.append(x)
    if fontes:
        base["_fonte"] = " + ".join(fontes)
    socios = _normalize_socios_api(base.get("socios") or base.get("qsa") or [])
    base["socios"] = socios
    base["qsa"] = socios
    base["donos"] = ", ".join([s.get("nome", "") for s in socios if s.get("nome")]) if socios else ""
    base["_api_combo_v1"] = True
    return base

def _score_api_payload(d):
    if not isinstance(d, dict) or d.get("error"):
        return 0
    keys = ["razao_social", "nome_fantasia", "abertura", "situacao", "capital_social", "porte", "natureza_juridica", "cnae_principal_descricao", "telefone_1", "telefone_2", "email", "logradouro", "bairro", "cep", "municipio", "uf"]
    score = sum(1 for k in keys if d.get(k))
    if d.get("socios") or d.get("qsa"):
        score += 5
    if d.get("cnaes_secundarios"):
        score += 2
    return score

def _formatar_brasilapi(d):
    socios = _normalize_socios_api(d.get("qsa") or [])
    cnaes_sec = []
    for a in d.get("cnaes_secundarios") or []:
        if isinstance(a, dict):
            cod = _s_api(a.get("codigo"))
            desc = _s_api(a.get("descricao"))
            if cod:
                cnaes_sec.append({"codigo": cod, "descricao": desc})
    tel1 = _phone_api(d.get("ddd_telefone_1") or d.get("ddd1"), d.get("telefone_1") or d.get("telefone"))
    tel2 = _phone_api(d.get("ddd_telefone_2") or d.get("ddd2"), d.get("telefone_2"))
    capital = _fmt_brl_api(d.get("capital_social"))
    abertura = _s_api(d.get("data_inicio_atividade") or d.get("abertura"))
    porte = _s_api(d.get("porte"))
    cnae_desc = _s_api(d.get("cnae_fiscal_descricao") or d.get("cnae_principal_descricao"))
    try:
        fat = estimar_faturamento(capital, porte, cnae_desc, abertura)
        funcs = estimar_funcionarios(porte, capital, abertura)
    except Exception:
        fat = ""
        funcs = ""
    return {"razao_social": _s_api(d.get("razao_social")), "nome_fantasia": _s_api(d.get("nome_fantasia")), "abertura": abertura, "tempo_vida": calcular_tempo_vida(abertura) or "", "situacao": _s_api(d.get("descricao_situacao_cadastral") or d.get("situacao_cadastral") or d.get("situacao")), "capital_social": capital, "faturamento_estimado": fat, "funcionarios_estimados": funcs, "porte": porte, "natureza_juridica": _s_api(d.get("natureza_juridica")), "cnae_principal_codigo": _s_api(d.get("cnae_fiscal")), "cnae_principal_descricao": cnae_desc, "cnaes_secundarios": cnaes_sec, "socios": socios, "qsa": socios, "telefone_1": tel1, "telefone_2": tel2, "email": _s_api(d.get("email")), "logradouro": " ".join(x for x in [_s_api(d.get("descricao_tipo_de_logradouro")), _s_api(d.get("logradouro")), _s_api(d.get("numero"))] if x), "complemento": _s_api(d.get("complemento")), "bairro": _s_api(d.get("bairro")), "cep": _s_api(d.get("cep")), "municipio": _s_api(d.get("municipio")), "uf": _s_api(d.get("uf")), "mei": str(d.get("opcao_pelo_mei", "")).lower(), "simples": str(d.get("opcao_pelo_simples", "")).lower(), "_fonte": "brasilapi"}

def _formatar_receitaws(d):
    if str(d.get("status", "")).upper() == "ERROR":
        return {"error": d.get("message", "ReceitaWS sem dados.")}
    socios = _normalize_socios_api(d.get("qsa") or [])
    cnaes_sec = []
    for a in d.get("atividades_secundarias") or []:
        if isinstance(a, dict):
            cod = re.sub(r"\D", "", _s_api(a.get("code") or a.get("codigo")))
            desc = _s_api(a.get("text") or a.get("descricao"))
            if cod:
                cnaes_sec.append({"codigo": cod, "descricao": desc})
    principal = {}
    aps = d.get("atividade_principal") or []
    if isinstance(aps, list) and aps:
        principal = aps[0] if isinstance(aps[0], dict) else {}
    cnae_code = re.sub(r"\D", "", _s_api(principal.get("code") or principal.get("codigo")))
    cnae_desc = _s_api(principal.get("text") or principal.get("descricao"))
    tel = _s_api(d.get("telefone"))
    tel1, tel2 = tel, ""
    if "/" in tel:
        parts = [x.strip() for x in tel.split("/") if x.strip()]
        tel1 = parts[0] if parts else ""
        tel2 = parts[1] if len(parts) > 1 else ""
    capital = _fmt_brl_api(d.get("capital_social"))
    abertura = _s_api(d.get("abertura"))
    porte = _s_api(d.get("porte"))
    try:
        fat = estimar_faturamento(capital, porte, cnae_desc, abertura)
        funcs = estimar_funcionarios(porte, capital, abertura)
    except Exception:
        fat = ""
        funcs = ""
    return {"razao_social": _s_api(d.get("nome")), "nome_fantasia": _s_api(d.get("fantasia")), "abertura": abertura, "tempo_vida": calcular_tempo_vida(abertura) or "", "tipo": _s_api(d.get("tipo")), "situacao": _s_api(d.get("situacao")), "capital_social": capital, "faturamento_estimado": fat, "funcionarios_estimados": funcs, "porte": porte, "natureza_juridica": _s_api(d.get("natureza_juridica")), "cnae_principal_codigo": cnae_code, "cnae_principal_descricao": cnae_desc, "cnaes_secundarios": cnaes_sec, "socios": socios, "qsa": socios, "telefone_1": tel1, "telefone_2": tel2, "email": _s_api(d.get("email")), "logradouro": " ".join(x for x in [_s_api(d.get("logradouro")), _s_api(d.get("numero"))] if x), "complemento": _s_api(d.get("complemento")), "bairro": _s_api(d.get("bairro")), "cep": _s_api(d.get("cep")), "municipio": _s_api(d.get("municipio")), "uf": _s_api(d.get("uf")), "mei": "", "simples": "", "_fonte": "receitaws"}

def fetch_cnpj_externo(cnpj_limpo, refresh=False):
    cnpj_limpo = normalize_cnpj(str(cnpj_limpo or ''))
    if len(cnpj_limpo) != 14:
        return {'error': 'CNPJ deve ter 14 digitos'}
    with _API_LOOKUP_LOCKS[hash(cnpj_limpo) % len(_API_LOOKUP_LOCKS)]:
        cached = _enrich_cache.get(cnpj_limpo)
        disk = _load_api_enrich_cache(cnpj_limpo)
        if not refresh:
            if cached and cached.get('data', {}).get('_enrich_version') == 2 and time.time() - cached.get('ts', 0) < CACHE_TTL:
                return dict(cached['data'], _from_cache=True)
            if disk:
                return dict(disk, _from_cache=True)
        result = _FREE_COMPANY_APIS.lookup(cnpj_limpo)
        if not result.get('error'):
            result['_enrich_version'] = 2
            result['tempo_vida'] = calcular_tempo_vida(result.get('abertura', '')) or ''
            result['faturamento_estimado'] = estimar_faturamento(result.get('capital_social', ''), result.get('porte', ''), result.get('cnae_principal_descricao', ''), result.get('abertura', ''))
            result['funcionarios_estimados'] = estimar_funcionarios(result.get('porte', ''), result.get('capital_social', ''), result.get('abertura', ''))
            _enrich_cache[cnpj_limpo] = {'data': result, 'ts': time.time()}
            if len(_enrich_cache) > 2000:
                _enrich_cache.pop(next(iter(_enrich_cache)), None)
            _save_api_enrich_cache(cnpj_limpo, result)
            return result
        if disk:
            return dict(disk, _from_cache=True, _enrichment_warning=result['error'])
        return result
# ===== FIM API GRATUITA MELHORADA =====


def _formatar_dados_externos(d):
    """
    Extrai e normaliza campos de múltiplas APIs:
    - publica.cnpj.ws
    - open.cnpja.com
    - receitaws.com.br
    """
    def s(v):
        return str(v).strip() if v else ''

    # Detectar formato CNPJá (open.cnpja.com)
    is_cnpja = 'taxId' in d or 'alias' in d

    if is_cnpja:
        # Normalizar formato CNPJá para o formato padrão
        company = d.get('company', {})
        address = d.get('address', {})
        phones  = d.get('phones', [])
        emails  = d.get('emails', [])
        simples = d.get('simples', {})
        simei   = d.get('simei', {})

        tel1 = ''
        if phones:
            p = phones[0]
            tel1 = f"({p.get('area','')}) {p.get('number','')}".strip()

        email1 = emails[0].get('address','') if emails else ''

        # Sócios
        socios_raw = company.get('members', d.get('members', []))
        socios_norm = []
        for m in socios_raw:
            person = m.get('person', m)
            nome = s(person.get('name','') or m.get('name',''))
            role = m.get('role', {})
            qual = s(role.get('text','') if isinstance(role, dict) else role)
            if nome:
                socios_norm.append({'nome': nome, 'qualificacao': qual})

        # CNAEs secundários
        cnaes_sec = []
        for c in d.get('sideActivities', []):
            cod  = s(c.get('id','') or c.get('code',''))
            desc = s(c.get('text','') or c.get('description',''))
            if cod: cnaes_sec.append({'codigo': cod, 'descricao': desc})

        # Natureza jurídica
        nature = d.get('nature', {})
        nat_desc = s(nature.get('text','') if isinstance(nature, dict) else nature)

        # Porte
        size = d.get('size', {})
        porte = s(size.get('text','') if isinstance(size, dict) else size)

        # Status
        status = d.get('status', {})
        situacao = s(status.get('text','') if isinstance(status, dict) else status)

        # CNAE principal
        mainActivity = d.get('mainActivity', {})
        cnae_desc = s(mainActivity.get('text','') if isinstance(mainActivity, dict) else mainActivity)

        # Capital
        equity = company.get('equity', 0)
        try:
            capital_fmt = f"R$ {float(equity):,.2f}".replace(',','X').replace('.', ',').replace('X','.')
        except:
            capital_fmt = ''

        # Abertura
        founded = s(d.get('founded',''))

        # Endereço
        logr = ' '.join(filter(None,[
            s(address.get('street','')),
            s(address.get('number','')),
            s(address.get('details','')),
        ]))
        bairro   = s(address.get('district',''))
        cep      = s(address.get('zip',''))
        municipio = s(address.get('city','') or (address.get('municipality',{}) or {}).get('name',''))
        uf_end   = s(address.get('state',''))

        simples_opt = str(simples.get('optant', False)).lower() == 'true' if isinstance(simples, dict) else False
        mei_opt     = str(simei.get('optant', False)).lower() == 'true' if isinstance(simei, dict) else False

        faturamento_est = estimar_faturamento(capital_fmt, porte, cnae_desc, founded)
        funcionarios_est = estimar_funcionarios(porte, capital_fmt, founded)

        return {
            'razao_social':            s(company.get('name', d.get('company',''))),
            'nome_fantasia':           s(d.get('alias','')),
            'abertura':                founded,
            'situacao':                situacao,
            'capital_social':          capital_fmt,
            'faturamento_estimado':    faturamento_est,
            'funcionarios_estimados':  funcionarios_est,
            'porte':                   porte,
            'natureza_juridica':       nat_desc,
            'cnae_principal_descricao': cnae_desc,
            'cnaes_secundarios':       cnaes_sec,
            'socios':                  socios_norm,
            'telefone_2':              tel1,
            'email':                   email1,
            'logradouro':              logr,
            'bairro':                  bairro,
            'cep':                     cep,
            'municipio':               municipio,
            'uf':                      uf_end,
            'mei':                     'true' if mei_opt else 'false',
            'simples':                 'true' if simples_opt else 'false',
            '_fonte':                  'cnpja.com',
        }

    # Socios — tenta todos os campos possiveis da API
    socios = []
    qsa_list = d.get('qsa') or d.get('socios') or d.get('quadro_societario') or []
    for q in qsa_list:
        if not isinstance(q, dict):
            continue
        # Nome: tenta varios campos possiveis
        nome = s(
            q.get('nome_socio') or
            q.get('nome') or
            q.get('nome_representante_legal') or
            q.get('razao_social') or ''
        )
        # Qualificacao: pode ser string ou dict com 'descricao'
        qual_raw = q.get('qualificacao_socio') or q.get('qualificacao') or ''
        if isinstance(qual_raw, dict):
            qual = s(qual_raw.get('descricao', '') or qual_raw.get('nome', ''))
        else:
            qual = s(qual_raw)
        # Pais do socio
        pais_raw = q.get('pais') or {}
        pais = s(pais_raw.get('descricao', '') if isinstance(pais_raw, dict) else pais_raw)
        if nome:
            entry = {'nome': nome, 'qualificacao': qual}
            if pais and pais.upper() not in ('BRASIL', 'BRAZIL', ''):
                entry['pais'] = pais
            socios.append(entry)
    # Fallback: log dos campos disponiveis para debug se nao encontrou socios
    if not socios and qsa_list:
        # qsa existe mas nao conseguiu extrair — retorna estrutura raw para debug
        socios = [{'nome': str(q), 'qualificacao': 'debug'} for q in qsa_list[:3]]

    # Atividades secundarias
    ativ_sec = []
    for a in d.get('cnaes_secundarios', []) or []:
        codigo = s(a.get('codigo', ''))
        desc = s(a.get('descricao', ''))
        if codigo:
            ativ_sec.append({'codigo': codigo, 'descricao': desc})

    # Capital social formatado
    capital_raw = s(d.get('capital_social', ''))
    try:
        capital_fmt = f"R$ {float(capital_raw.replace(',', '.')):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')
    except Exception:
        capital_fmt = capital_raw

    # Porte
    porte_map = {'00': 'Não informado', '01': 'Micro empresa', '03': 'Empresa de pequeno porte', '05': 'Demais'}
    porte_cod = s(d.get('porte', {}).get('id', '') if isinstance(d.get('porte'), dict) else d.get('porte', ''))
    porte_desc = s(d.get('porte', {}).get('descricao', '') if isinstance(d.get('porte'), dict) else '')
    porte = porte_desc or porte_map.get(porte_cod, porte_cod)

    # Natureza juridica
    nat = d.get('natureza_juridica') or {}
    nat_desc = s(nat.get('descricao', '') if isinstance(nat, dict) else nat)

    # CNAE principal descricao
    cnae_p = d.get('cnae_fiscal_principal') or d.get('cnae_principal') or {}
    cnae_desc = s(cnae_p.get('descricao', '') if isinstance(cnae_p, dict) else cnae_p)

    # Data abertura
    abertura = s(d.get('data_inicio_atividade', '') or d.get('abertura', ''))

    # Endereco completo
    end = d.get('endereco') or {}
    if isinstance(end, dict):
        logr = ' '.join(filter(None, [
            s(end.get('tipo_logradouro', '')),
            s(end.get('logradouro', '')),
            s(end.get('numero', '')),
            s(end.get('complemento', '')),
        ]))
        bairro = s(end.get('bairro', ''))
        cep = s(end.get('cep', ''))
        municipio = s(end.get('municipio', ''))
        uf_end = s(end.get('uf', ''))
    else:
        logr = bairro = cep = municipio = uf_end = ''

    # Telefones adicionais
    tel2 = ''
    ddd2 = s(d.get('ddd_telefone_2', ''))
    fone2 = s(d.get('telefone_2', ''))
    if ddd2 and fone2:
        tel2 = f'({ddd2}) {fone2}'
    elif fone2:
        tel2 = fone2

    # Faturamento estimado
    faturamento_est = estimar_faturamento(capital_fmt, porte, cnae_desc, abertura)
    # Funcionários estimados
    funcionarios_est = estimar_funcionarios(porte, capital_fmt, abertura)

    # Tempo de vida
    tempo_vida = calcular_tempo_vida(abertura)

    # Tipo (Matriz/Filial)
    tipo_raw = s(d.get('identificador_matriz_filial', '') or d.get('tipo', '') or d.get('matriz_filial', ''))
    tipo_map = {'1': 'Matriz', '2': 'Filial', 'MATRIZ': 'Matriz', 'FILIAL': 'Filial'}
    tipo = tipo_map.get(tipo_raw.upper(), tipo_raw) or 'Matriz'

    # Inscrição Estadual
    inscricoes = d.get('inscricoes_estaduais', d.get('inscricao_estadual', []))
    inscricao_est = ''
    if isinstance(inscricoes, list) and inscricoes:
        ie = inscricoes[0]
        if isinstance(ie, dict):
            inscricao_est = s(ie.get('inscricao_estadual', ie.get('numero', '')))
            ie_uf = s(ie.get('estado', ie.get('uf', '')))
            if ie_uf:
                inscricao_est = f"{ie_uf}: {inscricao_est}"
        else:
            inscricao_est = s(ie)
    elif isinstance(inscricoes, str):
        inscricao_est = inscricoes

    # Data opção Simples
    simples_data = s(d.get('data_opcao_pelo_simples', '') or d.get('opcao_simples_data', ''))

    # Complemento do endereço
    complemento = s(end.get('complemento', '')) if isinstance(end, dict) else ''

    # Telefone principal formatado
    tel1 = ''
    ddd1 = s(d.get('ddd_telefone_1', '') or d.get('ddd1', ''))
    fone1 = s(d.get('telefone_1', '') or d.get('telefone', ''))
    if ddd1 and fone1:
        tel1 = f'({ddd1}) {fone1}'
    elif fone1:
        tel1 = fone1

    return {
        'razao_social': s(d.get('razao_social', '')),
        'nome_fantasia': s(d.get('nome_fantasia', '')),
        'abertura': abertura,
        'tempo_vida': tempo_vida or '',
        'tipo': tipo,
        'situacao': s(d.get('descricao_situacao_cadastral', '') or d.get('situacao', '')),
        'capital_social': capital_fmt,
        'faturamento_estimado': faturamento_est,
        'funcionarios_estimados': funcionarios_est,
        'porte': porte,
        'natureza_juridica': nat_desc,
        'cnae_principal_descricao': cnae_desc,
        'cnaes_secundarios': ativ_sec,
        'socios': socios,
        'inscricao_estadual': inscricao_est,
        'simples_data': simples_data,
        'telefone_1': tel1,
        'telefone_2': tel2,
        'email': s(d.get('email', '') or d.get('correio_eletronico', '')),
        'logradouro': logr,
        'complemento': complemento,
        'bairro': bairro,
        'cep': cep,
        'municipio': municipio,
        'uf': uf_end,
        'mei': s(d.get('opcao_pelo_mei', '') or ''),
        'simples': s(d.get('opcao_pelo_simples', '') or ''),
        '_fonte': 'publica.cnpj.ws',
    }



# ===== CACHE MUNICIPIO / IBGE =====
_ibge_cache = {}

def fetch_dados_municipio(nome_municipio, uf):
    """Busca dados do município via API IBGE."""
    key = f"{nome_municipio}_{uf}".lower()
    if key in _ibge_cache:
        return _ibge_cache[key]
    try:
        # Buscar código do município
        url_busca = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
        req = Request(url_busca, headers={"User-Agent": "ProspectaX/1.0"})
        with urlopen(req, timeout=10) as resp:
            municipios = json.loads(resp.read().decode("utf-8"))

        nome_lower = nome_municipio.lower().strip()
        cod_ibge = None
        for m in municipios:
            if m.get("nome", "").lower().strip() == nome_lower:
                cod_ibge = m.get("id")
                break

        if not cod_ibge:
            # Busca parcial
            for m in municipios:
                if nome_lower in m.get("nome", "").lower():
                    cod_ibge = m.get("id")
                    break

        if not cod_ibge:
            result = {"erro": "Município não encontrado"}
            _ibge_cache[key] = result
            return result

        # Buscar dados do município
        url_dados = f"https://servicodados.ibge.gov.br/api/v3/malhas/municipios/{cod_ibge}/metadados"
        req2 = Request(url_dados, headers={"User-Agent": "ProspectaX/1.0"})
        try:
            with urlopen(req2, timeout=10) as resp2:
                dados = json.loads(resp2.read().decode("utf-8"))
        except:
            dados = {}

        result = {
            "codigo_ibge": str(cod_ibge),
            "nome": nome_municipio,
            "uf": uf.upper(),
        }
        _ibge_cache[key] = result
        return result

    except Exception as e:
        result = {"erro": str(e)[:80]}
        _ibge_cache[key] = result
        return result


def fetch_perfil_municipio(cod_ibge):
    """Busca indicadores do município via IBGE Cidades."""
    try:
        # Indicadores: população (29171), PIB per capita (47001), IDH (30255)
        indicadores = "29171|47001|30255"
        url = f"https://servicodados.ibge.gov.br/api/v1/pesquisas/indicadores/{indicadores}/resultados/{cod_ibge}"
        req = Request(url, headers={"User-Agent": "ProspectaX/1.0"})
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        resultado = {}
        for item in data:
            ind_id = str(item.get("id", ""))
            series = item.get("series", [])
            if series:
                # Pegar o valor mais recente
                periodos = series[0].get("serie", {})
                if periodos:
                    ano = max(periodos.keys())
                    val = periodos[ano]
                    if ind_id == "29171":
                        try:
                            pop = int(str(val).replace(".", "").replace(",", ""))
                            resultado["populacao"] = f"{pop:,}".replace(",", ".") + " hab."
                            resultado["populacao_ano"] = ano
                        except: pass
                    elif ind_id == "47001":
                        try:
                            pib = float(str(val).replace(".", "").replace(",", "."))
                            resultado["pib_per_capita"] = f"R$ {pib:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")
                            resultado["pib_ano"] = ano
                        except: pass
                    elif ind_id == "30255":
                        resultado["idh"] = val
                        resultado["idh_ano"] = ano
        return resultado
    except Exception as e:
        return {"erro": str(e)[:80]}


def contar_filiais(cnpj_basico):
    """Conta filiais da empresa nos dados locais via DuckDB."""
    try:
        if fast_db_available():
            conn = get_fast_db()
            q = f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN cnpj_ordem='0001' THEN 1 ELSE 0 END) as matriz
            FROM px_estabelecimentos
            WHERE cnpj_basico = '{cnpj_basico}'
            """
        else:
            conn = get_db()
            q = f"""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN trim(cnpj_ordem)='0001' THEN 1 ELSE 0 END) as matriz
            FROM read_parquet('{EST_PATTERN}')
            WHERE trim(cnpj_basico) = '{cnpj_basico}'
            """
        result = conn.execute(q).fetchone()
        if result:
            total = result[0] or 1
            filiais = max(0, total - 1)
            return {"total_estabelecimentos": total, "filiais": filiais}
    except Exception:
        pass
    return {"total_estabelecimentos": 1, "filiais": 0}


def buscar_concorrentes(cnae, municipio_cod, uf, limite=5):
    """Busca top concorrentes na mesma cidade e CNAE."""
    try:
        if fast_db_available():
            conn = get_fast_db()
            q = f"""
            SELECT nome, telefone, email, cnpj
            FROM px_busca
            WHERE filtro_cnae = '{cnae}'
              AND municipio_cod = '{municipio_cod}'
              AND uf = '{uf.upper()}'
              AND situacao_cadastral IN ('02','05')
            LIMIT {limite + 1}
            """
        else:
            conn = get_db()
            q = f"""
            SELECT
                coalesce(nullif(trim(e.nome_fantasia),''), nullif(trim(emp.razao_social),''), '-') AS nome,
                CASE WHEN nullif(trim(e.ddd_1),'') IS NOT NULL AND nullif(trim(e.telefone_1),'') IS NOT NULL
                     THEN '(' || trim(e.ddd_1) || ') ' || trim(e.telefone_1)
                     ELSE '' END AS telefone,
                lower(trim(e.correio_eletronico)) AS email,
                trim(e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) AS cnpj
            FROM read_parquet('{EST_PATTERN}') e
            LEFT JOIN read_parquet('{EMP_PATTERN}') emp
                ON trim(e.cnpj_basico) = trim(emp.cnpj_basico)
            WHERE regexp_replace(e.cnae_principal, '[^0-9]', '', 'g') = '{cnae}'
              AND trim(e.municipio) = '{municipio_cod}'
              AND upper(trim(e.uf)) = '{uf.upper()}'
              AND trim(e.situacao_cadastral) IN ('02','05')
            LIMIT {limite + 1}
            """
        rows = conn.execute(q).fetchall()
        return [{"nome": r[0], "telefone": r[1], "email": r[2], "cnpj": r[3]} for r in rows[:limite]]
    except Exception:
        return []


def calcular_tempo_vida(abertura_str):
    """Calcula tempo de vida da empresa."""
    try:
        from datetime import datetime, date
        if not abertura_str:
            return None
        if "-" in str(abertura_str):
            partes = str(abertura_str).split("-")
            dt = date(int(partes[0]), int(partes[1]), int(partes[2][:2]))
        else:
            partes = str(abertura_str).split("/")
            dt = date(int(partes[2]), int(partes[1]), int(partes[0]))
        hoje = date.today()
        anos = (hoje - dt).days // 365
        meses = ((hoje - dt).days % 365) // 30
        if anos >= 1:
            return f"{anos} ano{'s' if anos > 1 else ''} e {meses} mês{'es' if meses > 1 else ''}"
        elif meses > 0:
            return f"{meses} mês{'es' if meses > 1 else ''}"
        else:
            return "Menos de 1 mês"
    except:
        return None


def build_search_sql(cnaes, ufs, situacoes, excluir_mei=False, palavra='', limit=200000, offset=0, cnae_modo='todos'):
    where = []

    if cnaes:
        cnae_vals = ",".join("'" + v + "'" for v in cnaes)
        cnae_modo = 'primario' if str(cnae_modo or '').strip().lower() in {'primario', 'principal', 'primary'} else 'todos'
        if cnae_modo == 'primario':
            where.append(f"regexp_replace(e.cnae_principal, '[^0-9]', '', 'g') IN ({cnae_vals})")
        else:
            # Busca CNAE principal OU dentro do campo de CNAEs secundários
            sec_conditions = " OR ".join(
                f"regexp_replace(e.cnae_secundaria, '[^0-9,]', '', 'g') LIKE '%{v}%'"
                for v in cnaes
            )
            where.append(
                f"(regexp_replace(e.cnae_principal, '[^0-9]', '', 'g') IN ({cnae_vals}) OR ({sec_conditions}))"
            )

    if ufs:
        uf_vals = ",".join("'" + v + "'" for v in ufs)
        where.append(f"upper(trim(e.uf)) IN ({uf_vals})")

    if situacoes:
        sit_codes = []
        if 'ATIVA' in situacoes:
            sit_codes.extend(['02', '05'])
        if 'SUSPENSA' in situacoes:
            sit_codes.append('03')
        if 'INAPTA' in situacoes:
            sit_codes.append('04')
        if 'BAIXADA' in situacoes:
            sit_codes.append('08')
        if 'NULA' in situacoes:
            sit_codes.append('01')
        if sit_codes:
            sit_vals = ",".join("'" + v + "'" for v in sorted(set(sit_codes)))
            where.append(f"trim(e.situacao_cadastral) IN ({sit_vals})")

    if excluir_mei:
        mei_vals = ",".join("'" + v + "'" for v in sorted(MEI_NATUREZAS))
        where.append(f"trim(emp.natureza_juridica) NOT IN ({mei_vals})")

    if palavra:
        p = palavra.replace("'", "''").strip().lower()
        if p:
            where.append(
                "("
                f"lower(trim(coalesce(e.nome_fantasia, ''))) LIKE '%{p}%' OR "
                f"lower(trim(coalesce(emp.razao_social, ''))) LIKE '%{p}%'"
                ")"
            )

    where_sql = " AND ".join(where) if where else "1=1"
    limit = _sanitize_int(limit, default=200000, min_value=1, max_value=SEARCH_MAX_LIMIT)
    offset = _sanitize_int(offset, default=0, min_value=0, max_value=5_000_000)

    if Path(MUNI_PATTERN).exists():
        muni_cte = f"""
    ,
    muni AS (
        SELECT * FROM read_parquet('{MUNI_PATTERN}')
    )"""
        cidade_expr = "coalesce(nullif(trim(m.descricao), ''), trim(e.municipio), '')"
        muni_join = "LEFT JOIN muni m ON trim(e.municipio) = trim(m.codigo)"
    else:
        muni_cte = ""
        cidade_expr = "trim(e.municipio)"
        muni_join = ""

    return f"""
    WITH est AS (
        SELECT * FROM read_parquet('{EST_PATTERN}')
    ),
    emp AS (
        SELECT * FROM read_parquet('{EMP_PATTERN}')
    )
    {muni_cte}
    SELECT
        trim(e.cnpj_basico || e.cnpj_ordem || e.cnpj_dv) AS cnpj,
        coalesce(nullif(trim(e.nome_fantasia), ''), nullif(trim(emp.razao_social), ''), '-') AS nome,
        CASE trim(e.situacao_cadastral)
            WHEN '01' THEN 'NULA'
            WHEN '02' THEN 'ATIVA'
            WHEN '03' THEN 'SUSPENSA'
            WHEN '04' THEN 'INAPTA'
            WHEN '05' THEN 'ATIVA'
            WHEN '08' THEN 'BAIXADA'
            ELSE trim(e.situacao_cadastral)
        END AS situacao,
        regexp_replace(e.cnae_principal, '[^0-9]', '', 'g') AS cnae,
        trim(upper(e.uf)) AS uf,
        {cidade_expr} AS cidade,
        trim(e.municipio) AS municipio_cod,
        CASE
            WHEN nullif(trim(e.ddd_1), '') IS NOT NULL AND nullif(trim(e.telefone_1), '') IS NOT NULL THEN '(' || trim(e.ddd_1) || ') ' || trim(e.telefone_1)
            WHEN nullif(trim(e.telefone_1), '') IS NOT NULL THEN trim(e.telefone_1)
            ELSE ''
        END AS telefone,
        lower(trim(e.correio_eletronico)) AS email,
        trim(coalesce(e.tipo_logradouro, '') || ' ' || coalesce(e.logradouro, '') || ', ' || coalesce(e.numero, '')) AS end,
        trim(emp.natureza_juridica) AS natureza_juridica,
        trim(emp.porte_empresa) AS porte_empresa,
        CASE WHEN trim(emp.natureza_juridica) IN ({','.join("'"+v+"'" for v in sorted(MEI_NATUREZAS))}) THEN 'SIM' ELSE 'NAO' END AS mei
    FROM est e
    LEFT JOIN emp ON trim(e.cnpj_basico) = trim(emp.cnpj_basico)
    {muni_join}
    WHERE {where_sql}
    ORDER BY uf, cidade, nome
    LIMIT {limit}
    OFFSET {offset}
    """


def _normalize_cnae_modo(value):
    value = str(value or '').strip().lower()
    return 'primario' if value in {'primario', 'principal', 'primary', 'somente_primario', '1', 'true', 'sim'} else 'todos'


BASE_CACHE_SITUACOES = ['ATIVA', 'SUSPENSA', 'INAPTA', 'BAIXADA', 'NULA']


def _base_uf_cache_key(cnaes, uf, situacoes, palavra, cnae_modo, limit, cidades=None):
    return _search_cache_key(
        cnaes,
        [uf],
        BASE_CACHE_SITUACOES,
        False,
        palavra or '',
        cnae_modo=cnae_modo,
        limit=limit,
        offset=0,
        tipo='uf_base_inclui_mei_v2'
    )


def _execute_search_rows(cnaes, ufs, situacoes, excluir_mei=False, palavra='', limit=200000, offset=0, cnae_modo='todos', cidades=None):
    used_index = False
    last_index_error = ''
    cur = None
    if fast_index_available():
        try:
            conn = get_index_db()
            sql = build_search_sql_indexed(
                cnaes,
                ufs,
                situacoes,
                excluir_mei=excluir_mei,
                palavra=palavra,
                limit=limit,
                offset=offset,
                cnae_modo=cnae_modo,
                cidades=cidades
            )
            cur = conn.execute(sql)
            used_index = True
        except Exception as idx_err:
            last_index_error = str(idx_err)[:160]
            cur = None
    if cur is None:
        conn = get_db()
        sql = build_search_sql(
            cnaes,
            ufs,
            situacoes,
            excluir_mei=excluir_mei,
            palavra=palavra,
            limit=limit,
            offset=offset,
            cnae_modo=cnae_modo
        )
        cur = conn.execute(sql)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    out = []
    seen_cnpj = set()
    for row in rows:
        item = dict(zip(cols, row))
        cnpj = str(item.get('cnpj') or '').strip()
        if cnpj and cnpj in seen_cnpj:
            continue
        if cnpj:
            seen_cnpj.add(cnpj)
        out.append(item)
    return out, ('fast_index' if used_index else 'parquet_fallback'), last_index_error


def _filter_rows_memory(rows, excluir_mei=False, cidades=None, situacoes=None):
    city_set = {_normalizar(c) for c in (cidades or []) if str(c or '').strip()}
    sit_set = {str(s or '').strip().upper() for s in (situacoes or []) if str(s or '').strip()}
    if not excluir_mei and not city_set and not sit_set:
        return list(rows or [])
    out = []
    for r in rows or []:
        if excluir_mei and str((r or {}).get('mei') or '').strip().upper() == 'SIM':
            continue
        if sit_set and str((r or {}).get('situacao') or '').strip().upper() not in sit_set:
            continue
        if city_set and _normalizar((r or {}).get('cidade') or (r or {}).get('municipio') or '') not in city_set:
            continue
        out.append(r)
    return out


def _sort_search_rows(rows, palavra=''):
    termo = _normalizar(palavra or '')
    tokens = [t for t in re.split(r'\s+', termo) if t]
    if not tokens:
        return sorted(rows or [], key=lambda r: (str(r.get('uf') or ''), str(r.get('cidade') or ''), str(r.get('nome') or '')))

    def score(row):
        nome = _normalizar((row or {}).get('nome') or '')
        cidade = _normalizar((row or {}).get('cidade') or '')
        email = _normalizar((row or {}).get('email') or '')
        hay = ' '.join([nome, cidade, email, str((row or {}).get('cnae') or '')])
        if nome == termo:
            rank = 0
        elif nome.startswith(termo):
            rank = 1
        elif termo in nome:
            rank = 2
        elif all(t in nome for t in tokens):
            rank = 3
        elif all(t in hay for t in tokens):
            rank = 4
        else:
            rank = 9
        return (rank, nome, str((row or {}).get('uf') or ''), cidade)

    return sorted(rows or [], key=score)


_IBGE_CIDADES_UF_CACHE = {}


def _dedup_sort_cidades(cidades, termo=''):
    termo_norm = _normalizar(termo or '')
    out = []
    seen = set()
    for cidade in cidades or []:
        nome = str(cidade or '').strip()
        if not nome or re.fullmatch(r'\d+', nome):
            continue
        if termo_norm and termo_norm not in _normalizar(nome):
            continue
        key = _normalizar(nome)
        if key in seen:
            continue
        seen.add(key)
        out.append(nome)
    return sorted(out, key=lambda x: _normalizar(x))


def _fetch_cidades_ibge_por_ufs(ufs, termo=''):
    """Lista TODAS as cidades oficiais dos estados selecionados via IBGE.
    Isso não depende da busca de CNPJs nem do cache de resultados.
    """
    cidades = []
    for uf in sorted(set(clean_uf_list(ufs))):
        cached = _IBGE_CIDADES_UF_CACHE.get(uf)
        if cached is None:
            try:
                req = Request(
                    f'https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios?orderBy=nome',
                    headers={'User-Agent': 'ProspectaX/4.0'}
                )
                with urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode('utf-8', errors='ignore'))
                cached = [str(item.get('nome') or '').strip() for item in (data or []) if str(item.get('nome') or '').strip()]
                _IBGE_CIDADES_UF_CACHE[uf] = cached
            except Exception:
                cached = []
        cidades.extend(cached or [])
    return _dedup_sort_cidades(cidades, termo=termo)


def _query_cidades_local_por_ufs(ufs, termo=''):
    ufs = clean_uf_list(ufs)
    if not ufs:
        return []
    uf_vals = ','.join(_sql_quote(v) for v in sorted(set(ufs)))
    rows = []

    # 1) Banco rápido completo, se existir
    if fast_db_available():
        try:
            conn = get_fast_db()
            sql = f"""
            SELECT DISTINCT
                trim(upper(e.uf)) AS uf,
                trim(coalesce(nullif(m.descricao,''), e.municipio, '')) AS cidade
            FROM px_estabelecimentos e
            LEFT JOIN px_municipios m
              ON lpad(regexp_replace(trim(e.municipio),'[^0-9]','','g'),4,'0')
               = lpad(regexp_replace(trim(m.codigo),'[^0-9]','','g'),4,'0')
            WHERE trim(upper(e.uf)) IN ({uf_vals})
            """
            rows = conn.execute(sql).fetchall()
        except Exception:
            rows = []

    # 2) Índice rápido, se existir
    if not rows and fast_index_available():
        try:
            conn = get_index_db()
            if Path(MUNI_PATTERN).exists():
                sql = f"""
                SELECT DISTINCT
                    trim(upper(p.uf)) AS uf,
                    trim(coalesce(nullif(m.descricao,''), nullif(p.cidade,''), p.municipio_cod, '')) AS cidade
                FROM prospects_idx p
                LEFT JOIN read_parquet({_sql_quote(MUNI_PATTERN)}) m
                  ON lpad(regexp_replace(trim(p.municipio_cod),'[^0-9]','','g'),4,'0')
                   = lpad(regexp_replace(trim(m.codigo),'[^0-9]','','g'),4,'0')
                WHERE trim(upper(p.uf)) IN ({uf_vals})
                """
            else:
                sql = f"""
                SELECT DISTINCT
                    trim(upper(p.uf)) AS uf,
                    trim(coalesce(nullif(p.cidade,''), p.municipio_cod, '')) AS cidade
                FROM prospects_idx p
                WHERE trim(upper(p.uf)) IN ({uf_vals})
                """
            rows = conn.execute(sql).fetchall()
        except Exception:
            rows = []

    # 3) Parquet bruto
    if not rows:
        try:
            conn = get_db()
            if Path(MUNI_PATTERN).exists():
                muni_join_sql = f"""
                LEFT JOIN read_parquet({_sql_quote(MUNI_PATTERN)}) m
                  ON lpad(regexp_replace(trim(e.municipio),'[^0-9]','','g'),4,'0')
                   = lpad(regexp_replace(trim(m.codigo),'[^0-9]','','g'),4,'0')
                """
                cidade_expr = "trim(coalesce(nullif(m.descricao,''), e.municipio, ''))"
            else:
                muni_join_sql = ""
                cidade_expr = "trim(coalesce(e.municipio, ''))"
            sql = f"""
            SELECT DISTINCT
                trim(upper(e.uf)) AS uf,
                {cidade_expr} AS cidade
            FROM read_parquet({_sql_quote(EST_PATTERN)}) e
            {muni_join_sql}
            WHERE trim(upper(e.uf)) IN ({uf_vals})
            """
            rows = conn.execute(sql).fetchall()
        except Exception:
            rows = []

    return _dedup_sort_cidades([cidade for _, cidade in rows], termo=termo)


def _query_cidades_por_ufs(ufs, termo=''):
    # Para o filtro lateral, o correto é listar TODAS as cidades do estado,
    # não apenas cidades presentes nos CNPJs já carregados. Por isso o IBGE vem primeiro.
    cidades = _fetch_cidades_ibge_por_ufs(ufs, termo=termo)
    if cidades:
        return cidades
    return _query_cidades_local_por_ufs(ufs, termo=termo)


# ===== TUNEL ONLINE CLOUDFLARE =====
_TUNNEL_STATE = {
    'proc': None,
    'url': '',
    'started_at': 0.0,
    'last_error': '',
    'log': [],
}
_TUNNEL_LOCK = threading.Lock()

def _tunnel_cloudflared_path():
    nomes = ['cloudflared.exe', 'cloudflared']
    bases = []
    try:
        bases.append(BASE_DIR)
    except Exception:
        pass
    try:
        bases.append(APP_ROOT_DIR)
    except Exception:
        pass
    try:
        bases.append(Path.cwd())
    except Exception:
        pass

    for base in bases:
        for nome in nomes:
            p = Path(base) / nome
            if p.exists() and p.is_file():
                return p
    return None

def _tunnel_license_allowed():
    ok, dados, motivo = _licenca_status()
    if not ok:
        return False, motivo or 'Licença não ativada.'
    plano = str(dados.get('plano') or dados.get('tipo') or '').strip().lower()
    allowed = bool(dados.get('permitir_tunel') or dados.get('cloudflare_tunnel') or plano == 'empresa_tunel')
    if not allowed:
        return False, 'Esta licença não permite túnel online.'
    return True, ''

def _tunnel_public_payload():
    with _TUNNEL_LOCK:
        proc = _TUNNEL_STATE.get('proc')
        url = _TUNNEL_STATE.get('url') or ''
        running = bool(proc and proc.poll() is None)
        if proc and not running:
            _TUNNEL_STATE['proc'] = None
        return {
            'ok': True,
            'enabled': True,
            'running': running,
            'url': url if running else '',
            'started_at': _TUNNEL_STATE.get('started_at') or 0,
            'last_error': _TUNNEL_STATE.get('last_error') or '',
            'log_tail': list((_TUNNEL_STATE.get('log') or [])[-12:]),
        }

def _tunnel_reader(proc):
    import re
    padrao = re.compile(r'https://[-a-zA-Z0-9.]+\.trycloudflare\.com')
    try:
        for line in iter(proc.stdout.readline, ''):
            if not line:
                break
            line = str(line).strip()
            if not line:
                continue
            with _TUNNEL_LOCK:
                log = _TUNNEL_STATE.setdefault('log', [])
                log.append(line)
                if len(log) > 80:
                    del log[:-80]
                m = padrao.search(line)
                if m:
                    _TUNNEL_STATE['url'] = m.group(0)
                    _TUNNEL_STATE['last_error'] = ''
    except Exception as e:
        with _TUNNEL_LOCK:
            _TUNNEL_STATE['last_error'] = str(e)[:200]

def _tunnel_start():
    import subprocess
    allowed, motivo = _tunnel_license_allowed()
    if not allowed:
        return {'ok': False, 'error': motivo}

    exe = _tunnel_cloudflared_path()
    if not exe:
        return {'ok': False, 'error': 'cloudflared.exe não encontrado dentro do ProspectaX.'}

    with _TUNNEL_LOCK:
        proc = _TUNNEL_STATE.get('proc')
        if proc and proc.poll() is None:
            return _tunnel_public_payload()
        _TUNNEL_STATE.update({'proc': None, 'url': '', 'started_at': time.time(), 'last_error': '', 'log': []})

    url_local = f'http://127.0.0.1:{PORT}'
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        proc = subprocess.Popen(
            [str(exe), 'tunnel', '--url', url_local],
            cwd=str(exe.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=flags,
        )
    except Exception as e:
        with _TUNNEL_LOCK:
            _TUNNEL_STATE['last_error'] = str(e)[:200]
        return {'ok': False, 'error': 'Não foi possível iniciar o túnel: ' + str(e)[:160]}

    with _TUNNEL_LOCK:
        _TUNNEL_STATE['proc'] = proc

    threading.Thread(target=_tunnel_reader, args=(proc,), daemon=True).start()

    fim = time.time() + 18
    while time.time() < fim:
        with _TUNNEL_LOCK:
            if _TUNNEL_STATE.get('url'):
                return _tunnel_public_payload()
            if proc.poll() is not None:
                _TUNNEL_STATE['proc'] = None
                if not _TUNNEL_STATE.get('last_error'):
                    _TUNNEL_STATE['last_error'] = 'O cloudflared encerrou antes de gerar o link.'
                return {'ok': False, 'error': _TUNNEL_STATE.get('last_error')}
        time.sleep(0.25)

    payload = _tunnel_public_payload()
    payload['starting'] = True
    return payload

def _tunnel_stop():
    with _TUNNEL_LOCK:
        proc = _TUNNEL_STATE.get('proc')
        _TUNNEL_STATE['url'] = ''
        _TUNNEL_STATE['proc'] = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except Exception:
                proc.kill()
        except Exception:
            pass
    return {'ok': True, 'running': False, 'url': ''}

# ===== FIM TUNEL ONLINE CLOUDFLARE =====


# ===== TUNEL ONLINE CLOUDFLARE V2 =====
_TUNNEL2_STATE = {
    'proc': None,
    'url': '',
    'started_at': 0.0,
    'last_error': '',
    'log': [],
}
_TUNNEL2_LOCK = threading.RLock()

def _tunnel2_log_path():
    try:
        logs = BASE_DIR / 'logs'
    except Exception:
        logs = Path.cwd() / 'logs'
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return logs / 'cloudflared_tunnel_v2.log'

def _tunnel2_clean_text(text):
    try:
        return re.sub(r'\\x1b\\[[0-9;]*m', '', str(text or ''))
    except Exception:
        return str(text or '')

def _tunnel2_extract_url(text):
    text = _tunnel2_clean_text(text)
    m = re.search(r'https://[A-Za-z0-9][A-Za-z0-9.-]*\\.trycloudflare\\.com', text)
    return m.group(0) if m else ''

def _tunnel2_scan_log_for_url():
    try:
        lp = _tunnel2_log_path()
        if not lp.exists():
            return ''
        txt = lp.read_text(encoding='utf-8', errors='ignore')[-20000:]
        return _tunnel2_extract_url(txt)
    except Exception:
        return ''

def _tunnel2_cloudflared_path():
    nomes = ['cloudflared.exe', 'cloudflared']
    bases = []
    for value in [
        globals().get('BASE_DIR'),
        globals().get('APP_ROOT_DIR'),
        Path.cwd(),
    ]:
        try:
            if value:
                bases.append(Path(value))
        except Exception:
            pass

    expanded = []
    for b in bases:
        expanded.append(b)
        expanded.append(b / '_prospectax_app')
        try:
            expanded.append(b.parent)
        except Exception:
            pass

    seen = set()
    for base in expanded:
        try:
            key = str(base.resolve()).lower()
        except Exception:
            key = str(base).lower()
        if key in seen:
            continue
        seen.add(key)
        for nome in nomes:
            p = base / nome
            if p.exists() and p.is_file():
                return p
    return None

def _tunnel2_license_allowed():
    ok, dados, motivo = _licenca_status()
    if not ok:
        return False, motivo or 'Licença não ativada.'
    plano = str(dados.get('plano') or dados.get('tipo_plano') or dados.get('tipo') or '').strip().lower()
    allowed = bool(
        dados.get('permitir_tunel') or
        dados.get('cloudflare_tunnel') or
        plano == 'empresa_tunel'
    )
    if not allowed:
        return False, 'Esta licença não permite link online.'
    return True, ''

def _tunnel2_status():
    found = _tunnel2_scan_log_for_url()
    with _TUNNEL2_LOCK:
        if found:
            _TUNNEL2_STATE['url'] = found
            _TUNNEL2_STATE['last_error'] = ''
        proc = _TUNNEL2_STATE.get('proc')
        running = bool(proc and proc.poll() is None)
        if proc and not running:
            _TUNNEL2_STATE['proc'] = None
        return {
            'ok': True,
            'enabled': True,
            'running': running,
            'url': _TUNNEL2_STATE.get('url') or '',
            'started_at': _TUNNEL2_STATE.get('started_at') or 0,
            'last_error': _TUNNEL2_STATE.get('last_error') or '',
            'log_tail': list((_TUNNEL2_STATE.get('log') or [])[-25:]),
            'cloudflared_found': bool(_tunnel2_cloudflared_path()),
        }

def _tunnel2_reader(proc):
    lp = _tunnel2_log_path()
    try:
        for line in iter(proc.stdout.readline, ''):
            if not line:
                break
            line = str(line).rstrip()
            if not line:
                continue
            try:
                with lp.open('a', encoding='utf-8', errors='ignore') as f:
                    f.write(line + '\\n')
            except Exception:
                pass
            found = _tunnel2_extract_url(line)
            with _TUNNEL2_LOCK:
                log = _TUNNEL2_STATE.setdefault('log', [])
                log.append(_tunnel2_clean_text(line))
                if len(log) > 160:
                    del log[:-160]
                if found:
                    _TUNNEL2_STATE['url'] = found
                    _TUNNEL2_STATE['last_error'] = ''
    except Exception as e:
        with _TUNNEL2_LOCK:
            _TUNNEL2_STATE['last_error'] = str(e)[:250]

def _tunnel2_start():
    import subprocess
    allowed, motivo = _tunnel2_license_allowed()
    if not allowed:
        return {'ok': False, 'error': motivo}

    exe = _tunnel2_cloudflared_path()
    if not exe:
        return {'ok': False, 'error': 'cloudflared.exe não encontrado dentro do ProspectaX.'}

    with _TUNNEL2_LOCK:
        proc = _TUNNEL2_STATE.get('proc')
        if proc and proc.poll() is None:
            return _tunnel2_status()
        _TUNNEL2_STATE.update({'proc': None, 'url': '', 'started_at': time.time(), 'last_error': '', 'log': []})

    try:
        _tunnel2_log_path().write_text('', encoding='utf-8')
    except Exception:
        pass

    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    url_local = f'http://127.0.0.1:{PORT}'
    cmd = [str(exe), 'tunnel', '--no-autoupdate', '--url', url_local]

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(exe.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding='utf-8',
            errors='ignore',
            bufsize=1,
            creationflags=flags,
        )
    except Exception as e:
        with _TUNNEL2_LOCK:
            _TUNNEL2_STATE['last_error'] = str(e)[:250]
        return {'ok': False, 'error': 'Não foi possível iniciar o link online: ' + str(e)[:180]}

    with _TUNNEL2_LOCK:
        _TUNNEL2_STATE['proc'] = proc
        _TUNNEL2_STATE['started_at'] = time.time()

    threading.Thread(target=_tunnel2_reader, args=(proc,), daemon=True).start()

    fim = time.time() + 8
    while time.time() < fim:
        status = _tunnel2_status()
        if status.get('url'):
            return status
        if proc.poll() is not None:
            with _TUNNEL2_LOCK:
                _TUNNEL2_STATE['proc'] = None
                if not _TUNNEL2_STATE.get('last_error'):
                    _TUNNEL2_STATE['last_error'] = 'O cloudflared encerrou antes de gerar o link.'
            return {'ok': False, 'error': _TUNNEL2_STATE.get('last_error') or 'Falha ao iniciar o link online.'}
        time.sleep(0.35)

    status = _tunnel2_status()
    status['starting'] = True
    return status

def _tunnel2_stop():
    with _TUNNEL2_LOCK:
        proc = _TUNNEL2_STATE.get('proc')
        _TUNNEL2_STATE['url'] = ''
        _TUNNEL2_STATE['proc'] = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except Exception:
                proc.kill()
        except Exception:
            pass
    return {'ok': True, 'running': False, 'url': ''}

# ===== FIM TUNEL ONLINE CLOUDFLARE V2 =====


# ===== TUNEL ONLINE CLOUDFLARE V3 =====
import os as _px_t3_os
import re as _px_t3_re
import time as _px_t3_time
import subprocess as _px_t3_subprocess
import threading as _px_t3_threading
from pathlib import Path as _PX_T3_Path

_TUNNEL3_STATE = {'proc': None, 'url': '', 'started_at': 0.0, 'last_error': '', 'log_handle': None}
_TUNNEL3_LOCK = _px_t3_threading.RLock()

def _tunnel3_base_dir():
    for v in [globals().get('BASE_DIR'), globals().get('APP_ROOT_DIR'), _PX_T3_Path.cwd()]:
        try:
            if v:
                return _PX_T3_Path(v)
        except Exception:
            pass
    return _PX_T3_Path.cwd()

def _tunnel3_log_path():
    logs = _tunnel3_base_dir() / 'logs'
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return logs / 'cloudflared_tunnel_v3.log'

def _tunnel3_clean_text(text):
    text = str(text or '')
    try:
        text = _px_t3_re.sub(r'\x1b\[[0-9;]*m', '', text)
    except Exception:
        pass
    return text

def _tunnel3_find_url(text):
    text = _tunnel3_clean_text(text)
    urls = _px_t3_re.findall(r'https://[^\s|]+trycloudflare\.com', text)
    if urls:
        return urls[-1].strip().strip('|').strip()
    urls = _px_t3_re.findall(r'https://[A-Za-z0-9][A-Za-z0-9._-]*\.trycloudflare\.com', text)
    return urls[-1].strip() if urls else ''

def _tunnel3_read_log():
    lp = _tunnel3_log_path()
    if not lp.exists():
        return ''
    try:
        return lp.read_text(encoding='utf-8', errors='ignore')[-50000:]
    except Exception:
        try:
            return lp.read_text(errors='ignore')[-50000:]
        except Exception:
            return ''

def _tunnel3_cloudflared_path():
    nomes = ['cloudflared.exe', 'cloudflared']
    bases = []
    for v in [globals().get('BASE_DIR'), globals().get('APP_ROOT_DIR'), _PX_T3_Path.cwd()]:
        try:
            if v:
                p = _PX_T3_Path(v)
                bases += [p, p / '_prospectax_app', p.parent]
        except Exception:
            pass
    seen = set()
    for b in bases:
        try:
            key = str(b.resolve()).lower()
        except Exception:
            key = str(b).lower()
        if key in seen:
            continue
        seen.add(key)
        for nome in nomes:
            p = b / nome
            if p.exists() and p.is_file():
                return p
    return None

def _tunnel3_port():
    try:
        return int(globals().get('PORT') or globals().get('PORTA') or _px_t3_os.environ.get('PROSPECTAX_PORT') or 8765)
    except Exception:
        return 8765

def _tunnel3_license_allowed():
    try:
        ok, dados, motivo = _licenca_status()
    except Exception as e:
        return False, 'Não consegui validar a licença: ' + str(e)[:120]
    if not ok:
        return False, motivo or 'Licença não ativada.'
    plano = str(dados.get('plano') or dados.get('tipo_plano') or dados.get('tipo') or '').strip().lower()
    allowed = bool(dados.get('permitir_tunel') or dados.get('cloudflare_tunnel') or plano == 'empresa_tunel')
    if not allowed:
        return False, 'Esta licença não permite link online.'
    return True, ''

def _tunnel3_close_handle():
    h = _TUNNEL3_STATE.get('log_handle')
    if h:
        try:
            h.flush()
            h.close()
        except Exception:
            pass
    _TUNNEL3_STATE['log_handle'] = None

def _tunnel3_status():
    text = _tunnel3_read_log()
    found = _tunnel3_find_url(text)
    with _TUNNEL3_LOCK:
        if found:
            _TUNNEL3_STATE['url'] = found
            _TUNNEL3_STATE['last_error'] = ''
        proc = _TUNNEL3_STATE.get('proc')
        running = bool(proc and proc.poll() is None)
        if proc and not running:
            _TUNNEL3_STATE['proc'] = None
            _tunnel3_close_handle()
        tail = [l for l in _tunnel3_clean_text(text).splitlines()[-25:] if l.strip()]
        return {'ok': True, 'enabled': True, 'running': running, 'url': _TUNNEL3_STATE.get('url') or '', 'started_at': _TUNNEL3_STATE.get('started_at') or 0, 'last_error': _TUNNEL3_STATE.get('last_error') or '', 'cloudflared_found': bool(_tunnel3_cloudflared_path()), 'log_path': str(_tunnel3_log_path()), 'log_tail': tail}

def _tunnel3_stop():
    with _TUNNEL3_LOCK:
        proc = _TUNNEL3_STATE.get('proc')
        _TUNNEL3_STATE['proc'] = None
        _TUNNEL3_STATE['url'] = ''
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except Exception:
                proc.kill()
        except Exception:
            pass
    with _TUNNEL3_LOCK:
        _tunnel3_close_handle()
    return {'ok': True, 'running': False, 'url': ''}

def _tunnel3_start():
    allowed, motivo = _tunnel3_license_allowed()
    if not allowed:
        return {'ok': False, 'error': motivo}
    exe = _tunnel3_cloudflared_path()
    if not exe:
        return {'ok': False, 'error': 'cloudflared.exe não encontrado dentro do ProspectaX.'}

    with _TUNNEL3_LOCK:
        proc = _TUNNEL3_STATE.get('proc')
        if proc and proc.poll() is None:
            return _tunnel3_status()
        _tunnel3_close_handle()
        _TUNNEL3_STATE.update({'proc': None, 'url': '', 'started_at': _px_t3_time.time(), 'last_error': ''})

    lp = _tunnel3_log_path()
    try:
        lp.write_text('', encoding='utf-8')
    except Exception:
        pass

    flags = _px_t3_subprocess.CREATE_NO_WINDOW if _px_t3_os.name == 'nt' else 0
    url_local = 'http://127.0.0.1:%s' % _tunnel3_port()
    cmd = [str(exe), 'tunnel', '--no-autoupdate', '--url', url_local]

    try:
        log_handle = open(lp, 'a', encoding='utf-8', errors='ignore')
        log_handle.write('CMD: ' + ' '.join(cmd) + '\n')
        log_handle.flush()
        proc = _px_t3_subprocess.Popen(cmd, cwd=str(exe.parent), stdout=log_handle, stderr=_px_t3_subprocess.STDOUT, stdin=_px_t3_subprocess.DEVNULL, creationflags=flags)
    except Exception as e:
        try:
            log_handle.close()
        except Exception:
            pass
        with _TUNNEL3_LOCK:
            _TUNNEL3_STATE['last_error'] = str(e)[:250]
        return {'ok': False, 'error': 'Não foi possível iniciar o link online: ' + str(e)[:180]}

    with _TUNNEL3_LOCK:
        _TUNNEL3_STATE['proc'] = proc
        _TUNNEL3_STATE['log_handle'] = log_handle
        _TUNNEL3_STATE['started_at'] = _px_t3_time.time()

    fim = _px_t3_time.time() + 20
    while _px_t3_time.time() < fim:
        try:
            log_handle.flush()
        except Exception:
            pass
        st = _tunnel3_status()
        if st.get('url'):
            return st
        if proc.poll() is not None:
            with _TUNNEL3_LOCK:
                _TUNNEL3_STATE['proc'] = None
                _tunnel3_close_handle()
                if not _TUNNEL3_STATE.get('last_error'):
                    _TUNNEL3_STATE['last_error'] = 'O cloudflared encerrou antes de gerar o link.'
            return {'ok': False, 'error': _TUNNEL3_STATE.get('last_error') or 'Falha ao iniciar o link online.', 'log_path': str(lp)}
        _px_t3_time.sleep(0.5)

    st = _tunnel3_status()
    st['starting'] = True
    return st

# ===== FIM TUNEL ONLINE CLOUDFLARE V3 =====


# ===== LINK ONLINE FINAL 09 BACKEND =====
import os as _px_lo_os
import re as _px_lo_re
import time as _px_lo_time
import subprocess as _px_lo_subprocess
from pathlib import Path as _PX_LO_Path

_LINK_ONLINE_FINAL_STATE = {
    'proc': None,
    'url': '',
    'started_at': 0.0,
    'last_error': '',
    'log_handle': None,
}

def _lo_base_dir():
    for v in [globals().get('BASE_DIR'), globals().get('APP_ROOT_DIR'), _PX_LO_Path.cwd()]:
        try:
            if v:
                return _PX_LO_Path(v)
        except Exception:
            pass
    return _PX_LO_Path.cwd()

def _lo_log_path():
    logs = _lo_base_dir() / 'logs'
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return logs / 'link_online_cloudflared.log'

def _lo_read_log():
    p = _lo_log_path()
    if not p.exists():
        return ''
    try:
        return p.read_text(encoding='utf-8', errors='ignore')[-80000:]
    except Exception:
        try:
            return p.read_text(errors='ignore')[-80000:]
        except Exception:
            return ''

def _lo_clean(text):
    text = str(text or '')
    try:
        text = _px_lo_re.sub(r'\x1b\[[0-9;]*m', '', text)
    except Exception:
        pass
    return text

def _lo_extract_url(text):
    text = _lo_clean(text)
    patterns = [
        r'https://[A-Za-z0-9][A-Za-z0-9._-]*\.trycloudflare\.com',
        r'https://[^\s|]+trycloudflare\.com',
    ]
    for pat in patterns:
        urls = _px_lo_re.findall(pat, text)
        if urls:
            return urls[-1].strip().strip('|').strip()
    return ''

def _lo_cloudflared_path():
    nomes = ['cloudflared.exe', 'cloudflared']
    bases = []
    for v in [globals().get('BASE_DIR'), globals().get('APP_ROOT_DIR'), _PX_LO_Path.cwd()]:
        try:
            if v:
                p = _PX_LO_Path(v)
                bases.extend([p, p / '_prospectax_app', p.parent, p.parent / '_prospectax_app'])
        except Exception:
            pass
    seen = set()
    for b in bases:
        try:
            key = str(b.resolve()).lower()
        except Exception:
            key = str(b).lower()
        if key in seen:
            continue
        seen.add(key)
        for nome in nomes:
            p = b / nome
            if p.exists() and p.is_file():
                return p
    return None

def _lo_port():
    try:
        return int(globals().get('PORT') or globals().get('PORTA') or _px_lo_os.environ.get('PROSPECTAX_PORT') or 8765)
    except Exception:
        return 8765

def _lo_license_allowed():
    if not LICENSE_REQUIRED:
        return True, ''
    try:
        ok, dados, motivo = _licenca_status()
    except Exception as e:
        return False, 'Não consegui validar a licença do link online: ' + str(e)[:160]
    if not ok:
        return False, motivo or 'Licença não ativada.'
    plano = str(dados.get('plano') or dados.get('tipo_plano') or dados.get('tipo') or '').strip().lower()
    allowed = bool(dados.get('permitir_tunel') or dados.get('cloudflare_tunnel') or plano == 'empresa_tunel')
    if not allowed:
        return False, 'Esta licença não permite link online.'
    return True, ''

def _lo_close_handle():
    h = _LINK_ONLINE_FINAL_STATE.get('log_handle')
    if h:
        try:
            h.flush()
            h.close()
        except Exception:
            pass
    _LINK_ONLINE_FINAL_STATE['log_handle'] = None

def _lo_status():
    text = _lo_read_log()
    found = _lo_extract_url(text)
    if found:
        _LINK_ONLINE_FINAL_STATE['url'] = found
        _LINK_ONLINE_FINAL_STATE['last_error'] = ''
    proc = _LINK_ONLINE_FINAL_STATE.get('proc')
    running = bool(proc and proc.poll() is None)
    if proc and not running:
        _LINK_ONLINE_FINAL_STATE['proc'] = None
        _lo_close_handle()
    tail = [x for x in _lo_clean(text).splitlines()[-30:] if x.strip()]
    return {
        'ok': True,
        'running': running,
        'url': _LINK_ONLINE_FINAL_STATE.get('url') or '',
        'started_at': _LINK_ONLINE_FINAL_STATE.get('started_at') or 0,
        'last_error': _LINK_ONLINE_FINAL_STATE.get('last_error') or '',
        'cloudflared_found': bool(_lo_cloudflared_path()),
        'cloudflared_path': str(_lo_cloudflared_path() or ''),
        'log_path': str(_lo_log_path()),
        'log_tail': tail,
    }

def _lo_stop():
    proc = _LINK_ONLINE_FINAL_STATE.get('proc')
    _LINK_ONLINE_FINAL_STATE['proc'] = None
    _LINK_ONLINE_FINAL_STATE['url'] = ''
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except Exception:
                proc.kill()
        except Exception:
            pass
    _lo_close_handle()
    return {'ok': True, 'running': False, 'url': ''}

def _lo_start():
    allowed, motivo = _lo_license_allowed()
    if not allowed:
        return {'ok': False, 'error': motivo}

    exe = _lo_cloudflared_path()
    if not exe:
        return {'ok': False, 'error': 'cloudflared.exe não encontrado dentro do ProspectaX.'}

    proc = _LINK_ONLINE_FINAL_STATE.get('proc')
    if proc and proc.poll() is None:
        return _lo_status()

    _lo_close_handle()
    _LINK_ONLINE_FINAL_STATE.update({'proc': None, 'url': '', 'started_at': _px_lo_time.time(), 'last_error': ''})

    log_path = _lo_log_path()
    try:
        log_path.write_text('', encoding='utf-8')
    except Exception:
        pass

    port = _lo_port()
    local_url = 'http://127.0.0.1:%s' % port
    cmd = [str(exe), 'tunnel', '--no-autoupdate', '--protocol', 'http2', '--edge-ip-version', '4', '--url', local_url]
    flags = _px_lo_subprocess.CREATE_NO_WINDOW if _px_lo_os.name == 'nt' else 0

    try:
        h = open(log_path, 'a', encoding='utf-8', errors='ignore')
        h.write('INICIANDO LINK ONLINE\n')
        h.write('CMD: ' + ' '.join(cmd) + '\n')
        h.flush()
        proc = _px_lo_subprocess.Popen(
            cmd,
            cwd=str(exe.parent),
            stdout=h,
            stderr=_px_lo_subprocess.STDOUT,
            stdin=_px_lo_subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as e:
        try:
            h.close()
        except Exception:
            pass
        _LINK_ONLINE_FINAL_STATE['last_error'] = str(e)[:300]
        return {'ok': False, 'error': 'Não foi possível iniciar o Cloudflare: ' + str(e)[:200], 'log_path': str(log_path)}

    _LINK_ONLINE_FINAL_STATE['proc'] = proc
    remember_tunnel(BASE_DIR, proc.pid)
    _LINK_ONLINE_FINAL_STATE['log_handle'] = h
    _LINK_ONLINE_FINAL_STATE['started_at'] = _px_lo_time.time()

    deadline = _px_lo_time.time() + 25
    while _px_lo_time.time() < deadline:
        try:
            h.flush()
        except Exception:
            pass
        st = _lo_status()
        if st.get('url'):
            return st
        if proc.poll() is not None:
            _LINK_ONLINE_FINAL_STATE['proc'] = None
            _lo_close_handle()
            if not _LINK_ONLINE_FINAL_STATE.get('last_error'):
                _LINK_ONLINE_FINAL_STATE['last_error'] = 'O cloudflared encerrou antes de gerar o link.'
            return {'ok': False, 'error': _LINK_ONLINE_FINAL_STATE.get('last_error'), 'log_path': str(log_path), 'log_tail': st.get('log_tail', [])}
        _px_lo_time.sleep(0.5)

    st = _lo_status()
    st['starting'] = True
    return st

# ===== FIM LINK ONLINE FINAL 09 BACKEND =====


def _commercial_search_constraints(qs, sess):
    include = None
    exclude = []
    selected = set(filter(None, (qs.get('contatos', [''])[0] or '').split(',')))
    quick = qs.get('quick_status', ['todos'])[0]
    if selected or quick not in ('todos', 'favoritos', 'retorno_hoje'):
        records = load_contact_status_records(sess)
        statuses = _status_map_from_records(records)
        if quick == 'modificados':
            choices = {v for v in statuses.values() if v != 'novo'}
            selected = selected & choices if selected else choices
            include = []
        elif quick not in ('todos', 'favoritos', 'retorno_hoje'):
            selected = selected & {quick} if selected else {quick}
            include = []
        if 'novo' in selected:
            include = None
            exclude = [k for k, v in statuses.items() if v not in selected]
        elif selected:
            include = [k for k, v in statuses.items() if v in selected]
        else:
            include = []
    if 'lead_cnpjs' in qs:
        lead_ids = set(clean_cnpjs(qs['lead_cnpjs'][0].split(',')))
        include = sorted(lead_ids if include is None else lead_ids.intersection(include))
    return {'cnpjs': include, 'excluir_cnpjs': exclude, 'discovery': discovery_filters(qs)}


class Handler(BaseHTTPRequestHandler):

    def _export_search_csv(self, qs, sess):
        import csv
        import io
        started = False
        try:
            cnaes = clean_digits_list(qs.get('cnaes', [''])[0].split(','))
            if not cnaes or not fast_index_available():
                _write_json(self, {'error': 'Selecione CNAE e uma base indexada para exportar todos os resultados.'}, 400)
                return
            ufs = clean_uf_list(qs.get('ufs', [''])[0].split(','))
            situacoes = clean_situ_list(qs.get('situacoes', [''])[0].split(','))
            cities = [x.strip() for x in qs.get('cidades', [''])[0].split('|') if x.strip()]
            sql = build_search_sql_indexed(
                cnaes, ufs, situacoes,
                excluir_mei=qs.get('excluir_mei', ['0'])[0] == '1',
                palavra=qs.get('palavra', [''])[0], cnae_modo=qs.get('cnae_modo', ['todos'])[0],
                cidades=cities, paginate=False, **_commercial_search_constraints(qs, sess))
            statuses = _status_map_from_records(load_contact_status_records(sess))
            notes = load_notes(sess.get('usuario'))
            cur = get_index_db().execute(sql)
            cols = [col[0] for col in cur.description]
            self.send_response(200)
            self.send_header('Content-Type', 'text/csv; charset=utf-8')
            filename = 'prospeccao_' + ('_'.join(ufs) or 'Brasil') + '_' + time.strftime('%Y%m%d') + '.csv'
            self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            started = True
            self.wfile.write(b'\xef\xbb\xbf')
            buf = io.StringIO()
            writer = csv.writer(buf, delimiter=';', lineterminator='\r\n')
            writer.writerow(['Empresa', 'CNPJ', 'CNAE', 'Cidade', 'UF', 'Telefone', 'Email', 'Endereco', 'Situacao', 'Porte', 'MEI (base local)', 'Status comercial', 'Anotacao', 'CEP', 'Telefone 2', 'Data abertura', 'Bairro'])
            def cell(value):
                value = str(value or '')
                return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
            while True:
                batch = cur.fetchmany(2000)
                for row in batch:
                    d = dict(zip(cols, row))
                    cnpj = str(d.get('cnpj') or '')
                    formatted = f'{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}' if len(cnpj) == 14 else cnpj
                    writer.writerow([cell(v) for v in [d.get('nome'), formatted, d.get('cnae'), d.get('cidade'), d.get('uf'), d.get('telefone'), d.get('email'), d.get('end'), d.get('situacao'), d.get('porte_empresa'), d.get('mei'), statuses.get(cnpj, 'novo'), notes.get(cnpj, ''), d.get('cep'), d.get('telefone_2'), d.get('abertura'), d.get('bairro')]])
                self.wfile.write(buf.getvalue().encode('utf-8'))
                buf.seek(0)
                buf.truncate(0)
                if not batch:
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except Exception as exc:
            if not started:
                _write_json(self, {'error': 'Falha ao exportar: ' + str(exc)}, 500)
            else:
                # Interrompe o download em vez de inserir JSON em um CSV aparentemente valido.
                self.close_connection = True
                print('Exportacao interrompida:', type(exc).__name__)

    def _tok(self):
        return _get_token(self)

    def _sessao(self):
        return _validar_token(self._tok())

    def _set_headers(self, code=200, content_type='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def _redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        s = self._sessao()

        # ── Licença: rotas públicas e bloqueio antes do login/sistema ────
        if path in ('/ativar_licenca', '/ativar_licenca.html', '/ativar', '/licenca'):
            self.serve_static('/ativar_licenca.html')
            return

        if path == '/api/licenca/status':
            qs_lic = parse_qs(parsed.query)
            force_lic = str((qs_lic.get('force') or qs_lic.get('refresh') or ['0'])[0]).lower() in {'1','true','sim','yes'}
            ok, dados, motivo = _licenca_status(force=force_lic)
            payload = {
                'ok': True,
                'licenciada': bool(ok),
                'maquina_id_final': _machine_id(),
                'erro': motivo if not ok else '',
                'status': dados.get('status', ''),
                'cliente': dados.get('cliente', ''),
                'email': dados.get('email', ''),
                'cnpj': dados.get('cnpj', ''),
                'plano': dados.get('plano', dados.get('tipo', '')),
                'max_usuarios': dados.get('max_usuarios', dados.get('limite_subcontas', 1)),
                'limite_subcontas': dados.get('limite_subcontas', dados.get('max_usuarios', 1)),
                'permitir_tunel': bool(dados.get('permitir_tunel', False)),
                'company_id': dados.get('company_id', dados.get('empresa_id', '')),
                'empresa_nome': dados.get('empresa_nome', dados.get('company_name', '')),
                'admin_empresa_login': dados.get('admin_empresa_login', ''),
                'validacao_remota': _sanitize_license_public(dados.get('validacao_remota', {})),
            }
            self._set_headers(200)
            self.wfile.write(jdump(payload))
            return

        if LICENSE_REQUIRED and path != '/api/health':
            lic_ok, _, lic_motivo = _licenca_status()
            if not lic_ok:
                if path.startswith('/api/'):
                    self._set_headers(403)
                    self.wfile.write(jdump({'error': 'Licença não ativada', 'erro': lic_motivo, 'redirect': '/ativar_licenca'}))
                    return
                self._redirect('/ativar_licenca')
                return

        # Todas as APIs privadas exigem sessao antes de qualquer rota legada.
        if path.startswith('/api/') and path not in {'/api/health', '/api/licenca/status'} and not s:
            _write_json(self, {'error': 'Nao autenticado', 'redirect': '/login'}, 401)
            return

        # ── Rotas públicas / gate de autenticação ───────────────────────
        if path in ('/login', '/login.html'):
            if s:
                self.send_response(302); self.send_header('Location','/'); self.send_header('Content-Length','0'); self.end_headers(); return
            self.serve_static('/login.html')
            return

        if path in ('/', '/index.html'):
            if not s:
                self.send_response(302); self.send_header('Location','/login'); self.send_header('Content-Length','0'); self.end_headers(); return
            self.serve_static('/index.html')
            return

        if path in ('/admin', '/admin.html'):
            if not s:
                self.send_response(302); self.send_header('Location','/login'); self.send_header('Content-Length','0'); self.end_headers(); return
            if s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            self.serve_static('/admin.html')
            return

        if path == '/api/logout':
            token = self._tok()
            if token and token in _tokens_mem: del _tokens_mem[token]
            self._set_headers(200); self.wfile.write(jdump({'ok':True})); return

        if path == '/api/me':
            s = self._sessao()
            if not s: self._set_headers(401); self.wfile.write(jdump({'error':'Nao autenticado'})); return
            self._set_headers(200); self.wfile.write(jdump({'usuario':s['usuario'],'nome':s['nome'],'role':s['role'],'scope':s.get('scope'),'company_id':s.get('company_id'),'company_name':s.get('company_name'),'subcontas_limite':s.get('subcontas_limite',0),'is_global_admin':s.get('is_global_admin',False)})); return


        if path == '/api/tunel/status':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            allowed, motivo = _tunnel_license_allowed()
            if not allowed:
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'enabled': False, 'error': motivo})); return
            self._set_headers(200); self.wfile.write(jdump(_tunnel_public_payload())); return


        if path == '/api/tunel2/status':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            allowed, motivo = _tunnel2_license_allowed()
            if not allowed:
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'enabled': False, 'error': motivo})); return
            self._set_headers(200); self.wfile.write(jdump(_tunnel2_status())); return


        if path == '/api/tunel3/status':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            allowed, motivo = _tunnel3_license_allowed()
            if not allowed:
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'enabled': False, 'error': motivo})); return
            self._set_headers(200); self.wfile.write(jdump(_tunnel3_status())); return


        if path == '/api/link-online-final/status':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            allowed, motivo = _lo_license_allowed()
            if not allowed:
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'error': motivo})); return
            self._set_headers(200); self.wfile.write(jdump(_lo_status())); return

        if path == '/api/admin/users':
            s = self._sessao()
            if not s or s.get('role') != 'admin': self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            usuarios = _carregar_usuarios()
            empresas = _carregar_empresas()
            lista = []
            for u, d in usuarios.items():
                cid = d.get('company_id')
                if _is_company_admin(s) and not _is_global_admin(s) and cid != s.get('company_id'):
                    continue
                emp = empresas.get(cid, {}) if cid else {}
                lista.append({
                    'usuario': u,
                    'nome': d.get('nome', u),
                    'role': d.get('role', 'cliente'),
                    'scope': d.get('scope', 'seller'),
                    'ativo': d.get('ativo', True),
                    'empresa_id': cid,
                    'empresa_nome': emp.get('nome') or d.get('company_name') or '',
                    'subcontas_limite': emp.get('limite_subcontas', 0) if cid else 0,
                    'subcontas_usadas': _contar_subcontas_empresa(cid, usuarios) if cid else 0,
                })
            self._set_headers(200); self.wfile.write(jdump({'users': lista}))
            return

        if path == '/api/admin/companies':
            s = self._sessao()
            if not s or s.get('role') != 'admin': self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            empresas = _carregar_empresas()
            usuarios = _carregar_usuarios()
            lista = []
            for cid, emp in empresas.items():
                if _is_company_admin(s) and not _is_global_admin(s) and cid != s.get('company_id'):
                    continue
                lista.append({
                    'id': cid,
                    'nome': emp.get('nome', cid),
                    'ativo': emp.get('ativo', True),
                    'admin_user': emp.get('admin_user', ''),
                    'limite_subcontas': emp.get('limite_subcontas', 1),
                    'subcontas_usadas': _contar_subcontas_empresa(cid, usuarios),
                })
            self._set_headers(200); self.wfile.write(jdump({'companies': lista}))
            return

        # ── Protege todas as rotas /api/ ──────────────────────────────────
        if path.startswith('/api/') and path not in ('/api/health', '/api/licenca/status', '/api/china-suppliers/suggestions'):
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'error':'Nao autenticado','redirect':'/login'})); return
            if s.get('role') == 'cliente' and path.startswith('/api/config'):
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return

        if _radar_get(self, path, parse_qs(parsed.query), s or {}, globals()):
            return
        if _intelligence_get(self, path, parse_qs(parsed.query), s or {}, globals()):
            return
        if _business_get(self, path, parse_qs(parsed.query), s or {}, globals()):
            return
        if _partners_get(self, path, parse_qs(parsed.query), s or {}, globals()):
            return
        if _discovery_get(self, path, parse_qs(parsed.query), s or {}, globals()):
            return

        if path == '/api/apify/status':
            self._set_headers(200)
            self.wfile.write(jdump(_apify_usage_status()))
            return

        if path == '/api/enrichment-status':
            _write_json(self, {'providers': _FREE_COMPANY_APIS.status(), 'cache_days': 30}, 200)
            return

        if path == '/api/search-export':
            self._export_search_csv(parse_qs(parsed.query), s)
            return

        if path == '/api/china-suppliers/search':
            params = parse_qs(parsed.query)
            _discovery_get(self, '/api/products/search', params, s or {}, globals())
            return

        if path == '/api/china-suppliers/suggestions':
            qs = parse_qs(parsed.query)
            cnae = (qs.get('cnae', [''])[0] or '').strip()
            suggestions = _china_product_suggestions_for_cnae(cnae)
            self._set_headers(200)
            self.wfile.write(jdump({
                'ok': True,
                'cnae': re.sub(r'\D', '', cnae or ''),
                'suggestions': suggestions,
            }))
            return

        if path == '/api/cnae-search':
            qs = parse_qs(parsed.query)
            q = (qs.get('q', [''])[0] or '').strip()
            results = search_cnae(q) if q else []
            self._set_headers(200)
            self.wfile.write(jdump({'results': results}))
            return

        if path == '/api/health':
            ok = DATA_DIR.exists() and SITE_DIR.exists()
            payload = {
                'ok': ok,
                'base_dir': str(BASE_DIR),
                'data_dir': str(DATA_DIR),
                'site_dir': str(SITE_DIR),
                'fast_index': fast_index_available(), 'partners': True, 'business_enabled': True, 'intelligence_enabled': True,
                'tunnel_running': bool(_LINK_ONLINE_FINAL_STATE.get('proc') and _LINK_ONLINE_FINAL_STATE['proc'].poll() is None),
                'fast_db': fast_db_available(),
                'index_db': str(INDEX_DB_PATH),
            }
            self._set_headers(200)
            self.wfile.write(jdump(payload))
            return

        if path == '/api/contact-status':
            records = load_contact_status_records(s)
            self._set_headers(200)
            self.wfile.write(jdump({'statuses': _status_map_from_records(records), 'records': records}))
            return

        # ── DEBUG: retorna JSON bruto da API externa ──────────────────────
        if path.startswith('/api/cnpj-raw/'):
            cnpj_raw = path.split('/api/cnpj-raw/')[-1]
            cnpj_limpo = normalize_cnpj(cnpj_raw)
            if len(cnpj_limpo) != 14:
                self._set_headers(400)
                self.wfile.write(jdump({'error': 'CNPJ deve ter 14 digitos'}))
                return
            try:
                req = Request(
                    f'https://publica.cnpj.ws/cnpj/{cnpj_limpo}',
                    headers={'User-Agent': 'ProspectorBramais/1.0'}
                )
                with urlopen(req, timeout=10) as resp:
                    raw_data = json.loads(resp.read().decode('utf-8'))
                self._set_headers(200)
                self.wfile.write(jdump(raw_data))
            except Exception as e:
                self._set_headers(502)
                self.wfile.write(jdump({'error': str(e)}))
            return

        # ── Salvar configurações de API ───────────────────────────────────
        if parsed.path == '/api/config':
            global ANTHROPIC_API_KEY, GOOGLE_API_KEY, GOOGLE_CX, INFOSIMPLES_TOKEN, APIFY_TOKEN, GEOAPIFY_API_KEY, CEPABERTO_TOKEN
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                if data.get('anthropic_key'):
                    ANTHROPIC_API_KEY = data['anthropic_key']
                if data.get('google_key'):
                    GOOGLE_API_KEY = data['google_key']
                if data.get('google_cx'):
                    GOOGLE_CX = data['google_cx']
                if data.get('infosimples_token'):
                    INFOSIMPLES_TOKEN = data['infosimples_token']
                if data.get('apify_token'):
                    APIFY_TOKEN = data['apify_token']
                if data.get('geoapify_key'):
                    GEOAPIFY_API_KEY = data['geoapify_key']
                if data.get('cepaberto_token'):
                    CEPABERTO_TOKEN = data['cepaberto_token']
                self._set_headers(200)
                self.wfile.write(jdump({'ok': True,
                    'anthropic': bool(ANTHROPIC_API_KEY),
                    'google': bool(GOOGLE_API_KEY and GOOGLE_CX),
                    'infosimples': bool(INFOSIMPLES_TOKEN),
                    'apify': bool(APIFY_TOKEN),
                    'geoapify': bool(GEOAPIFY_API_KEY),
                    'cepaberto': bool(CEPABERTO_TOKEN),
                }))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(jdump({'error': str(e)}))
            return

        # ── Google Custom Search — site e redes sociais ────────────────────
        if parsed.path == '/api/google-search':
            params = parse_qs(parsed.query)
            query = unquote(params.get('q', [''])[0])
            if not query or not GOOGLE_API_KEY or not GOOGLE_CX:
                self._set_headers(200)
                self.wfile.write(jdump({'error': 'Google API não configurada', 'items': []}))
                return
            try:
                url = (f'https://www.googleapis.com/customsearch/v1'
                       f'?key={GOOGLE_API_KEY}&cx={GOOGLE_CX}'
                       f'&q={urllib.parse.quote(query)}&num=3')
                req = Request(url, headers={'User-Agent': 'ProspectaX/1.0'})
                with urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                items = []
                for item in data.get('items', [])[:3]:
                    items.append({
                        'title': item.get('title',''),
                        'link':  item.get('link',''),
                        'snippet': item.get('snippet',''),
                    })
                self._set_headers(200)
                self.wfile.write(jdump({'items': items}))
            except Exception as e:
                self._set_headers(200)
                self.wfile.write(jdump({'error': str(e), 'items': []}))
            return

        # ── Infosimples — CND / Dívida Ativa ─────────────────────────────────
        if parsed.path.startswith('/api/cnd/'):
            cnpj_raw = path.split('/api/cnd/')[-1]
            cnpj_limpo = normalize_cnpj(cnpj_raw)
            if not INFOSIMPLES_TOKEN:
                self._set_headers(200)
                self.wfile.write(jdump({'error': 'Infosimples não configurada', 'configured': False}))
                return
            try:
                url = 'https://api.infosimples.com/api/v2/consultas/receita-federal/pgfn/cnd'
                body = json.dumps({
                    'token': INFOSIMPLES_TOKEN,
                    'cnpj': cnpj_limpo,
                    'timeout': 600,
                }).encode('utf-8')
                req = Request(url, data=body, headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'ProspectaX/1.0',
                }, method='POST')
                with urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                self._set_headers(200)
                self.wfile.write(jdump({'configured': True, 'data': data}))
            except Exception as e:
                self._set_headers(200)
                self.wfile.write(jdump({'configured': True, 'error': str(e)}))
            return

        # ── IA Pitch via Claude API ────────────────────────────────────────
        if parsed.path == '/api/pitch':
            _write_json(self, {'error': 'Use POST para gerar o roteiro gratuito.'}, 405)
            return

        if parsed.path == '/api/municipio':
            params = parse_qs(parsed.query)
            nome = unquote(params.get('nome', [''])[0])
            uf   = unquote(params.get('uf', [''])[0])
            cod  = unquote(params.get('cod', [''])[0])  # codigo do municipio da Receita
            if not uf:
                self._set_headers(400)
                self.wfile.write(jdump({'error': 'Informe uf'}))
                return
            # Se tiver código da Receita, tenta mapear para código IBGE
            dados_muni = fetch_dados_municipio(nome, uf)
            if dados_muni.get('codigo_ibge'):
                perfil = fetch_perfil_municipio(dados_muni['codigo_ibge'])
                dados_muni.update(perfil)
            self._set_headers(200)
            self.wfile.write(jdump(dados_muni))
            return

        # ── Concorrentes na mesma cidade ───────────────────────────────────────
        if parsed.path == '/api/concorrentes':
            params = parse_qs(parsed.query)
            cnae   = re.sub(r'\D', '', unquote(params.get('cnae', [''])[0]))
            muni   = unquote(params.get('municipio', [''])[0])
            uf     = unquote(params.get('uf', [''])[0])
            if not cnae:
                self._set_headers(400)
                self.wfile.write(jdump({'error': 'Informe cnae'}))
                return
            concorrentes = buscar_concorrentes(cnae, muni, uf)
            self._set_headers(200)
            self.wfile.write(jdump({'concorrentes': concorrentes, 'total': len(concorrentes)}))
            return

        # ── Filiais da empresa ─────────────────────────────────────────────────
        if parsed.path.startswith('/api/filiais/'):
            cnpj_basico = path.split('/api/filiais/')[-1][:8]
            result = contar_filiais(cnpj_basico)
            self._set_headers(200)
            self.wfile.write(jdump(result))
            return

        # ── Dívida ativa PGFN ─────────────────────────────────────────────
        if path.startswith('/api/divida/'):
            cnpj_raw = path.split('/api/divida/')[-1]
            cnpj_limpo = normalize_cnpj(cnpj_raw)
            if len(cnpj_limpo) != 14:
                self._set_headers(400)
                self.wfile.write(jdump({'error': 'CNPJ deve ter 14 digitos'}))
                return
            resultado = fetch_divida_ativa(cnpj_limpo)
            self._set_headers(200)
            self.wfile.write(jdump(resultado))
            return



        # ── Enriquecimento por CNPJ — API primeiro, local como fallback ───────
        if path.startswith('/api/cnpj/'):
            cnpj_raw = path.split('/api/cnpj/')[-1]
            cnpj_limpo = normalize_cnpj(cnpj_raw)
            if len(cnpj_limpo) != 14:
                _write_json(self, {'error': 'CNPJ deve ter 14 digitos'}, 400)
                return

            usar_externo = os.environ.get('PROSPECTAX_CNPJ_EXTERNO', '1') != '0'
            qs_cnpj = parse_qs(parsed.query)
            quick_enrich = str((qs_cnpj.get('quick') or ['0'])[0]).lower() in ('1','true','sim','yes')
            full_enrich = str((qs_cnpj.get('full') or qs_cnpj.get('completo') or ['0'])[0]).lower() in ('1','true','sim','yes')

            refresh_enrich = str((qs_cnpj.get('refresh') or ['0'])[0]).lower() in ('1', 'true', 'sim')
            index_local = fetch_cnpj_index_local(cnpj_limpo)
            if index_local and (quick_enrich or not full_enrich):
                _write_json(self, index_local, 200)
                return
            if quick_enrich:
                _write_json(self, {'error': 'Sem dados rápidos na base local.'}, 404)
                return
            local = index_local or {}
            externo = {'error': 'Consulta externa desativada'}
            if usar_externo:
                externo = fetch_cnpj_externo(cnpj_limpo, refresh=refresh_enrich)
                if isinstance(externo, dict) and 'error' not in externo:
                    # Fonte cadastral completa tem precedencia sobre o nome resumido do indice.
                    externo = _merge_api_payload(externo, local)
                    _write_json(self, externo, 200)
                    return
            # Fallback offline somente na ficha completa, nunca nos previews da lista.
            if full_enrich and not index_local:
                complete_local = fetch_cnpj_local(cnpj_limpo)
                if complete_local and not complete_local.get('error'):
                    local = _anexar_socios_donos(cnpj_limpo, complete_local)
                    local['_detalhes_parciais'] = False
            if local and not local.get('error'):
                local['_enrichment_warning'] = externo.get('error', '')
                local['_enrich_version'] = 2
                _write_json(self, local, 200)
            else:
                _write_json(self, externo if isinstance(externo, dict) else {'error': 'CNPJ não encontrado.'}, 502)
            return

        # ── NOVO: notas por CNPJ ───────────────────────────────────────────
        if path == '/api/notes':
            self._set_headers(200)
            self.wfile.write(jdump({'notes': load_notes(s.get('usuario'))}))
            return

        if path == '/api/history':
            self._set_headers(200)
            self.wfile.write(jdump({'history': load_history(_team_history_owner(s))}))
            return

        if path == '/api/user-prefs':
            self._set_headers(200)
            self.wfile.write(jdump({'prefs': load_user_prefs(s.get('usuario'))}))
            return

        if path == '/api/team-status-rules':
            if not s or s.get('role') != 'admin' or not s.get('company_id'):
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            self._set_headers(200)
            self.wfile.write(jdump({'rules': load_team_status_rules(s.get('company_id')), 'team': s.get('company_name','')}))
            return

        if path == '/api/team-dashboard':
            if not s or s.get('role') != 'admin' or not s.get('company_id'):
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            self._set_headers(200)
            self.wfile.write(jdump({'team': s.get('company_name',''), 'sellers': _compute_team_dashboard(s.get('company_id'))}))
            return

        if path == '/api/saved-clients':
            clients = load_saved_clients(s)
            self._set_headers(200)
            self.wfile.write(jdump({'clients': clients, 'total': len(clients)}))
            return

        if path in {'/api/cidades-list', '/api/municipios-list'}:
            qs = parse_qs(parsed.query)
            ufs = clean_uf_list((qs.get('ufs', [''])[0]).split(','))
            termo = (qs.get('q', [''])[0] or '').strip()
            if not ufs:
                _write_json(self, {'cidades': [], 'municipios': []}, 200)
                return
            try:
                cidades = _query_cidades_por_ufs(ufs, termo=termo)
                _write_json(self, {'cidades': cidades, 'municipios': cidades, 'total': len(cidades)}, 200)
            except Exception as e:
                _write_json(self, {'cidades': [], 'municipios': [], 'total': 0, 'error': str(e)}, 200)
            return

        if path == '/api/localidade/regional':
            qs = parse_qs(parsed.query)
            cnaes = clean_digits_list((qs.get('cnaes', [''])[0]).split(','))
            ufs = clean_uf_list((qs.get('ufs', [''])[0]).split(','))
            situacoes = clean_situ_list((qs.get('situacoes', [''])[0]).split(','))
            excluir_mei = (qs.get('excluir_mei', ['0'])[0] or '0').strip() in {'1', 'true', 'True', 'sim', 'SIM'}
            cnae_modo = _normalize_cnae_modo((qs.get('cnae_modo', ['todos'])[0] or 'todos'))
            centro = (qs.get('centro', ['Maringa'])[0] or 'Maringa').strip()
            raio = qs.get('raio', ['50'])[0] or '50'
            if not situacoes:
                situacoes = ['ATIVA']
            try:
                payload = _locality_regional_payload(
                    cnaes=cnaes,
                    ufs=ufs or ['PR'],
                    situacoes=situacoes,
                    excluir_mei=excluir_mei,
                    cnae_modo=cnae_modo,
                    centro=centro,
                    raio_km=raio,
                )
                _write_json(self, payload, 200 if payload.get('ok') else 400)
            except Exception as e:
                _write_json(self, {'ok': False, 'error': str(e), 'cities': [], 'total': 0}, 500)
            return

        if path == '/api/localidade/proximidade':
            qs = parse_qs(parsed.query)
            cnaes = clean_digits_list((qs.get('cnaes', [''])[0]).split(','))
            ufs = clean_uf_list((qs.get('ufs', [''])[0]).split(','))
            situacoes = clean_situ_list((qs.get('situacoes', [''])[0]).split(','))
            excluir_mei = (qs.get('excluir_mei', ['0'])[0] or '0').strip() in {'1', 'true', 'True', 'sim', 'SIM'}
            cnae_modo = _normalize_cnae_modo((qs.get('cnae_modo', ['todos'])[0] or 'todos'))
            mode = (qs.get('mode', ['address'])[0] or 'address').strip()
            value = (qs.get('value', [''])[0] or '').strip()
            atividade = (qs.get('atividade', [''])[0] or '').strip()
            todas = (qs.get('todas', ['0'])[0] or '0').strip() in {'1', 'true', 'True', 'sim', 'SIM'}
            force_all = (qs.get('force_all', ['0'])[0] or '0').strip() in {'1', 'true', 'True', 'sim', 'SIM'}
            raio = qs.get('raio', ['5'])[0] or '5'
            limit = qs.get('limit', ['100'])[0] or '100'
            if not situacoes:
                situacoes = ['ATIVA']
            try:
                payload = _locality_proximity_payload(
                    mode=mode,
                    value=value,
                    cnaes=cnaes,
                    ufs=ufs,
                    situacoes=situacoes,
                    excluir_mei=excluir_mei,
                    cnae_modo=cnae_modo,
                    raio_km=raio,
                    limit=limit,
                    atividade=atividade,
                    todas=todas,
                    force_all=force_all,
                )
                _write_json(self, payload, 200 if payload.get('ok') else 400)
            except Exception as e:
                _write_json(self, {'ok': False, 'error': str(e), 'results': [], 'total': 0}, 500)
            return


        if path == '/api/search-progress':
            qs = parse_qs(parsed.query)
            job_id = (qs.get('job_id', [''])[0] or '').strip()
            _write_json(self, _search_progress_get(job_id), 200)
            return

        if path == '/api/search-cache-info':
            _ensure_search_cache_dir()
            arquivos = []
            for meta_file in sorted(SEARCH_CACHE_DIR.glob('*.meta.json'), key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    meta = json.loads(meta_file.read_text(encoding='utf-8'))
                    arquivos.append({
                        'arquivo': meta_file.name,
                        'created_at': meta.get('created_at'),
                        'rows': meta.get('rows', 0),
                        'params': meta.get('params', {}),
                    })
                except Exception:
                    continue
            self._set_headers(200)
            self.wfile.write(jdump({'cache_dir': str(SEARCH_CACHE_DIR), 'items': arquivos[:20]}))
            return

        if path == '/api/search':
            s = self._sessao() or {}
            qs = parse_qs(parsed.query)
            cnaes = clean_digits_list((qs.get('cnaes', [''])[0]).split(','))
            ufs = clean_uf_list((qs.get('ufs', [''])[0]).split(','))
            situacoes = clean_situ_list((qs.get('situacoes', [''])[0]).split(','))
            excluir_mei = (qs.get('excluir_mei', ['0'])[0] or '0').strip() in {'1', 'true', 'True', 'sim', 'SIM'}
            palavra = (qs.get('palavra', [''])[0] or '').strip()
            cnae_modo = _normalize_cnae_modo((qs.get('cnae_modo', ['todos'])[0] or 'todos'))
            cidades = [unquote(x).strip() for x in (qs.get('cidades', [''])[0] or '').split('|') if unquote(x).strip()]
            force_refresh = (qs.get('refresh', ['0'])[0] or '0').strip() in {'1', 'true', 'True', 'sim', 'SIM'}
            limit = _sanitize_int((qs.get('limit', ['200000'])[0] or '200000'), default=200000, min_value=1, max_value=SEARCH_MAX_LIMIT)
            offset = _sanitize_int((qs.get('offset', ['0'])[0] or '0'), default=0, min_value=0, max_value=5_000_000)
            cache_base = (qs.get('cache_base', ['0'])[0] or '0').strip() in {'1', 'true', 'True', 'sim', 'SIM'}
            job_id = (qs.get('job_id', [''])[0] or '').strip()
            if job_id:
                _search_progress_update(job_id, status='running', phase='Preparando busca...', percent=2, current=0, total=0, rows=0)
            if cache_base:
                # Busca primária nacional por CNAE: sempre ampla para reaproveitar o cache.
                # UF, situação, MEI e CNAE primário são filtros locais no frontend após o cache.
                ufs = []
                situacoes = ['ATIVA', 'SUSPENSA', 'INAPTA', 'BAIXADA', 'NULA']
                excluir_mei = False
                cnae_modo = 'todos'
                cidades = []
                palavra = ''
                offset = 0
                limit = SEARCH_MAX_LIMIT

            if not cnaes:
                _write_json(self, {'error': 'Informe pelo menos 1 CNAE'}, 400)
                return

            try:
                acquisition = discovery_filters(qs)
                if acquisition and (not fast_index_available() or cache_base):
                    raise ValueError('Os novos filtros precisam da busca paginada no indice local. A busca CNAE/UF existente continua disponivel.')
                if acquisition:
                    filter_sql(acquisition, _index_columns())
            except ValueError as exc:
                _write_json(self, {'error': str(exc)}, 400)
                return
            requested_ufs = sorted(set(ufs or ALL_UFS))
            if fast_index_available() and not cache_base:
                try:
                    commercial = _commercial_search_constraints(qs, s)
                    revision = database_revision(INDEX_DB_PATH)
                    if job_id:
                        _search_progress_update(job_id, status='running', phase='Buscando pagina no indice rapido...', percent=15, current=0, total=1, rows=0)
                    conn = get_index_db()
                    count_sql = build_search_sql_indexed(
                        cnaes, requested_ufs, situacoes, excluir_mei=excluir_mei,
                        palavra=palavra, limit=1, offset=0, cnae_modo=cnae_modo, count_only=True, cidades=cidades, count_by_uf=True, **commercial
                    )
                    if job_id:
                        _search_progress_update(job_id, status='running', phase='Calculando total exato por estado...', percent=35, current=0, total=len(requested_ufs), rows=0)
                    count_started = time.perf_counter()
                    count_rows, count_cache_hit = _EXACT_COUNT_CACHE.get((revision, count_sql), lambda: conn.execute(count_sql).fetchall(), refresh=force_refresh)
                    count_ms = int((time.perf_counter() - count_started) * 1000)
                    totais_uf = {str(uf or '').strip().upper(): int(total or 0) for uf, total in count_rows if str(uf or '').strip()}
                    total_antes_paginacao = sum(totais_uf.values())
                    if job_id:
                        _search_progress_update(job_id, status='running', phase='Buscando pagina de empresas...', percent=70, current=0, total=len(requested_ufs), rows=0, count_ms=count_ms)
                    query_started = time.perf_counter()
                    page_rows = []
                    remaining = limit
                    skip = offset
                    current_fetch = 0
                    for uf_page in requested_ufs:
                        uf_total = int(totais_uf.get(uf_page, 0) or 0)
                        if uf_total <= 0:
                            continue
                        if skip >= uf_total:
                            skip -= uf_total
                            continue
                        rows_sql = build_search_sql_indexed(
                            cnaes, [uf_page], situacoes, excluir_mei=excluir_mei,
                            palavra=palavra, limit=remaining, offset=skip, cnae_modo=cnae_modo, order_results=True, cidades=cidades, **commercial
                        )
                        def fetch_page():
                            cur = conn.execute(rows_sql)
                            cols = [d[0] for d in cur.description]
                            return [dict(zip(cols, row)) for row in cur.fetchall()]
                        if limit <= 1000:
                            fetched_part, _ = _PAGE_QUERY_CACHE.get((revision, rows_sql), fetch_page, refresh=force_refresh)
                        else:
                            fetched_part = fetch_page()
                        page_rows.extend(fetched_part)
                        current_fetch += 1
                        remaining = max(0, limit - len(page_rows))
                        skip = 0
                        if job_id:
                            _search_progress_update(job_id, status='running', phase=f'Buscando pagina em {uf_page}...', percent=min(86, 70 + current_fetch * 4), current=current_fetch, total=len(requested_ufs), rows=len(page_rows), count_ms=count_ms)
                        if remaining <= 0:
                            break
                    page_ms = int((time.perf_counter() - query_started) * 1000)
                    has_more = (offset + len(page_rows)) < total_antes_paginacao
                    if palavra:
                        page_rows = _sort_search_rows(page_rows, palavra=palavra)
                    if job_id:
                        _search_progress_update(job_id, status='running', phase='Mesclando status e preparando tela...', percent=88, current=1, total=1, rows=len(page_rows), page_ms=page_ms, count_ms=count_ms)
                    result = merge_live_client_state(page_rows, s)
                    payload = {
                        'rows': result,
                        'limit': limit,
                        'offset': offset,
                        'total': total_antes_paginacao,
                        'totais_uf': totais_uf,
                        'has_more': has_more,
                        'source': 'fast_index_page',
                        'index_db': str(INDEX_DB_PATH),
                        'cnae_modo': cnae_modo,
                        'total_estimado': False,
                        'timing': {
                            'page_ms': page_ms,
                            'count_ms': count_ms,
                            'total_ms': page_ms + count_ms,
                        },
                        'cache': {'hit': count_cache_hit, 'source': 'total_exato_reutilizado' if count_cache_hit else 'total_exato_calculado', 'cache_base': False},
                        'mei_filter_warning': 'Esta base antiga estima MEI pela natureza juridica. A consulta completa confirma a opcao MEI na fonte.' if excluir_mei else ''
                    }
                    if job_id:
                        _search_progress_update(job_id, status='done', phase='Busca concluida.', percent=100, current=1, total=1, rows=len(result), total_rows=total_antes_paginacao)
                    _write_json(self, payload, 200)
                    return
                except Exception as e:
                    if job_id:
                        _search_progress_update(job_id, status='error', phase=str(e)[:180], percent=100, error=str(e)[:300])
                    _write_json(self, {'error': str(e)}, 500)
                    return
            if job_id:
                tipo_busca = 'cache nacional do CNAE' if cache_base else 'busca filtrada'
                _search_progress_update(job_id, status='running', phase=f'Iniciando {tipo_busca} em {len(requested_ufs)} estado(s)...', percent=4, current=0, total=len(requested_ufs), rows=0)
            base_limit = max(limit, 200000)
            base_limit = min(base_limit, SEARCH_MAX_LIMIT)
            cache_situacoes = BASE_CACHE_SITUACOES

            try:
                all_base_rows = []
                missing_ufs = []
                cache_hits = 0
                fontes = set()
                warnings = []

                # Cache incremental por UF: sempre guarda a base incluindo MEI.
                # Depois, "Ocultar MEI" vira filtro em memória e não reconsulta o banco.
                for idx_uf, uf in enumerate(requested_ufs, 1):
                    if job_id:
                        pct = 5 + int((idx_uf - 1) * 15 / max(1, len(requested_ufs)))
                        _search_progress_update(job_id, status='running', phase=f'Verificando cache de {uf}...', percent=pct, current=idx_uf-1, total=len(requested_ufs), rows=len(all_base_rows))
                    base_key = _base_uf_cache_key(cnaes, uf, situacoes, palavra, cnae_modo, base_limit, cidades=cidades)
                    cached = None if force_refresh else load_search_cache(base_key)
                    if cached is not None and isinstance(cached, dict):
                        cached_params = ((cached.get('_cache_meta') or {}).get('params') or {})
                        if cached_params.get('cidades'):
                            cached = None
                    if cached is not None and isinstance(cached, dict):
                        all_base_rows.extend(cached.get('rows', []) or [])
                        cache_hits += 1
                        if cached.get('source'):
                            fontes.add(str(cached.get('source')))
                    else:
                        missing_ufs.append(uf)

                if job_id:
                    _search_progress_update(job_id, status='running', phase=f'Cache encontrado em {cache_hits} estado(s). Consultando {len(missing_ufs)} estado(s)...', percent=20, current=cache_hits, total=len(requested_ufs), rows=len(all_base_rows), cache_hits=cache_hits, missing_ufs=len(missing_ufs))

                if missing_ufs:
                    if job_id:
                        _search_progress_update(job_id, status='running', phase=f'Consultando base local de {len(missing_ufs)} estado(s)...', percent=35, current=cache_hits, total=len(requested_ufs), rows=len(all_base_rows), missing_ufs=len(missing_ufs))
                    batch_limit = min(SEARCH_MAX_LIMIT, max(base_limit, base_limit * max(1, len(missing_ufs))))
                    rows_missing, source_missing, warning_missing = _execute_search_rows(
                        cnaes,
                        missing_ufs,
                        cache_situacoes,
                        excluir_mei=False,
                        palavra=palavra,
                        limit=batch_limit,
                        offset=0,
                        cnae_modo=cnae_modo
                    )
                    fontes.add(source_missing)
                    if warning_missing:
                        warnings.append(warning_missing)
                    grouped_rows = {uf: [] for uf in missing_ufs}
                    for row in rows_missing:
                        uf_row = str((row or {}).get('uf') or '').strip().upper()
                        if uf_row in grouped_rows:
                            grouped_rows[uf_row].append(row)
                    for idx_missing, uf in enumerate(missing_ufs, 1):
                        rows_uf = grouped_rows.get(uf, [])
                        all_base_rows.extend(rows_uf)
                        if job_id:
                            pct = 35 + int(idx_missing * 45 / max(1, len(missing_ufs)))
                            _search_progress_update(job_id, status='running', phase=f'Salvando cache de {uf} ({len(rows_uf):,} registros)...'.replace(',', '.'), percent=min(84, pct), current=cache_hits+idx_missing, total=len(requested_ufs), rows=len(all_base_rows), uf=uf)
                        base_key = _base_uf_cache_key(cnaes, uf, situacoes, palavra, cnae_modo, base_limit, cidades=cidades)
                        save_search_cache(base_key, {
                            'rows': rows_uf,
                            'limit': base_limit,
                            'offset': 0,
                            'has_more': len(rows_uf) >= base_limit,
                            'complete': len(rows_uf) < base_limit,
                            'source': source_missing,
                            'index_db': str(INDEX_DB_PATH),
                            'cache': {'hit': False, 'key': base_key, 'dir': str(SEARCH_CACHE_DIR), 'source': source_missing, 'tipo': 'uf_base'}
                        }, {
                            'cnaes': cnaes,
                            'ufs': [uf],
                            'situacoes': cache_situacoes,
                            'excluir_mei': False,
                            'palavra': palavra,
                            'cidades': [],
                            'cnae_modo': cnae_modo,
                            'limit': base_limit,
                            'offset': 0,
                            'tipo': 'uf_base_inclui_mei_v2',
                            'source': source_missing,
                        })

                if job_id:
                    _search_progress_update(job_id, status='running', phase='Aplicando filtros e ordenando resultados...', percent=88, current=len(requested_ufs), total=len(requested_ufs), rows=len(all_base_rows))
                filtered_rows = _filter_rows_memory(all_base_rows, excluir_mei=excluir_mei, cidades=cidades, situacoes=situacoes)
                filtered_rows = _sort_search_rows(filtered_rows, palavra=palavra)
                total_antes_paginacao = len(filtered_rows)
                if cache_base:
                    page_rows = filtered_rows
                else:
                    page_rows = filtered_rows[offset:offset + limit]
                if job_id:
                    _search_progress_update(job_id, status='running', phase='Mesclando status, notas e histórico...', percent=94, current=len(requested_ufs), total=len(requested_ufs), rows=len(page_rows))
                result = merge_live_client_state(page_rows, s)
                source_label = '+'.join(sorted(fontes)) if fontes else 'cache'
                payload = {
                    'rows': result,
                    'limit': len(page_rows) if cache_base else limit,
                    'offset': offset,
                    'total': total_antes_paginacao,
                    'has_more': False if cache_base else ((offset + limit) < total_antes_paginacao),
                    'source': source_label,
                    'index_db': str(INDEX_DB_PATH),
                    'cnae_modo': cnae_modo,
                    'cache': {
                        'hit': bool(cache_hits and not missing_ufs),
                        'partial_hit': bool(cache_hits and missing_ufs),
                        'ufs_cache_hit': cache_hits,
                        'ufs_consultadas': len(missing_ufs),
                        'ufs_total': len(requested_ufs),
                        'dir': str(SEARCH_CACHE_DIR),
                        'source': 'uf_incremental',
                        'cache_base': bool(cache_base)
                    }
                }
                if warnings:
                    payload['index_warning'] = '; '.join(warnings[:3])
                if job_id:
                    _search_progress_update(job_id, status='done', phase='Busca concluída.', percent=100, current=len(requested_ufs), total=len(requested_ufs), rows=len(result), total_rows=total_antes_paginacao)
                _write_json(self, payload, 200)
            except Exception as e:
                if job_id:
                    _search_progress_update(job_id, status='error', phase=str(e)[:180], percent=100, error=str(e)[:300])
                _write_json(self, {'error': str(e)}, 500)
            return

        self.serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        commercial_paths = {'/api/contact-status', '/api/notes', '/api/history', '/api/contact-status-bulk', '/api/notes-bulk', '/api/user-prefs', '/api/team-status-rules', '/api/team-reset-statuses'}
        if path in commercial_paths:
            with _COMMERCIAL_WRITE_LOCK:
                return self._do_POST_impl()
        return self._do_POST_impl()

    def _do_POST_impl(self):
        parsed = urlparse(self.path)

        # ── Licença: ativação pública antes do login ──────────────────────
        if parsed.path == '/api/licenca/ativar':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
            except Exception:
                data = {}
            result = _ativar_licenca_remota(data.get('email', ''), data.get('cnpj', ''))
            self._set_headers(200 if result.get('ok') else 400)
            self.wfile.write(jdump(result))
            return

        if LICENSE_REQUIRED:
            lic_ok, _, lic_motivo = _licenca_status()
            if not lic_ok:
                self._set_headers(403)
                self.wfile.write(jdump({'error': 'Licença não ativada', 'erro': lic_motivo, 'redirect': '/ativar_licenca'}))
                return

        # ── Login ─────────────────────────────────────────────────────────
        if parsed.path == '/api/login':
            length = int(self.headers.get('Content-Length','0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                u = data.get('usuario','').strip()
                s = data.get('senha','')
                sess = _autenticar(u, s)
                if sess:
                    token = _sec.token_hex(32)
                    _tokens_mem[token] = {**sess, 'ts': time.time()}
                    self._set_headers(200)
                    self.wfile.write(jdump({'token':token,'usuario':sess['usuario'],'nome':sess['nome'],'role':sess['role']}))
                else:
                    self._set_headers(401)
                    self.wfile.write(jdump({'error':'Usuário ou senha incorretos'}))
            except Exception as e:
                self._set_headers(400); self.wfile.write(jdump({'error':str(e)}))
            return

        # ── Logout ────────────────────────────────────────────────────────
        if parsed.path == '/api/logout':
            token = self._tok()
            if token and token in _tokens_mem: del _tokens_mem[token]
            self._set_headers(200); self.wfile.write(jdump({'ok':True})); return

        sess = self._sessao()
        if not sess:
            _write_json(self, {'error': 'Nao autenticado', 'redirect': '/login'}, 401)
            return
        if _radar_post(self, parsed.path, sess, globals()):
            return
        if _intelligence_post(self, parsed.path, sess, globals()):
            return
        if _business_post(self, parsed.path, sess, globals()):
            return
        if _partners_post(self, parsed.path, sess, globals()):
            return
        if _discovery_post(self, parsed.path, sess, globals()):
            return

        if parsed.path == '/api/config':
            if not _is_global_admin(sess):
                _write_json(self, {'error': 'Acesso restrito ao administrador geral.'}, 403)
                return
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'error':'Nao autenticado','redirect':'/login'})); return
            global ANTHROPIC_API_KEY, GOOGLE_API_KEY, GOOGLE_CX, INFOSIMPLES_TOKEN, APIFY_TOKEN, GEOAPIFY_API_KEY, CEPABERTO_TOKEN
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                if data.get('anthropic_key'):
                    ANTHROPIC_API_KEY = data['anthropic_key']
                if data.get('google_key'):
                    GOOGLE_API_KEY = data['google_key']
                if data.get('google_cx'):
                    GOOGLE_CX = data['google_cx']
                if data.get('infosimples_token'):
                    INFOSIMPLES_TOKEN = data['infosimples_token']
                if data.get('apify_token'):
                    APIFY_TOKEN = data['apify_token']
                if data.get('geoapify_key'):
                    GEOAPIFY_API_KEY = data['geoapify_key']
                if data.get('cepaberto_token'):
                    CEPABERTO_TOKEN = data['cepaberto_token']
                self._set_headers(200)
                self.wfile.write(jdump({
                    'ok': True,
                    'anthropic': bool(ANTHROPIC_API_KEY),
                    'google': bool(GOOGLE_API_KEY and GOOGLE_CX),
                    'infosimples': bool(INFOSIMPLES_TOKEN),
                    'apify': bool(APIFY_TOKEN),
                    'geoapify': bool(GEOAPIFY_API_KEY),
                    'cepaberto': bool(CEPABERTO_TOKEN),
                }))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(jdump({'error': str(e)}))
            return


        if parsed.path == '/api/tunel/start':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            if s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'error': 'Apenas admin pode iniciar o link online.'})); return
            result = _tunnel_start()
            self._set_headers(200 if result.get('ok') else 400)
            self.wfile.write(jdump(result)); return

        if parsed.path == '/api/tunel/stop':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            if s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'error': 'Apenas admin pode desligar o link online.'})); return
            self._set_headers(200); self.wfile.write(jdump(_tunnel_stop())); return


        if parsed.path == '/api/tunel2/start':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            if s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'error': 'Apenas admin pode gerar o link online.'})); return
            result = _tunnel2_start()
            self._set_headers(200 if result.get('ok') else 400)
            self.wfile.write(jdump(result)); return

        if parsed.path == '/api/tunel2/stop':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            if s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'error': 'Apenas admin pode desligar o link online.'})); return
            self._set_headers(200); self.wfile.write(jdump(_tunnel2_stop())); return


        if parsed.path == '/api/tunel3/start':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            if s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'error': 'Apenas admin pode gerar o link online.'})); return
            result = _tunnel3_start()
            self._set_headers(200 if result.get('ok') else 400)
            self.wfile.write(jdump(result)); return

        if parsed.path == '/api/tunel3/stop':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            if s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'error': 'Apenas admin pode desligar o link online.'})); return
            self._set_headers(200); self.wfile.write(jdump(_tunnel3_stop())); return


        if parsed.path == '/api/link-online-final/start':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            if s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'error': 'Apenas admin pode gerar o link online.'})); return
            result = _lo_start()
            self._set_headers(200 if result.get('ok') else 400)
            self.wfile.write(jdump(result)); return

        if parsed.path == '/api/link-online-final/stop':
            s = self._sessao()
            if not s:
                self._set_headers(401); self.wfile.write(jdump({'ok': False, 'error': 'Nao autenticado'})); return
            if s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'ok': False, 'error': 'Apenas admin pode desligar o link online.'})); return
            self._set_headers(200); self.wfile.write(jdump(_lo_stop())); return

        if parsed.path == '/api/search-cache/clear':
            s = self._sessao()
            if not s or s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            total = clear_search_cache()
            self._set_headers(200); self.wfile.write(jdump({'ok': True, 'arquivos_removidos': total, 'cache_dir': str(SEARCH_CACHE_DIR)})); return

        # ── Admin: criar usuário ──────────────────────────────────────────
        if parsed.path == '/api/admin/users':
            s = self._sessao()
            if not s or s.get('role') != 'admin': self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            length = int(self.headers.get('Content-Length','0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                usuario = data.get('usuario','').strip()
                senha   = data.get('senha','')
                nome    = (data.get('nome', usuario) or usuario).strip()
                perfil  = (data.get('perfil','cliente') or 'cliente').strip()
                if not usuario or not senha: raise ValueError('usuario e senha obrigatorios')
                usuarios = _carregar_usuarios()
                existente = usuarios.get(usuario)
                if isinstance(existente, dict) and existente:
                    raise ValueError('Usuário já existe')

                if _is_company_admin(s) and not _is_global_admin(s):
                    if perfil != 'cliente':
                        raise ValueError('Admin da empresa pode criar apenas vendedores')
                    cid = s.get('company_id')
                    empresas = _carregar_empresas()
                    emp = empresas.get(cid) or {}
                    usados = _contar_subcontas_empresa(cid, usuarios)
                    limite = _sanitize_limit(emp.get('limite_subcontas', 1), 1)
                    if usados >= limite:
                        raise ValueError(f'Limite de subcontas atingido ({usados}/{limite})')
                    usuarios[usuario] = {
                        'nome': nome,
                        'senha': _phash(senha),
                        'role': 'cliente',
                        'scope': 'seller',
                        'company_id': cid,
                        'ativo': True,
                        'criado_por': s.get('usuario'),
                    }
                    _salvar_usuarios(usuarios)
                    self._set_headers(200); self.wfile.write(jdump({'ok':True,'empresa_id':cid,'empresa_nome':emp.get('nome','')}))
                    return

                if not _is_global_admin(s):
                    raise ValueError('Acesso negado')

                empresas = _carregar_empresas()
                company_id = data.get('empresa_id') or data.get('company_id') or ''
                company_name = (data.get('empresa_nome') or data.get('company_name') or '').strip()
                limite = _sanitize_limit(data.get('limite_subcontas', 1), 1)

                if perfil == 'admin':
                    company_id = _slug_company_id(company_id or company_name or usuario)
                    if company_id in empresas and empresas[company_id].get('admin_user') not in ('', usuario):
                        raise ValueError('Empresa já vinculada a outro administrador')
                    empresa = _garantir_empresa(company_id, nome=company_name or nome, limite_subcontas=limite, admin_user=usuario, criado_por=s.get('usuario'))
                    usuarios[usuario] = {
                        'nome': nome,
                        'senha': _phash(senha),
                        'role': 'admin',
                        'scope': 'company_admin',
                        'company_id': company_id,
                        'ativo': True,
                        'criado_por': s.get('usuario'),
                    }
                    _salvar_usuarios(usuarios)
                    self._set_headers(200); self.wfile.write(jdump({'ok':True,'empresa_id':company_id,'empresa_nome':empresa.get('nome'),'subcontas_limite':empresa.get('limite_subcontas',1)}))
                    return

                if company_id:
                    company_id = _slug_company_id(company_id)
                    emp = empresas.get(company_id)
                    if not emp:
                        raise ValueError('Empresa informada não existe')
                    usados = _contar_subcontas_empresa(company_id, usuarios)
                    limite = _sanitize_limit(emp.get('limite_subcontas', 1), 1)
                    if usados >= limite:
                        raise ValueError(f'Limite de subcontas atingido ({usados}/{limite})')
                usuarios[usuario] = {
                    'nome': nome,
                    'senha': _phash(senha),
                    'role': 'cliente',
                    'scope': 'seller',
                    'company_id': _slug_company_id(company_id) if company_id else None,
                    'ativo': True,
                    'criado_por': s.get('usuario'),
                }
                _salvar_usuarios(usuarios)
                self._set_headers(200); self.wfile.write(jdump({'ok':True,'empresa_id':_slug_company_id(company_id) if company_id else None}))
            except Exception as e:
                self._set_headers(400); self.wfile.write(jdump({'error':str(e)}))
            return


        if parsed.path == '/api/contact-status':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                cnpj = normalize_cnpj(data.get('cnpj', ''))
                status = (data.get('status', 'novo') or 'novo').strip()
                if not cnpj:
                    raise ValueError('CNPJ inválido')
                sess = self._sessao() or {}
                records = load_contact_status_records(sess)
                prev = _normalize_status_record(records.get(cnpj, {})) if cnpj in records else {'status': 'novo'}
                prev_status = prev.get('status', 'novo')
                is_company_admin = bool(sess.get('role') == 'admin' and sess.get('company_id') and sess.get('scope') != 'global_admin')
                if prev_status != 'novo' and prev.get('owner_login') and prev.get('owner_login') != sess.get('usuario') and not is_company_admin:
                    self._set_headers(403)
                    self.wfile.write(jdump({'error': f"Status bloqueado por {prev.get('owner_nome') or prev.get('owner_login')}", 'owner_login': prev.get('owner_login'), 'owner_nome': prev.get('owner_nome'), 'status': prev_status}))
                    return
                now_iso = time.strftime('%Y-%m-%dT%H:%M:%S')
                rec = {'status': status, 'updated_at': now_iso}
                if status != 'novo':
                    rec['owner_login'] = sess.get('usuario', '')
                    rec['owner_nome'] = sess.get('nome', sess.get('usuario', ''))
                    days = int(load_team_status_rules(sess.get('company_id')).get(status, 0) or 0) if sess.get('company_id') else 0
                    if days > 0:
                        rec['reset_at'] = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(time.time() + days * 86400))
                records[cnpj] = rec
                save_contact_status_records(sess, records)
                if prev_status != status:
                    _append_team_history(sess, cnpj, status, prev_status)
                saved = upsert_saved_client(cnpj, status=status, touched_from='status', sess=sess)
                self._set_headers(200)
                self.wfile.write(jdump({'ok': True, 'client': saved, 'record': rec, 'previous_status': prev_status}))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(jdump({'error': str(e)}))
            return

        # ── NOVO: salvar nota de um CNPJ ───────────────────────────────────
        if parsed.path == '/api/notes':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                cnpj = normalize_cnpj(data.get('cnpj', ''))
                nota = (data.get('nota', '') or '').strip()
                if not cnpj:
                    raise ValueError('CNPJ inválido')
                sess = self._sessao() or {}
                owner = sess.get('usuario')
                notes = load_notes(owner)
                if nota:
                    notes[cnpj] = nota
                else:
                    notes.pop(cnpj, None)
                save_notes(notes, owner)
                saved = upsert_saved_client(cnpj, nota_marker=True, nota_valor=nota, touched_from='note', sess=sess)
                self._set_headers(200)
                self.wfile.write(jdump({'ok': True, 'client': saved}))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(jdump({'error': str(e)}))
            return

        # ── NOVO: salvar historico ────────────────────────────────────────
        if parsed.path == '/api/history':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                sess = self._sessao() or {}
                owner = _team_history_owner(sess)
                history = data.get('history', {})
                save_history(history, owner)
                for cnpj in list(history.keys()):
                    upsert_saved_client(cnpj, touched_from='history', sess=sess)
                self._set_headers(200)
                self.wfile.write(jdump({'ok': True, 'total': len(history)}))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(jdump({'error': str(e)}))
            return


        # ── Bulk restore contact-status (backup) ──────────────────────────
        if parsed.path == '/api/contact-status-bulk':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                sess = self._sessao() or {}
                statuses = data.get('statuses', {})
                if not isinstance(statuses, dict):
                    raise ValueError('statuses deve ser um objeto')
                current = load_contact_status_records(sess)
                if sess.get('role') != 'admin':
                    for key, record in current.items():
                        if record.get('owner_login') and record['owner_login'] != sess.get('usuario'):
                            if key not in statuses or _normalize_status_record(statuses[key]).get('status') != record.get('status'):
                                raise ValueError('A restauracao nao pode substituir atendimentos de outro vendedor.')
                restored = {normalize_cnpj(k): _normalize_status_record(v) for k, v in statuses.items()}
                if sess.get('role') != 'admin':
                    for key, record in restored.items():
                        if key in current and current[key].get('owner_login') != sess.get('usuario'):
                            restored[key] = current[key]
                        elif record.get('status') != 'novo':
                            record['owner_login'] = sess.get('usuario')
                            record['owner_nome'] = sess.get('nome', sess.get('usuario'))
                save_contact_status_records(sess, restored)
                self._set_headers(200)
                self.wfile.write(jdump({'ok': True, 'count': len(statuses)}))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(jdump({'error': str(e)}))
            return

        # ── Bulk restore notes (backup) ────────────────────────────────────
        if parsed.path == '/api/notes-bulk':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                sess = self._sessao() or {}
                owner = sess.get('usuario')
                notes = data.get('notes', {})
                if not isinstance(notes, dict):
                    raise ValueError('notes deve ser um objeto')
                save_notes(notes, owner)
                self._set_headers(200)
                self.wfile.write(jdump({'ok': True, 'count': len(notes)}))
            except Exception as e:
                self._set_headers(400)
                self.wfile.write(jdump({'error': str(e)}))
            return


        if parsed.path == '/api/user-prefs':
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                sess = self._sessao() or {}
                prefs = load_user_prefs(sess.get('usuario'))
                cnaes = data.get('cnaes', prefs.get('cnaes', []))
                if not isinstance(cnaes, list):
                    raise ValueError('cnaes deve ser uma lista')
                prefs['cnaes'] = clean_digits_list(cnaes)
                save_user_prefs(sess.get('usuario'), prefs)
                self._set_headers(200); self.wfile.write(jdump({'ok': True, 'prefs': prefs}))
            except Exception as e:
                self._set_headers(400); self.wfile.write(jdump({'error': str(e)}))
            return

        if parsed.path == '/api/team-status-rules':
            s = self._sessao() or {}
            if not s or s.get('role') != 'admin' or not s.get('company_id'):
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                rules = data.get('rules', {})
                if not isinstance(rules, dict):
                    raise ValueError('rules deve ser um objeto')
                save_team_status_rules(s.get('company_id'), rules)
                self._set_headers(200); self.wfile.write(jdump({'ok': True, 'rules': load_team_status_rules(s.get('company_id'))}))
            except Exception as e:
                self._set_headers(400); self.wfile.write(jdump({'error': str(e)}))
            return

        if parsed.path == '/api/team-reset-statuses':
            s = self._sessao() or {}
            if not s or s.get('role') != 'admin' or not s.get('company_id'):
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            length = int(self.headers.get('Content-Length', '0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                statuses = data.get('statuses', [])
                if statuses and not isinstance(statuses, list):
                    raise ValueError('statuses deve ser uma lista')
                total = _reset_team_statuses(s.get('company_id'), statuses)
                self._set_headers(200); self.wfile.write(jdump({'ok': True, 'resetados': total}))
            except Exception as e:
                self._set_headers(400); self.wfile.write(jdump({'error': str(e)}))
            return

        # ── Admin: alterar usuário ────────────────────────────────────────
        import re as _re2
        m = _re2.match(r'^/api/admin/users/([^/]+)$', parsed.path)
        if m:
            s = self._sessao()
            if not s or s.get('role') != 'admin': self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            login_alvo = m.group(1)
            if login_alvo == 'admin' and not _is_global_admin(s): self._set_headers(400); self.wfile.write(jdump({'error':'Nao e possivel alterar o admin'})); return
            length = int(self.headers.get('Content-Length','0'))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            try:
                data = json.loads(raw)
                usuarios = _carregar_usuarios()
                if login_alvo not in usuarios: raise ValueError('Usuario nao encontrado')
                alvo = usuarios[login_alvo]
                pode_alterar_admin_geral = _is_global_admin(s) and login_alvo == s.get('usuario') == 'admin'
                pode_alterar_como_global = _is_global_admin(s) and login_alvo != 'admin'
                pode_alterar_como_empresa = _pode_gerir_usuario(s, login_alvo, alvo)
                if not (pode_alterar_admin_geral or pode_alterar_como_global or pode_alterar_como_empresa):
                    raise ValueError('Acesso negado para alterar este usuário')
                if login_alvo == 'admin':
                    campos_permitidos = {'senha', 'nome'}
                    extras = set(data.keys()) - campos_permitidos
                    if extras:
                        raise ValueError('No administrador geral só é permitido alterar nome e senha')
                if 'senha' in data and data['senha']:
                    alvo['senha'] = _phash(data['senha'])
                if 'nome' in data:
                    alvo['nome'] = data['nome']
                if 'ativo' in data:
                    alvo['ativo'] = bool(data['ativo'])
                if _is_global_admin(s) and 'perfil' in data and data['perfil']:
                    novo_perfil = data['perfil']
                    alvo['role'] = novo_perfil
                    alvo['scope'] = 'company_admin' if novo_perfil == 'admin' and alvo.get('company_id') else ('seller' if novo_perfil != 'admin' else alvo.get('scope','admin_legacy'))
                updated_company = None
                if _is_global_admin(s) and alvo.get('role') == 'admin' and alvo.get('company_id'):
                    empresas = _carregar_empresas()
                    cid = _slug_company_id(alvo.get('company_id'))
                    emp = dict(empresas.get(cid, {}) or {})
                    changed_emp = False
                    if 'limite_subcontas' in data:
                        novo_limite = _sanitize_limit(data.get('limite_subcontas', emp.get('limite_subcontas', 1)), emp.get('limite_subcontas', 1) or 1)
                        if emp.get('limite_subcontas') != novo_limite:
                            emp['limite_subcontas'] = novo_limite
                            alvo['subcontas_limite'] = novo_limite
                            changed_emp = True
                    if 'empresa_nome' in data and data.get('empresa_nome') is not None:
                        novo_nome = str(data.get('empresa_nome') or '').strip()
                        if novo_nome and emp.get('nome') != novo_nome:
                            emp['nome'] = novo_nome
                            alvo['company_name'] = novo_nome
                            changed_emp = True
                    if changed_emp or not emp:
                        emp.setdefault('id', cid)
                        emp.setdefault('ativo', True)
                        emp['admin_user'] = login_alvo
                        if not emp.get('nome'):
                            emp['nome'] = alvo.get('company_name') or cid
                        if not emp.get('limite_subcontas'):
                            emp['limite_subcontas'] = _sanitize_limit(alvo.get('subcontas_limite', 1), 1)
                        empresas[cid] = emp
                        _salvar_empresas(empresas)
                    updated_company = {
                        'company_id': cid,
                        'empresa_nome': emp.get('nome', ''),
                        'limite_subcontas': emp.get('limite_subcontas', 1),
                        'subcontas_usadas': _contar_subcontas_empresa(cid, usuarios),
                    }
                _salvar_usuarios(usuarios)
                if updated_company is None and alvo.get('company_id'):
                    empresas = _carregar_empresas()
                    cid = _slug_company_id(alvo.get('company_id'))
                    emp = dict(empresas.get(cid, {}) or {})
                    updated_company = {
                        'company_id': cid,
                        'empresa_nome': emp.get('nome') or alvo.get('company_name', ''),
                        'limite_subcontas': emp.get('limite_subcontas', alvo.get('subcontas_limite', 0)),
                        'subcontas_usadas': _contar_subcontas_empresa(cid, usuarios),
                    }
                self._set_headers(200); self.wfile.write(jdump({'ok':True, 'user': {'usuario': login_alvo, 'nome': alvo.get('nome', login_alvo), 'role': alvo.get('role', 'cliente'), 'scope': alvo.get('scope', 'seller'), 'ativo': alvo.get('ativo', True), **(updated_company or {})}}))
            except Exception as e:
                self._set_headers(400); self.wfile.write(jdump({'error':str(e)}))
            return

        # ── Admin: deletar usuário ───────────────────────────────────────
        import re as _re3
        m2 = _re3.match(r'^/api/admin/users/([^/]+)$', parsed.path)
        if m2 and self.command == 'DELETE':
            s = self._sessao()
            if not s or s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            login_del = m2.group(1)
            if login_del == 'admin':
                self._set_headers(400); self.wfile.write(jdump({'error':'Não é possível deletar o admin'})); return
            try:
                usuarios = _carregar_usuarios()
                alvo = usuarios.get(login_del)
                if not alvo:
                    self._set_headers(404); self.wfile.write(jdump({'error':'Usuário não encontrado'})); return
                if not _pode_gerir_usuario(s, login_del, alvo) and not (_is_global_admin(s) and login_del != 'admin'):
                    self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
                _delete_user(login_del)
                self._set_headers(200); self.wfile.write(jdump({'ok': True})); return
            except Exception as e:
                self._set_headers(400); self.wfile.write(jdump({'error': str(e)})); return

        # ── Admin: deletar usuário (POST) ────────────────────────────────
        import re as _re5
        m3 = _re5.match(r'^/api/admin/users/([^/]+)/delete$', parsed.path)
        if m3:
            s = self._sessao()
            if not s or s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            login_del = m3.group(1)
            if login_del == 'admin':
                self._set_headers(400); self.wfile.write(jdump({'error':'Não é possível deletar o admin'})); return
            try:
                usuarios = _carregar_usuarios()
                alvo = usuarios.get(login_del)
                if not alvo:
                    self._set_headers(404); self.wfile.write(jdump({'error':'Usuário não encontrado'})); return
                if not _pode_gerir_usuario(s, login_del, alvo) and not (_is_global_admin(s) and login_del != 'admin'):
                    self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
                _delete_user(login_del)
                self._set_headers(200); self.wfile.write(jdump({'ok': True})); return
            except Exception as e:
                self._set_headers(400); self.wfile.write(jdump({'error': str(e)})); return

        self._set_headers(404)
        self.wfile.write(jdump({'error': 'Rota não encontrada'}))

    def serve_static(self, path):
        decoded = unquote(path).replace('\\', '/')
        public_assets = {'/login.html', '/login.js', '/login.css', '/discovery.css'}
        private_assets = {'/', '/index.html', '/admin.html', '/diag.html', '/discovery.js', '/fornecedores.html', '/fornecedores.js', '/fornecedores.css', '/negocios.html', '/negocios.js', '/negocios.css', '/business_bridge.js', '/inteligencia.html', '/inteligencia.js', '/inteligencia.css', '/radar.html', '/radar.js', '/radar.css'}
        if decoded not in public_assets | private_assets:
            self._set_headers(404, 'text/plain; charset=utf-8')
            self.wfile.write(b'Arquivo nao encontrado')
            return
        if decoded in private_assets and not self._sessao():
            self._redirect('/login.html')
            return
        if decoded == '/admin.html' and (self._sessao() or {}).get('role') != 'admin':
            _write_json(self, {'error': 'Acesso negado'}, 403)
            return
        path = decoded
        if path == '/':
            path = '/index.html'
        fs_path = (SITE_DIR / unquote(path.lstrip('/'))).resolve()
        try:
            fs_path.relative_to(SITE_DIR.resolve())
        except Exception:
            self._set_headers(403, 'text/plain; charset=utf-8')
            self.wfile.write(b'Acesso negado')
            return

        if not fs_path.exists() or not fs_path.is_file():
            self._set_headers(404, 'text/plain; charset=utf-8')
            self.wfile.write(b'Arquivo nao encontrado')
            return

        suffix = fs_path.suffix.lower()
        ctype = 'text/plain; charset=utf-8'
        if suffix == '.html':
            ctype = 'text/html; charset=utf-8'
        elif suffix == '.js':
            ctype = 'application/javascript; charset=utf-8'
        elif suffix == '.css':
            ctype = 'text/css; charset=utf-8'
        elif suffix == '.json':
            ctype = 'application/json; charset=utf-8'

        raw = fs_path.read_bytes()
        use_gzip = suffix in {'.html', '.js', '.css', '.json', '.svg', '.txt'} and len(raw) >= 1024 and _accepts_gzip(self)
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache' if suffix == '.html' else 'public, max-age=86400')
        if use_gzip:
            raw = gzip.compress(raw, compresslevel=6)
            self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)



    def do_PUT(self):
        self.do_POST()


    def do_DELETE(self):
        parsed = urlparse(self.path)
        self.command = 'DELETE'
        self.do_POST.__func__
        import re as _re4
        m = _re4.match(r'^/api/admin/users/([^/]+)$', parsed.path)
        if m:
            s = self._sessao()
            if not s or s.get('role') != 'admin':
                self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
            login_del = m.group(1)
            if login_del == 'admin':
                self._set_headers(400); self.wfile.write(jdump({'error':'Não é possível deletar o admin'})); return
            try:
                usuarios = _carregar_usuarios()
                alvo = usuarios.get(login_del)
                if not alvo:
                    self._set_headers(404); self.wfile.write(jdump({'error':'Usuário não encontrado'})); return
                if not _pode_gerir_usuario(s, login_del, alvo) and not (_is_global_admin(s) and login_del != 'admin'):
                    self._set_headers(403); self.wfile.write(jdump({'error':'Acesso negado'})); return
                _delete_user(login_del)
                self._set_headers(200); self.wfile.write(jdump({'ok': True})); return
            except Exception as e:
                self._set_headers(400); self.wfile.write(jdump({'error': str(e)})); return
        self._set_headers(404); self.wfile.write(jdump({'error':'Rota não encontrada'}))

if __name__ == '__main__':
    adopt_existing_tunnel(globals())
    print(f'PASTA BASE    : {BASE_DIR}')
    print(f'PASTA PARQUET : {DATA_DIR}')
    print(f'PASTA SITE    : {SITE_DIR}')
    print(f'MODO          : PARQUET + AUTH + LICENCA')
    print(f'ABRINDO       : http://{HOST}:{PORT}')
    _carregar_usuarios()
    warmup_index_db_async()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        if os.environ.get("PROSPECTAX_NO_BROWSER", "0") != "1":
            webbrowser.open(f'http://127.0.0.1:{PORT}')
    except Exception:
        pass
    httpd.serve_forever()
