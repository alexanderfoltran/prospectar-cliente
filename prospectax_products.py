"""Busca de produtos por API oficial gratuita com cadastro e importacao de links.

As credenciais pertencem ao administrador; nunca sao devolvidas ao navegador.
"""
import hashlib
import hmac
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from prospectax_discovery import DiscoveryCache, json_request, norm


TRANSLATIONS = {'cadeira de barbeiro': 'barber chair', 'cadeira barbeiro': 'barber chair',
    'maquina de cortar cabelo': 'hair clipper', 'secador de cabelo': 'hair dryer',
    'maca de estetica': 'beauty bed', 'maca estetica': 'beauty bed', 'vaporizador facial': 'facial steamer',
    'luva descartavel': 'disposable gloves', 'luvas descartaveis': 'disposable gloves',
    'escova de cabelo': 'hair brush', 'tesoura de cabelo': 'hair cutting scissors',
    'navalha de barbeiro': 'barber straight razor', 'embalagem': 'packaging'}


def product_query(text):
    query = str(text or '').strip()[:200]
    normalized = norm(query)
    for term in sorted(TRANSLATIONS, key=len, reverse=True):
        if normalized.startswith(term):
            # Preserva atributos/modelos digitados apos o nome conhecido.
            return TRANSLATIONS[term] + normalized[len(term):]
    return query


def number(value):
    try:
        value = Decimal(str(value))
        return float(value) if value.is_finite() and value >= 0 else None
    except (ValueError, InvalidOperation):
        return None


def safe_url(url):
    p = urlparse(str(url or '').strip())
    return str(url).strip() if p.scheme == 'https' and p.hostname and not p.username and not p.password and p.port in (None, 443) else ''


def sign_top(params, secret):
    content = ''.join(str(k) + str(v) for k, v in sorted(params.items()) if k != 'sign')
    return hashlib.md5((secret + content + secret).encode('utf-8')).hexdigest().upper()


def sign_sync(params, secret):
    content = ''.join(str(k) + str(v) for k, v in sorted(params.items()) if k != 'sign')
    return hmac.new(secret.encode(), content.encode(), hashlib.sha256).hexdigest().upper()


class ProductSearch:
    DOCS = 'https://developer.alibaba.com/docs/api.htm?apiId=45803'

    def __init__(self, directory, request_json=json_request):
        self.directory = Path(directory)
        self.config_file = self.directory / 'products_config.json'
        self.cache = DiscoveryCache(self.directory / 'products_cache')
        self.request_json = request_json
        self.lock = threading.Lock()
        self.last_query = 0
        self.cooldown_until = 0

    def _config(self):
        try:
            return json.loads(self.config_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def status(self):
        c = self._config()
        return {'configured': bool(c.get('app_key') and c.get('app_secret')), 'protocol': c.get('protocol', 'top'),
                'provider': 'AliExpress Affiliate', 'requires_free_registration': True, 'docs_url': self.DOCS}

    def configure(self, data):
        with self.lock:
            c = self._config()
            if data.get('remove') is True:
                c = {}
            else:
                for key in ('app_key', 'app_secret', 'tracking_id'):
                    value = str(data.get(key, '')).strip()
                    if len(value) > 500:
                        raise ValueError('Credencial invalida.')
                    if value:
                        c[key] = value
                if 'protocol' in data:
                    if data['protocol'] not in ('top', 'sync'):
                        raise ValueError('Protocolo invalido.')
                    c['protocol'] = data['protocol']
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self.config_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(c), encoding='utf-8')
            tmp.replace(self.config_file)
        return self.status()

    def search(self, keyword, page=1, max_price='', use_cache=True):
        keyword = str(keyword or '').strip()
        if not 2 <= len(keyword) <= 200:
            raise ValueError('Descreva o produto entre 2 e 200 caracteres.')
        page = int(page)
        if not 1 <= page <= 100:
            raise ValueError('Pagina invalida.')
        c = self._config()
        if not c.get('app_key') or not c.get('app_secret'):
            return {'configured': False, 'products': [], 'total': 0, 'error': 'Para pesquisar o catalogo gratuito, o administrador precisa cadastrar a chave da API AliExpress Affiliate. Tambem e possivel adicionar um produto por link.', 'docs_url': self.DOCS}
        maximum = number(max_price) if max_price != '' else None
        if max_price != '' and maximum is None:
            raise ValueError('Preco maximo invalido.')
        translated = product_query(keyword)
        key = json.dumps(['affiliate-v1', hashlib.sha256(c['app_key'].encode()).hexdigest()[:16], translated, page, maximum])
        cached = self.cache.get(key, ttl=6*3600) if use_cache else None
        if cached is not None:
            return {**cached, 'from_cache': True}
        params = {'app_key': c['app_key'], 'method': 'aliexpress.affiliate.product.query', 'keywords': translated,
                  'page_no': str(page), 'page_size': '20', 'target_language': 'PT', 'target_currency': 'BRL',
                  'ship_to_country': 'BR', 'format': 'json'}
        if c.get('tracking_id'):
            params['tracking_id'] = c['tracking_id']
        # O filtro em BRL e aplicado aos precos convertidos retornados; o
        # parametro de origem pode ter moeda/unidade diferente por protocolo.
        if c.get('protocol', 'top') == 'top':
            endpoint = 'https://eco.taobao.com/router/rest'
            params.update(v='2.0', sign_method='md5', timestamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'))
            params['sign'] = sign_top(params, c['app_secret'])
        else:
            endpoint = 'https://api-sg.aliexpress.com/sync'
            params.update(sign_method='sha256', timestamp=str(int(time.time()*1000)))
            params['sign'] = sign_sync(params, c['app_secret'])
        with self.lock:
            if time.time() < self.cooldown_until:
                raise ValueError('O catalogo solicitou uma pausa. Tente novamente mais tarde.')
            time.sleep(max(0, 2 - (time.monotonic() - self.last_query)))
            self.last_query = time.monotonic()
            try:
                raw = self.request_json(endpoint, data=urlencode(params).encode(), headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=25)
            except HTTPError as exc:
                if exc.code == 429:
                    self.cooldown_until = time.time() + 60
                raise ValueError(f'Catalogo indisponivel (HTTP {exc.code}). Nenhum servico pago foi acionado.') from None
        if raw.get('error_response'):
            err = raw['error_response']
            # Nunca retornar o request ou credenciais que o provedor possa ecoar.
            raise ValueError('A API recusou a consulta (codigo ' + re.sub(r'[^a-zA-Z0-9_.-]', '', str(err.get('code', 'desconhecido')))[:50] + '). Confira as permissoes e o protocolo do aplicativo no painel AliExpress.')
        body = raw.get('aliexpress_affiliate_product_query_response', {}).get('resp_result', {})
        if str(body.get('resp_code')) != '200':
            raise ValueError('O catalogo nao confirmou a consulta. Confira as permissoes da API gratuita.')
        result = body.get('result') or {}
        items = (result.get('products') or {}).get('product', [])
        if isinstance(items, dict):
            items = [items]
        products = []
        for item in items:
            url = safe_url(item.get('product_detail_url'))
            if not url or not item.get('product_title'):
                continue
            price = number(item.get('target_sale_price'))
            currency = str(item.get('target_sale_price_currency') or '')
            if maximum is not None and (price is None or currency != 'BRL' or price > maximum):
                continue
            products.append({'id': str(item.get('product_id') or url), 'title': str(item['product_title']), 'url': url,
                             'image_url': safe_url(item.get('product_main_image_url')), 'price': price, 'currency': currency,
                             'supplier': 'Loja ' + str(item['shop_id']) if item.get('shop_id') else '',
                             'supplier_url': safe_url(item.get('shop_url')), 'moq': None,
                             'source': 'AliExpress', 'source_type': 'API oficial', 'origin_country': None})
        payload = {'configured': True, 'products': products, 'total': int(result.get('total_record_count') or len(products)),
                   'has_more': int(result.get('total_page_no') or 1) > page, 'page': page, 'query': translated,
                   'consulted_at': datetime.now(timezone.utc).isoformat(), 'notice': 'Catalogo do programa de afiliados. Origem, frete, tributos e quantidade minima devem ser confirmados no anuncio.'}
        self.cache.put(key, payload)
        return {**payload, 'from_cache': False}


PRODUCT_HOSTS = ('aliexpress.com', 'aliexpress.us', 'alibaba.com', 'made-in-china.com', 'dhgate.com')


def validate_product_url(url):
    url = safe_url(url)
    p = urlparse(url)
    if not url or not any(p.hostname == host or p.hostname.endswith('.' + host) for host in PRODUCT_HOSTS):
        raise ValueError('Use um link HTTPS de produto do AliExpress, Alibaba, Made-in-China ou DHgate.')
    if not any(part in p.path.lower() for part in ('/item/', '/product/', '/product-detail/')):
        raise ValueError('Cole o link de um produto especifico, nao uma pagina de busca.')
    return url


class ProductHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.blocks, self.parts = {}, [], []
        self.in_json = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta':
            self.meta[attrs.get('property') or attrs.get('name') or ''] = attrs.get('content', '')
        if tag == 'script' and attrs.get('type', '').lower() == 'application/ld+json':
            self.in_json, self.parts = True, []

    def handle_data(self, data):
        if self.in_json:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.in_json:
            self.blocks.append(''.join(self.parts))
            self.in_json = False


def parse_product_html(html, url):
    parser = ProductHTML()
    parser.feed(html)
    objects = []
    for block in parser.blocks:
        try:
            parsed = json.loads(block)
            objects.extend(parsed if isinstance(parsed, list) else [parsed])
        except (ValueError, TypeError):
            pass
    product = None
    while objects:
        obj = objects.pop(0)
        if not isinstance(obj, dict):
            continue
        kind = obj.get('@type', [])
        if kind == 'Product' or isinstance(kind, list) and 'Product' in kind:
            product = obj
            break
        objects.extend(obj.get('@graph', []) if isinstance(obj.get('@graph'), list) else [])
    if not product or not product.get('name'):
        raise ValueError('A pagina nao disponibilizou dados estruturados de produto. Abra o anuncio original; nao foi criado um produto com informacoes presumidas.')
    offers = product.get('offers') or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        offers = {}
    image = product.get('image') or parser.meta.get('og:image') or ''
    if isinstance(image, list):
        image = image[0] if image else ''
    if isinstance(image, dict):
        image = image.get('url', '')
    seller = offers.get('seller') or {}
    price = number(offers.get('price'))
    low = number(offers.get('lowPrice')) if price is None else None
    high = number(offers.get('highPrice')) if low is not None else None
    return {'id': url, 'title': unescape(str(product['name']))[:500], 'url': url,
            'image_url': safe_url(urljoin(url, str(image))), 'price': price,
            'price_min': low, 'price_max': high,
            'currency': str(offers.get('priceCurrency') or ''), 'supplier': str(seller.get('name') or '') if isinstance(seller, dict) else '',
            'supplier_url': '', 'moq': None, 'source': urlparse(url).hostname, 'source_type': 'Dados publicados no anuncio',
            'origin_country': None, 'consulted_at': datetime.now(timezone.utc).isoformat()}


class ProductRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_product_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def import_product_url(url):
    url = validate_product_url(str(url or '').strip())
    req = Request(url, headers={'User-Agent': 'ProspectaX-Local/3.0', 'Accept': 'text/html'})
    try:
        with build_opener(ProductRedirect()).open(req, timeout=18) as response:
            html = response.read(2_000_001)
            if len(html) > 2_000_000:
                raise ValueError('Pagina muito grande para importar. Abra o anuncio original.')
            return parse_product_html(html.decode(response.headers.get_content_charset() or 'utf-8', errors='replace'), url)
    except HTTPError as exc:
        raise ValueError(f'O fornecedor nao permitiu a leitura automatica (HTTP {exc.code}). Abra o anuncio no site de origem.') from None
