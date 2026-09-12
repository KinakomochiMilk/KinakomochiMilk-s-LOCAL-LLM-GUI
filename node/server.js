const http = require("http");
const { URL } = require("url");

const CONFIG = {
  host: process.env.BRIDGE_HOST || "127.0.0.1",
  port: Number(process.env.BRIDGE_PORT || 18767),
  ollama: process.env.OLLAMA_HOST || "http://127.0.0.1:11434"
};

let activeRequest = null;

function json(res, status, value) {
  const body = JSON.stringify(value);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-cache",
    "Access-Control-Allow-Origin": "*"
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let body = "";
    req.setEncoding("utf8");
    req.on("data", chunk => {
      body += chunk;
      if (body.length > 20 * 1024 * 1024) {
        req.destroy();
        reject(new Error("Request body too large"));
      }
    });
    req.on("end", () => {
      try { resolve(body ? JSON.parse(body) : {}); }
      catch { reject(new Error("Invalid JSON")); }
    });
    req.on("error", reject);
  });
}

function ollamaRequest(path, options = {}) {
  const target = new URL(path, CONFIG.ollama);
  return new Promise((resolve, reject) => {
    const req = http.request({
      protocol: target.protocol,
      hostname: target.hostname,
      port: target.port,
      path: target.pathname + target.search,
      method: options.method || "GET",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) }
    }, resolve);
    req.on("error", reject);
    if (options.body !== undefined) req.write(options.body);
    req.end();
  });
}

function collect(upstream) {
  return new Promise((resolve, reject) => {
    let body = "";
    upstream.setEncoding("utf8");
    upstream.on("data", chunk => body += chunk);
    upstream.on("end", () => resolve(body));
    upstream.on("error", reject);
  });
}

async function handleModels(res) {
  try {
    const upstream = await ollamaRequest("/api/tags");
    const body = await collect(upstream);
    if (upstream.statusCode < 200 || upstream.statusCode >= 300) { json(res,502,{ok:false,error:`Ollama returned HTTP ${upstream.statusCode}`}); return; }
    const parsed = JSON.parse(body);
    json(res,200,{ok:true,models:Array.isArray(parsed.models)?parsed.models:[]});
  } catch (e) { json(res,503,{ok:false,error:`Ollama connection failed: ${e.message}`}); }
}

async function handleHealth(res) {
  try {
    const upstream = await ollamaRequest("/api/tags");
    upstream.resume();
    upstream.on("end",()=>json(res,200,{ok:upstream.statusCode>=200&&upstream.statusCode<300,ollama:CONFIG.ollama,status:upstream.statusCode}));
  } catch (e) { json(res,503,{ok:false,ollama:CONFIG.ollama,error:e.message}); }
}

async function handleSetOllama(req,res) {
  try {
    const payload=await readBody(req);
    const host=String(payload.host||"").trim();
    const port=Number(payload.port);
    if(!host || !Number.isInteger(port) || port<1 || port>65535) { json(res,400,{ok:false,error:"Invalid Ollama host or port"}); return; }
    const normalized=host.startsWith("http://")||host.startsWith("https://")?host.replace(/\/$/,""):`http://${host}:${port}`;
    const parsed=new URL(normalized);
    CONFIG.ollama=parsed.origin;
    json(res,200,{ok:true,ollama:CONFIG.ollama});
  } catch(e) { json(res,400,{ok:false,error:e.message}); }
}

async function handlePull(req,res) {
  let payload;
  try { payload=await readBody(req); } catch(e) { json(res,400,{ok:false,error:e.message}); return; }
  if(!payload.name||typeof payload.name!=="string") { json(res,400,{ok:false,error:"name is required"}); return; }
  let upstream;
  try { upstream=await ollamaRequest("/api/pull",{method:"POST",body:JSON.stringify({name:payload.name.trim(),stream:true})}); }
  catch(e) { json(res,503,{ok:false,error:`Ollama connection failed: ${e.message}`}); return; }
  if(upstream.statusCode<200||upstream.statusCode>=300) { const body=await collect(upstream); json(res,502,{ok:false,error:`Ollama returned HTTP ${upstream.statusCode}`,detail:body.slice(0,4000)}); return; }
  res.writeHead(200,{"Content-Type":"application/x-ndjson; charset=utf-8","Cache-Control":"no-cache","Connection":"keep-alive","Access-Control-Allow-Origin":"*"});
  upstream.on("data",chunk=>res.write(chunk));
  upstream.on("end",()=>{if(!res.writableEnded)res.end();});
  upstream.on("error",()=>{if(!res.writableEnded)res.end();});
  req.on("close",()=>upstream.destroy());
}

async function showModel(name) {
  const upstream=await ollamaRequest("/api/show",{method:"POST",body:JSON.stringify({name})});
  const body=await collect(upstream);
  if(upstream.statusCode<200||upstream.statusCode>=300)throw new Error(`Ollama returned HTTP ${upstream.statusCode}`);
  return JSON.parse(body);
}

async function handleVisionModel(req,res) {
  let payload={};
  try{payload=await readBody(req);}catch(e){json(res,400,{ok:false,error:e.message});return;}
  try{
    const upstream=await ollamaRequest("/api/tags");
    const tags=JSON.parse(await collect(upstream));
    const names=Array.isArray(tags.models)?tags.models.map(item=>item&&item.name).filter(Boolean):[];
    const preferred=typeof payload.preferred==="string"?payload.preferred.trim():"";
    const ordered=preferred?[preferred,...names.filter(name=>name!==preferred)]:names;
    for(const name of ordered){
      try{
        const info=await showModel(name);
        const capabilities=Array.isArray(info.capabilities)?info.capabilities.map(String):[];
        const hasVision=capabilities.includes("vision")||/vision|llava|qwen2\.5-vl|qwen3-vl|gemma3/i.test(`${info.modelfile||""} ${info.template||""} ${name}`);
        if(hasVision){json(res,200,{ok:true,model:name,capabilities});return;}
      }catch{}
    }
    json(res,200,{ok:false,model:null,error:"No Vision model found"});
  }catch(e){json(res,503,{ok:false,error:e.message});}
}

async function handleChat(req,res) {
  let payload;
  try{payload=await readBody(req);}catch(e){json(res,400,{ok:false,error:e.message});return;}
  if(!payload.model||!Array.isArray(payload.messages)){json(res,400,{ok:false,error:"model and messages are required"});return;}
  let upstream;
  try{upstream=await ollamaRequest("/api/chat",{method:"POST",body:JSON.stringify({model:payload.model,messages:payload.messages,stream:true,options:payload.options||undefined})});}
  catch(e){json(res,503,{ok:false,error:`Ollama connection failed: ${e.message}`});return;}
  if(upstream.statusCode<200||upstream.statusCode>=300){const body=await collect(upstream);json(res,502,{ok:false,error:`Ollama returned HTTP ${upstream.statusCode}`,detail:body.slice(0,4000)});return;}
  res.writeHead(200,{"Content-Type":"application/x-ndjson; charset=utf-8","Cache-Control":"no-cache","Access-Control-Allow-Origin":"*"});
  activeRequest=upstream;
  upstream.on("data",chunk=>res.write(chunk));
  upstream.on("end",()=>{activeRequest=null;if(!res.writableEnded)res.end();});
  upstream.on("error",()=>{activeRequest=null;if(!res.writableEnded)res.end();});
  req.on("close",()=>{if(activeRequest===upstream){upstream.destroy();activeRequest=null;}});
}

function handleStop(res){if(activeRequest){activeRequest.destroy();activeRequest=null;json(res,200,{ok:true,stopped:true});}else json(res,200,{ok:true,stopped:false});}

const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,`http://${CONFIG.host}:${CONFIG.port}`);
  if(req.method==="OPTIONS"){res.writeHead(204,{"Access-Control-Allow-Origin":"*","Access-Control-Allow-Methods":"GET,POST,OPTIONS","Access-Control-Allow-Headers":"Content-Type"});res.end();return;}
  try{
    if(req.method==="GET"&&url.pathname==="/health"){await handleHealth(res);return;}
    if(req.method==="GET"&&url.pathname==="/models"){await handleModels(res);return;}
    if(req.method==="POST"&&url.pathname==="/set-ollama"){await handleSetOllama(req,res);return;}
    if(req.method==="POST"&&url.pathname==="/chat"){await handleChat(req,res);return;}
    if(req.method==="POST"&&url.pathname==="/vision-model"){await handleVisionModel(req,res);return;}
    if(req.method==="POST"&&url.pathname==="/pull"){await handlePull(req,res);return;}
    if(req.method==="POST"&&url.pathname==="/stop"){handleStop(res);return;}
    json(res,404,{ok:false,error:"Not found"});
  }catch(e){if(!res.headersSent)json(res,500,{ok:false,error:e.message});else if(!res.writableEnded)res.end();}
});

server.listen(CONFIG.port,CONFIG.host,()=>{console.log(`Local LLM GUI bridge listening on http://${CONFIG.host}:${CONFIG.port}`);console.log(`Ollama: ${CONFIG.ollama}`);});

function shutdown(){if(activeRequest){activeRequest.destroy();activeRequest=null;}server.close(()=>process.exit(0));}
process.on("SIGINT",shutdown);
process.on("SIGTERM",shutdown);
