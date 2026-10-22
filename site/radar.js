(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const labels = {pncp:'Compras públicas',obras:'Obras públicas',expansao:'Expansão empresarial',inpi:'Marcas · INPI',comex:'Comércio exterior',web:'Na web'};
  const ufs = 'AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split(' ');
  const results = {}, queries = {}, versions = {}, busy = {};
  let saved = [], noteItem = null, editionsReady = false, comexReady = false;
  let autoGroup='';
  const E = (tag, attrs = {}, children = []) => {
    const element = document.createElement(tag);
    for (const [key,value] of Object.entries(attrs)) {
      if (key === 'text') element.textContent = value;
      else if (key === 'class') element.className = value;
      else element.setAttribute(key,value);
    }
    for (const child of children) if (child) element.append(child);
    return element;
  };
  const safeUrl = value => {try {const u = new URL(value);return ['https:','http:'].includes(u.protocol) && !u.username && !u.password ? u.href : '';} catch {return '';}};
  const num = value => Number(value).toLocaleString('pt-BR',{maximumFractionDigits:2});
  const money = (value,currency='BRL') => value === null || value === undefined ? 'Não informado' : new Intl.NumberFormat('pt-BR',{style:'currency',currency,maximumFractionDigits:2}).format(value);
  const date = value => {
    if (!value) return 'Não informada';
    if (/^\d{2}\/\d{2}\/\d{4}$/.test(value)) return value;
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value.split('-').reverse().join('/');
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('pt-BR',{timeZone:'America/Sao_Paulo'});
  };
  const keyOf = item => [item.kind,item.id,item.url].join('|');
  const notice = (message,error=false) => { $('message').textContent=message;$('message').classList.toggle('error',error);$('message').hidden=false; };
  async function api(path,data) {
    const response = await fetch('/api/radar/'+path,{method:data === undefined?'GET':'POST',headers:{'X-Auth-Token':localStorage.getItem('prospectax_token')||'',...(data === undefined?{}:{'Content-Type':'application/json'})},body:data === undefined?undefined:JSON.stringify(data),cache:'no-store'});
    if (response.status === 401) {location.href='/login.html';throw Error('Entre novamente para continuar.');}
    let payload;
    try {payload=await response.json();} catch {throw Error('O servidor não retornou uma resposta válida.');}
    if (!response.ok) throw Error(payload.error||'Não foi possível concluir a consulta.');
    return payload;
  }
  async function run(action,button) {
    if (button) button.disabled=true;
    try {await action();} catch (error) {notice(error.message,true);} finally {if(button)button.disabled=false;}
  }
  function button(title,action,attrs={}) {
    const node=E('button',{type:'button',text:title,...attrs});
    node.addEventListener('click',()=>run(()=>action(node),node));
    return node;
  }
  function sourceLink(url,title) {
    const href=safeUrl(url);
    return href?E('a',{href,target:'_blank',rel:'noopener noreferrer',class:'button',text:title}):null;
  }
  const empty = message => E('div',{class:'empty',text:message});
  function detail(label,value) {
    return value ? E('span',{class:'detail'},[E('strong',{text:label}),document.createTextNode(String(value))]) : null;
  }
  function itemCard(item, savedMode=false) {
    const card=E('article',{class:'opportunity'});
    const tags=E('div');
    for (const tag of [labels[item.kind],item.status,item.uf,...(item.ufs||[])]) if(tag)tags.append(E('span',{class:'tag',text:tag}));
    card.append(E('div',{class:'card-head'},[E('div',{},[tags,E('h3',{text:item.title})])]));
    if (item.description) card.append(E('p',{class:'description',text:item.description}));
    if (item.related_note) card.append(E('p',{class:'related-note',text:item.related_note}));
    if (item.ai_reason) card.append(E('div',{class:'ai-reason'},[E('strong',{text:'Destaque da IA · '}),document.createTextNode(item.ai_reason)]));
    const details=E('div',{class:'details'});
    const fields=[];
    if (item.kind==='pncp') fields.push(['Órgão',item.organization],['Local',[item.city,item.uf].filter(Boolean).join(' / ')],['Modalidade',item.modality],['Publicação',item.published&&date(item.published)],['Prazo',item.deadline&&date(item.deadline)],['Fim de vigência',item.contract_end&&date(item.contract_end)],[item.value_label||'Valor',item.value === 0?'Não informado / confira no edital':money(item.value)],['Fornecedor',item.supplier],['CNPJ do fornecedor',item.supplier_cnpj]);
    if (item.kind==='obras') fields.push(['Projeto',item.id],['Intervenção',item.intervention],['Responsável',item.organization],['Endereço',item.address],['Início previsto',item.start&&date(item.start)],['Conclusão prevista',item.end&&date(item.end)],['Executores',(item.executors||[]).join('; ')]);
    if (item.kind==='expansao') fields.push(['Veículo',item.source],['Detectada no índice',item.captured&&date(item.captured)]);
    if (item.kind==='inpi') fields.push(['Processo',item.process],['Titular',(item.owners||[]).join('; ')],['Classe Nice',(item.classes||[]).join(', ')],['Depósito',item.deposited],['Publicação',item.published],['Ato',(item.dispatches||[]).map(d=>d.name).join('; ')]);
    if (item.kind==='comex') fields.push(['NCM',item.ncm],['Período',item.period],['Valor US$ FOB',money(item.value,'USD')],['Peso líquido',item.kg === undefined?'':num(item.kg)+' kg']);
    for (const [label,value] of fields) {const node=detail(label,value);if(node)details.append(node);}
    if(details.children.length)card.append(details);
    if(item.note)card.append(E('div',{class:'note',text:item.note}));
    const actions=E('div',{class:'actions'});
    const source=sourceLink(item.url,item.kind==='inpi'?'Conferir na revista oficial ↗':item.kind==='obras'?'Ver registro na API oficial ↗':'Abrir fonte ↗');
    if(source)actions.append(source);
    // Internal links are built separately; external links always use a checked protocol.
    if(item.supplier_cnpj) {
      const internal=E('a',{href:'/inteligencia.html?cnpj='+encodeURIComponent(item.supplier_cnpj),class:'button',text:'Pesquisar fornecedor'});
      actions.append(internal);
    }
    if(savedMode) {
      actions.append(button('Anotar',()=>openNote(item)));
      actions.append(button('Remover das salvas',async()=>{await api('remove',{id:item.saved_id});await loadSaved();notice('Oportunidade removida das salvas.');},{class:'save'}));
    } else {
      const existing=saved.find(row=>keyOf(row)===keyOf(item));
      if(existing)actions.append(E('span',{class:'button saved save',text:'Salva ✓'}));
      else actions.append(button('Salvar oportunidade',async()=>{await api('save',{item});await loadSaved();if(item.kind!=='web')renderCards(item.kind);if(results.auto)renderAuto();notice('Oportunidade salva na sua conta.');},{class:'save'}));
    }
    card.append(actions);
    card.append(E('div',{class:'source-footer'},[E('span',{text:item.source||''}),E('span',{text:item.consulted_at?'Consulta: '+date(item.consulted_at):item.saved_at?'Salva em '+date(item.saved_at):''})]));
    return card;
  }
  function renderCards(name) {
    if(!results[name])return;
    const rows=results[name].items||[];
    $(name+'Results').replaceChildren(...(rows.length?rows.map(row=>itemCard(row)):[empty('Nenhum resultado retornado para estes filtros. Ajuste a pesquisa ou consulte a fonte diretamente.')]));
  }
  function renderPager(name) {
    const target=$(name+'Pager');if(!target)return;
    const result=results[name];target.hidden=false;target.replaceChildren();
    const prev=button('← Anterior',()=>search(name,result.page-1,queries[name]));prev.disabled=result.page<=1;
    const next=button('Próxima →',()=>search(name,result.page+1,queries[name]));next.disabled=!result.has_more;
    target.append(prev,E('span',{text:'Página '+result.page}),next);
  }
  function metrics(result) {
    $('comexSummary').hidden=false;
    $('comexSummary').replaceChildren(...[['Valor total · US$ FOB',money(result.total_fob,'USD')],['Peso líquido total',num(result.total_kg)+' kg'],['Países com registros',String(result.total)]].map(([label,value])=>E('div',{class:'metric'},[E('span',{text:label}),E('strong',{text:value})])));
    const months=result.months||[];
    $('comexChart').hidden=!months.length;
    if(!months.length){$('comexChart').replaceChildren();return;}
    const chart=E('div',{class:'chart'},[E('h3',{text:'Valor mensal · US$ FOB'})]);
    const peak=Math.max(...months.map(m=>m.value),1);
    const bars=E('div',{class:'bars',role:'img','aria-label':'Evolução mensal do valor FOB. Os valores estão na tabela abaixo.'});
    for(const m of months)bars.append(E('div',{class:'bar-column'},[E('div',{class:'bar',style:'height:'+Math.max(m.value/peak*100,0)+'%',title:m.month+' · '+money(m.value,'USD')})]));
    chart.append(bars,E('div',{class:'chart-labels'},[E('span',{text:months[0].month}),E('span',{text:months.at(-1).month})]));
    const table=E('table',{},[E('thead',{},[E('tr',{},[E('th',{text:'Mês',scope:'col'}),E('th',{text:'Valor US$ FOB',scope:'col'})])]),E('tbody',{},months.map(m=>E('tr',{},[E('td',{text:m.month}),E('td',{text:money(m.value,'USD')})])))]);
    chart.append(E('details',{},[E('summary',{text:'Ver valores por mês'}),table]));
    $('comexChart').replaceChildren(chart);
  }
  async function search(name,page=1,captured=null) {
    if(busy[name])return;
    busy[name]=true;
    const form=$(name+'Form'), submit=form.querySelector('button[type="submit"]');
    submit.disabled=true;form.setAttribute('aria-busy','true');
    const version=(versions[name]||0)+1;versions[name]=version;
    const query=captured?{...captured}:Object.fromEntries(new FormData(form));query.page=page;
    queries[name]={...query};delete results[name];
    $(name+'Results').replaceChildren();
    const pager=$(name+'Pager');if(pager)pager.hidden=true;
    const exportButton=document.querySelector(`[data-export="${name}"]`);exportButton.disabled=true;
    if(name==='comex'){$('comexSummary').hidden=true;$('comexChart').hidden=true;}
    $(name+'Status').textContent=name==='inpi'?'Consultando a edição. Na primeira busca, o XML oficial será preparado…':'Consultando a fonte…';
    try {
      const endpoint={pncp:'pncp',obras:'obras',expansao:'expansao',inpi:'inpi/search',comex:'comex/search'}[name];
      const result=await api(endpoint+'?'+new URLSearchParams(query));
      if(versions[name]!==version)return;
      result.items=(result.items||[]).map(item=>({...item,consulted_at:result.consulted_at}));
      results[name]=result;
      const context=[query.q&&'Termo: '+query.q,query.uf&&'UF: '+query.uf].filter(Boolean).join(' · ');
      $(name+'Status').textContent=`${result.items.length} nesta página · ${num(result.total)} ${name==='comex'?'países':'registros'} na consulta${context?' · '+context:''}\n${result.coverage}\nFonte: ${result.source} · Consulta: ${date(result.consulted_at)}${result.from_cache?' · resultado em cache':''}${result.updated?' · Base atualizada em '+date(result.updated):''}`;
      renderCards(name);renderPager(name);if(name==='comex')metrics(result);
      exportButton.disabled=!result.items.length;
    } catch(error) {
      if(versions[name]===version){$(name+'Status').textContent=error.message;$(name+'Results').replaceChildren(empty('A consulta não foi concluída. Tente novamente ou abra o portal da fonte. Nenhum resultado foi presumido.'));}
    } finally {busy[name]=false;submit.disabled=false;form.removeAttribute('aria-busy');}
  }
  async function loadEditions() {
    $('inpiCatalogStatus').textContent='Consultando catálogo oficial…';
    try {
      const result=await api('inpi/editions');
      const previous=$('inpiEdition').value;
      $('inpiEdition').replaceChildren(...result.editions.map(edition=>E('option',{value:edition.edition,text:'RPI '+edition.edition+' · '+edition.date})));
      if(result.editions.some(r=>r.edition===previous))$('inpiEdition').value=previous;
      $('inpiCatalogStatus').textContent='Edições disponíveis no portal oficial · consulta '+date(result.consulted_at);editionsReady=true;
    } catch(error) {$('inpiCatalogStatus').textContent=error.message;editionsReady=false;}
  }
  async function loadComex() {
    $('comexCatalogStatus').textContent='Consultando período disponível…';
    try {
      const result=await api('comex/meta');
      $('comexFrom').max=result.latest;$('comexTo').max=result.latest;
      if(!$('comexFrom').value)$('comexFrom').value=result.start;
      if(!$('comexTo').value)$('comexTo').value=result.latest;
      $('comexCatalogStatus').textContent='Dados até '+result.latest+' · atualização da base: '+date(result.updated);comexReady=true;
    } catch(error) {$('comexCatalogStatus').textContent=error.message;comexReady=false;}
  }
  async function loadSaved() {
    const response=await api('saved');saved=response.items||[];$('savedCount').textContent=saved.length;
    document.querySelector('[data-export="saved"]').disabled=!saved.length;renderSaved();
  }
  function renderSaved() {
    const kind=$('savedFilter').value;
    const rows=saved.filter(item=>!kind||item.kind===kind);
    $('savedResults').replaceChildren(...(rows.length?rows.map(item=>itemCard(item,true)):[empty('Nenhuma oportunidade salva para este filtro. Use “Salvar oportunidade” nos resultados.')]));
  }
  function openNote(item) {noteItem=item;$('noteTitle').textContent=item.title;$('noteText').value=item.note||'';$('noteDialog').showModal();}
  function choose(name) {
    if(!['auto','pncp','obras','inpi','comex','saved'].includes(name))name='auto';
    document.querySelectorAll('[data-panel]').forEach(panel=>panel.hidden=panel.dataset.panel!==name);
    document.querySelectorAll('[data-tab]').forEach(button=>{const active=button.dataset.tab===name;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
    history.replaceState(null,'',location.pathname+'#'+name);
    if(name==='inpi'&&!editionsReady)run(loadEditions);
    if(name==='comex'&&!comexReady)run(loadComex);
    if(name==='saved')run(loadSaved);
  }
  function exportRows(name) {
    const rows=name==='saved'?saved.filter(row=>!$('savedFilter').value||row.kind===$('savedFilter').value):results[name]?.items||[];
    if(!rows.length){notice('Não há resultados para exportar.');return;}
    const fields=[['Módulo',r=>labels[r.kind]],['Título',r=>r.title],['Descrição',r=>r.description],['Organização',r=>r.organization],['Fornecedor',r=>r.supplier],['CNPJ do fornecedor',r=>r.supplier_cnpj],['Titulares',r=>(r.owners||[]).join('; ')],['Processo INPI',r=>r.process],['Edição RPI',r=>r.edition],['Cidade',r=>r.city],['UF',r=>r.uf||(r.ufs||[]).join(', ')],['País',r=>r.country],['NCM',r=>r.ncm],['Período',r=>r.period],['Valor',r=>r.value],['Moeda',r=>r.kind==='comex'?'USD':r.kind==='pncp'?'BRL':''],['Peso líquido kg',r=>r.kg],['Publicação',r=>r.published],['Prazo',r=>r.deadline],['Fonte',r=>r.source],['URL',r=>r.url],['Consultado em',r=>r.consulted_at],['Anotação',r=>r.note]];
    const cell=value=>{let s=String(value??'');if(/^[\t\r\n]|^[\s\uFEFF]*[=+\-@]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};
    const csv='\ufeff'+[fields.map(f=>cell(f[0])).join(';'),...rows.map(row=>fields.map(f=>cell(f[1](row))).join(';'))].join('\r\n');
    const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
    const anchor=E('a',{href:url,download:'ProspectaX_radar_'+name+'_'+new Date().toISOString().slice(0,10)+'.csv'});document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  document.querySelectorAll('select.uf').forEach(select=>select.append(...ufs.map(uf=>E('option',{value:uf,text:uf}))));
  for(const name of ['pncp','obras','expansao','inpi','comex'])$(name+'Form').addEventListener('submit',event=>{event.preventDefault();run(()=>search(name));});
  document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>choose(button.dataset.tab)));
  document.querySelectorAll('[data-export]').forEach(button=>button.addEventListener('click',()=>exportRows(button.dataset.export)));
  document.querySelectorAll('[data-obras-mode]').forEach(button=>button.addEventListener('click',()=>{const news=button.dataset.obrasMode==='news';$('publicWorks').hidden=news;$('expansionNews').hidden=!news;document.querySelectorAll('[data-obras-mode]').forEach(b=>b.classList.toggle('active',b===button));}));
  $('reloadEditions').addEventListener('click',event=>run(loadEditions,event.currentTarget));
  $('reloadComex').addEventListener('click',event=>run(loadComex,event.currentTarget));
  $('reloadSaved').addEventListener('click',event=>run(loadSaved,event.currentTarget));
  $('savedFilter').addEventListener('change',renderSaved);
  $('cancelNote').addEventListener('click',()=>$('noteDialog').close());
  $('noteForm').addEventListener('submit',event=>{event.preventDefault();const item=noteItem;if(!item)return;run(async()=>{await api('save',{item,note:$('noteText').value});await loadSaved();$('noteDialog').close();notice('Anotação salva.');},event.currentTarget.querySelector('button[type="submit"]'));});
  async function loadAutoStatus() {
    try {
      const d=await api('auto/status');
      $('autoConnections').textContent='Fontes públicas conectadas · '+(d.ai_enabled?'NVIDIA ativada':d.ai_configured?'NVIDIA conectada, análise desativada':'NVIDIA ainda não conectada')+' · '+(d.web_configured?'Busca geral na web conectada':'Busca geral na web opcional, ainda não conectada');
      $('autoSetup').hidden=!d.admin;
      $('autoAiEnabled').checked=d.ai_enabled||!d.ai_configured;
      $('autoModel').replaceChildren(...d.models.map(m=>E('option',{value:m.id,text:m.label||m.id})));
      $('autoModel').value=d.model;
      if(!d.admin&&!d.ai_enabled)$('autoConnections').append(document.createTextNode(' · O administrador pode conectar a NVIDIA.'));
    } catch(error) {$('autoConnections').textContent=error.message;}
  }
  function renderAuto() {
    const d=results.auto;if(!d)return;
    const rows=d.items.filter(row=>!autoGroup||row.group===autoGroup);
    $('autoResults').replaceChildren(...(rows.length?rows.map(row=>{const card=itemCard(row);card.id='auto-result-'+row.source_id;return card;}):[empty(d.sources.some(s=>s.status==='unavailable')?'As fontes que responderam não retornaram resultados para este filtro. Veja as fontes indisponíveis acima e tente novamente.':'Nenhum resultado retornado para este filtro nas fontes consultadas.')]));
    const filters=$('autoFilter');filters.hidden=!d.items.length;filters.replaceChildren();
    const all=button('Todos · '+d.items.length,()=>{autoGroup='';renderAuto();});all.classList.toggle('active',!autoGroup);filters.append(all);
    for(const source of d.sources){const count=d.items.filter(row=>row.group===source.id).length;if(!count)continue;const b=button(source.label+' · '+count,()=>{autoGroup=source.id;renderAuto();});b.classList.toggle('active',autoGroup===source.id);filters.append(b);}
  }
  async function automaticSearch() {
    if(busy.auto)return;busy.auto=true;
    const form=$('autoForm'),submit=form.querySelector('button[type="submit"]');submit.disabled=true;form.setAttribute('aria-busy','true');
    $('autoStatus').textContent='Pesquisando nas fontes conectadas. O sistema prepara a edição do INPI e cruza descrições de produtos; a análise de IA vem ao final.';
    $('autoResults').replaceChildren();$('autoSummary').hidden=true;$('autoSources').hidden=true;$('autoFilter').hidden=true;
    document.querySelector('[data-export="auto"]').disabled=true;delete results.auto;autoGroup='';
    try {
      const fields=Object.fromEntries(new FormData(form));
      const d=await api('auto/search',{item:fields.item,uf:fields.uf,use_ai:$('autoUseAi').checked});results.auto=d;
      const responding=d.sources.filter(s=>s.status==='ok'||s.status==='partial').length;
      $('autoStatus').textContent=d.total+' resultados reunidos para “'+d.item+'” · '+responding+' fontes responderam · '+date(d.consulted_at)+(d.from_cache?' · resultado recente em cache':'');
      const summary=$('autoSummary');summary.hidden=false;summary.replaceChildren();
      if(d.analysis?.summary)summary.append(E('h3',{text:'Visão geral'}),E('p',{text:d.analysis.summary}));
      summary.append(E('p',{class:'auto-ai-status',text:d.ai.source==='nvidia'?'Resumo para revisão · NVIDIA · '+d.ai.model+' · '+d.ai.sample_size+' resultados analisados, até quatro por fonte':d.ai.note}));
      if(d.analysis?.highlights?.length){const list=E('ul');for(const highlight of d.analysis.highlights){const li=E('li',{text:highlight.text+' '});for(const ref of highlight.sources){const a=E('a',{href:'#auto-result-'+ref,text:'['+ref+'] ',class:'evidence-ref'});a.addEventListener('click',event=>{event.preventDefault();autoGroup='';renderAuto();$('auto-result-'+ref)?.scrollIntoView({behavior:'smooth',block:'start'});});li.append(a);}list.append(li);}summary.append(list);}
      const details=E('details',{class:'source-report'},[E('summary',{text:'Ver fontes consultadas e cobertura da busca'})]);
      for(const source of d.sources)details.append(E('p',{},[E('strong',{text:source.label+' · '}),document.createTextNode(source.status==='unavailable'?'Indisponível: '+source.error:source.status==='not_configured'?'Ainda não conectada. '+source.coverage:source.returned+' resultados retornados. '+source.coverage),source.query?E('small',{text:' Busca utilizada: '+source.query}):null]));
      if(d.trade?.candidates?.length)details.append(E('p',{text:'Códigos relacionados localizados no catálogo oficial: '+d.trade.candidates.map(c=>c.description).join('; ')}));
      details.append(E('p',{text:d.coverage}));$('autoSources').hidden=false;$('autoSources').replaceChildren(details);
      renderAuto();document.querySelector('[data-export="auto"]').disabled=!d.items.length;
    } catch(error) {$('autoStatus').textContent=error.message;$('autoResults').replaceChildren(empty('A busca não foi concluída. Tente novamente.'));}
    finally {busy.auto=false;submit.disabled=false;form.removeAttribute('aria-busy');}
  }
  $('autoForm').addEventListener('submit',event=>{event.preventDefault();run(automaticSearch);});
  $('autoConfigForm').addEventListener('submit',event=>{event.preventDefault();const submit=event.currentTarget.querySelector('button[type="submit"]');run(async()=>{await api('auto/config',{nvidia_key:$('autoNvidiaKey').value,search_key:$('autoSearchKey').value,enabled:$('autoAiEnabled').checked,model:$('autoModel').value});$('autoNvidiaKey').value='';$('autoSearchKey').value='';await loadAutoStatus();notice('Conexões salvas. Agora digite o item e faça a busca.');},submit);});
  choose(location.hash.slice(1)||'auto');run(loadSaved);run(loadAutoStatus);
})();
