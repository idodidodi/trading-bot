import {readFile, readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';

export async function buildManifest(root){
 const ui=['web/index.html','web/app.js','web/style.css','web/chart.js','web/favicon.svg','web/vendor/lightweight-charts.js'];
 async function walk(dir){const rows=await readdir(resolve(root,dir),{withFileTypes:true});return (await Promise.all(rows.map(r=>r.isDirectory()?walk(dir+'/'+r.name):[dir+'/'+r.name]))).flat();}
 const scanner=[...(await readdir(root)).filter(p=>p.endsWith('.py')&&!p.startsWith('test_')), ...await walk('skills'),'scanner.json','stock_universe.json'];
 async function hash(paths){const rows=await Promise.all(paths.map(async p=>{const raw=Buffer.from((await readFile(resolve(root,p))).toString('latin1').replace(/\r\n/g,'\n'),'latin1');const sha=createHash('sha1').update(`blob ${raw.length}\0`).update(raw).digest('hex');return `${p}:${sha}`;}));return createHash('sha256').update(rows.sort().join('\n')).digest('hex').slice(0,16);}
 const source=await readFile(resolve(root,'app_version.py'),'utf8'), version=source.match(/VERSION\s*=\s*['"]([^'"]+)/)?.[1];
 if(!version)throw Error('Application version missing');
 return {version,ui:await hash(ui),scanner:await hash(scanner)};
}
