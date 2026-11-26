// Offline JSQuAD paragraph retrieval diagnostic; no network or production changes.
import {readFileSync,statSync} from 'node:fs';
import {createHash} from 'node:crypto';
function evaluate(data) {
 const docs=[], qs=[];
 for (let ai=0;ai<data.data.length;ai++) for(const p of data.data[ai].paragraphs){const id=docs.length;docs.push(p.context);for(const q of p.qas){if(!q.answers?.some(a=>p.context.slice(a.answer_start,a.answer_start+a.text.length)===a.text))continue;qs.push({id,q:q.question,split:ai<10?"dev":"test"})}}
 const norm=s=>s.normalize("NFKC").toLowerCase();
 const grams=(s,n)=>{let out=[];for(const m of norm(s).matchAll(/[぀-ヿ㐀-䶿一-鿿豈-﫿々〆ー가-힯]+|[0-9a-z]+(?:[._\-/][0-9a-z]+)*/gu)){let w=m[0];if(/^[0-9a-z]/.test(w)){out.push(w.replace(/[.\-/]/g,"_"));if(/[._\-/]/.test(w))out.push(...w.split(/[._\-/]/).filter(Boolean));}else if(w.length<n){if(n===2)out.push(w)}else for(let i=0;i<=w.length-n;i++)out.push(w.slice(i,i+n));}return out};
 function build(n){const postings=new Map(),lens=[];for(let i=0;i<docs.length;i++){let ts=grams(docs[i],n),freq=new Map();lens.push(ts.length);for(let t of ts)freq.set(t,(freq.get(t)||0)+1);for(let [t,f] of freq){if(!postings.has(t))postings.set(t,[]);postings.get(t).push([i,f]);}}return {postings,lens,avg:lens.reduce((a,b)=>a+b,0)/lens.length}}
 const out={documents:docs.length,questions:qs.length,skipped:data.data.reduce((n,a)=>n+a.paragraphs.reduce((k,p)=>k+p.qas.length,0),0)-qs.length,results:{}};
 for(const n of [2,3]){let {postings,lens,avg}=build(n);let stats={dev:{count:0,hit1:0,hit10:0,mrr10:0,ndcg10:0,empty:0},test:{count:0,hit1:0,hit10:0,mrr10:0,ndcg10:0,empty:0}};for(const {id,q,split} of qs){const scores=new Map();for(const t of new Set(grams(q,n).filter(t=>t.length>=2))){let rows=postings.get(t)||[];let idf=Math.log(1+(docs.length-rows.length+.5)/(rows.length+.5));for(const [i,f] of rows)scores.set(i,(scores.get(i)||0)+idf*f*2.2/(f+1.2*(.25+.75*lens[i]/avg)));}let ranked=[...scores].sort((a,b)=>b[1]-a[1]||a[0]-b[0]).slice(0,10).map(x=>x[0]);let pos=ranked.indexOf(id)+1,s=stats[split];s.count++;if(!ranked.length)s.empty++;if(pos){s.hit10++;s.hit1+=+(pos===1);s.mrr10+=1/pos;s.ndcg10+=1/Math.log2(pos+1);}}out.results["cjk"+n]=Object.fromEntries(Object.entries(stats).map(([k,s])=>[k,{...s,recall1:s.hit1/s.count,recall10:s.hit10/s.count,mrr10:s.mrr10/s.count,ndcg10:s.ndcg10/s.count}]));}
 return out;
}
const path=process.argv[2];
if(!path || statSync(path).size>8*1024*1024) throw Error('supply pinned JSQuAD JSON');
const raw=readFileSync(path);
const sha=createHash('sha1').update('blob '+raw.length+'\0').update(raw).digest('hex');
if(sha!=='bcde414e0b7131607bb1fa2c4542dd888f68771a') throw Error('source blob mismatch');
console.log(JSON.stringify(evaluate(JSON.parse(raw.toString('utf8'))),null,2));
