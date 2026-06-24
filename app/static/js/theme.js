// --- Theme: system default, manual toggle overrides, persisted in localStorage ---
(function(){
  const root=document.documentElement;
  const toggle=document.getElementById('theme-toggle');
  function systemPrefersDark(){return window.matchMedia&&window.matchMedia('(prefers-color-scheme: dark)').matches;}
  function effectiveTheme(){
    let stored=null;
    try{stored=localStorage.getItem('theme');}catch(e){}
    if(stored==='dark'||stored==='light')return stored;
    return systemPrefersDark()?'dark':'light';
  }
  function apply(t){
    root.setAttribute('data-theme',t);
    if(toggle)toggle.setAttribute('aria-checked', t==='dark' ? 'true' : 'false');
  }
  apply(effectiveTheme());
  if(toggle){
    toggle.onclick=()=>{
      const next=(root.getAttribute('data-theme')==='dark')?'light':'dark';
      try{localStorage.setItem('theme',next);}catch(e){}
      apply(next);
    };
  }
  // React to system change only if user hasn't chosen manually
  if(window.matchMedia){
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',()=>{
      let stored=null;try{stored=localStorage.getItem('theme');}catch(e){}
      if(stored!=='dark'&&stored!=='light')apply(effectiveTheme());
    });
  }
})();
