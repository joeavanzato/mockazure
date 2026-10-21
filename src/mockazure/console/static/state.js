const views=new Set(['resources','workbench','profiles','runs','hunting','inspector','admin']);
const keys=['tenant','family','record','parent','run','operation','mode'];
export function parseRoute(hash){
 const [path,search='']=hash.replace(/^#\/?/,'').split('?');
 const result={view:views.has(path)?path:'resources'}, params=new URLSearchParams(search);
 for(const key of keys){const value=params.get(key);if(value && value.length<=2048)result[key]=value;}
 return result;
}
export function routeHash(value){
 const params=new URLSearchParams();for(const key of keys)if(value[key])params.set(key,value[key]);
 return '#/'+(views.has(value.view)?value.view:'resources')+(params.size?'?'+params:'');
}
export class Revision{
 #value=0;
 capture(){return this.#value;}
 invalidate(){this.#value++;}
 current(value){return value===this.#value;}
}
