// Musik's implementation of Nuclear's public host interfaces.
import readline from 'node:readline';
import {pathToFileURL} from 'node:url';

const write = value => process.stdout.write(JSON.stringify(value) + '\n');
// Keep plugin logging out of the RPC stream (and avoid logging credentials).
for (const name of ['log', 'debug', 'info', 'warn', 'error']) console[name] = () => {};
let callbackId = 0;
const callbacks = new Map(), providers = new Map(), loaded = new Map();
const events = new Map(), subscriptions = new Map();
let definitions = new Map(), active = {}, failures = {};
function callback(method, args) {
  return new Promise((resolve, reject) => {
    const id = ++callbackId;
    const timer = setTimeout(() => { callbacks.delete(id); reject(new Error('Host request timed out')); }, 60000);
    callbacks.set(id, {resolve, reject, timer});
    write({callback:id, method, args});
  });
}
function subscribe(key, listener) {
  const list = subscriptions.get(key) ?? new Set();
  subscriptions.set(key, list); list.add(listener);
  return () => list.delete(listener);
}
async function notify(key, value) {
  await Promise.allSettled([...subscriptions.get(key) ?? []].map(fn => fn(value)));
}
function provider(id, kind) {
  const found = id ? providers.get(id) : providers.get(active[kind]) ?? [...providers.values()].find(p => p.kind === kind);
  if (!found || found.kind !== kind) throw new Error(`No ${kind} provider available`);
  return found;
}
async function invoke(id, kind, method, args=[]) {
  const p = provider(id, kind);
  if (typeof p[method] !== 'function') throw new Error(`${p.name} does not support ${method}`);
  return await p[method](...args);
}
async function search(params, id) {
  const p = provider(id, 'metadata'), result = {artists:[], albums:[], tracks:[], playlists:[]};
  for (const type of params.types ?? ['artists','albums','tracks']) {
    const method = `search${type[0].toUpperCase()}${type.slice(1)}`;
    if (typeof p[method] === 'function') result[type] = await p[method](params);
  }
  return result;
}
function apiFor(pluginId) {
  const key = id => `${pluginId}:${id}`;
  const api = {
    Providers: {
      register(p) {
        if (!p?.id || !p.kind) throw new Error('Invalid provider descriptor');
        if (providers.has(p.id)) throw new Error(`Duplicate provider ID: ${p.id}`);
        providers.set(p.id, {...p, pluginId}); notify('providers', null); return p.id;
      },
      unregister: id => providers.delete(id),
      list: kind => [...providers.values()].filter(p => !kind || p.kind === kind),
      get: (id, kind) => {try {return provider(id, kind);} catch {return undefined;}},
      subscribe: fn => subscribe('providers', fn),
    },
    Settings: {
      register: async defs => {
        for (const d of defs) definitions.set(key(d.id), {...d, pluginId});
        await callback('settings.register',[pluginId,defs]);
        return {registered:defs.map(d=>d.id)};
      },
      get: async id => callback('settings.get',[pluginId,id]),
      set: async (id, value) => {
        const d=definitions.get(key(id));
        if(!d) throw new Error('Unknown plugin setting');
        if(['string','enum'].includes(d.kind) && typeof value !== 'string') throw new Error('Setting requires a string');
        if(d.kind==='boolean' && typeof value !== 'boolean') throw new Error('Setting requires true or false');
        if(d.kind==='number' && (typeof value !== 'number' || !Number.isFinite(value) || (d.min!=null && value<d.min) || (d.max!=null && value>d.max))) throw new Error('Setting requires a number within its allowed range');
        if(d.kind==='enum' && !d.options?.some(option=>option.value===value)) throw new Error('Invalid setting option');
        await callback('settings.set',[pluginId,id,value]); await notify(key(id), value);
      },
      getGlobal: async id => callback('settings.get',['global',id]),
      setGlobal: async (id,value) => {await callback('settings.set',['global',id,value]); await notify(`global:${id}`, value);},
      subscribe: (id,fn) => subscribe(key(id), fn),
      registerWidget: () => {throw new Error('Custom React settings widgets require a terminal adapter');},
      unregisterWidget: () => {},
    },
    Http: {fetch: async (input,init={}) => {
      const url = String(input instanceof Request ? input.url : input);
      const response = await callback('http', [url, {...init, headers:Object.fromEntries(new Headers(init.headers ?? {}))}]);
      return new Response([204,205,304].includes(response.status) ? null : response.body, {status:response.status,headers:response.headers});
    }},
    Ytdlp: Object.fromEntries(['search','getStream','getPlaylist'].map(method=>[method,(...args)=>callback(`ytdlp.${method}`,args)])),
    Logger: Object.fromEntries(['debug','info','warn','error'].map(name=>[name,()=>{}])),
    Events: {
      on: (event,fn) => {const list=events.get(event) ?? new Set(); events.set(event,list); list.add(fn); return ()=>list.delete(fn);},
      emit: (event,payload) => Promise.allSettled([...events.get(event) ?? []].map(fn=>fn(payload))),
    },
    Metadata: {search},
    Streaming: {
      resolveCandidatesForTrack: async track => {try {const p=provider(undefined,'streaming'); const candidates=p.searchForTrackV2 ? await p.searchForTrackV2(track) : await p.searchForTrack(track.artists?.map(a=>a.name).join(', ') ?? '',track.title,track.album?.title); return {success:true,candidates};} catch(e) {return {success:false,error:e.message};}},
      resolveStreamForCandidate: async candidate => {const p=provider(candidate.source?.provider,'streaming'); const stream=p.getStreamUrlV2 ? await p.getStreamUrlV2(candidate) : await p.getStreamUrl(candidate.id); return {...candidate,stream};},
    },
    Lyrics: {getLyricsForTrack: async (track,id) => {const p=provider(id,'lyrics'); const lyrics=await p.getLyrics(track,{}); return lyrics ? [{providerId:p.id,providerName:p.name,lyrics}] : [];}},
    Discovery: {getRecommendations: (context,options,id) => invoke(id,'discovery','getRecommendations',[context,options])},
    Shell: {openExternal: url => callback('shell.openExternal',[url])},
  };
  for (const method of ['fetchArtistBio','fetchArtistSocialStats','fetchArtistAlbums','fetchArtistTopTracks','fetchArtistPlaylists','fetchArtistRelatedArtists','fetchAlbumDetails']) api.Metadata[method] = (id,pid)=>invoke(pid,'metadata',method,[id]);
  api.Dashboard = {};
  for (const method of ['fetchTopTracks','fetchTopArtists','fetchTopAlbums','fetchEditorialPlaylists','fetchNewReleases']) api.Dashboard[method] = async id => {
    const ps=id ? [provider(id,'dashboard')] : [...providers.values()].filter(p=>p.kind==='dashboard' && typeof p[method]==='function');
    return await Promise.all(ps.map(async p=>({providerId:p.id,metadataProviderId:p.metadataProviderId,providerName:p.name,items:await p[method]()})));
  };
  // Desktop/player control requests are handled by Musik, never fake success.
  for (const [area,methods] of Object.entries({
    Queue:['getQueue','getCurrentItem','addToQueue','addNext','addAt','removeByIds','removeByIndices','clearQueue','reorder','updateItemState','goToNext','goToPrevious','goToIndex','goToId'],
    Playback:['getState','play','pause','stop','toggle','seekTo','getVolume','setVolume','isMuted','setMuted','isShuffleEnabled','setShuffleEnabled','getRepeatMode','setRepeatMode'],
    Playlists:['getIndex','getPlaylist','createPlaylist','deletePlaylist','addTracks','removeTracks','reorderTracks','importPlaylist','saveQueueAsPlaylist'],
    Favorites:['getTracks','getAlbums','getArtists','addTrack','removeTrack','isTrackFavorite','addAlbum','removeAlbum','isAlbumFavorite','addArtist','removeArtist','isArtistFavorite'],
  })) {
    api[area]=Object.fromEntries(methods.map(method=>[method,(...args)=>callback(`${area}.${method}`,args)]));
    api[area].subscribe=fn=>subscribe(area,fn);
    if (area==='Queue') api[area].subscribeToCurrentItem=fn=>subscribe('currentItem',fn);
  }
  return api;
}
// Providers using the browser's global fetch receive the same HTTP bridge.
globalThis.fetch = apiFor('musik').Http.fetch;
async function unload() {
  for (const [id,{plugin,api}] of [...loaded].reverse()) {
    try {await plugin.onDisable?.(api); await plugin.onUnload?.(api);} catch {}
    loaded.delete(id);
  }
  providers.clear(); events.clear(); subscriptions.clear(); definitions.clear(); failures={};
}
async function handle(method,args) {
  if (method==='init') {
    await unload(); active=args.active ?? {};
    for (const entry of args.plugins) {
      const api=apiFor(entry.id);
      const savedEvents = new Map([...events].map(([key,set])=>[key,new Set(set)]));
      const savedSubscriptions = new Map([...subscriptions].map(([key,set])=>[key,new Set(set)]));
      let plugin;
      try {
        const module=await import(pathToFileURL(entry.path).href);
        plugin=module.default?.default ?? module.default;
        if (!plugin || !['onEnable','onLoad'].some(k=>typeof plugin[k]==='function')) throw new Error('No Nuclear plugin lifecycle entry point');
        await plugin.onLoad?.(api); await plugin.onEnable?.(api);
        if (![...providers.values()].some(p=>p.pluginId===entry.id)) throw new Error('This plugin requires a desktop/player integration rather than a music provider');
        loaded.set(entry.id,{plugin,api});
      } catch(e) {
        try {await plugin?.onDisable?.(api); await plugin?.onUnload?.(api);} catch {}
        events.clear(); for (const [key,set] of savedEvents) events.set(key,set);
        subscriptions.clear(); for (const [key,set] of savedSubscriptions) subscriptions.set(key,set);
        for (const [key,d] of definitions) if(d.pluginId===entry.id) definitions.delete(key);
        for (const [id,p] of providers) if(p.pluginId===entry.id) providers.delete(id);
        failures[entry.id]=String(e.message).slice(0,250);
      }
    }
    return handle('status',{});
  }
  if (method==='status') return {providers:[...providers.values()].map(p=>({id:p.id,name:p.name,kind:p.kind,pluginId:p.pluginId,streamingProviderId:p.streamingProviderId,methods:Object.keys(p).filter(k=>typeof p[k]==='function')})),loaded:[...loaded.keys()],failures,settings:[...definitions.values()]};
  if (method==='search') return search(args.params,args.provider);
  if (method==='invoke') return invoke(args.provider,args.kind,args.method,args.args);
  if (method==='stream') {
    const track=args.track, p=provider(args.provider ?? track.source?.provider,'streaming');
    let candidates;
    const metadata=providers.get(track.source?.provider);
    const explicit=track.streamCandidates?.filter(c=>c.source?.provider===p.id);
    if(explicit?.length) candidates=explicit;
    else if (track.source?.provider===p.id || metadata?.streamingProviderId===p.id) candidates=[{id:track.source.id,source:{...track.source,provider:p.id},title:track.title}];
    else candidates=p.searchForTrackV2 ? await p.searchForTrackV2(track) : await p.searchForTrack(track.artists?.map(a=>a.name).join(', ') ?? '',track.title,track.album?.title);
    for (const c of candidates ?? []) {
      try {const stream=p.getStreamUrlV2 ? await p.getStreamUrlV2(c) : await p.getStreamUrl(c.id); if(stream?.url) return stream;} catch {}
    }
    throw new Error('Plugin could not resolve a playable audio stream');
  }
  if (method==='lyrics') return apiFor('musik').Lyrics.getLyricsForTrack(args.track,args.provider);
  if (method==='setting') {await apiFor(args.plugin).Settings.set(args.id,args.value); return true;}
  if (method==='event') return apiFor('musik').Events.emit(args.event,args.payload);
  if (method==='notify') return notify(args.key,args.value);
  if (method==='close') {await unload(); setImmediate(()=>process.exit(0)); return true;}
  throw new Error(`Unsupported host method: ${method}`);
}
readline.createInterface({input:process.stdin}).on('line',async line=>{
  let message;
  try {
    message=JSON.parse(line);
    if ('callbackResult' in message) {
      const pending=callbacks.get(message.callbackResult); if(!pending)return;
      callbacks.delete(message.callbackResult); clearTimeout(pending.timer);
      message.error ? pending.reject(new Error(message.error)) : pending.resolve(message.result); return;
    }
    const result=await handle(message.method,message.args ?? {});
    write({id:message.id,result:result ?? null});
  } catch(e) {write({id:message?.id,error:String(e.message).slice(0,300)});}
});
process.stdin.on('end',()=>process.exit(0));
