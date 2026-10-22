"""Ferramentas de negócio: catálogo, cotações e área do fornecedor.

Estado próprio em SQLite; a base da Receita é acessada pelo contexto somente
para pesquisa. Não envia mensagens externas e não toca no túnel.
"""
import csv
import io
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import parse_qs
from prospectax_partners import validate as validate_partner, owner, url, text, Conflict

PROFILE_FIELDS = {'description', 'products', 'website', 'logo', 'city', 'uf', 'coverage', 'national', 'wholesale', 'small_business', 'minimum_order', 'contact_name', 'whatsapp', 'email', 'benefit', 'conditions', 'benefit_until'}
STATUSES = {'novo': 'Novo contato', 'contato_realizado': 'Conversando', 'nao_atendeu': 'Não atendeu', 'aguardando_retorno': 'Aguardando retorno', 'proposta_enviada': 'Proposta enviada', 'fechado': 'Fechado', 'sem_interesse': 'Sem interesse'}
OUTCOMES = {'quote': 'Pedi cotação', 'negotiating': 'Iniciei negociação', 'purchased': 'Comprei'}


class Forbidden(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def number(value, maximum='1000000000', minimum='0'):
    if isinstance(value, bool):
        raise ValueError('Valor numérico inválido.')
    try:
        d = Decimal(str(value or 0))
    except InvalidOperation:
        raise ValueError('Informe números válidos.') from None
    if not d.is_finite() or not Decimal(minimum) <= d <= Decimal(maximum):
        raise ValueError('Valor fora do intervalo permitido.')
    return d


def money(value):
    return str(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))


def day(value):
    value = text(value or '', 10)
    if value:
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError('Data inválida.') from None
    return value


def cnpj(value):
    value = re.sub('[^A-Z0-9]', '', str(value).upper())
    if not re.fullmatch('[A-Z0-9]{12}[0-9]{2}', value):
        raise ValueError('Informe um CNPJ completo.')
    return value


def calculate_margin(data):
    quantity = number(data.get('quantity'), minimum='.001', maximum='1000000')
    purchase = number(data.get('purchase'))
    freight = number(data.get('freight'))
    other = number(data.get('other'))
    sale = number(data.get('sale'), minimum='.01')
    taxes = number(data.get('tax_percent'), maximum='99.99')
    fees = number(data.get('fee_percent'), maximum='99.99')
    percent = (taxes + fees) / 100
    if percent >= 1:
        raise ValueError('A soma dos percentuais precisa ser menor que 100%.')
    cost = purchase + (freight + other) / quantity
    charges = sale * percent
    profit = sale - charges - cost
    return {'unit_cost': money(cost), 'unit_charges': money(charges), 'unit_profit': money(profit), 'batch_profit': money(profit * quantity), 'margin_percent': money(profit / sale * 100), 'markup_percent': money((sale / cost - 1) * 100) if cost else None, 'break_even': money(cost / (1 - percent)), 'quantity': str(quantity)}


class BusinessStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.executescript('''
        CREATE TABLE IF NOT EXISTS records(kind TEXT NOT NULL, id TEXT NOT NULL, owner TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, updated TEXT NOT NULL, PRIMARY KEY(kind,id));
        CREATE INDEX IF NOT EXISTS record_owner ON records(kind,owner);
        CREATE TABLE IF NOT EXISTS assignments(partner TEXT PRIMARY KEY, owner TEXT NOT NULL);
        ''')
        try:
            with con:
                yield con
        finally:
            con.close()

    def all(self, kind, who=None):
        with self.connect() as con:
            rows = con.execute('SELECT * FROM records WHERE kind=?' + (' AND owner=?' if who is not None else ''), [kind] + ([who] if who is not None else [])).fetchall()
        return [dict(json.loads(r['payload']), id=r['id'], owner=r['owner'], revision=r['revision'], updated_at=r['updated']) for r in rows]

    def get(self, kind, identifier):
        with self.connect() as con:
            row = con.execute('SELECT * FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
        if not row:
            raise ValueError('Registro não encontrado.')
        return dict(json.loads(row['payload']), id=row['id'], owner=row['owner'], revision=row['revision'], updated_at=row['updated'])

    def put(self, kind, identifier, who, payload, revision=0):
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT revision,owner FROM records WHERE kind=? AND id=?', (kind, identifier)).fetchone()
            if revision != (row['revision'] if row else 0):
                raise Conflict('Este registro mudou. Atualize a tela antes de salvar.')
            if row and row['owner'] != who:
                raise Forbidden('Registro de outra conta.')
            cleaned = {k:v for k,v in payload.items() if k not in {'id','owner','revision','updated_at'}}
            con.execute('INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated=excluded.updated', (kind, identifier, who, revision+1, json.dumps(cleaned,ensure_ascii=False), now()))
        return self.get(kind, identifier)

    def assigned(self, who=None):
        with self.connect() as con:
            rows=con.execute('SELECT * FROM assignments'+(' WHERE owner=?' if who is not None else ''), [who] if who is not None else []).fetchall()
        return {r['partner']:r['owner'] for r in rows}

    def assign(self, partner, who):
        with self.connect() as con:
            if who:
                con.execute('INSERT INTO assignments VALUES(?,?) ON CONFLICT(partner) DO UPDATE SET owner=excluded.owner',(partner,who))
            else:
                con.execute('DELETE FROM assignments WHERE partner=?',(partner,))


def has_partner(store, sess, partner, ctx):
    return ctx['_is_global_admin'](sess) or store.assigned(owner(sess)).get(partner) == owner(sess)


def product_payload(data):
    title = text(data.get('title',''),140)
    if not title:
        raise ValueError('Informe o nome do produto.')
    return {'partner':text(data.get('partner',''),40),'title':title,'description':text(data.get('description',''),1600), 'photo':url(data.get('photo','')), 'link':url(data.get('link','')), 'minimum_quantity':str(number(data.get('minimum_quantity',1),minimum='.001',maximum='1000000')), 'unit':text(data.get('unit','unidade'),30), 'price':money(number(data['price'])) if data.get('price') not in (None,'') else '', 'price_max':money(number(data['price_max'])) if data.get('price_max') not in (None,'') else '', 'category':text(data.get('category',''),80)}


def lookup(ctx, values):
    if not ctx['fast_index_available']():
        raise ValueError('A carteira precisa do índice local da Receita.')
    con=ctx['get_index_db']()
    rows=con.execute('SELECT cnpj,nome,cnae,cidade,uf FROM prospects_idx WHERE cnpj IN ('+','.join('?' for _ in values)+')',values).fetchall() if values else []
    return {r[0]:{'cnpj':r[0],'name':r[1], 'cnae':str(r[2] or ''),'city':r[3],'uf':r[4]} for r in rows}


def board(ctx,sess,store):
    who=owner(sess)
    leads={}
    for key,item in ctx['load_saved_clients'](sess).items():
        if isinstance(item,dict):
            leads[key]={'cnpj':key,'name':item.get('nome') or item.get('name') or key,'follow_date':''}
    tools=ctx['_DISCOVERY_STORE'].load(who).get('leadTools',{})
    for key,item in tools.items():
        if isinstance(item,dict) and (item.get('favorite') or item.get('followUp')):
            leads[key]={'cnpj':key,'name':item.get('nome') or key,'follow_date':item.get('followUp','')}
    for item in store.all('lead',who):
        leads[item['cnpj']]={**leads.get(item['cnpj'],{}),**item}
        if item['cnpj'] in tools and isinstance(tools[item['cnpj']],dict) and 'followUp' in tools[item['cnpj']]:
            leads[item['cnpj']]['follow_date']=tools[item['cnpj']]['followUp']
    records=ctx['load_contact_status_records'](sess)
    for key,item in leads.items():
        rec=records.get(key,{})
        item['status']=rec.get('status','novo') if isinstance(rec,dict) else rec or 'novo'
        item['status_owner']=rec.get('owner_login','') if isinstance(rec,dict) else ''
    values=list(leads.values())
    values.sort(key=lambda p:(p.get('follow_date') or '9999',p.get('name','')))
    return {'leads':values[:500],'total':len(values),'statuses':STATUSES}


def alerts(ctx,sess,store):
    today=date.today()
    who=owner(sess)
    result=[]
    for row in board(ctx,sess,store)['leads']:
        due=row.get('follow_date','')
        if due and due<=today.isoformat() and row['status'] not in {'fechado','sem_interesse'}:
            result.append({'id':'return:'+row['cnpj']+':'+due,'kind':'return','title':'Retorno: '+row.get('name',row['cnpj']),'detail':'Data prevista: '+due,'target':'quadro'})
    for partner in ctx['_PARTNER_STORE'].public(who,{})['partners']:
        expiry=partner.get('benefit_until','')
        if partner.get('benefit') and expiry and today.isoformat()<=expiry<=(today+timedelta(days=7)).isoformat():
            result.append({'id':'benefit:'+partner['id']+':'+expiry,'kind':'benefit','title':'Benefício próximo do fim: '+partner['name'],'detail':'Validade: '+expiry,'target':'catalogo'})
        if partner.get('score',0)>0 and str(partner.get('created_at',''))[:10]>=(today-timedelta(days=14)).isoformat():
            result.append({'id':'partner:'+partner['id'],'kind':'partner','title':'Novo parceiro para explorar: '+partner['name'],'detail':'; '.join(partner.get('reasons',[])),'target':'catalogo'})
    dismissed={r['key'] for r in store.all('dismissed',who)}
    return [r for r in result if r['id'] not in dismissed]


def state(ctx,sess):
    store=ctx['_BUSINESS_STORE']; who=owner(sess); admin=ctx['_is_global_admin'](sess)
    own=store.assigned(who)
    partners=ctx['_PARTNER_STORE'].public(who,{})['partners']
    allowed={p['id'] for p in partners}
    products=[{k:v for k,v in p.items() if k!='owner'} for p in store.all('product') if p.get('status')=='aprovado' and p['partner'] in allowed]
    quotes=store.all('quote',who)
    inbox=[q for q in store.all('quote') if q.get('shared') and any(i in own for i in q['partners'])]
    alloffers=store.all('offer')
    for q in quotes:
        q['offers']=[dict({k:v for k,v in o.items() if k!='owner'},editable=o['owner']==who,total=money(Decimal(o['price'])*Decimal(q['quantity'])+Decimal(o['freight'])),meets_minimum=Decimal(q['quantity'])>=Decimal(o['minimum_quantity'])) for o in alloffers if o['quote']==q['id']]
    for q in inbox:
        q['partners']=[i for i in q['partners'] if i in own]
        q['offers']=[dict({k:v for k,v in o.items() if k!='owner'},editable=o['owner']==who) for o in alloffers if o['quote']==q['id'] and o['partner'] in own]
        q.pop('owner',None)
    private_partners=[{k:v for k,v in p.items() if k!='partnership_notes'} for p in ctx['_PARTNER_STORE'].all() if p['id'] in own]
    reports=ctx['_PARTNER_STORE'].report(30)
    actions=store.all('outcome')
    cutoff=(date.today()-timedelta(days=29)).isoformat()
    for r in reports:
        r['declared_results']={kind:sum(a.get('enabled',False) for a in actions if a['partner']==r['id'] and a['kind']==kind and a['updated_at'][:10]>=cutoff) for kind in OUTCOMES}
    result={'account_name':sess.get('nome') or sess.get('usuario'),'admin':admin,'supplier':bool(own),'partners':partners,'products':products,'quotes':quotes,'inbox':inbox,'supplier_partners':private_partners,'supplier_products':[p for p in store.all('product') if p['partner'] in own], 'portfolio':store.all('portfolio',who), 'outcomes':store.all('outcome',who),'board':board(ctx,sess,store),'alerts':alerts(ctx,sess,store),'reports':[r for r in reports if admin or r['id'] in own]}
    reviews=store.all('review')
    result['reviews']=[r for r in reviews if admin or r['owner']==who]
    if admin:
        result['all_partners']=ctx['_PARTNER_STORE'].all()
        result['all_products']=store.all('product')
        users=ctx['_carregar_usuarios']()
        result['accounts']=[{'login':login,'name':u.get('nome',login),'owner':owner({'usuario':login,'company_id':u.get('company_id')})} for login,u in users.items() if u.get('ativo',True) and not (u.get('scope')=='global_admin')]
        result['assignments']=store.assigned()
    return result


def post(ctx,sess,operation,data):
    store=ctx['_BUSINESS_STORE']; who=owner(sess); admin=ctx['_is_global_admin'](sess)
    if 'revision' in data and (type(data['revision']) is not int or not 0<=data['revision']<=1000000000):
        raise ValueError('Revisão inválida.')
    if operation=='margin':
        return {'result':calculate_margin(data)}
    if operation=='assign':
        if not admin: raise Forbidden('Somente o administrador geral vincula contas.')
        partner=text(data.get('partner',''),40)
        if not any(p['id']==partner for p in ctx['_PARTNER_STORE'].all()): raise ValueError('Fornecedor inexistente.')
        user=data.get('login','')
        if user:
            account=ctx['_carregar_usuarios']().get(user)
            if not account or not account.get('ativo',True) or account.get('scope')=='global_admin': raise ValueError('Escolha uma conta ativa de fornecedor.')
            store.assign(partner,owner({'usuario':user,'company_id':account.get('company_id')}))
        else: store.assign(partner,'')
        return {'ok':True}
    if operation=='product':
        item=product_payload(data)
        if not any(p['id']==item['partner'] for p in ctx['_PARTNER_STORE'].all()): raise ValueError('Fornecedor inexistente.')
        if not has_partner(store,sess,item['partner'],ctx): raise Forbidden('Fornecedor não vinculado à sua conta.')
        if item['price_max'] and (not item['price'] or Decimal(item['price_max'])<Decimal(item['price'])): raise ValueError('A faixa de preço precisa de valor inicial e final compatíveis.')
        identifier=text(data.get('id',''),90) or uuid.uuid4().hex
        existing=store.get('product',identifier) if data.get('id') else None
        if existing and existing['partner']!=item['partner']: raise Forbidden('Não é possível transferir este produto.')
        revision=data.get('revision',0)
        if not isinstance(revision,int): raise ValueError('Revisão inválida.')
        if not admin:
            if existing and existing['revision']!=revision: raise Conflict('O produto mudou. Atualize a tela.')
            review=store.put('review',uuid.uuid4().hex,who,{'type':'product','partner':item['partner'],'record':identifier,'base_revision':revision,'patch':item,'status':'aguardando'})
            return {'ok':True,'review':review,'message':'Produto enviado para aprovação.'}
        item['status']=text(data.get('status','rascunho'),20)
        if item['status'] not in {'rascunho','aguardando','aprovado','pausado'}: raise ValueError('Situação inválida.')
        saved=store.put('product',identifier,existing['owner'] if existing else who,item,revision)
        return {'ok':True,'product':saved}
    if operation=='supplier-profile':
        partner=next((p for p in ctx['_PARTNER_STORE'].all() if p['id']==data.get('partner')),None)
        if not partner or not has_partner(store,sess,partner['id'],ctx): raise Forbidden('Fornecedor não vinculado à sua conta.')
        patch={k:data[k] for k in PROFILE_FIELDS if k in data}
        clean=validate_partner(dict(partner,**patch))
        review=store.put('review',uuid.uuid4().hex,who,{'type':'profile','partner':partner['id'],'base_revision':partner['revision'],'patch':{k:clean[k] for k in PROFILE_FIELDS if k in clean},'status':'aguardando'})
        return {'ok':True,'review':review}
    if operation=='review':
        if not admin: raise Forbidden('Aprovação restrita ao administrador geral.')
        review=store.get('review',data.get('id'))
        if review['revision']!=data.get('revision') or review['status']!='aguardando': raise Conflict('Esta solicitação já mudou.')
        action=data.get('action')
        if action not in {'approve','reject'}: raise ValueError('Escolha aprovar ou rejeitar.')
        if action=='approve':
            if review['type']=='profile':
                partner=next((p for p in ctx['_PARTNER_STORE'].all() if p['id']==review['partner']),None)
                if not partner or partner['revision']!=review['base_revision']: raise Conflict('O perfil mudou desde a solicitação. Peça um novo envio.')
                ctx['_PARTNER_STORE'].save(dict(partner,**review['patch']))
            else:
                current=next((p for p in store.all('product') if p['id']==review['record']),None)
                if (current['revision'] if current else 0)!=review['base_revision']: raise Conflict('O produto mudou desde a solicitação. Peça um novo envio.')
                store.put('product',review['record'],current['owner'] if current else review['owner'],dict(review['patch'],status='aprovado'),review['base_revision'])
        store.put('review',review['id'],review['owner'],dict(review,status='aprovado' if action=='approve' else 'rejeitado'),review['revision'])
        return {'ok':True}
    if operation=='quote':
        identifier=text(data.get('id',''),40) or uuid.uuid4().hex
        previous=store.get('quote',identifier) if data.get('id') else None
        if previous and previous['owner']!=who: raise Forbidden('Cotação de outra conta.')
        if previous and previous.get('shared'): raise ValueError('Cotação compartilhada não pode ser alterada. Prepare uma nova para mudar os termos.')
        selected=data.get('partners',[])
        if not isinstance(selected,list) or not 1<=len(selected)<=25: raise ValueError('Escolha de 1 a 25 fornecedores.')
        allowed={p['id'] for p in ctx['_PARTNER_STORE'].public(who,{})['partners']}
        if any(not isinstance(i,str) or i not in allowed for i in selected): raise ValueError('Escolha fornecedores aprovados.')
        product=text(data.get('product',''),200); city=text(data.get('city',''),100)
        if not product or not city: raise ValueError('Informe produto e cidade de entrega.')
        payload={'partners':list(dict.fromkeys(selected)),'product':product,'quantity':str(number(data.get('quantity'),minimum='.001',maximum='1000000')),'city':city,'notes':text(data.get('notes',''),1800),'requester':text(data.get('requester') or sess.get('nome') or sess.get('usuario'),120),'shared':False,'created_at':now()}
        return {'ok':True,'quote':store.put('quote',identifier,who,payload,data.get('revision',0))}
    if operation=='share-quote':
        q=store.get('quote',data.get('id'))
        if q['owner']!=who: raise Forbidden('Cotação de outra conta.')
        return {'ok':True,'quote':store.put('quote',q['id'],who,dict(q,shared=True),data.get('revision'))}
    if operation=='offer':
        q=store.get('quote',data.get('quote')); partner=text(data.get('partner',''),40)
        buyer=q['owner']==who
        if partner not in q['partners'] or not buyer and (not q.get('shared') or not has_partner(store,sess,partner,ctx)): raise Forbidden('Você não participa desta cotação.')
        identifier=q['id']+'_'+partner
        prev=next((o for o in store.all('offer') if o['id']==identifier),None)
        if prev and prev['owner']!=who: raise Forbidden('A proposta recebida só pode ser editada por quem a registrou.')
        payload={'quote':q['id'],'partner':partner,'price':money(number(data.get('price'))),'freight':money(number(data.get('freight'))),'minimum_quantity':str(number(data.get('minimum_quantity',1),minimum='.001',maximum='1000000')),'delivery':text(data.get('delivery',''),120),'notes':text(data.get('notes',''),1200),'source':'manual' if buyer else 'fornecedor'}
        return {'ok':True,'offer':store.put('offer',identifier,who,payload,data.get('revision',0))}
    if operation=='outcome':
        partner=text(data.get('partner',''),40); kind=data.get('kind'); enabled=data.get('enabled')
        ctx['_PARTNER_STORE'].approved(partner)
        if kind not in OUTCOMES or not isinstance(enabled,bool): raise ValueError('Resultado inválido.')
        identifier=uuid.uuid5(uuid.NAMESPACE_URL,who+partner+kind).hex
        previous=next((a for a in store.all('outcome',who) if a['id']==identifier),None)
        store.put('outcome',identifier,who,{'partner':partner,'kind':kind,'enabled':enabled},previous['revision'] if previous else 0)
        return {'ok':True}
    if operation=='lead':
        key=cnpj(data.get('cnpj','')); name=text(data.get('name',''),160); follow=day(data.get('follow_date',''))
        if not name: raise ValueError('Informe o nome da empresa.')
        # Uma transação mantém o retorno do quadro e da Prospecção consistente.
        discovery=ctx['_DISCOVERY_STORE']; discovery.load_record(who)
        identifier=uuid.uuid5(uuid.NAMESPACE_URL,who+key).hex
        with store.connect() as con:
            con.execute('ATTACH DATABASE ? AS discovery',(str(discovery.path),))
            con.execute('BEGIN IMMEDIATE')
            existing=con.execute('SELECT revision,owner FROM records WHERE kind="lead" AND id=?',(identifier,)).fetchone()
            revision=data.get('revision',0)
            if revision!=(existing['revision'] if existing else 0): raise Conflict('Este retorno mudou. Atualize a tela antes de salvar.')
            row=con.execute('SELECT payload,version FROM discovery.discovery_state WHERE owner=?',(who,)).fetchone()
            payload=json.loads(row['payload']) if row else {}
            lead_tools=payload.setdefault('leadTools',{})
            lead_tools.setdefault(key,{}).update({'nome':name,'followUp':follow,'updatedAt':now()})
            raw=json.dumps(payload,ensure_ascii=False)
            if len(lead_tools)>20000 or len(raw.encode('utf-8'))>4000000: raise ValueError('Seu estado comercial atingiu o limite de tamanho.')
            stamp=now()
            con.execute('INSERT INTO discovery.discovery_state(owner,payload,updated_at,version) VALUES(?,?,?,?) ON CONFLICT(owner) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at,version=excluded.version',(who,raw,stamp,(row['version'] if row else 0)+1))
            con.execute('INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated=excluded.updated',('lead',identifier,who,revision+1,json.dumps({'cnpj':key,'name':name,'follow_date':follow},ensure_ascii=False),stamp))
        return {'ok':True,'lead':store.get('lead',identifier)}
    if operation=='dismiss':
        key=text(data.get('key',''),180)
        if key not in {a['id'] for a in alerts(ctx,sess,store)}: raise ValueError('Alerta indisponível.')
        store.put('dismissed',uuid.uuid5(uuid.NAMESPACE_URL,who+key).hex,who,{'key':key})
        return {'ok':True}
    if operation=='portfolio':
        raw=data.get('rows')
        if not isinstance(raw,list) or not 1<=len(raw)<=500: raise ValueError('Importe de 1 a 500 CNPJs por carteira.')
        values=list(dict.fromkeys(cnpj(v.get('cnpj','') if isinstance(v,dict) else v) for v in raw))
        found=lookup(ctx,values)
        if not found: raise ValueError('Nenhum CNPJ encontrado no índice local. Confira o arquivo.')
        groups={}
        for row in found.values():
            code=row['cnae']; groups[code]=groups.get(code,0)+1
        segments=[{'code':code,'count':count,'description':ctx['_cnae_desc_from_code'](code)} for code,count in sorted(groups.items(),key=lambda i:(-i[1],i[0]))]
        record=store.put('portfolio',uuid.uuid4().hex,who,{'name':text(data.get('name') or 'Carteira de clientes',120),'companies':list(found.values()),'segments':segments,'missing':[v for v in values if v not in found],'total':len(values),'created_at':now()})
        return {'ok':True,'portfolio':record}
    raise ValueError('Operação desconhecida.')


def get_route(handler,path,qs,sess,ctx):
    if path not in {'/api/business/state','/api/business/alerts'}: return False
    try:
        result={'alerts':alerts(ctx,sess,ctx['_BUSINESS_STORE'])} if path.endswith('/alerts') else state(ctx,sess)
        ctx['_write_json'](handler,result)
    except (ValueError,TypeError) as exc:
        ctx['_write_json'](handler,{'error':str(exc)},400)
    except Exception:
        ctx['_write_json'](handler,{'error':'Não foi possível carregar as ferramentas. Tente novamente.'},500)
    return True


def post_route(handler,path,sess,ctx):
    if not path.startswith('/api/business/'): return False
    try:
        length=int(handler.headers.get('Content-Length','0'))
        if not 0<length<=400000: raise ValueError('Requisição vazia ou muito grande.')
        data=json.loads(handler.rfile.read(length).decode('utf-8'))
        if not isinstance(data,dict): raise ValueError('Envie um objeto JSON.')
        result=post(ctx,sess,path.rsplit('/',1)[-1],data)
        ctx['_write_json'](handler,result)
    except Forbidden as exc:
        ctx['_write_json'](handler,{'error':str(exc)},403)
    except Conflict as exc:
        ctx['_write_json'](handler,{'error':str(exc)},409)
    except (ValueError,TypeError,InvalidOperation) as exc:
        ctx['_write_json'](handler,{'error':str(exc)},400)
    except Exception:
        ctx['_write_json'](handler,{'error':'Não foi possível salvar. Atualize a tela e confira os dados.'},500)
    return True
