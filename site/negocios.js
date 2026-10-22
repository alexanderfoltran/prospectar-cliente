(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  const E=(tag,attrs={},children=[])=>{
    const n=document.createElement(tag);
    for(const [k,v] of Object.entries(attrs)){
      if(k==='text')n.textContent=v;
      else if(k==='class')n.className=v;
      else n.setAttribute(k,v);
    }
    for(const c of children)if(c)n.append(c);
    return n;
  };
  const button=(label,action,cls='secondary')=>{const b=E('button',{type:'button',text:label,class:cls});b.addEventListener('click',()=>run(action,b));return b;};
  const cash=v=>v===''||v==null?'A consultar':new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format(Number(v));
  const qty=v=>new Intl.NumberFormat('pt-BR',{maximumFractionDigits:3}).format(Number(v));
  const date=v=>v?v.slice(0,10).split('-').reverse().join('/'):'Não informado';
  const safeUrl=v=>{try{const u=new URL(v);return ['https:','http:'].includes(u.protocol)?u.href:'';}catch{return '';}};
  const labels={quote:'Pedi cotação',negotiating:'Iniciei negociação',purchased:'Comprei'};
  let state=null,tab=location.hash.slice(1)||'catalogo',editingProduct=null,editingQuote=null,editingProfile=null,editingLead=null,supplierOffer=false;
  let loadVersion=0;
  const fields=form=>Object.fromEntries(new FormData(form));
  function message(s,error=false){$('message').textContent=s;$('message').className='message'+(error?' error':'');$('message').hidden=false;}
  async function api(path,data){
    const token=localStorage.getItem('prospectax_token')||'';
    const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'X-Auth-Token':token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data),cache:'no-store'});
    const d=await r.json();
    if(r.status===401){location.href='/login.html';throw new Error('Entre novamente para continuar.');}
    if(!r.ok)throw new Error(d.error||'Não foi possível concluir.');
    return d;
  }
  async function run(action,b){
    if(b)b.disabled=true;
    try{await action();}catch(e){message(e.message,true);}finally{if(b)b.disabled=false;}
  }
  async function mutate(action,data,success='Salvo.'){const r=await api('/api/business/'+action,data);message(r.message||success);await load();return r;}
  function selectOptions(select,items,value=select.value){
    select.replaceChildren(...items.map(i=>E('option',{value:i.value,text:i.label})));
    if(items.some(i=>i.value===value))select.value=value;
  }
  function fill(form,data){form.reset();for(const [k,v] of Object.entries(data||{})){const f=form.elements.namedItem(k);if(f&&'value' in f)f.value=v==null?'':v;}}
  function empty(s){return E('div',{class:'empty',text:s});}
  function partnerName(id){return [...state.partners,...(state.all_partners||[]),...state.supplier_partners].find(p=>p.id===id)?.name||'Fornecedor';}
  function table(headers,rows){return E('div',{class:'table-wrap'},[E('table',{},[E('thead',{},[E('tr',{},headers.map(t=>E('th',{text:t})))]),E('tbody',{},rows.map(r=>E('tr',{},r.map(c=>typeof c==='string'?E('td',{text:c}):E('td',{},[c])))))])]);}
  function csv(name,headers,rows){
    const cell=v=>{let s=String(v??'');if(/^\s*[=+@-]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';};
    const body='\ufeff'+[headers,...rows].map(r=>r.map(cell).join(';')).join('\r\n');
    const link=E('a',{href:URL.createObjectURL(new Blob([body],{type:'text/csv;charset=utf-8'})),download:name+'.csv'});
    document.body.append(link);link.click();setTimeout(()=>{URL.revokeObjectURL(link.href);link.remove();},1000);
  }
  async function copy(s){try{await navigator.clipboard.writeText(s);message('Texto copiado. Revise antes de enviar.');}catch{window.prompt('Copie o texto abaixo:',s);}}
  function choose(next){
    if(!document.querySelector(`[data-panel="${next}"]`)||next==='gestao'&&!state?.admin)next='catalogo';
    tab=next;history.replaceState(null,'','#'+tab);
    document.querySelectorAll('[data-panel]').forEach(n=>n.hidden=n.dataset.panel!==tab);
    document.querySelectorAll('[data-tab]').forEach(n=>{n.classList.toggle('active',n.dataset.tab===tab);n.setAttribute('aria-current',n.dataset.tab===tab?'page':'false');});
  }
  function renderProducts(){
    const q=$('productSearch').value.trim().toLocaleLowerCase('pt-BR'),partner=$('productPartner').value,category=$('productCategory').value;
    const items=state.products.filter(p=>(!partner||p.partner===partner)&&(!category||p.category===category)&&(!q||[p.title,p.description,p.category,partnerName(p.partner)].join(' ').toLocaleLowerCase('pt-BR').includes(q)));
    $('productCards').replaceChildren(...items.map(p=>{
      const image=safeUrl(p.photo)?E('img',{src:safeUrl(p.photo),alt:p.title,loading:'lazy',referrerpolicy:'no-referrer'}):E('div',{class:'product-picture',text:'▧'});
      if(image.tagName==='IMG')image.addEventListener('error',()=>image.replaceWith(E('div',{class:'product-picture',text:'▧'})),{once:true});
      const info=E('div',{class:'card-body'},[E('span',{class:'chip',text:partnerName(p.partner)}),E('h3',{text:p.title}),E('p',{text:p.description}),E('div',{class:'card-price',text:cash(p.price)+(p.price_max?' a '+cash(p.price_max):'')}),E('p',{text:'Por '+p.unit+' · Pedido mínimo: '+qty(p.minimum_quantity)}),E('div',{class:'actions'},[button('Pedir cotação',()=>{fill($('quoteForm'),{product:p.title,quantity:p.minimum_quantity,requester:$('quoteForm').elements.requester.value});$('quotePartners').querySelectorAll('input').forEach(i=>i.checked=i.value===p.partner);choose('cotacoes');$('quoteForm').elements.city.focus();},'primary'),button('Simular margem',()=>{fill($('marginForm'),{purchase:p.price,quantity:p.minimum_quantity,freight:0,other:0,tax_percent:0,fee_percent:0});choose('margem');$('marginForm').elements.sale.focus();})])]);
      if(safeUrl(p.link))info.append(E('a',{href:safeUrl(p.link),target:'_blank',rel:'noopener noreferrer',text:'Abrir oferta do fornecedor ↗'}));
      return E('article',{class:'product-card'},[image,info]);
    }));
    if(!items.length)$('productCards').append(empty(state.products.length?'Nenhum produto atende aos filtros.':'Os produtos aparecerão aqui após o cadastro e a aprovação.'));
  }
  function renderOutcomes(){
    const partner=$('outcomePartner').value;
    $('outcomeButtons').replaceChildren(...Object.entries(labels).map(([kind,label])=>{
      const enabled=state.outcomes.some(o=>o.partner===partner&&o.kind===kind&&o.enabled);
      const b=button((enabled?'✓ ':'')+label,()=>mutate('outcome',{partner,kind,enabled:!enabled},'Seu resultado foi registrado.'),'secondary'+(enabled?' selected':''));
      b.disabled=!partner; b.setAttribute('aria-pressed',String(enabled)); return b;
    }));
  }
  function quoteText(q){return `Olá! Gostaria de uma cotação.\nProduto: ${q.product}\nQuantidade: ${qty(q.quantity)}\nEntrega: ${q.city}\nResponsável: ${q.requester}\nDetalhes: ${q.notes||'Sem observações adicionais.'}\nPor favor informe preço, frete, pedido mínimo e prazo.`;}
  function renderQuotes(){
    $('quoteCards').replaceChildren(...state.quotes.slice().reverse().map(q=>{
      const card=E('article',{class:'quote-card'},[E('div',{class:'heading'},[E('h3',{text:q.product}),E('span',{class:'chip',text:q.shared?'Compartilhada no sistema':'Pedido preparado'})]),E('p',{text:`${qty(q.quantity)} · ${q.city} · ${date(q.created_at)}`}),E('p',{text:q.notes}),E('div',{class:'actions'},[button('Copiar pedido',()=>copy(quoteText(q))),...(!q.shared?[button('Compartilhar no sistema',()=>mutate('share-quote',{id:q.id,revision:q.revision},'Pedido disponibilizado às contas dos fornecedores selecionados.'))]:[]),button('Registrar proposta recebida',()=>openOffer(q,false))])]);
      if(q.offers.length){
        const offers=q.offers.slice().sort((a,b)=>Number(a.total)-Number(b.total));
        card.append(table(['Fornecedor','Unidade','Frete','Total do pedido','Mínimo / prazo','Ações'],offers.map(o=>[partnerName(o.partner),cash(o.price),cash(o.freight),cash(o.total),qty(o.minimum_quantity)+' · '+(o.meets_minimum?'Mínimo atendido':'Abaixo do mínimo')+' · '+(o.delivery||'Prazo a confirmar'),E('div',{class:'actions'},[...(o.editable?[button('Editar',()=>openOffer(q,false,o.partner))]:[]),button('Condições',()=>message(o.notes||'Sem condições adicionais.'))])])));
      }else card.append(E('p',{class:'muted',text:'Ainda não há propostas. Registre uma resposta recebida ou aguarde uma conta de fornecedor responder.'}));
      card.append(E('p',{class:'muted',text:'Total = preço por unidade × quantidade + frete. Confira o mínimo, os prazos e demais condições antes de escolher.'}));return card;
    }));
    if(!state.quotes.length)$('quoteCards').append(empty('Prepare a primeira cotação usando o formulário acima.'));
  }
  function openOffer(q,isSupplier=false,partner=''){
    editingQuote=q;supplierOffer=isSupplier;
    fill($('offerForm'),{freight:0,minimum_quantity:1});
    const ids=q.partners.filter(id=>!isSupplier||state.supplier_partners.some(p=>p.id===id));
    selectOptions($('offerForm').elements.partner,ids.map(id=>({value:id,label:partnerName(id)})),partner||ids[0]);
    $('offerDescription').textContent=`${q.product} · ${qty(q.quantity)} · ${q.city}`;
    fillOffer();$('offerDialog').showModal();
  }
  function fillOffer(){
    const form=$('offerForm'),id=form.elements.partner.value;
    const offer=editingQuote.offers.find(o=>o.partner===id);
    for(const key of ['price','freight','minimum_quantity','delivery','notes'])form.elements[key].value=offer?.[key]??({freight:0,minimum_quantity:1}[key]??'');
    form.querySelector('[type="submit"]').disabled=!!offer&&!offer.editable;
    if(offer&&!offer.editable)message('Esta proposta foi registrada por outra conta e só pode ser editada por ela.');
  }
  function renderBoard(){
    const q=$('boardSearch').value.toLowerCase().replace(/[.\/-]/g,'');
    const leads=state.board.leads.filter(r=>[r.name,r.cnpj].join(' ').toLowerCase().replace(/[.\/-]/g,'').includes(q));
    $('boardCount').textContent=`${leads.length} exibidos · ${state.board.total} na carteira`;
    $('board').replaceChildren(...Object.entries(state.board.statuses).map(([status,label])=>{
      const items=leads.filter(r=>(state.board.statuses[r.status]?r.status:'novo')===status);
      const col=E('div',{class:'column'},[E('h3',{},[E('span',{text:label}),E('span',{text:items.length})])]);
      col.addEventListener('dragover',e=>{e.preventDefault();col.classList.add('dragover');});
      col.addEventListener('dragleave',()=>col.classList.remove('dragover'));
      col.addEventListener('drop',e=>{e.preventDefault();col.classList.remove('dragover');const key=e.dataTransfer.getData('text/plain');const lead=state.board.leads.find(r=>r.cnpj===key);if(lead)run(()=>moveLead(lead,status));});
      for(const lead of items){
        const due=lead.follow_date&&lead.follow_date<=new Date().toLocaleDateString('sv-SE');
        const select=E('select',{'aria-label':'Etapa de '+lead.name});selectOptions(select,Object.entries(state.board.statuses).map(([value,label])=>({value,label})),lead.status);
        select.addEventListener('change',()=>run(()=>moveLead(lead,select.value),select));
        const card=E('article',{class:'lead-card',draggable:'true'},[E('h4',{text:lead.name}),E('p',{class:'muted',text:lead.cnpj}),E('p',{class:'muted'+(due?' due':''),text:lead.follow_date?'Retorno: '+date(lead.follow_date):'Sem data de retorno'}),select,button('Programar retorno',()=>{editingLead=lead;fill($('leadEditForm'),lead);$('leadDialog').showModal();})]);
        card.addEventListener('dragstart',e=>e.dataTransfer.setData('text/plain',lead.cnpj));col.append(card);
      }return col;
    }));
  }
  async function moveLead(lead,status){await api('/api/contact-status',{cnpj:lead.cnpj,status});message('Etapa atualizada também na Prospecção.');await load();}
  function renderPortfolio(){
    $('portfolioCards').replaceChildren(...state.portfolio.slice().reverse().map(p=>{
      const card=E('article',{class:'sub-card'},[E('h3',{text:p.name}),E('p',{class:'muted',text:`${p.companies.length} encontrados de ${p.total} CNPJs únicos · ${date(p.created_at)}`})]);
      card.append(table(['Atividade (CNAE)','Clientes','Participação','Próxima busca'],p.segments.map(s=>[s.code+' · '+s.description,String(s.count),qty(s.count/p.companies.length*100)+'%',E('a',{href:'/?carteira_cnae='+encodeURIComponent(s.code),text:'Usar na Prospecção ↗'})])));
      card.append(E('div',{class:'actions'},[button('Exportar empresas',()=>csv('carteira_clientes',['CNPJ','Empresa','CNAE','Cidade','UF'],p.companies.map(c=>[c.cnpj,c.name,c.cnae,c.city,c.uf]))),E('a',{href:'/?carteira_cnae='+encodeURIComponent(p.segments.slice(0,5).map(s=>s.code).join(',')),text:'Usar os 5 principais CNAEs ↗'})]));
      if(p.missing.length)card.append(E('details',{},[E('summary',{text:p.missing.length+' CNPJs não encontrados na base local'}),E('p',{class:'muted',text:p.missing.join(', ')})]));return card;
    }));
    if(!state.portfolio.length)$('portfolioCards').append(empty('Importe uma carteira para identificar seus segmentos mais frequentes.'));
  }
  function renderAlerts(){
    $('alertBadge').textContent=state.alerts.length||'';
    $('alertsList').replaceChildren(...state.alerts.map(a=>E('article',{class:'alert-card'},[E('div',{},[E('b',{text:a.title}),E('p',{text:a.detail}),button('Abrir área',()=>choose(a.target))]),button('Marcar como visto',()=>mutate('dismiss',{key:a.id},'Alerta marcado como visto.'))])));
    if(!state.alerts.length)$('alertsList').append(empty('Nenhum alerta pendente neste momento.'));
  }
  function openProduct(product=null){
    editingProduct=product;fill($('productForm'),product||{unit:'unidade',minimum_quantity:1,status:'rascunho'});
    const partners=state.admin?state.all_partners:state.supplier_partners;
    selectOptions($('productForm').elements.partner,partners.map(p=>({value:p.id,label:p.name})),product?.partner||partners[0]?.id);
    $('productStatusLabel').hidden=!state.admin;$('productApprovalNote').hidden=state.admin;
    $('productDialogTitle').textContent=product?'Editar produto':'Novo produto do parceiro';$('productDialog').showModal();
  }
  function productTable(products,admin=false){
    if(!products.length)return empty('Nenhum produto cadastrado.');
    return table(['Produto','Fornecedor','Preço','Situação','Ações'],products.map(p=>[p.title,partnerName(p.partner),cash(p.price),p.status,E('div',{class:'actions'},[button('Editar',()=>openProduct(p)),...(admin&&p.status==='aprovado'?[button('Pausar',()=>mutate('product',{...p,status:'pausado'},'Produto ocultado do catálogo.'))]:[])]) ]));
  }
  function reviewCards(reviews,admin=false){
    const fields={title:'Produto',description:'Descrição',products:'Produtos/serviços',website:'Site',photo:'Foto',link:'Link',price:'Preço inicial',price_max:'Preço final',minimum_quantity:'Mínimo',unit:'Unidade',category:'Categoria',contact_name:'Contato',whatsapp:'WhatsApp',email:'E-mail',benefit:'Benefício',benefit_until:'Validade',conditions:'Condições',minimum_order:'Pedido mínimo',city:'Cidade',uf:'UF',coverage:'Estados atendidos',national:'Nacional',wholesale:'Atacado',small_business:'Pequenos negócios',logo:'Logo'};
    return reviews.map(r=>{
      const dl=E('dl',{class:'review-values'});
      for(const [key,value] of Object.entries(r.patch))if(fields[key])dl.append(E('dt',{text:fields[key]}),E('dd',{text:typeof value==='boolean'?(value?'Sim':'Não'):Array.isArray(value)?value.join(', '):String(value)}));
      return E('article',{class:'sub-card review-card'},[E('h3',{text:(r.type==='profile'?'Perfil de ':'Produto de ')+partnerName(r.partner)}),E('span',{class:'chip',text:r.status}),E('details',{},[E('summary',{text:'Conferir alterações enviadas'}),dl]),...(admin&&r.status==='aguardando'?[E('div',{class:'actions'},[button('Aprovar e publicar',()=>mutate('review',{id:r.id,revision:r.revision,action:'approve'},'Alterações aprovadas.'),'primary'),button('Rejeitar',()=>mutate('review',{id:r.id,revision:r.revision,action:'reject'},'Solicitação rejeitada.'),'danger')])]:[])]);
    });
  }
  function reports(target){
    target.replaceChildren(E('h3',{text:'Resultados dos parceiros · últimos 30 dias'}));
    if(!state.reports.length){target.append(empty('Não há resultados de parceiros vinculados a esta conta.'));return;}
    const rows=state.reports.map(r=>[r.name,String(r.views),String(r.site_clicks),String(r.contact_clicks),String(r.declared_results.quote),String(r.declared_results.negotiating),String(r.declared_results.purchased)]);
    const headers=['Parceiro','Fichas','Site','Contato','Cotações declaradas','Negociações declaradas','Compras declaradas'];
    target.append(table(headers,rows),button('Exportar relatório CSV',()=>csv('resultados_parceiros',headers,rows)),E('p',{class:'report-note',text:'Fichas e cliques medem interesse. As três últimas colunas contam usuários que marcaram cada resultado nos últimos 30 dias; não são pedidos faturados nem vendas verificadas.'}));
  }
  function renderSupplier(){
    $('supplierNewProduct').hidden=!state.supplier;
    $('supplierAccess').replaceChildren(...(!state.supplier?[empty('O administrador geral precisa vincular sua conta a um parceiro para liberar esta área.')]:[]));
    $('supplierProfiles').replaceChildren(...state.supplier_partners.map(p=>E('article',{class:'sub-card'},[E('h3',{text:p.name}),E('p',{text:p.description}),E('span',{class:'chip',text:'Situação: '+p.status}),E('div',{class:'actions'},[button('Enviar atualização do perfil',()=>{editingProfile=p;fill($('profileForm'),p);$('profileDialog').showModal();})])])));
    $('supplierProducts').replaceChildren(...(state.supplier?[E('h3',{text:'Seu catálogo'}),productTable(state.supplier_products)]:[]));
    $('supplierInbox').replaceChildren(...(state.supplier?[E('h3',{text:'Pedidos compartilhados'}),...state.inbox.slice().reverse().map(q=>E('article',{class:'sub-card'},[E('h3',{text:q.product}),E('p',{text:`${qty(q.quantity)} · ${q.city} · ${q.requester}`}),E('p',{text:q.notes}),button('Responder / atualizar proposta',()=>openOffer(q,true))]))]:[]));
    if(state.supplier&&!state.inbox.length)$('supplierInbox').append(empty('Nenhum pedido compartilhado com os seus parceiros.'));
    $('supplierReviews').replaceChildren(...(state.supplier?[E('h3',{text:'Seus envios para aprovação'}),...reviewCards(state.reviews)]:[]));
    $('supplierReports').hidden=!state.supplier;if(state.supplier)reports($('supplierReports'));
  }
  function renderAdmin(){
    $('adminTab').hidden=!state.admin;if(!state.admin)return;
    selectOptions($('assignPartner'),state.all_partners.map(p=>({value:p.id,label:p.name})));
    selectOptions($('assignLogin'),[{value:'',label:'Remover vínculo'},...state.accounts.map(a=>({value:a.login,label:a.name+' ('+a.login+')'}))]);
    $('assignmentList').replaceChildren(table(['Fornecedor','Conta responsável'],Object.entries(state.assignments).map(([id,key])=>[partnerName(id),state.accounts.find(a=>a.owner===key)?.login||'Conta indisponível'])));
    const pending=state.reviews.filter(r=>r.status==='aguardando');
    $('adminReviews').replaceChildren(E('h3',{text:'Alterações aguardando aprovação'}),...(pending.length?reviewCards(pending,true):[empty('Nenhuma solicitação pendente.')]));
    $('adminProducts').replaceChildren(E('h3',{text:'Catálogo completo'}),productTable(state.all_products,true));reports($('adminReports'));
  }
  async function load(){
    const version=++loadVersion;
    const d=await api('/api/business/state');if(version!==loadVersion)return;state=d;
    $('welcome').textContent=d.account_name||'Conta conectada';
    if(!$('quoteForm').elements.requester.value)$('quoteForm').elements.requester.value=d.account_name||'';
    $('stats').replaceChildren(...[[d.products.length,'Produtos publicados'],[d.quotes.length,'Seus pedidos de cotação'],[d.board.total,'Cartões na carteira'],[d.alerts.length,'Alertas pendentes']].map(([n,label])=>E('div',{class:'stat'},[E('b',{text:n}),E('span',{text:label})])));
    selectOptions($('productPartner'),[{value:'',label:'Todos'},...d.partners.map(p=>({value:p.id,label:p.name}))]);
    selectOptions($('productCategory'),[{value:'',label:'Todas'},...Array.from(new Set(d.products.map(p=>p.category).filter(Boolean))).sort().map(c=>({value:c,label:c}))]);
    selectOptions($('outcomePartner'),d.partners.map(p=>({value:p.id,label:p.name})));
    const checked=new Set(Array.from($('quotePartners').querySelectorAll('input:checked'),n=>n.value));
    $('quotePartners').replaceChildren(...d.partners.map(p=>{const input=E('input',{type:'checkbox',name:'partner',value:p.id});input.checked=checked.has(p.id);return E('label',{},[input,E('span',{text:p.name})]);}));
    if(!d.partners.length)$('quotePartners').append(E('p',{class:'muted',text:'Cadastre e aprove um fornecedor na rede de parceiros para preparar uma cotação.'}));
    renderProducts();renderOutcomes();renderQuotes();renderBoard();renderPortfolio();renderAlerts();renderSupplier();renderAdmin();choose(tab);
  }
  function bindForm(id,action){$(id).addEventListener('submit',e=>{e.preventDefault();run(()=>action($(id)),e.submitter||$(id).querySelector('[type="submit"]'));});}
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>choose(b.dataset.tab)));
  document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
  $('refresh').addEventListener('click',()=>run(load,$('refresh')));
  for(const id of ['productSearch','productPartner','productCategory'])$(id).addEventListener(id==='productSearch'?'input':'change',renderProducts);
  $('outcomePartner').addEventListener('change',renderOutcomes);$('boardSearch').addEventListener('input',renderBoard);
  $('supplierNewProduct').addEventListener('click',()=>openProduct());$('adminNewProduct').addEventListener('click',()=>openProduct());
  $('offerForm').elements.partner.addEventListener('change',fillOffer);
  bindForm('quoteForm',async form=>{const data=fields(form);data.partners=Array.from($('quotePartners').querySelectorAll('input:checked'),i=>i.value);await mutate('quote',data,'Pedido preparado. Copie a mensagem ou compartilhe no sistema.');});
  bindForm('offerForm',async form=>{const data=fields(form),prev=editingQuote.offers.find(o=>o.partner===data.partner);await mutate('offer',{...data,quote:editingQuote.id,revision:prev?.revision||0},'Proposta salva.');$('offerDialog').close();});
  bindForm('productForm',async form=>{const data=fields(form);if(editingProduct){data.id=editingProduct.id;data.revision=editingProduct.revision;}await mutate('product',data);$('productDialog').close();});
  bindForm('profileForm',async form=>{await mutate('supplier-profile',{...fields(form),partner:editingProfile.id},'Atualização enviada para aprovação.');$('profileDialog').close();});
  bindForm('assignmentForm',async form=>{await mutate('assign',fields(form),'Vínculo de acesso atualizado.');});
  bindForm('leadForm',async form=>{await mutate('lead',fields(form),'Empresa adicionada ao quadro.');form.reset();});
  bindForm('leadEditForm',async form=>{await mutate('lead',{...fields(form),cnpj:editingLead.cnpj,revision:editingLead.revision||0},'Retorno atualizado no quadro.');$('leadDialog').close();});
  bindForm('marginForm',async form=>{
    const d=await api('/api/business/margin',fields(form));const r=d.result;
    const dl=E('dl');for(const [label,key] of [['Custo por unidade','unit_cost'],['Impostos/taxas por unidade','unit_charges'],['Resultado por unidade','unit_profit'],['Resultado do lote','batch_profit'],['Preço de equilíbrio','break_even']])dl.append(E('dt',{text:label}),E('dd',{text:cash(r[key])}));
    $('marginResult').replaceChildren(E('h3',{text:'Margem estimada sobre a venda'}),E('strong',{text:qty(r.margin_percent)+'%'}),dl,E('p',{class:'muted',text:'Markup sobre o custo: '+(r.markup_percent==null?'indefinido (custo zero)':qty(r.markup_percent)+'%')+'. Os resultados consideram somente os custos e percentuais informados.'}));
  });
  function parseCsv(raw){
    const first=raw.split(/\r?\n/,1)[0];const delimiter=(first.match(/;/g)||[]).length>(first.match(/,/g)||[]).length?';':',';
    const rows=[];let row=[],cell='',quoted=false;
    for(let i=0;i<raw.length;i++){const c=raw[i];if(c==='"'){if(quoted&&raw[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}else if(c===delimiter&&!quoted){row.push(cell);cell='';}else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&raw[i+1]==='\n')i++;row.push(cell);if(row.some(v=>v.trim()))rows.push(row);row=[];cell='';}else cell+=c;}
    if(quoted)throw new Error('O CSV tem aspas sem fechamento.');row.push(cell);if(row.some(v=>v.trim()))rows.push(row);return rows;
  }
  let xlsxPromise;
  async function ensureXlsx(){if(window.XLSX)return window.XLSX;if(!xlsxPromise)xlsxPromise=new Promise((resolve,reject)=>{const script=E('script',{src:'https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js'});script.onload=()=>resolve(window.XLSX);script.onerror=()=>{xlsxPromise=null;reject(new Error('Não foi possível carregar a leitura de XLSX. Use CSV ou tente com internet disponível.'));};document.head.append(script);});return xlsxPromise;}
  bindForm('portfolioForm',async form=>{
    const file=$('portfolioFile').files[0];if(!file)throw new Error('Escolha o arquivo da carteira.');if(file.size>2000000)throw new Error('O arquivo deve ter até 2 MB.');
    let rows;if(file.name.toLowerCase().endsWith('.xlsx')){const x=await ensureXlsx();const book=x.read(await file.arrayBuffer(),{type:'array'});rows=x.utils.sheet_to_json(book.Sheets[book.SheetNames[0]],{header:1,defval:'',raw:true});}else if(file.name.toLowerCase().endsWith('.csv'))rows=parseCsv((await file.text()).replace(/^\ufeff/,''));else throw new Error('Use um arquivo CSV ou XLSX.');
    if(rows.length<2)throw new Error('Informe o cabeçalho CNPJ e pelo menos uma empresa.');
    const col=rows[0].findIndex(v=>String(v).trim().toUpperCase()==='CNPJ');if(col<0)throw new Error('A primeira linha precisa ter uma coluna chamada CNPJ.');
    const values=rows.slice(1).filter(r=>String(r[col]??'').trim()).map(r=>({cnpj:typeof r[col]==='number'?String(r[col]).padStart(14,'0'):String(r[col]).trim()}));
    if(values.length>500)throw new Error('Divida a carteira em arquivos com até 500 CNPJs.');
    await mutate('portfolio',{name:fields(form).name,rows:values},'Carteira analisada na base local.');
  });
  load().catch(e=>message(e.message,true));
})();
