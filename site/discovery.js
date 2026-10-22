/* Descoberta de compradores e consultas gratuitas. Sem chaves no navegador. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const html = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const cnpj = value => String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  const https = value => { try { const u = new URL(value); return u.protocol === 'https:' && !u.username && !u.password ? u.href : ''; } catch { return ''; } };
  const money = (value, currency='BRL') => { if(value === null || value === undefined || !Number.isFinite(Number(value))) return 'Preço não informado'; try { return new Intl.NumberFormat('pt-BR',{style:'currency',currency}).format(Number(value)); } catch { return `${value} ${currency}`; } };
  const productPrice = p => p.price !== null && p.price !== undefined ? money(p.price,p.currency) : p.price_min !== null && p.price_min !== undefined ? (p.price_max !== null && p.price_max !== undefined && p.price_max > p.price_min ? money(p.price_min,p.currency)+' a '+money(p.price_max,p.currency) : 'A partir de '+money(p.price_min,p.currency)) : 'Preço não informado';
  const dateTime = value => value ? new Date(value).toLocaleString('pt-BR') : 'Não informada';
  const msg = (id, text, error=false) => { if($(id)) $(id).innerHTML=`<div class="discovery-notice ${error?'discovery-error':''}">${html(text)}</div>`; };
  async function api(url, data) {
    const res = await fetch(url, data === undefined ? {cache:'no-store'} : {method:'POST', headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
    let result; try { result=await res.json(); } catch { throw new Error('O servidor não retornou uma resposta válida.'); }
    if(!res.ok){const error=new Error(result.error || 'Não foi possível concluir.');error.status=res.status;error.result=result;throw error;}
    return result;
  }
  let ready=false, saving=null, saveTimer=null, changeVersion=0, savedVersion=0, stateVersion=0, baseline={}, journalKey='';
  let segments=[], similarCodes=[], mapCities=[], mapRows=[], products=[], comparison=[], productPage=1;
  const filterNames=['contact','porte','cep','tipo','exclude','bairro','abertura_de','abertura_ate'];
  const filterState=()=>Object.fromEntries(filterNames.map(key=>[key,$('discovery_'+key)?.value||'']));
  const restoreFilters = values => filterNames.forEach(key=>{if($('discovery_'+key)) $('discovery_'+key).value=values?.[key]||'';});
  function snapshot(){return {leadTools:Object.fromEntries(Object.entries(leadTools).map(([id,r])=>[id,Object.fromEntries(Object.entries(r).filter(([k])=>k!=='lastEnrich'))])),savedSearches,discovery:filterState(),productSelection:comparison};}
  function journal(){if(journalKey)localStorage.setItem(journalKey,JSON.stringify({base:baseline,state:snapshot(),version:stateVersion}));}
  const equal=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
  function reconcile(base,local,remote){
    if(equal(local,base))return remote;if(equal(remote,base)||equal(local,remote))return local;
    if(Array.isArray(local)&&Array.isArray(remote)&&[...(base||[]),...local,...remote].every(x=>x&&x.id)){
      const map=a=>Object.fromEntries((a||[]).map(x=>[x.id,x]));return Object.values(reconcile(map(base),map(local),map(remote)));
    }
    if(local&&remote&&typeof local==='object'&&typeof remote==='object'&&!Array.isArray(local)&&!Array.isArray(remote)){
      const out={};for(const key of new Set([...Object.keys(base||{}),...Object.keys(local),...Object.keys(remote)])){const value=reconcile(base?.[key],local[key],remote[key]);if(value!==undefined)out[key]=value;}return out;
    }
    throw new Error('O mesmo dado foi alterado em outra janela. Sua versão foi preservada neste navegador; exporte o backup antes de resolver o conflito.');
  }
  function applyState(state){state=JSON.parse(JSON.stringify(state));leadTools=state.leadTools||{};savedSearches=state.savedSearches||[];restoreFilters(state.discovery||{});comparison=Array.isArray(state.productSelection)?state.productSelection:[];}
  function queueSave(){if(!ready)return;changeVersion++;try{journal();}catch{showToast('Não foi possível guardar a cópia local. Exporte um backup.','error');}clearTimeout(saveTimer);saveTimer=setTimeout(()=>flush().catch(e=>showToast(e.status===409?e.message:'Não foi possível sincronizar. A cópia local será recuperada no próximo acesso.','error')),700);}
  async function flush(){
    if(!ready)return;
    clearTimeout(saveTimer);
    if(saving){await saving;if(savedVersion<changeVersion)return flush();return;}
    const version=changeVersion;
    const payload=JSON.parse(JSON.stringify(snapshot()));const captured=JSON.parse(JSON.stringify(payload));journal();
    saving=(async()=>{
      try{return await api('/api/discovery/state',{...payload,_version:stateVersion});}
      catch(error){
        if(error.status!==409)throw error;
        const remote=error.result.state||{};
        const merged=reconcile(baseline,payload,remote);
        // Uma segunda alteracao concorrente gera conflito explicito e preserva o journal.
        const result=await api('/api/discovery/state',{...merged,_version:error.result.version});
        applyState(changeVersion===version?merged:reconcile(captured,snapshot(),merged));
        Object.assign(payload,merged);return result;
      }
    })();
    try{const result=await saving;stateVersion=result.version;baseline=JSON.parse(JSON.stringify(payload));savedVersion=version;if(savedVersion===changeVersion)localStorage.removeItem(journalKey);else journal();}finally{saving=null;}
    if(savedVersion<changeVersion)return flush();
  }
  function csv(name, rows){
    if(!rows.length){showToast('Não há registros para exportar.','error');return;}
    const keys=Object.keys(rows[0]);
    const cell=v=>{let s=String(v??'');if(/^[\s]*[=+@-]/.test(s))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};
    const raw='\uFEFF'+[keys.map(cell).join(';'),...rows.map(row=>keys.map(k=>cell(row[k])).join(';'))].join('\r\n');
    const url=URL.createObjectURL(new Blob([raw],{type:'text/csv;charset=utf-8'}));
    const a=document.createElement('a');a.href=url;a.download=name+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function copy(value){await navigator.clipboard.writeText(value);showToast('Copiado.','success');}
  function useCodes(items){
    for(const it of items){const code=String(it.code).replace(/\D/g,'');if(code.length===7&&!cnaes.includes(code))cnaes.push(code);if(it.desc)cnaeDescMap[code]=it.desc;}
    renderCnaeTags();saveUserPrefs();switchPage('prospector');saveSearchState();showToast('CNAEs adicionados. Escolha o estado e clique em Filtrar Clientes.','success');
  }
  function installUI(){
    const tab=document.createElement('button');tab.className='topbar-tab';tab.textContent='Encontrar compradores';tab.dataset.action='open-discovery';
    document.querySelector('.topbar-tabs').insertBefore(tab,document.querySelector('.topbar-tabs').children[1]);
    const page=document.createElement('div');page.className='page';page.id='page-discovery';
    page.innerHTML=`<div class="discovery-area"><div class="discovery-header"><div><h2>Encontre seus próximos compradores</h2><p class="discovery-muted">Parta dos produtos que você vende, de um cliente conhecido ou de estabelecimentos nos mapas públicos.</p></div><button class="tool-btn" data-action="back-search">Busca por CNAE e estado</button></div>
      <div class="discovery-grid">
        <section class="discovery-card"><h3>O que você vende?</h3><p class="discovery-muted">Selecione os segmentos compatíveis com seu catálogo. Os CNAEs serão acrescentados à busca atual.</p><form data-action="buyer-search"><label for="buyerOffer">Produtos ou segmento</label><input id="buyerOffer" placeholder="Ex.: utilidades de estética, cosméticos, embalagens" maxlength="200"><div class="discovery-actions"><button class="primary" type="submit">Sugerir compradores</button></div></form><div id="buyerResults" aria-live="polite"></div></section>
        <section class="discovery-card"><h3>Empresas parecidas com um cliente</h3><p class="discovery-muted">Use o CNAE principal e os secundários de uma empresa já conhecida para ampliar sua busca.</p><form data-action="similar-search"><label for="similarCnpj">CNPJ de referência</label><input id="similarCnpj" placeholder="CNPJ completo, com números ou letras" maxlength="18"><div class="discovery-actions"><button class="primary" type="submit">Encontrar atividades</button></div></form><div id="similarResults" aria-live="polite"></div></section>
        <section class="discovery-card discovery-full"><h3>Mais estabelecimentos nos mapas públicos</h3><p class="discovery-muted">Encontre nomes, sites e contatos publicados no OpenStreetMap. A cobertura é colaborativa e os resultados podem não ter CNPJ.</p><div class="discovery-grid"><div><form data-action="find-city"><label for="mapsCity">Cidade</label><input id="mapsCity" placeholder="Ex.: Maringá" maxlength="100"><label for="mapsUf">Estado</label><select id="mapsUf">${STATE_ORDER.map(s=>`<option value="${s}" ${s==='PR'?'selected':''}>${s}</option>`).join('')}</select><div class="discovery-actions"><button type="submit">Localizar cidade</button></div></form><div id="mapCityResults" aria-live="polite"></div></div><div><label for="mapsSegment">Estabelecimentos</label><select id="mapsSegment"><option value="beleza">Estética, salões e barbearias</option><option value="revenda_cosmeticos">Lojas de cosméticos</option><option value="alimentacao">Restaurantes e lanchonetes</option><option value="mercados">Mercados e mercearias</option><option value="pet">Pet shops</option><option value="construcao">Materiais de construção</option><option value="auto">Autopeças e oficinas</option></select><label for="mapsRadius">Raio a partir do centro escolhido</label><select id="mapsRadius"><option value="1000">1 km</option><option value="3000" selected>3 km</option><option value="5000">5 km</option><option value="10000">10 km</option><option value="15000">15 km</option></select><div class="discovery-actions"><button class="primary" data-action="maps-search">Buscar estabelecimentos</button><button data-action="maps-export">Exportar encontrados</button></div></div></div><div id="mapsStatus" aria-live="polite"></div><div id="mapsResults" class="places-results"></div><p class="discovery-muted" style="margin-top:12px">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors · ODbL</a>. Busca por raio, até 200 elementos por consulta. Fontes gratuitas com uso moderado: cache de 24 horas e até 40 consultas externas por dia nesta instalação.</p></section>
        <section class="discovery-card discovery-full"><h3>Pendências e consultas públicas</h3><p class="discovery-muted">Consulte dívida ativa e sanções publicadas. Cada fonte informa um tipo de registro; não há classificação automática de crédito.</p><form data-action="public-check"><label for="publicCnpj">CNPJ da empresa</label><input id="publicCnpj" maxlength="18" placeholder="Informe o CNPJ"><div class="discovery-actions"><button class="primary" type="submit">Consultar fontes gratuitas</button></div></form><div id="publicResults" class="public-checks" aria-live="polite"></div>
        <details class="discovery-settings" id="publicSettings"><summary>Configurar consultas dentro da ferramenta</summary><p class="discovery-muted">A dívida ativa usa os CSVs oficiais importados pelo administrador com o arquivo IMPORTAR_DIVIDA_ATIVA.bat. A API de sanções é gratuita e exige uma chave do Portal da Transparência.</p><a href="https://www.gov.br/pgfn/pt-br/assuntos/divida-ativa-da-uniao/transparencia-fiscal-1/dados-abertos" target="_blank" rel="noopener noreferrer">Baixar dados oficiais PGFN</a> · <a href="https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email" target="_blank" rel="noopener noreferrer">Obter chave gratuita CGU</a><label for="cguKey">Chave CGU — somente administrador geral</label><input id="cguKey" type="password" autocomplete="new-password"><div class="discovery-actions"><button data-action="save-cgu">Salvar chave no servidor</button></div><div id="publicSettingsStatus"></div></details></section>
      </div></div>`;
    $('page-china').before(page);
    const filters=document.createElement('details');filters.className='discovery-filters';filters.id='discoveryFilters';
    filters.innerHTML=`<summary>Potencializar a busca</summary><p class="discovery-muted">Filtros sobre toda a base encontrada, incluindo todas as páginas e a exportação.</p>
      <label for="discovery_contact">Contato disponível na base</label><select id="discovery_contact"><option value="">Todos</option><option value="qualquer">Telefone ou e-mail</option><option value="telefone">Com telefone</option><option value="email">Com e-mail</option><option value="ambos">Com telefone e e-mail</option><option value="sem_contato">Sem telefone/e-mail utilizável</option></select>
      <label for="discovery_porte">Porte</label><select id="discovery_porte"><option value="">Todos</option><option value="01">Microempresa</option><option value="03">Empresa de pequeno porte</option><option value="05">Demais</option></select>
      <label for="discovery_cep">CEP ou início do CEP</label><input id="discovery_cep" placeholder="Ex.: 870" maxlength="9" inputmode="numeric">
      <label for="discovery_exclude">Excluir nomes que contenham</label><input id="discovery_exclude" placeholder="Separe palavras por vírgula" maxlength="250">
      <details><summary>Mais campos da Receita</summary><label for="discovery_bairro">Bairro</label><input id="discovery_bairro" maxlength="100"><label for="discovery_tipo">Estabelecimento</label><select id="discovery_tipo"><option value="">Matrizes e filiais</option><option value="matriz">Matrizes</option><option value="filial">Filiais</option></select><label for="discovery_abertura_de">Abertura a partir de</label><input id="discovery_abertura_de" type="date"><label for="discovery_abertura_ate">Abertura até</label><input id="discovery_abertura_ate" type="date"><p class="discovery-muted" id="discoveryCapabilities">Conferindo os campos disponíveis na base…</p></details>
      <p class="discovery-muted">Telefone/e-mail cadastrado não comprova disponibilidade no WhatsApp ou entrega de mensagens.</p><div class="discovery-actions"><button data-action="apply-discovery" class="primary">Aplicar filtros</button><button data-action="clear-discovery">Limpar extras</button></div>`;
    document.querySelector('.sidebar').insertBefore(filters,$('btnRun').parentElement);
    $('page-china').innerHTML=`<div class="discovery-area"><div class="discovery-header"><div><h2>Produtos para importar</h2><p class="discovery-muted">Pesquise produtos específicos, compare anúncios e abra a oferta original.</p></div></div><div class="discovery-grid"><section class="discovery-card"><h3>Buscar no catálogo</h3><form data-action="product-search"><label for="productKeyword">Produto e características</label><input id="productKeyword" placeholder="Ex.: cadeira de barbeiro hidráulica preta" maxlength="200"><label for="productMaxPrice">Preço máximo anunciado em R$ (opcional)</label><input id="productMaxPrice" type="number" min="0" step="0.01"><div class="discovery-actions"><button type="submit" class="primary">Buscar produtos</button></div></form><div id="productConnection" class="discovery-notice">Conferindo integração gratuita…</div><details class="discovery-settings" id="productSettings"><summary>Configurar API gratuita com cadastro</summary><p class="discovery-muted">O catálogo AliExpress Affiliate exige aplicativo aprovado e chave própria. Não são usados créditos da Apify. A disponibilidade depende das permissões da sua conta.</p><a href="https://developer.alibaba.com/docs/api.htm?apiId=45803" target="_blank" rel="noopener noreferrer">Documentação oficial</a><label for="productAppKey">App Key</label><input id="productAppKey" autocomplete="off"><label for="productAppSecret">App Secret</label><input id="productAppSecret" type="password" autocomplete="new-password"><label for="productTracking">Tracking ID (se exigido pela sua conta)</label><input id="productTracking" autocomplete="off"><label for="productProtocol">Protocolo autorizado no aplicativo</label><select id="productProtocol"><option value="top">TOP — referência oficial do método</option><option value="sync">AliExpress Open Platform — Sync</option></select><div class="discovery-actions"><button data-action="product-config">Salvar no servidor</button></div><div id="productConfigStatus"></div></details></section>
      <section class="discovery-card"><h3>Já tem o link de um produto?</h3><p class="discovery-muted">Adicione um anúncio específico para comparar os dados publicados. Funciona quando a página permite leitura e disponibiliza dados de produto.</p><form data-action="product-import"><label for="productUrl">Link do anúncio</label><input id="productUrl" type="url" placeholder="https://…"><div class="discovery-actions"><button type="submit">Adicionar produto por link</button></div></form><div id="productImportStatus"></div><div class="discovery-notice">Preço anunciado não inclui automaticamente frete, tributos ou custos de importação. Origem e pedido mínimo precisam ser confirmados no anúncio.</div></section>
      <section class="discovery-card discovery-full"><div class="discovery-header"><h3>Produtos encontrados</h3><div class="discovery-actions" style="margin:0"><button data-action="products-export">Exportar produtos</button><button data-action="compare-products">Comparar selecionados</button></div></div><div id="productsStatus" aria-live="polite"></div><div id="productsResults" class="product-grid"></div><div class="discovery-actions"><button id="productsPrev" data-action="products-prev" disabled>Anterior</button><span id="productsPage">Página 1</span><button id="productsNext" data-action="products-next" disabled>Próxima</button></div><div id="productsComparison" class="comparison-wrap"></div></section></div></div>`;
  }
  async function loadBuyers(){
    msg('buyerResults','Buscando segmentos…');
    const d=await api('/api/discovery/buyers?q='+encodeURIComponent($('buyerOffer').value));segments=d.segments||[];
    $('buyerResults').innerHTML=segments.length?segments.map((s,i)=>`<div class="discovery-result"><strong>${html(s.name)}</strong><p class="discovery-muted">${html(s.reason)}</p>${s.cnaes.map(c=>`<label class="discovery-choice"><input type="checkbox" data-buyer-group="${i}" value="${html(c.code)}" checked><span>${html(c.code)} · ${html(c.desc)}</span></label>`).join('')}<div class="discovery-actions"><button data-action="use-buyers" data-index="${i}">Usar CNAEs selecionados</button></div></div>`).join(''):'<p class="discovery-muted">Ainda não há grupo sugerido para esse termo. Use a busca por descrição ou código CNAE na aba Prospecção.</p>';
  }
  async function loadSimilar(){
    msg('similarResults','Consultando atividades na base local…');const d=await api('/api/discovery/similar?cnpj='+encodeURIComponent(cnpj($('similarCnpj').value)));similarCodes=d.cnaes||[];
    $('similarResults').innerHTML=`<div class="discovery-result"><strong>${html(d.nome)}</strong><p class="discovery-muted">${html(d.cidade)} / ${html(d.uf)} · ${html(formatCnpj(d.cnpj))}</p>${similarCodes.map(c=>`<label class="discovery-choice"><input type="checkbox" data-similar-code value="${html(c.code)}" ${c.primary?'checked':''}><span>${html(c.code)} · ${html(c.desc||'Atividade cadastrada')} ${c.primary?'(principal)':''}</span></label>`).join('')}<div class="discovery-actions"><button data-action="use-similar">Buscar compradores destas atividades</button></div></div>`;
  }
  async function findCities(){
    msg('mapCityResults','Localizando cidade…');mapCities=[];
    const d=await api('/api/discovery/cities?'+new URLSearchParams({city:$('mapsCity').value,uf:$('mapsUf').value}));mapCities=d.cities||[];
    $('mapCityResults').innerHTML=mapCities.length?`<label for="mapsCenter">Confirme o centro da busca</label><select id="mapsCenter">${mapCities.map((c,i)=>`<option value="${i}">${html(c.name)} / ${html(c.uf)}</option>`).join('')}</select>`:'<p class="discovery-muted">Cidade não localizada nessa UF. Confira o nome completo.</p>';
  }
  async function searchMap(){
    const city=mapCities[Number($('mapsCenter')?.value)];if(!city)throw new Error('Localize e confirme a cidade primeiro.');
    mapRows=[];$('mapsResults').innerHTML='';msg('mapsStatus','Consultando estabelecimentos públicos…');
    const d=await api('/api/discovery/places?'+new URLSearchParams({segment:$('mapsSegment').value,lat:city.lat,lon:city.lon,radius:$('mapsRadius').value}));mapRows=d.rows||[];
    msg('mapsStatus',`${mapRows.length} estabelecimentos com nome · ${d.from_cache?'resultado em cache':'consulta pública'} · ${dateTime(d.consulted_at)}${d.limited?' · Limite atingido: reduza o raio para detalhar.':''}`);
    $('mapsResults').innerHTML=mapRows.length?mapRows.map((r,i)=>`<article class="place-card"><strong>${html(r.nome)}</strong><p>${html(r.endereco||'Endereço não informado no mapa')}</p><p>${html(r.telefone||'Telefone não informado')}<br>${html(r.email||'E-mail não informado')}${r.cnpj?'<br>CNPJ: '+html(formatCnpj(r.cnpj)):''}</p><div class="discovery-actions">${https(r.website)?`<a href="${html(https(r.website))}" target="_blank" rel="noopener noreferrer">Site publicado</a>`:''}<a href="${html(https(r.source_url))}" target="_blank" rel="noopener noreferrer">Ver no mapa</a><button data-action="copy-place" data-index="${i}">Copiar contato</button>${r.cnpj?`<button data-action="place-cnpj" data-index="${i}">Consultar CNPJ</button>`:''}</div></article>`).join(''):'<p class="discovery-muted">Nenhum estabelecimento com nome encontrado nesse raio. A ausência no mapa não significa ausência de empresas.</p>';
  }
  async function productStatus(){const d=await api('/api/products/status');msg('productConnection',d.configured?'API cadastrada. A busca consulta produtos do catálogo autorizado.':'Pesquisa automática precisa da chave gratuita AliExpress Affiliate. Sem chave, você pode adicionar um produto por link.');$('productProtocol').value=d.protocol||'top';}
  function renderProducts(){
    $('productsResults').innerHTML=products.map((p,i)=>`<article class="product-card"><div class="product-image">${https(p.image_url)?`<img src="${html(https(p.image_url))}" loading="lazy" referrerpolicy="no-referrer" alt="${html(p.title)}">`:'Imagem não publicada'}</div><div class="product-info"><span class="product-badge">${html(p.source_type||'Anúncio público')}</span><h4>${html(p.title)}</h4><div class="product-price">${html(productPrice(p))}</div><p class="discovery-muted">${html(p.supplier||'Fornecedor: consulte o anúncio')}<br>Pedido mínimo: ${html(p.moq||'não informado')}</p><div class="product-actions"><a href="${html(https(p.url))}" target="_blank" rel="noopener noreferrer">Ver oferta</a><button data-action="select-product" data-index="${i}">${comparison.some(c=>c.id===p.id)?'Remover da comparação':'Comparar'}</button></div><details><summary class="discovery-muted">Origem do anúncio</summary><p class="discovery-muted">${html(p.source)} · ${html(dateTime(p.consulted_at))}</p></details></div></article>`).join('');
    $('productsResults').querySelectorAll('img').forEach(img=>img.addEventListener('error',()=>{img.parentElement.textContent='Imagem indisponível';},{once:true}));
  }
  async function searchProducts(page=1){
    products=[];renderProducts();msg('productsStatus','Buscando produtos no catálogo…');$('productsPrev').disabled=true;$('productsNext').disabled=true;
    const d=await api('/api/products/search?'+new URLSearchParams({keyword:$('productKeyword').value,max_price:$('productMaxPrice').value,page}));
    if(d.error){msg('productsStatus',d.error,true);return;}productPage=page;
    products=(d.products||[]).map(p=>({...p,consulted_at:d.consulted_at}));renderProducts();
    msg('productsStatus',`${products.length} produtos nesta página · ${d.from_cache?'cache local':'catálogo consultado'} · termo: ${d.query}${products.length?'':' · Nenhum produto confirmado para estes filtros.'}`);
    $('productsPage').textContent='Página '+page;$('productsPrev').disabled=page<=1;$('productsNext').disabled=!d.has_more;
  }
  function renderComparison(){
    const target=$('productsComparison');if(!comparison.length){target.innerHTML='<p class="discovery-muted">Selecione até quatro produtos para comparar.</p>';return;}
    target.innerHTML=`<table class="comparison-table"><thead><tr><th>Comparação</th>${comparison.map(p=>`<th>${html(p.title)}</th>`).join('')}</tr></thead><tbody>${[['Preço anunciado',productPrice],['Fornecedor',p=>p.supplier||'Não informado'],['Pedido mínimo',p=>p.moq||'Não informado'],['Fonte',p=>p.source],['Consulta',p=>dateTime(p.consulted_at)]].map(([label,fn])=>`<tr><th>${label}</th>${comparison.map(p=>`<td>${html(fn(p))}</td>`).join('')}</tr>`).join('')}<tr><th>Anúncio</th>${comparison.map(p=>`<td><a href="${html(https(p.url))}" target="_blank" rel="noopener noreferrer">Abrir oferta</a></td>`).join('')}</tr></tbody></table><div class="discovery-actions"><button data-action="clear-comparison">Limpar comparação</button><button data-action="export-comparison">Exportar comparação</button></div>`;
  }
  function publicPanel(d){
    const debt=d.debt||{}, sanctions=d.sanctions||{};
    return `<div class="discovery-notice">${html(d.scope)}</div><div class="public-check"><strong>Dívida ativa — PGFN</strong><p>${html(debt.message||'Consulta não realizada.')}</p>${debt.dataset_date?`<p>Referência: ${html(debt.dataset_date)} · ${html(debt.coverage)}</p>`:''}${debt.found?`<p>${Number(debt.count)} inscrição(ões) na base · Valor registrado: ${html(money(debt.amount))}</p><details><summary>Ver registros</summary><ul>${(debt.entries||[]).map(e=>`<li>${html(e.registration)} · ${html(e.situation||'Situação não informada')} · ${html(money(e.amount))}</li>`).join('')}</ul></details>`:''}</div><div class="public-check"><strong>Sanções administrativas — CEIS/CNEP</strong><p>${html(sanctions.message||'Consulta não realizada.')}</p>${sanctions.status==='not_found'?'<p>Nenhum registro retornado nesta consulta a CEIS/CNEP. Isso não é uma consulta de crédito.</p>':''}${sanctions.status==='partial'?'<p>Uma ou mais fontes não concluíram a consulta. Resultado parcial.</p>':''}${(sanctions.entries||[]).length?`<ul>${sanctions.entries.map(e=>`<li><strong>${html(e.registry)}</strong>: ${html(e.description)} · ${html(e.authority)} · ${html(e.start)} a ${html(e.end||'fim não informado')}</li>`).join('')}</ul>`:''}</div><div class="public-check-links">${(d.links||[]).map(l=>`<button data-action="public-portal" data-url="${html(https(l.url))}" data-cnpj="${html(d.cnpj)}">${html(l.label)}${l.prefilled?' — CNPJ preenchido':' — copiar CNPJ e abrir'}</button>`).join('')}</div><p class="discovery-muted">Se a cópia automática for bloqueada: <button data-action="copy-cnpj" data-cnpj="${html(d.cnpj)}">Copiar CNPJ</button> ${html(formatCnpj(d.cnpj))}<br>Consulta: ${html(dateTime(d.consulted_at))}. Certidões e CAPTCHA são concluídos no portal oficial.</p>`;
  }
  async function checkPublic(value, target='publicResults'){
    const id=cnpj(value);if(id.length!==14)throw new Error('Informe um CNPJ completo.');msg(target,'Consultando fontes públicas disponíveis…');
    const d=await api('/api/public-checks?cnpj='+encodeURIComponent(id));if($(target))$(target).innerHTML=publicPanel(d);
  }
  const actions={
    'open-discovery':()=>{switchPage('discovery');if(!segments.length)loadBuyers().catch(e=>msg('buyerResults',e.message,true));},
    'back-search':()=>switchPage('prospector'), 'buyer-search':loadBuyers, 'similar-search':loadSimilar,
    'use-buyers':el=>{const s=segments[Number(el.dataset.index)];const codes=[...document.querySelectorAll(`[data-buyer-group="${Number(el.dataset.index)}"]:checked`)].map(x=>x.value);if(!codes.length)throw new Error('Selecione pelo menos um CNAE.');useCodes(s.cnaes.filter(c=>codes.includes(c.code)));},
    'use-similar':()=>{const codes=[...document.querySelectorAll('[data-similar-code]:checked')].map(x=>x.value);if(!codes.length)throw new Error('Selecione uma atividade.');useCodes(similarCodes.filter(c=>codes.includes(c.code)));},
    'apply-discovery':()=>{queueSave();runFilter();}, 'clear-discovery':()=>{restoreFilters({});queueSave();if(cnaes.length)runFilter();},
    'find-city':findCities,'maps-search':searchMap,
    'maps-export':()=>csv('estabelecimentos_mapa',mapRows.map(r=>({Empresa:r.nome,Telefone:r.telefone,Email:r.email,Site:r.website,Endereco:r.endereco,CNPJ:r.cnpj,Fonte:r.source_url}))),
    'copy-place':el=>{const r=mapRows[Number(el.dataset.index)];return copy([r.nome,r.telefone,r.email,r.website,r.endereco,r.source_url].filter(Boolean).join('\n'));},
    'place-cnpj':el=>{openCnpjModal();$('cnpjModalInput').value=mapRows[Number(el.dataset.index)].cnpj;},
    'product-search':()=>searchProducts(1),'products-prev':()=>searchProducts(productPage-1),'products-next':()=>searchProducts(productPage+1),
    'product-config':async()=>{await api('/api/products/config',{app_key:$('productAppKey').value,app_secret:$('productAppSecret').value,tracking_id:$('productTracking').value,protocol:$('productProtocol').value});$('productAppKey').value='';$('productAppSecret').value='';$('productTracking').value='';msg('productConfigStatus','Configuração salva. Faça uma busca para validar o acesso autorizado.');await productStatus();},
    'product-import':async()=>{msg('productImportStatus','Lendo os dados publicados no anúncio…');const d=await api('/api/products/import-url',{url:$('productUrl').value});products=[d.product,...products.filter(p=>p.id!==d.product.id)];renderProducts();msg('productImportStatus','Produto adicionado com os dados disponíveis na página.');},
    'select-product':el=>{const p=products[Number(el.dataset.index)];if(comparison.some(c=>c.id===p.id))comparison=comparison.filter(c=>c.id!==p.id);else{if(comparison.length>=4)throw new Error('Compare até quatro produtos por vez.');comparison.push(p);}renderProducts();renderComparison();queueSave();},
    'compare-products':renderComparison, 'clear-comparison':()=>{comparison=[];renderProducts();renderComparison();queueSave();},
    'products-export':()=>csv('produtos_encontrados',products.map(p=>({Produto:p.title,Preco:p.price,PrecoMinimo:p.price_min,PrecoMaximo:p.price_max,Moeda:p.currency,Fornecedor:p.supplier,PedidoMinimo:p.moq,Link:p.url,Fonte:p.source,Consulta:p.consulted_at}))),
    'export-comparison':()=>csv('comparacao_produtos',comparison.map(p=>({Produto:p.title,Preco:p.price,PrecoMinimo:p.price_min,PrecoMaximo:p.price_max,Moeda:p.currency,Fornecedor:p.supplier,PedidoMinimo:p.moq,Link:p.url,Fonte:p.source,Consulta:p.consulted_at}))),
    'public-check':()=>checkPublic($('publicCnpj').value), 'drawer-public-check':el=>checkPublic(el.dataset.cnpj,'dividaBox'),
    'public-portal':el=>{const url=https(el.dataset.url);if(!url)return;window.open(url,'_blank','noopener,noreferrer');return copy(el.dataset.cnpj).catch(()=>showToast('Portal aberto. Use o botão Copiar CNPJ se necessário.',''));},
    'copy-cnpj':el=>copy(el.dataset.cnpj),
    'save-cgu':async()=>{await api('/api/public-checks/config',{cgu_key:$('cguKey').value});$('cguKey').value='';msg('publicSettingsStatus','Chave salva no servidor.');}
  };
  async function dispatch(action, el){
    const targets={'buyer-search':'buyerResults','similar-search':'similarResults','find-city':'mapCityResults','maps-search':'mapsStatus','product-search':'productsStatus','products-next':'productsStatus','products-prev':'productsStatus','product-import':'productImportStatus','product-config':'productConfigStatus','public-check':'publicResults','save-cgu':'publicSettingsStatus','drawer-public-check':'dividaBox'};
    const button=el.tagName==='FORM'?el.querySelector('button[type=submit]'):el;if(button)button.disabled=true;
    try{await actions[action]?.(el);}catch(e){if(targets[action])msg(targets[action],e.message,true);else showToast(e.message,'error');}finally{if(button && !['products-prev','products-next'].includes(action))button.disabled=false;}
  }
  installUI();
  document.addEventListener('submit',e=>{const form=e.target.closest('form[data-action]');if(form){e.preventDefault();dispatch(form.dataset.action,form);}});
  document.addEventListener('click',e=>{const el=e.target.closest('button[data-action],a[data-action]');if(el){e.preventDefault();dispatch(el.dataset.action,el);}});
  $('mapsCity').addEventListener('input',()=>{mapCities=[];$('mapCityResults').innerHTML='';});$('mapsUf').addEventListener('change',()=>{mapCities=[];$('mapCityResults').innerHTML='';});
  $('discoveryFilters').addEventListener('change',queueSave);
  const originalParams=buildSearchParamsForPage;
  buildSearchParamsForPage=function(page){const params=originalParams(page);for(const [key,value] of Object.entries(filterState()))if(value && !$('discovery_'+key).disabled)params.set('discovery_'+key,value);return params;};
  const originalPersist=persistLeadTools;persistLeadTools=function(){originalPersist();queueSave();};
  const originalJsonSave=saveJsonStore;saveJsonStore=function(key,value){originalJsonSave(key,value);if(key===SAVED_SEARCHES_KEY)queueSave();};
  const originalSaveSearch=saveCurrentSearch;saveCurrentSearch=function(){const first=savedSearches[0]?.id;originalSaveSearch();if(savedSearches[0]?.id!==first){savedSearches[0].discovery=filterState();saveJsonStore(SAVED_SEARCHES_KEY,savedSearches);}};
  const originalApplySearch=applySavedSearch;applySavedSearch=function(id){restoreFilters(savedSearches.find(s=>s.id===id)?.discovery||{});return originalApplySearch(id);};
  const originalSwitch=switchPage;switchPage=function(name,button){originalSwitch(name,button);if(name==='discovery'){document.querySelectorAll('.topbar-tab').forEach(b=>b.classList.toggle('active',b.dataset.action==='open-discovery'));}if(name==='china')productStatus().catch(e=>msg('productConnection',e.message,true));};
  refreshApifyStatus=async()=>{};
  mostrarBotoesPortais=function(box,value){box.innerHTML=`<div class="public-checks"><p class="discovery-muted">Dívida ativa e sanções públicas, com fonte e referência. Consulta gratuita conforme disponibilidade.</p><button data-action="drawer-public-check" data-cnpj="${html(cnpj(value))}">Consultar pendências públicas</button></div>`;};
  carregarDivida=value=>checkPublic(value,'dividaBox').catch(e=>msg('dividaBox',e.message,true));
  const originalLogout=doLogout;doLogout=async function(){try{changeVersion++;await flush();originalLogout();}catch{showToast('Não foi possível salvar favoritos e retornos no servidor. Tente sair novamente quando a conexão voltar.','error');}};
  async function initialize(){
    const me=await api('/api/me');const owner=String(me.company_id||'local')+':'+String(me.usuario||me.user||'');
    journalKey='prospectax_discovery_pending_'+owner;
    const response=await api('/api/discovery/state');const state=response.state||{};stateVersion=response.version||0;baseline=JSON.parse(JSON.stringify(state));
    let pending=null;try{pending=JSON.parse(localStorage.getItem(journalKey)||'null');}catch{}
    if(pending){applyState(reconcile(pending.base||{},pending.state||{},state));}
    else if(Object.keys(state).length){applyState(state);}
    else{
      // Migra estado legado somente uma vez, para a sessao que ja o utilizava.
      const oldOwner=localStorage.getItem('prospectax_discovery_migrated_owner');
      if(oldOwner && oldOwner!==owner){leadTools={};savedSearches=[];}
      localStorage.setItem('prospectax_discovery_migrated_owner',owner);
    }
    originalPersist();originalJsonSave(SAVED_SEARCHES_KEY,savedSearches);renderSavedSearchBox();ready=true;queueSave();
    const canConfigure=me.role==='admin'&&me.scope==='global_admin';$('productSettings').hidden=!canConfigure;$('publicSettings').hidden=!canConfigure;
    const caps=await api('/api/discovery/capabilities');
    for(const [name,field] of Object.entries({contact:'contact',porte:'porte',cep:'cep',tipo:'tipo',bairro:'bairro',abertura_de:'abertura',abertura_ate:'abertura'})){const input=$('discovery_'+name);input.disabled=!caps.filters?.[field];if(input.disabled){input.value='';input.title='Disponível após gerar o índice atualizado da Receita.';}}
    $('discoveryCapabilities').textContent=['tipo','bairro','abertura'].some(k=>!caps.filters?.[k])?'Campos desabilitados ainda não existem no seu índice. Serão incluídos ao gerar o índice atualizado da Receita.':'Todos os campos estão disponíveis nesta base.';
    await productStatus();
  }
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'&&ready&&changeVersion>savedVersion){journal();flush().catch(()=>{});}});
  initialize().catch(e=>{msg('productConnection',e.message,true);console.warn('Inicializacao das ferramentas de descoberta incompleta.');});
})();
