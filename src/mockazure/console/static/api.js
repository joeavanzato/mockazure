let csrf = null;
export function setCSRF(value) { csrf = value; }
export async function api(path, {method="GET", json, body, anonymous=false}={}) {
  if (!path.startsWith("/_mock/")) throw new Error("Only local management paths are allowed.");
  const headers = {};
  if (json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(json); }
  if (csrf && !["GET","HEAD"].includes(method)) headers["X-MockAzure-CSRF"] = csrf;
  const response = await fetch(path, {method, headers, body, credentials:"same-origin", redirect:"error"});
  const value = response.status === 204 ? {} : await response.json();
  if (!response.ok) {
    if (response.status === 401 && !anonymous) window.dispatchEvent(new Event("lab:unauthorized"));
    throw new Error(`${value.error?.message || "Request failed"} (${response.status})`);
  }
  return value;
}
export function el(tag, attrs={}, ...children) {
  const node = document.createElement(tag);
  for (const [key,value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
export const field = (label, input, cls="") => el("label", {class:cls}, label, input);
export const button = (label, action, cls="") => el("button", {type:"button", class:cls, onclick:async event=>{
  const modal=event.currentTarget.closest("dialog");
  modal?.querySelector(".dialog-error")?.remove();
  try { await action(event); }
  catch(error) {
    if(modal?.open) modal.querySelector("#editor-content").append(el("p",{class:"dialog-error error",role:"alert"},error.message));
    else window.dispatchEvent(new CustomEvent("lab:error",{detail:error.message}));
  }
}}, label);
export const jsonView = value => el("pre",{},JSON.stringify(value,null,2));
export function query(values) {return new URLSearchParams(Object.entries(values).filter(([,v])=>v!==null && v!==undefined && v!=="")).toString();}
export function download(name, value, type="application/json") {
  const url=URL.createObjectURL(new Blob([typeof value === "string" ? value : JSON.stringify(value,null,2)],{type}));
  const link=el("a",{href:url,download:name}); link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
}
export function table(columns, rows, action) {
  return el("div",{class:"table-wrap"},el("table",{},el("thead",{},el("tr",{},columns.map(c=>el("th",{scope:"col"},c)),action?el("th",{scope:"col"},"Open"):null)),
    el("tbody",{},rows.map(row=>el("tr",{},columns.map(c=>el("td",{},format(row[c]))),action?el("td",{},button("Inspect",()=>action(row),"small")):null)))));
}
function format(value) {return value === undefined || value === null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);}
export function heading(title, description, actions=[]) {return el("div",{class:"heading"},el("div",{},el("p",{class:"eyebrow"},"LAB WORKSPACE"),el("h1",{},title),el("p",{},description)),el("div",{class:"actions"},actions));}
export function dialog(title, children) {
  const host=document.querySelector("#editor"),content=document.querySelector("#editor-content");
  content.replaceChildren(el("div",{class:"dialog-head"},el("h2",{},title),button("Close",()=>host.close(),"small")),...children);
  if (!host.open) host.showModal();
  return host;
}
