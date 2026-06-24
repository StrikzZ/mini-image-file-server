const grid=document.getElementById('grid');
const drop=document.getElementById('drop');
const fileInput=document.getElementById('file');
const prog=document.getElementById('prog');
const tabImg=document.getElementById('tab-img');
const tabFiles=document.getElementById('tab-files');
const btnPrev=document.getElementById('prev');
const btnNext=document.getElementById('next');
const pagerInfo=document.getElementById('pager-info');
let current='images';
const PER_PAGE=15;
const page={ images:1, files:1 };
let totalPages={ images:1, files:1 };
let totals={ images:0, files:0 };

const MAX_MB = Number(document.getElementById("app-config")?.dataset.maxFileMb || 15);
const MAX_BYTES = MAX_MB*1024*1024;

tabImg.onclick=()=>{current='images';tabImg.classList.add('active');tabFiles.classList.remove('active');fetchList();};
tabFiles.onclick=()=>{current='files';tabFiles.classList.add('active');tabImg.classList.remove('active');fetchList();};
btnPrev.onclick=()=>{if(page[current]>1){page[current]--;fetchList();}};
btnNext.onclick=()=>{if(page[current]<totalPages[current]){page[current]++;fetchList();}};

function updatePager(meta){totals[current]=meta.total??0;totalPages[current]=meta.total_pages??1;
const p=meta.page??1;const per=meta.per_page??PER_PAGE;const start=(p-1)*per+1;const end=Math.min(p*per,totals[current]);
pagerInfo.textContent=totals[current]?`Page ${p}/${totalPages[current]} · ${start}-${end} of ${totals[current]}`:'No items';
btnPrev.disabled=(p<=1);btnNext.disabled=(p>=totalPages[current]);}

async function fetchList(){
  const p=page[current];
  const url=current==='images'?`/list/images?page=${p}&limit=${PER_PAGE}`:`/list/files?page=${p}&limit=${PER_PAGE}`;
  const r=await fetch(url);const data=await r.json();renderGrid(data.items,current);updatePager(data);
}

function copyHandler(rawUrl, btn){
  return async()=>{const url=new URL(rawUrl,window.location.origin).href;
    try{await navigator.clipboard.writeText(url);btn.textContent='Copied!';btn.classList.add('success');
      setTimeout(()=>{btn.textContent='Copy';btn.classList.remove('success');},1200);
    }catch(e){const ta=document.createElement('textarea');ta.value=url;document.body.appendChild(ta);
      ta.select();document.execCommand('copy');document.body.removeChild(ta);
      btn.textContent='Copied!';setTimeout(()=>{btn.textContent='Copy';},1200);}
  };
}

function renderGrid(items,kind){grid.innerHTML='';for(const it of items){
  const card=document.createElement('div');card.className='card';
  if(kind==='images'){const img=document.createElement('img');img.className='thumb';img.loading='lazy';img.src=it.raw_url;img.alt=it.id;card.append(img);
    const meta=document.createElement('div');meta.className='meta';
    const left=document.createElement('span');left.className='meta-left';left.textContent=timeAgo(new Date(it.created));
    const actions=document.createElement('div');actions.className='actions';
    const aOpen=document.createElement('a');aOpen.href=it.page_url;aOpen.target='_blank';aOpen.className='btn';aOpen.textContent='Open';
    const btnCopy=document.createElement('button');btnCopy.className='btn';btnCopy.textContent='Copy';
    btnCopy.onclick=copyHandler(it.raw_url,btnCopy);
    actions.append(aOpen,btnCopy);meta.append(left,actions);card.append(meta);
  }else{const img=document.createElement('img');img.className='thumb file-icon';img.loading='lazy';
    img.src='/static/img/zip_icon.png';img.alt=it.original_name||it.id;card.append(img);
    const cap=document.createElement('div');cap.className='caption';cap.title=it.original_name||it.id;cap.textContent=it.original_name||it.id;card.append(cap);
    const meta=document.createElement('div');meta.className='meta';
    const left=document.createElement('span');left.className='meta-left';left.textContent=timeAgo(new Date(it.created));
    const actions=document.createElement('div');actions.className='actions';
    const aOpen=document.createElement('a');aOpen.href=it.page_url;aOpen.target='_blank';aOpen.className='btn';aOpen.textContent='Open';
    const aDl=document.createElement('a');
    aDl.href=it.raw_url;aDl.className='btn icon';aDl.title='Download';aDl.setAttribute('download','');
    aDl.innerHTML='<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 11 5 5 5-5"/><path d="M5 21h14"/></svg>';
    actions.append(aOpen,aDl);meta.append(left,actions);card.append(meta);}
  grid.append(card);}}

function timeAgo(date){const s=Math.floor((Date.now()-date.getTime())/1000);
const i=Math.floor(s/60);const h=Math.floor(i/60);const d=Math.floor(h/24);
if(s<60)return s+'s';if(i<60)return i+'m';if(h<24)return h+'h';return d+'d';}

function uploadFile(file){
  if (file.size > MAX_BYTES) {
    alert(`Datei ist größer als ${MAX_MB} MB`);
    return Promise.reject('too large');
  }
  const fd=new FormData();fd.append('file',file);
  prog.style.display='block';prog.value=0;
  return new Promise((res,rej)=>{const xhr=new XMLHttpRequest();xhr.open('POST','/upload');
  xhr.upload.onprogress=e=>{if(e.lengthComputable)prog.value=(e.loaded/e.total)*100;};xhr.onload=()=>{prog.style.display='none';prog.value=0;if(xhr.status>=200&&xhr.status<300)res(JSON.parse(xhr.responseText));else rej(xhr.responseText);};
  xhr.onerror=()=>{prog.style.display='none';rej('network error');};xhr.send(fd);});
}

['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();e.stopPropagation();drop.classList.add('drag');}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();e.stopPropagation();drop.classList.remove('drag');}));
drop.addEventListener('drop',async(e)=>{const f=e.dataTransfer.files;if(!f||!f.length)return;
try{await uploadFile(f[0]);page[current]=1;await fetchList();}catch(err){/* handled */}});
fileInput.addEventListener('change',async()=>{if(!fileInput.files||!fileInput.files.length)return;
try{await uploadFile(fileInput.files[0]);fileInput.value='';page[current]=1;await fetchList();}catch(err){/* handled */}});

document.addEventListener('paste', async (e) => {
  const dt = e.clipboardData || window.clipboardData;
  if (!dt) return;
  const items = dt.items || [];
  const files = [];
  for (const item of items) {
    if (item && item.kind === 'file') {
      const f = item.getAsFile();
      if (f && f.size) files.push(f);
    }
  }
  if (!files.length) return;
  e.preventDefault();
  try {
    await uploadFile(files[0]);
    page[current]=1;
    await fetchList();
  } catch (err) {
    /* handled */
  }
});

fetchList();
