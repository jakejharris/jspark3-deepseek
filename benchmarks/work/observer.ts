import {appendFileSync,realpathSync,existsSync} from 'node:fs';
import {resolve,sep,dirname} from 'node:path';
import {spawn} from 'node:child_process';
export default function(pi:any) {
 const root=resolve(process.cwd());
 const log=(kind:string,data:any)=>appendFileSync(process.env.WORK_PAYLOAD_LOG!,JSON.stringify({at:Date.now(),kind,...data})+'\n');
 pi.on('before_provider_request',(e:any)=>{e.payload.temperature=0;e.payload.max_tokens=65536;log('request',{payload:e.payload});return e.payload;});
 pi.on('tool_call',(e:any)=>{
   if(['read','write','edit'].includes(e.toolName)) { const p=resolve(root,e.input.path);let parent=p;while(!existsSync(parent))parent=dirname(parent);const actual=realpathSync(parent);if((p!==root&&!p.startsWith(root+sep))||(actual!==root&&!actual.startsWith(root+sep)))return {block:true,reason:'Work benchmark restricts files to this disposable workspace'}; }
 });
 pi.registerTool({name:'bash',label:'Bash',description:'Execute a bounded shell command inside the disposable workspace, with no network. Standard system utilities and Python standard library are available. Paths are identical to the other file tools.',parameters:{type:'object',properties:{command:{type:'string'},timeout:{type:'number'}},required:['command']},
 async execute(_id:any,args:any,signal:any){
  return await new Promise((done)=>{
   const argv=['--die-with-parent','--unshare-user','--unshare-pid','--unshare-net','--new-session','--ro-bind','/usr','/usr','--ro-bind','/bin','/bin','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--bind',root,root,'--chdir',root,'--setenv','PATH','/usr/bin:/bin','--setenv','HOME','/tmp','--setenv','OMP_NUM_THREADS','1','/bin/bash','--noprofile','--norc','-c',args.command];
   const p=spawn('/usr/bin/bwrap',argv,{env:{PATH:'/usr/bin:/bin',HOME:'/tmp'}});let output='';let finished=false;const kill=()=>p.kill('SIGKILL'); const timer=setTimeout(kill,Math.min(args.timeout||30,60)*1000);signal?.addEventListener('abort',kill,{once:true});
   const collect=(b:any)=>{output=(output+b.toString()).slice(-24000)};p.stdout.on('data',collect);p.stderr.on('data',collect);
   const finish=(code:any)=>{if(finished)return;finished=true;clearTimeout(timer);signal?.removeEventListener('abort',kill);done({content:[{type:'text',text:output+'\nExit code: '+code}],details:{exitCode:code,sandbox:true}})};
   p.on('error',(e:any)=>{output+=String(e);finish(-1)});p.on('close',finish);
  });
 }});
}
