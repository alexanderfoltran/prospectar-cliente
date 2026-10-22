"""Busca única: coleta fontes reais em paralelo e usa NVIDIA apenas para organizá-las."""
import hashlib
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from prospectax_radar import COMEX, link, norm, now, state, text
from prospectax_research_net import SourceUnavailable
from prospectax_routes import owner_key, read_body

_LOCKS = [threading.Lock() for _ in range(32)]
_STOP = {'a','as','o','os','de','da','das','do','dos','e','em','para','com','sem','um','uma','item','produto'}
_UF_NAMES=dict(zip('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split(),
    ['Acre','Alagoas','Amapá','Amazonas','Bahia','Ceará','Distrito Federal','Espírito Santo','Goiás','Maranhão','Mato Grosso','Mato Grosso do Sul','Minas Gerais','Pará','Paraíba','Paraná','Pernambuco','Piauí','Rio de Janeiro','Rio Grande do Norte','Rio Grande do Sul','Rondônia','Roraima','Santa Catarina','São Paulo','Sergipe','Tocantins']))
_NEWS_TERMS={'cafe':'coffee','uniform':'uniform','embalag':'packaging','limpez':'cleaning','movel':'furniture','moveis':'furniture','comput':'computer','cosmetic':'cosmetics','luva':'gloves','tinta':'paint'}
# Related project types are commercial hypotheses, never confirmed demand.
_RELATED = [
    ({'uniform','roup','vestuar'},['vestuário','roupas'], 'escola'),
    ({'limpez','detergent','desinfet'},['limpeza','detergentes'], 'hospital'),
    ({'embalag','sacol','caix'},['embalagens','sacos','caixas'], 'mercado'),
    ({'comput','informat','notebook'},['computadores','informática'], 'escola'),
    ({'movel','moveis','cadeir','mesa'},['móveis','cadeiras'], 'escola'),
    ({'cimento','construc','tijol','tint'},['construção'], 'construção'),
    ({'cafe'},['café'], ''),
    ({'cosmetic','perfume'},['cosméticos'], ''),
    ({'luva'},['luvas'], 'hospital'),
]


def words(value):
    return [w for w in re.findall(r'[a-z0-9]+', norm(value)) if w not in _STOP and len(w)>2][:12]


def stem(value):
    value=norm(value)
    for ending in ('agens','agem','oes','ais','eis','es','os','as','s'):
        if value.endswith(ending) and len(value)-len(ending)>=4:
            return value[:-len(ending)]
    return value


def plan_for(item):
    tokens=words(item)
    related=[];project='';matched=False
    for prefixes,aliases,works in _RELATED:
        if any(any(token.startswith(prefix) for prefix in prefixes) for token in tokens):
            related.extend(aliases)
            project=project or works
            matched=True
    news_term=next((v for prefix,v in _NEWS_TERMS.items() if any(t.startswith(prefix) for t in tokens)),item)
    return {'item':item,'terms':list(dict.fromkeys([item,*related]))[:4], 'news_term':news_term,
            'project_term':project or item,'project_related':bool(project),'has_aliases':matched}


def metadata(service,ctx,sess):
    cfg=service.config()
    provider=cfg.get('search_provider','auto')
    web=provider in {'auto','brave'} and cfg.get('search_configured',False)
    web=web or (provider in {'auto','google'} and bool(ctx.get('GOOGLE_API_KEY') and ctx.get('GOOGLE_CX')))
    return {'ai_configured':bool(cfg.get('nvidia_configured')),'ai_enabled':bool(cfg.get('enabled') and cfg.get('nvidia_configured')),
        'web_configured':bool(web),'admin':bool(ctx['_is_global_admin'](sess)), 'models':cfg.get('models',[]),
        'model':cfg.get('routing',{}).get('analysis',''), 'daily_limit':cfg.get('daily_limit',20),
        'source':'NVIDIA organiza os resultados coletados. As fontes públicas são consultadas pelo sistema; busca geral na web usa o buscador conectado.'}


class Automatic:
    def __init__(self,radar,intelligence,ctx,sess):
        self.radar=radar;self.intelligence=intelligence;self.ctx=ctx;self.sess=sess

    def ncm_catalog(self):
        def build():
            payload=self.radar.api(COMEX+'/general/filters/ncm?language=pt')
            if not isinstance(payload,dict) or payload.get('success') is False:
                raise SourceUnavailable('A lista oficial de produtos não respondeu.')
            raw=payload.get('data');pending=[raw];rows=[]
            while pending:
                part=pending.pop()
                if isinstance(part,list):
                    pending.extend(reversed(part))
                elif isinstance(part,dict) and re.fullmatch(r'\d{8}',str(part.get('id',''))):
                    rows.append({'code':str(part['id']),'description':text(part.get('text'),700)})
            if not rows:
                raise SourceUnavailable('A lista oficial de produtos mudou de formato.')
            return {'items':rows}
        return self.radar.cached('auto-ncm-catalog-v1',86400,build)['items']

    def ncm_candidates(self,plan):
        catalog=self.ncm_catalog();original={stem(t) for t in words(plan['item'])}
        aliases={stem(t) for phrase in plan['terms'][1:] for t in words(phrase)}-original
        ranked=[]
        for entry in catalog:
            if entry['code']==plan['item']:
                ranked.append({**entry,'match_score':1000,'match':'código informado'});continue
            description=norm(entry['description']);tokens={stem(w) for w in re.findall(r'[a-z]+',norm(entry['description'][11:])) if w not in _STOP}
            exact=sum(1 for t in original if any(t==w or (len(t)>=4 and w.startswith(t)) for w in tokens))
            related=sum(1 for t in aliases if any(t==w or (len(t)>=4 and w.startswith(t)) for w in tokens))
            if not exact and not related:
                continue
            score=exact*10+min(related,2)*3
            if norm(plan['item']) in description:score+=15
            # Codes ending in 00 are often broader than specific descriptions.
            if entry['code'].endswith('00'):score-=0.1
            ranked.append({**entry,'match_score':score,'match':'nome do item' if exact else 'termo relacionado'})
        return sorted(ranked,key=lambda r:(-r['match_score'],r['code']))[:6]

    def trade(self,plan,uf):
        candidates=self.ncm_candidates(plan)
        if not candidates:
            return {'items':[],'total':0,'candidates':[],'coverage':'Nenhuma NCM com descrição próxima foi identificada automaticamente. A busca nas demais fontes continua.'}
        meta=self.radar.comex_meta();items=[];queries=[];failures=[]
        # Two real catalog matches; separate cards avoid treating distinct NCMs as one product.
        for candidate in candidates[:2]:
            try:
                result=self.radar.comex({'flow':'export','ncm':candidate['code'],'from':meta['start'],'to':meta['latest'],'uf':uf})
                for row in result['items'][:5]:
                    items.append({**row,'ncm_description':candidate['description'], 'description':candidate['description']+' · '+row['description'],
                                  'consulted_at':result['consulted_at']})
                queries.append({'ncm':candidate['code'],'description':candidate['description'],'countries':result['total']})
            except (SourceUnavailable,ValueError) as exc:
                failures.append(text(exc,300))
        if not queries and failures:
            raise SourceUnavailable(failures[0])
        return {'items':items,'total':len(items),'candidates':candidates,'queries':queries,'notes':failures,
            'coverage':'Até dois códigos relacionados por descrição e cinco destinos por código. Correspondências para pesquisa, não classificação fiscal confirmada. Exportações no último período de 12 meses disponível.'}

    def inpi(self,plan,uf):
        catalog=self.radar.editions();edition=catalog['editions'][0]['edition']
        chosen=next((term for term in plan['terms'] if len(words(term))==1),plan['item'])
        result=self.radar.inpi({'edition':edition,'q':chosen,'uf':uf,'dispatch':'IPAS009'})
        return {**result,'query_used':chosen,'edition_used':edition}

    def web(self,plan,uf):
        from prospectax_intelligence import search
        query=plan['item']+' '+uf+' fornecedor empresa site oficial'
        hits,note=search(self.intelligence,self.ctx,query)
        if note and not hits:
            return {'items':[],'total':0,'not_configured':True,'coverage':note}
        rows=[]
        for hit in hits[:6]:
            url=link(hit.get('url'))
            if not url:continue
            rows.append({'kind':'web','id':hashlib.sha256(url.encode()).hexdigest()[:24], 'title':text(hit.get('title')),
                'description':text(hit.get('snippet'),700),'url':url,'source':'Busca web · resultado do buscador',
                'consulted_at':now(),'evidence':'Trecho do buscador. Confira o site antes de entrar em contato.'})
        return {'items':rows,'total':len(rows),'coverage':'Resultados do buscador conectado. A presença na busca não confirma vínculo comercial nem titularidade dos contatos.'}

    def run(self,data):
        item=text(data.get('item'),120)
        if not 3<=len(item)<=120 or not words(item):
            raise ValueError('Digite um item, produto ou serviço com pelo menos três caracteres.')
        uf=state(data.get('uf'));use_ai=data.get('use_ai',True) is not False
        config=metadata(self.intelligence,self.ctx,self.sess)
        fingerprint=json.dumps([item,uf,use_ai,config['ai_enabled'],config['web_configured'],config['model']],ensure_ascii=False)
        cache_key=hashlib.sha256((owner_key(self.sess)+'\0'+fingerprint).encode()).hexdigest()
        lock=_LOCKS[int(cache_key[:4],16)%len(_LOCKS)]
        with lock:
            with self.radar.db() as con:
                con.execute('CREATE TABLE IF NOT EXISTS automatic_cache(owner TEXT,key TEXT,payload TEXT,expires REAL,PRIMARY KEY(owner,key))')
                cached=con.execute('SELECT payload,expires FROM automatic_cache WHERE owner=? AND key=?',(owner_key(self.sess),cache_key)).fetchone()
            if cached and cached[1]>time.time():
                return {**json.loads(cached[0]),'from_cache':True}
            plan=plan_for(item)
            calls={
                'pncp':('Compras públicas',lambda:self.radar.pncp({'q':item,'uf':uf})),
                'contracts':('Fornecedores contratados',lambda:self.radar.pncp({'q':item,'uf':uf,'mode':'contrato'})),
                'obras':('Obras relacionadas',lambda:self.radar.obras({'q':plan['project_term'],'uf':uf,'situation':'Em execução'})),
                'expansao':('Notícias de expansão',lambda:self.radar.news({'q':plan['news_term'],'region':_UF_NAMES.get(uf,''),'days':30})),
                'inpi':('Pedidos de marca',lambda:self.inpi(plan,uf)),
                'comex':('Mercados internacionais',lambda:self.trade(plan,uf)),
                'web':('Na web',lambda:self.web(plan,uf)),
            }
            statuses={};by_source={};trade={}
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs={pool.submit(fn):key for key,(_,fn) in calls.items()}
                for future in as_completed(jobs):
                    key=jobs[future];label=calls[key][0]
                    try:
                        result=future.result();rows=result.get('items',[])
                        by_source[key]=rows[:20]
                        statuses[key]={'id':key,'label':label,'status':'not_configured' if result.get('not_configured') else 'partial' if result.get('notes') else 'ok',
                            'returned':len(rows),'total':result.get('total',len(rows)), 'coverage':text(result.get('coverage'),900),
                            'query':result.get('query_used') or (plan['project_term'] if key=='obras' else plan['news_term'] if key=='expansao' else item),
                            'consulted_at':result.get('consulted_at') or now()}
                        if key=='comex':trade={k:result[k] for k in ('candidates','queries','notes') if k in result}
                    except Exception as exc:
                        statuses[key]={'id':key,'label':label,'status':'unavailable','returned':0,
                            'error':text(exc,400) if isinstance(exc,(ValueError,SourceUnavailable)) else 'A fonte não respondeu nesta consulta.'}
            rows=[];seen=set()
            for key in calls:
                for row in by_source.get(key,[]):
                    unique=(row.get('kind'),row.get('id'),row.get('url'))
                    if unique in seen:continue
                    seen.add(unique)
                    row=dict(row);row['source_id']='R'+str(len(rows)+1);row['group']=key
                    row.setdefault('consulted_at',statuses[key].get('consulted_at'))
                    if key=='obras' and plan['project_related']:
                        row['related_note']='Possível aplicação do item em projetos de '+plan['project_term']+'. Hipótese comercial, sem confirmação de demanda.'
                    rows.append(row)
            ai={'source':'local','note':'Resultados organizados por fonte, sem análise de IA.'};analysis=None
            if use_ai and config['ai_enabled'] and rows:
                from prospectax_intelligence import redact
                sample=[r for key in calls for r in [row for row in rows if row['group']==key][:4]]
                evidence=[{'id':r['source_id'],'type':r['group'],'title':redact(r.get('title')),
                           'excerpt':redact((r.get('description') or '')[:500]),'location':text(r.get('city') or r.get('uf'),100)} for r in sample]
                try:
                    raw,ai=self.intelligence.complete(self.sess,'analysis',{'item':redact(item),'instruction':'Organize oportunidades para este item. Diferencie editais abertos, fornecedores contratados, hipóteses de obra, notícias, marcas e estatísticas. Cite apenas IDs existentes. Não invente empresas nem classifique risco. Selecione até seis achados mais pertinentes.', 'sources':evidence})
                    ai['sample_size']=len(evidence)
                    allowed={r['source_id']:r for r in sample};highlights=[]
                    if raw:
                        for recommendation in raw.get('opportunities',[])[:6]:
                            if not isinstance(recommendation,dict):continue
                            refs=[ref for ref in recommendation.get('sources',[]) if isinstance(ref,str) and ref in allowed]
                            if refs:
                                why=redact(text(recommendation.get('text'),650))
                                highlights.append({'text':why,'sources':list(dict.fromkeys(refs))})
                                for ref in refs:allowed[ref]['ai_reason']=why
                        analysis={'summary':redact(text(raw.get('summary'),1400)),'highlights':highlights}
                except Exception as exc:
                    ai={'source':'local','note':text(exc,350) if isinstance(exc,(ValueError,SourceUnavailable)) else 'A NVIDIA não respondeu. Os resultados das fontes foram preservados.'}
            elif use_ai and not config['ai_enabled']:
                ai['note']='Conecte e ative a NVIDIA uma vez para receber resumo e destaques. A busca nas fontes públicas já foi executada.'
            result={'item':item,'uf':uf,'items':rows,'total':len(rows),'sources':[statuses[k] for k in calls],
                'plan':plan,'trade':trade,'ai':ai,'analysis':analysis,'consulted_at':now(),'from_cache':False,
                'coverage':'Pesquisa nas fontes conectadas, com limites de cobertura. Não representa toda a internet; resultados indisponíveis e fontes não conectadas são indicados separadamente.'}
            # A failed collection/AI call is immediately retryable; partial results remain visible.
            if all(s['status'] in {'ok','not_configured'} for s in statuses.values()) and not (use_ai and config['ai_enabled'] and ai['source']!='nvidia'):
                with self.radar.db() as con:
                    con.execute('INSERT OR REPLACE INTO automatic_cache VALUES(?,?,?,?)',(owner_key(self.sess),cache_key,json.dumps(result,ensure_ascii=False),time.time()+600))
                    con.execute('DELETE FROM automatic_cache WHERE expires<?',(time.time(),))
            return result


def get_route(handler,path,qs,sess,ctx):
    if not path.startswith('/api/radar/auto/'):return False
    if not sess:
        ctx['_write_json'](handler,{'error':'Não autenticado'},401);return True
    if path!='/api/radar/auto/status':
        ctx['_write_json'](handler,{'error':'Rota não encontrada'},404);return True
    try:ctx['_write_json'](handler,metadata(ctx['_INTELLIGENCE'],ctx,sess),200)
    except Exception:ctx['_write_json'](handler,{'error':'Não foi possível ler o estado das conexões.'},500)
    return True


def post_route(handler,path,sess,ctx):
    if not path.startswith('/api/radar/auto/'):return False
    if not sess:
        ctx['_write_json'](handler,{'error':'Não autenticado'},401);return True
    try:
        if path=='/api/radar/auto/search':
            data=read_body(handler,limit=4000)
            result=Automatic(ctx['_RADAR'],ctx['_INTELLIGENCE'],ctx,sess).run(data)
        elif path=='/api/radar/auto/config':
            if not ctx['_is_global_admin'](sess):
                ctx['_write_json'](handler,{'error':'Somente o administrador geral pode conectar os serviços.'},403);return True
            data=read_body(handler,limit=4000);service=ctx['_INTELLIGENCE'];current=service.config()
            payload={k:current[k] for k in ('enabled','daily_limit','models','routing','search_provider') if k in current}
            for key in ('nvidia_key','search_key'):
                if str(data.get(key) or '').strip():payload[key]=str(data[key]).strip()
            if 'enabled' in data:payload['enabled']=data['enabled'] is True
            if payload.get('search_key'):payload['search_provider']='auto'
            model=str(data.get('model') or '')
            if model:
                if model not in {m['id'] for m in current.get('models',[])}:raise ValueError('Selecione um modelo habilitado.')
                payload['routing']={**current['routing'],'analysis':model}
            service.save_config(payload)
            result=metadata(service,ctx,sess)
        else:
            ctx['_write_json'](handler,{'error':'Rota não encontrada'},404);return True
        ctx['_write_json'](handler,result,200)
    except (ValueError,TypeError) as exc:ctx['_write_json'](handler,{'error':str(exc)},400)
    except Exception:ctx['_write_json'](handler,{'error':'A busca não pôde ser concluída. Tente novamente.'},502)
    return True
