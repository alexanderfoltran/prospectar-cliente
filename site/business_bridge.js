(() => {
  'use strict';
  const token=localStorage.getItem('prospectax_token')||'';
  fetch('/api/business/alerts',{headers:{'X-Auth-Token':token},cache:'no-store'}).then(r=>r.ok?r.json():null).then(d=>{const badge=document.getElementById('businessAlertCount');if(badge&&d?.alerts?.length)badge.textContent='('+d.alerts.length+')';}).catch(()=>{});
  const codes=(new URLSearchParams(location.search).get('carteira_cnae')||'').split(',').filter(c=>/^\d{7}$/.test(c)).slice(0,10);
  if(codes.length){
    window.applyImportedPortfolio=function(){
      if(typeof cnaes==='undefined'||typeof renderCnaeTags!=='function')return;
      cnaes=[...new Set(codes)];renderCnaeTags();
      if(typeof saveUserPrefs==='function')saveUserPrefs();
      if(typeof showToast==='function')showToast('CNAEs da carteira carregados. Escolha a região e execute a busca.','success');
      history.replaceState(null,'',location.pathname);
    };
    window.addEventListener('prospectax-ready',window.applyImportedPortfolio,{once:true});
    if(window.prospectaxReady)window.applyImportedPortfolio();
  }
})();
