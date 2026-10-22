"""Rotas adicionais. Autenticacao continua no servidor principal."""
import json
import re
from urllib.parse import parse_qs
from prospectax_discovery import buyer_suggestions, normalize_cnpj, DiscoveryConflict
from prospectax_products import import_product_url


def owner_key(session):
    return str(session.get('company_id') or 'local') + ':' + str(session.get('usuario') or session.get('user') or '')


def read_body(handler, limit=4_100_000):
    try:
        length = int(handler.headers.get('Content-Length', '0'))
    except ValueError:
        raise ValueError('Tamanho da requisicao invalido.') from None
    if not 0 <= length <= limit:
        raise ValueError('Requisicao muito grande.')
    data = json.loads(handler.rfile.read(length).decode('utf-8') or '{}')
    if not isinstance(data, dict):
        raise ValueError('Envie um objeto JSON.')
    return data


def get_route(handler, path, qs, session, ctx):
    routes = {'/api/discovery/capabilities', '/api/discovery/buyers', '/api/discovery/similar',
              '/api/discovery/cities', '/api/discovery/places', '/api/discovery/state',
              '/api/products/status', '/api/products/search', '/api/public-checks/status', '/api/public-checks'}
    if path not in routes:
        return False
    one = lambda k, default='': qs.get(k, [default])[0]
    try:
        if path == '/api/discovery/capabilities':
            cols = ctx['_index_columns']()
            result = {'columns': sorted(cols), 'indexed': bool(cols), 'version': '3.0-discovery',
                      'filters': {'contact': bool(cols), 'porte': 'porte_empresa' in cols, 'cep': 'cep' in cols,
                                  'bairro': 'bairro' in cols, 'abertura': 'abertura' in cols, 'tipo': 'matriz_filial' in cols}}
        elif path == '/api/discovery/buyers':
            result = {'segments': buyer_suggestions(one('q'), ctx.get('_cnae_cache')), 'source': 'Sugestoes comerciais editaveis; CNAE/IBGE'}
        elif path == '/api/discovery/similar':
            cnpj = normalize_cnpj(one('cnpj'))
            if not re.fullmatch(r'[A-Z0-9]{12}[0-9]{2}', cnpj):
                raise ValueError('Informe um CNPJ completo.')
            if not ctx['fast_index_available']():
                raise ValueError('A busca de empresas semelhantes precisa do indice local da Receita.')
            con = ctx['get_index_db']()
            row = con.execute('SELECT nome, cnae, cidade, uf FROM prospects_idx WHERE cnpj=? LIMIT 1', [cnpj]).fetchone()
            if not row:
                raise ValueError('CNPJ nao encontrado na base local. Confira o numero ou atualize a base.')
            codes = [r[0] for r in con.execute('SELECT DISTINCT cnae FROM cnae_empresa_idx WHERE cnpj=? ORDER BY cnae LIMIT 100', [cnpj]).fetchall()]
            result = {'cnpj': cnpj, 'nome': row[0], 'cidade': row[2], 'uf': row[3],
                      'cnaes': [{'code': str(c), 'desc': ctx['_cnae_desc_from_code'](str(c)), 'primary': str(c) == row[1]} for c in codes],
                      'source': 'Receita Federal — base local'}
        elif path == '/api/discovery/cities':
            result = ctx['_PUBLIC_PLACES'].cities(one('city'), one('uf'))
        elif path == '/api/discovery/places':
            result = ctx['_PUBLIC_PLACES'].search(one('segment'), one('lat'), one('lon'), one('radius', '3000'))
        elif path == '/api/discovery/state':
            result = ctx['_DISCOVERY_STORE'].load_record(owner_key(session))
        elif path == '/api/products/status':
            result = ctx['_PRODUCT_SEARCH'].status()
        elif path == '/api/products/search':
            result = ctx['_PRODUCT_SEARCH'].search(one('keyword'), one('page', '1'), one('max_price'), one('cache', '1') != '0')
        elif path == '/api/public-checks/status':
            result = ctx['_PUBLIC_CHECKS'].status()
        else:
            result = ctx['_PUBLIC_CHECKS'].lookup(one('cnpj'))
        ctx['_write_json'](handler, result, 200)
    except (ValueError, TypeError) as exc:
        ctx['_write_json'](handler, {'error': str(exc)}, 400)
    except Exception:
        ctx['_write_json'](handler, {'error': 'A fonte nao respondeu nesta consulta. Tente novamente mais tarde; nenhum resultado foi presumido.'}, 502)
    return True


def post_route(handler, path, session, ctx):
    if path not in {'/api/discovery/state', '/api/products/config', '/api/products/import-url', '/api/public-checks/config', '/api/pitch'}:
        return False
    try:
        if path in {'/api/products/config', '/api/public-checks/config'} and not ctx['_is_global_admin'](session):
            ctx['_write_json'](handler, {'error': 'Somente o administrador geral pode configurar integracoes desta instalacao.'}, 403)
            return True
        data = read_body(handler)
        if path == '/api/discovery/state':
            result = ctx['_DISCOVERY_STORE'].save(owner_key(session), data, data.get('_version'))
        elif path == '/api/products/config':
            result = ctx['_PRODUCT_SEARCH'].configure(data)
        elif path == '/api/public-checks/config':
            result = ctx['_PUBLIC_CHECKS'].configure(data)
        elif path == '/api/pitch':
            result = ctx['_free_pitch'](data)
        else:
            result = {'product': import_product_url(data.get('url', ''))}
        ctx['_write_json'](handler, result, 200)
    except DiscoveryConflict as exc:
        ctx['_write_json'](handler, {'error': str(exc), 'state': exc.state, 'version': exc.version}, 409)
    except (ValueError, TypeError) as exc:
        ctx['_write_json'](handler, {'error': str(exc)}, 400)
    except Exception:
        ctx['_write_json'](handler, {'error': 'Nao foi possivel concluir a operacao. Nenhum resultado foi presumido.'}, 502)
    return True
