(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const UFS = 'AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split(' ');
  const STATUS = {rascunho:'Rascunho', aguardando:'Aguardando parceria', aprovado:'Aprovado', pausado:'Pausado'};
  const state = {tab:'catalogo', items:[], admin:[], report:[], categories:{}, profile:{}, isAdmin:false, edit:null, request:0};
  function el(tag, attrs={}, children=[]) {
    const node = document.createElement(tag);
    for (const [key,value] of Object.entries(attrs)) {
      if (key === 'text') node.textContent = value;
      else if (key === 'class') node.className = value;
      else node.setAttribute(key,value);
    }
    for (const child of children) node.append(child);
    return node;
  }
  function message(text, error=false) {
    $('message').textContent=text; $('message').className='message'+(error?' error':''); $('message').hidden=false;
  }
  async function api(path, data) {
    const options = {headers:{'Content-Type':'application/json'}, credentials:'same-origin'};
    if(data !== undefined) {options.method='POST'; options.body=JSON.stringify(data);}
    const response=await fetch(path,options);
    if(response.status===401) {location.href='/login'; throw Error('Entre novamente para continuar.');}
    const result=await response.json();
    if(!response.ok) throw Error(result.error||'Não foi possível concluir a operação.');
    return result;
  }
  function run(action) {return async event => {event?.preventDefault(); try{await action(event);}catch(error){message(error.message,true);}};}
  function options(select, entries, initial) {
    select.replaceChildren();
    if(initial) select.append(el('option',{value:'',text:initial}));
    for(const [key,label] of Object.entries(entries)) select.append(el('option',{value:key,text:label}));
  }
  for(const select of document.querySelectorAll('.uf-select')) for(const uf of UFS) select.append(el('option',{value:uf,text:uf}));
  function avatar(p) {
    const node=el('div',{class:'avatar',text:p.name.split(/\s+/).slice(0,2).map(w=>w[0]).join('').toUpperCase()});
    if(p.logo) {
      const img=el('img',{src:p.logo,alt:'Logo de '+p.name,loading:'lazy',referrerpolicy:'no-referrer'});
      img.addEventListener('error',()=>img.remove()); node.append(img);
      img.addEventListener('load',()=>{for(const child of [...node.childNodes])if(child.nodeType===3)child.remove();});
    }
    return node;
  }
  function badge(text, cls='') {return el('span',{class:'badge '+cls,text});}
  function locationText(p) {return p.national?'Atende todo o Brasil':(p.coverage.length?'Atende '+p.coverage.join(', '):[p.city,p.uf].filter(Boolean).join(' / ')||'Consulte a área de atendimento');}
  function favoriteButton(p) {
    const button=el('button',{type:'button',class:'favorite'+(p.favorite?' active':''),text:p.favorite?'♥':'♡','aria-label':(p.favorite?'Remover dos favoritos: ':'Salvar favorito: ')+p.name,'aria-pressed':String(p.favorite)});
    button.addEventListener('click',run(async()=>{
      button.disabled=true;
      try {const result=await api('/api/partners/favorite',{id:p.id,enabled:!p.favorite});p.favorite=result.favorite;render();}
      finally{button.disabled=false;}
    }));
    return button;
  }
  function card(p) {
    const node=el('article',{class:'partner-card'+(p.sponsored?' sponsored':'')});
    const identity=el('div',{class:'card-identity'},[el('h3',{text:p.name}),el('span',{class:'category',text:state.categories[p.category]})]);
    node.append(el('div',{class:'card-top'},[avatar(p),identity,favoriteButton(p)]));
    const badges=el('div',{class:'badges'});
    if(p.sponsored)badges.append(badge('PATROCINADO','sponsor'));
    if(p.wholesale)badges.append(badge('ATACADO'));
    if(p.small_business)badges.append(badge('PEQUENOS NEGÓCIOS'));
    const body=el('div',{class:'card-body'},[badges,el('p',{class:'card-description',text:p.description}),el('p',{class:'location',text:locationText(p)})]);
    if(state.tab==='oportunidades'&&p.reasons.length)body.append(el('p',{class:'reason',text:p.reasons[0]}));
    if(p.benefit)body.append(el('div',{class:'benefit-box'},[el('strong',{text:'Benefício para usuários'}),el('span',{text:p.benefit.length>140?p.benefit.slice(0,137)+'…':p.benefit})]));
    node.append(body);
    const more=el('button',{type:'button',class:'text-button',text:'Conhecer fornecedor ↗'});
    more.addEventListener('click',()=>openDetail(p));
    node.append(el('div',{class:'card-bottom'},[more,el('span',{text:p.minimum_order?'Pedido: '+p.minimum_order:''})]));
    return node;
  }
  function render() {
    if(state.tab==='gestao')return;
    let items=state.items;
    if(state.tab==='favoritos')items=items.filter(p=>p.favorite);
    if(state.tab==='oportunidades')items=items.filter(p=>p.score>0);
    const featured=state.tab==='catalogo'?items.filter(p=>p.sponsored).slice(0,3):[];
    const featuredIds=new Set(featured.map(p=>p.id));
    $('featured').replaceChildren(...featured.map(card)); $('featuredSection').hidden=!featured.length;
    $('partnerGrid').replaceChildren(...items.filter(p=>!featuredIds.has(p.id)).map(card));
    $('resultCount').textContent=items.length+' fornecedor'+(items.length===1?'':'es');
    $('listTitle').textContent={catalogo:featured.length?'Mais fornecedores':'Explore os fornecedores',oportunidades:'Parceiros para explorar',favoritos:'Seus fornecedores favoritos'}[state.tab];
    $('emptyState').hidden=items.length>0;
    const filters=new FormData($('filters'));
    const filtered=['q','category','uf','wholesale','small_business'].some(key=>filters.get(key));
    $('emptyTitle').textContent=state.tab==='favoritos'?'Guarde suas próximas parcerias':state.tab==='oportunidades'?'Novas oportunidades a caminho':filtered?'Nenhum fornecedor encontrado':'A rede está começando';
    $('emptyText').textContent=state.tab==='favoritos'?'Toque no coração de um fornecedor para salvar sua ficha aqui.':state.tab==='oportunidades'?'Salve seu perfil e explore as categorias sugeridas. Parceiros relacionados aparecerão conforme a rede crescer.':filtered?'Ajuste os filtros para explorar outros fornecedores disponíveis.':'Os fornecedores aparecerão aqui depois que as parcerias forem cadastradas e aprovadas.';
    $('emptyAdd').hidden=!state.isAdmin||filtered||state.tab!=='catalogo';
    $('suggestionBox').hidden=state.tab!=='oportunidades';
  }
  async function loadCatalog(initial=false) {
    const request=++state.request;
    const form=new FormData($('filters'));const params=new URLSearchParams();
    for(const key of ['q','category','uf'])if(form.get(key))params.set(key,form.get(key));
    for(const key of ['wholesale','small_business'])if(form.has(key))params.set(key,'1');
    const data=await api('/api/partners?'+params);
    if(request!==state.request)return;
    state.items=data.partners;state.categories=data.categories;state.profile=data.profile;
    if(initial) {
      options($('filters').elements.category,data.categories,'Todas as categorias');options($('partnerForm').elements.category,data.categories);
      for(const key of ['segment','goal','uf'])$('profileForm').elements[key].value=data.profile[key]||'';
    }
    $('partnerCount').textContent=data.total+(data.total===1?' parceiro disponível':' parceiros disponíveis')+' na rede';
    $('suggestionChips').replaceChildren(...data.suggestions.map(s=>{
      const button=el('button',{type:'button',text:s.label,class:$('filters').elements.category.value===s.id?'active':''});
      button.addEventListener('click',run(async()=>{$('filters').elements.category.value=s.id;await loadCatalog();}));return button;
    }));render();
  }
  function track(id,kind){api('/api/partners/event',{id,kind}).catch(()=>{});}
  function openDetail(p) {
    const container=$('detailBody'); container.replaceChildren();
    container.append(el('div',{class:'detail-title'},[avatar(p),el('div',{},[el('h2',{text:p.name}),el('span',{class:'category',text:state.categories[p.category]})])]));
    if(p.sponsored)container.append(badge('PATROCINADO','sponsor'));
    container.append(el('p',{class:'detail-text',text:p.description}));
    container.append(el('a',{href:'/inteligencia.html?partner='+encodeURIComponent(p.id),text:'Pesquisar este fornecedor com fontes e IA ↗',class:'button'}));
    if(p.products)container.append(el('h3',{class:'detail-label',text:'PRODUTOS E SERVIÇOS'}),el('p',{class:'detail-text',text:p.products}));
    if(p.benefit)container.append(el('div',{class:'benefit-box'},[el('strong',{text:'Benefício para usuários do ProspectaX'}),el('span',{text:p.benefit})]));
    if(p.benefit_until)container.append(el('p',{class:'helper',text:'Validade do benefício: '+p.benefit_until.split('-').reverse().join('/')}));
    if(p.conditions)container.append(el('h3',{class:'detail-label',text:'CONDIÇÕES COMERCIAIS'}),el('p',{class:'detail-text',text:p.conditions}));
    const kv=el('div',{class:'detail-kv'});
    for(const [label,value]of [['Atendimento',locationText(p)],['Pedido mínimo',p.minimum_order||'Consulte o fornecedor'],['Atacado',p.wholesale?'Sim':'Consulte o fornecedor'],['Pequenos negócios',p.small_business?'Atende':'Consulte o fornecedor'],['Contato',p.contact_name],['CNPJ',p.cnpj]])if(value)kv.append(el('div',{},[el('small',{text:label}),el('span',{text:value})]));
    container.append(kv);
    const actions=el('div',{class:'detail-actions'});
    function link(label,href,kind,cls) {const anchor=el('a',{class:'button '+cls,href,target:'_blank',rel:'noopener noreferrer',text:label});anchor.addEventListener('click',()=>track(p.id,kind));actions.append(anchor);}
    if(p.whatsapp)link('Conversar no WhatsApp','https://wa.me/'+p.whatsapp,'contact','orange');
    if(p.email)link('Enviar e-mail','mailto:'+p.email,'contact','navy');
    if(p.website)link('Visitar site ↗',p.website,'site','light');
    if(!p.whatsapp&&!p.email&&!p.website)actions.append(el('p',{class:'helper',text:'O fornecedor ainda não informou um canal de contato.'}));
    container.append(actions,el('p',{class:'helper',text:'Confirme disponibilidade e condições diretamente com o fornecedor.'}));
    $('detailDialog').showModal();track(p.id,'view');
  }
  async function switchTab(tab) {
    if(tab==='gestao'&&!state.isAdmin)return;
    state.tab=tab;
    for(const b of document.querySelectorAll('[data-tab]'))b.setAttribute('aria-selected',String(b.dataset.tab===tab));
    $('managePanel').hidden=tab!=='gestao';$('catalogPanel').hidden=tab==='gestao';$('profile').hidden=tab==='gestao';
    if(tab==='gestao')await loadAdmin();else{await loadCatalog();render();}
  }
  async function loadAdmin() {
    const data=await api('/api/partners/admin');state.admin=data.partners;state.categories=data.categories;
    $('statTotal').textContent=data.partners.length;
    $('statApproved').textContent=data.partners.filter(p=>p.status==='aprovado').length;
    $('statPending').textContent=data.partners.filter(p=>p.status==='aguardando').length;
    $('statSponsored').textContent=data.partners.filter(p=>p.sponsored).length;
    const rows=data.partners.slice().sort((a,b)=>b.updated_at.localeCompare(a.updated_at)).map(p=>{
      const edit=el('button',{type:'button',class:'text-button',text:'Editar'});edit.addEventListener('click',()=>openEdit(p));
      const pitch=el('button',{type:'button',class:'text-button',text:'Copiar convite'});pitch.addEventListener('click',run(async()=>{await copy(invitation(p.name));message('Convite copiado. Revise e envie pelo canal de sua preferência.');}));
      return el('tr',{},[el('td',{text:p.name}),el('td',{text:data.categories[p.category]}),el('td',{},[badge(STATUS[p.status],p.status)]),el('td',{text:p.sponsored?{geral:'Vitrine geral',categoria:'Categoria',regional:'Regional'}[p.sponsor_scope]:'—'}),el('td',{},[edit,pitch])]);
    });
    if(!rows.length)rows.push(el('tr',{},[el('td',{colspan:5,text:'Nenhum fornecedor cadastrado. Comece com “Novo fornecedor”.'})]));
    $('adminRows').replaceChildren(...rows);await loadReport();
  }
  async function loadReport() {
    const data=await api('/api/partners/metrics?days='+$('reportDays').value);state.report=data.metrics;
    const rows=data.metrics.map(p=>el('tr',{},[el('td',{text:p.name}),el('td',{text:p.views}),el('td',{text:p.site_clicks}),el('td',{text:p.contact_clicks})]));
    if(!rows.length)rows.push(el('tr',{},[el('td',{colspan:4,text:'Os resultados aparecerão após o cadastro e uso da rede.'})]));
    $('metricRows').replaceChildren(...rows);
  }
  function openEdit(p=null) {
    state.edit=p;$('partnerForm').reset();$('formMessage').hidden=true;
    $('editTitle').textContent=p?'Editar fornecedor':'Novo fornecedor';
    if(p) {
      for(const [key,value]of Object.entries(p)) {
        const field=$('partnerForm').elements[key]; if(!field)continue;
        if(field.type==='checkbox')field.checked=value;
        else field.value=Array.isArray(value)?value.join(', '):value;
      }
      for(const goal of ['ampliar','custos','operacao'])$('partnerForm').elements['goal_'+goal].checked=p.goals.includes(goal);
    }
    $('editDialog').showModal();
  }
  function formData() {
    const form=$('partnerForm'),data={};
    for(const input of form.elements)if(input.name&&!input.name.startsWith('goal_'))data[input.name]=input.type==='checkbox'?input.checked:input.value.trim();
    for(const key of ['coverage','segments','sponsor_ufs'])data[key]=data[key].split(',').map(s=>s.trim()).filter(Boolean);
    data.coverage=data.coverage.map(s=>s.toUpperCase());data.sponsor_ufs=data.sponsor_ufs.map(s=>s.toUpperCase());
    data.goals=['ampliar','custos','operacao'].filter(goal=>form.elements['goal_'+goal].checked);
    if(state.edit){data.id=state.edit.id;data.revision=state.edit.revision;}
    return data;
  }
  function invitation(name='') {
    return `Olá${name?', equipe da '+name:''}! Sou responsável pelo ProspectaX, uma plataforma que ajuda empresários a encontrar clientes, fornecedores e oportunidades para seus negócios.\n\nEstamos formando uma rede de fornecedores parceiros e gostaríamos de apresentar sua empresa aos nossos usuários. A parceria pode incluir uma ficha com site, produtos, região atendida e contato, além de condições especiais para usuários do ProspectaX. Também temos espaços de patrocínio por categoria e região, com relatório de visualizações e cliques.\n\nPodemos conversar sobre a participação da sua empresa e as condições da parceria?`;
  }
  async function copy(value) {
    if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(value);return;}
    const field=el('textarea');field.value=value;document.body.append(field);field.select();const ok=document.execCommand('copy');field.remove();if(!ok)throw Error('Não foi possível copiar neste navegador.');
  }
  $('partnerForm').addEventListener('submit',async event=>{
    event.preventDefault();$('savePartner').disabled=true;$('formMessage').hidden=true;
    try{await api('/api/partners/save',formData());$('editDialog').close();message('Fornecedor salvo. Apenas parcerias aprovadas aparecem na vitrine.');await loadAdmin();}
    catch(error){$('formMessage').textContent=error.message;$('formMessage').hidden=false;$('formMessage').scrollIntoView({block:'nearest'});}
    finally{$('savePartner').disabled=false;}
  });
  $('profileForm').addEventListener('submit',run(async()=>{
    const data=Object.fromEntries(new FormData($('profileForm')));await api('/api/partners/profile',data);await switchTab('oportunidades');message('Perfil salvo. Explore as sugestões para seu negócio.');
  }));
  $('filters').addEventListener('submit',run(()=>loadCatalog()));
  $('clearFilters').addEventListener('click',run(async()=>{$('filters').reset();await loadCatalog();}));
  for(const b of document.querySelectorAll('[data-tab]'))b.addEventListener('click',run(()=>switchTab(b.dataset.tab)));
  for(const b of document.querySelectorAll('[data-close]'))b.addEventListener('click',()=>b.closest('dialog').close());
  $('newPartner').addEventListener('click',()=>openEdit());
  $('emptyAdd').addEventListener('click',run(async()=>{await switchTab('gestao');openEdit();}));
  $('copyPitch').addEventListener('click',run(async()=>{await copy(invitation());message('Proposta de parceria copiada.');}));
  $('reportDays').addEventListener('change',run(()=>loadReport()));
  $('exportReport').addEventListener('click',run(async()=>{
    const csvCell=value=>{let text=String(value);if(/^[=+@\-]/.test(text.trimStart()))text="'"+text;return '"'+text.replaceAll('"','""')+'"';};
    const lines=[['Fornecedor','Aberturas da ficha','Cliques no site','Cliques no contato'],...state.report.map(p=>[p.name,p.views,p.site_clicks,p.contact_clicks])];
    const blob=new Blob(['\ufeff'+lines.map(row=>row.map(csvCell).join(';')).join('\r\n')],{type:'text/csv;charset=utf-8'});
    const href=URL.createObjectURL(blob),anchor=el('a',{href,download:'parceiros_'+$('reportDays').value+'dias.csv'});document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(href),1000);
  }));
  (async()=>{
    try{
      const me=await api('/api/me');state.isAdmin=me.role==='admin'&&me.scope==='global_admin';$('userName').textContent=me.nome||me.usuario||'';
      $('manageTab').hidden=!state.isAdmin;$('adminLink').hidden=me.role!=='admin';
      await loadCatalog(true);
      if(new URLSearchParams(location.search).get('gestao')==='1'&&state.isAdmin)await switchTab('gestao');
    }catch(error){message(error.message,true);$('partnerCount').textContent='Não foi possível carregar a rede';}
  })();
})();
