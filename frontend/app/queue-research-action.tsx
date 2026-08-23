"use client";
import { useEffect, useState } from "react";
const apiBase=process.env.NEXT_PUBLIC_API_BASE_URL??"http://127.0.0.1:8000";
export function QueueResearchAction({playerId,source="user",context}:{playerId:number;source?:string;context?:Record<string,unknown>}){
 const [state,setState]=useState<string|null>(null); const [busy,setBusy]=useState(false);
 const load=()=>fetch(`${apiBase}/fpl/research-queue`).then(r=>r.json()).then((x:any[])=>setState(x.find(i=>i.player_id===playerId&&["queued","running","snoozed"].includes(i.status))?.status??null)).catch(()=>{});
 useEffect(()=>{void load()},[playerId]);
 async function add(){setBusy(true);await fetch(`${apiBase}/fpl/research-queue`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({player_id:playerId,source,source_context:context})});setBusy(false);load()}
 async function remove(){const rows=await fetch(`${apiBase}/fpl/research-queue`).then(r=>r.json());const item=rows.find((i:any)=>i.player_id===playerId&&["queued","running","snoozed"].includes(i.status));if(item) await fetch(`${apiBase}/fpl/research-queue/${item.id}/remove`,{method:"POST"});load()}
 return <button className="quiet-button" disabled={busy||state==="running"} onClick={state?remove:add}>{state?state==="running"?"Running":"Queued · Remove":busy?"Adding…":"Queue research"}</button>;
}
