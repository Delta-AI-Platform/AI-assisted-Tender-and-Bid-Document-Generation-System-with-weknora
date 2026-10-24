<template>
  <section v-if="status || error" class="libraries" aria-label="项目资料库">
    <header><strong>{{ fixedLibrary === 'knowledge' ? '企业知识库' : fixedLibrary === 'history' ? '历史响应权威库' : '项目资料库' }}</strong><button @click="refresh" :disabled="busy">刷新</button></header>
    <template v-if="status">
      <div class="summary">
        <span v-if="fixedLibrary !== 'history'">企业知识库 <b>{{ knowledge?.active_documents?.toLocaleString() }}</b> 项</span>
        <span v-if="fixedLibrary !== 'knowledge'">历史案例 <b>{{ history?.cases }}</b> 个 · 响应 <b>{{ history?.actual_response_pairs?.toLocaleString() }}</b> 条</span>
      </div>
      <p v-if="status.readability && fixedLibrary !== 'knowledge'" class="verified">已核验 {{ status.readability.readable }}/{{ status.readability.total }} 个历史文件可读 · 已移除 {{ status.readability.excluded_parse_failed_records }} 条失败记录</p>
      <p class="hint">历史资料仅供参考，使用前需核对本项目适用性。旧版 Word 可能通过转换副本读取。</p>
      <form @submit.prevent="search">
        <label v-if="!fixedLibrary">资料来源<select v-model="library" @change="clearResults"><option value="knowledge">企业知识库</option><option value="history">历史响应案例</option></select></label>
        <label>检索内容<input v-model="query" maxlength="200" placeholder="例如：营业执照、售后、WLL-5" /></label>
        <button type="submit" :disabled="busy || !query.trim()">{{ busy ? '读取中…' : '检索' }}</button>
      </form>
      <p v-if="searched">找到 {{ matches }} 条{{ matches > results.length ? '，展示前 10 条' : '' }}</p>
      <ul><li v-for="item in results" :key="item.id">
        <strong>{{ item.title || item.project || '历史响应' }}</strong>
        <p v-if="item.project">{{ item.project }}</p>
        <p v-if="item.requirement">要求：{{ item.requirement }}</p>
        <p v-if="item.response">历史响应：{{ item.response }}</p>
        <button @click="read(item)" :disabled="busy">查看来源</button>
      </li></ul>
      <div v-if="detail" class="detail">
        <header><strong>来源内容</strong><button @click="detail=null; source=null">关闭</button></header>
        <template v-if="detail.members">
          <p>{{ detail.title }}</p>
          <div v-for="member in detail.members" :key="member.id" class="member"><span>{{ member.display_name || member.source_rel }}</span><button @click="inspect(member.id)" :disabled="busy">核对原件</button></div>
          <button v-if="detail.offset + detail.members.length < detail.total_members" @click="moreMembers" :disabled="busy">下一组原件</button>
        </template>
        <template v-else-if="detail.record">
          <p>{{ detail.record.block.section_title }}</p>
          <p>要求：{{ detail.record.block.requirement }}</p><p>历史响应：{{ detail.record.block.response }}</p>
          <p v-if="detail.record.block.proof">证明引用：{{ detail.record.block.proof }}</p>
          <p>来源：{{ detail.record.block.source_ref?.source_path }}</p>
          <button @click="inspect(detail.record.source_id)" :disabled="busy">核对原件</button>
        </template>
        <template v-else-if="detail.blocks">
          <p>{{ detail.project }}</p><p v-for="block in detail.blocks" :key="block.block_id">{{ block.text || block.requirement }} {{ block.response }}</p>
          <button @click="inspect(detail.source_id)" :disabled="busy">核对原件</button>
        </template>
        <div v-if="source" class="source"><strong>{{ source.name }}</strong><p>{{ source.index_hash_matches ? '原件可读取，内容与索引一致' : '原件与索引版本不同，采用前需重新核对' }}</p><p>{{ (source.bytes / 1024 / 1024).toFixed(2) }} MB</p></div>
      </div>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>
<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { get } from '@/utils/request'
const props=defineProps<{sessionId:string; active?:boolean; fixedLibrary?: 'knowledge' | 'history'}>()
const status=ref<any>(null), library=ref('knowledge'), query=ref(''), results=ref<any[]>([]), detail=ref<any>(null), source=ref<any>(null), error=ref(''), busy=ref(false), searched=ref(false), matches=ref(0)
const knowledge=computed(()=>status.value?.libraries?.find((x:any)=>x.library==='knowledge'))
const history=computed(()=>status.value?.libraries?.find((x:any)=>x.library==='history'))
let generation=0
function endpoint(args:Record<string,string>){return `/api/v1/sessions/${encodeURIComponent(props.sessionId)}/bid-libraries?${new URLSearchParams(args)}`}
function clearResults(){generation++;results.value=[];detail.value=null;source.value=null;searched.value=false;error.value='';busy.value=false}
async function refresh(){clearResults();status.value=null;if(!props.sessionId||props.active===false)return;const g=generation;busy.value=true;try{const r:any=await get(endpoint({action:'status'}));if(g===generation)status.value=r.data}catch(e:any){if(g===generation&&e?.status!==404)error.value='资料库暂时无法连接，请刷新重试。'}finally{if(g===generation)busy.value=false}}
async function search(){clearResults();const g=generation;busy.value=true;try{const r:any=await get(endpoint({action:'search',library:library.value,q:query.value.trim()}));if(g===generation){results.value=r.data.results;matches.value=r.data.matches;searched.value=true}}catch{if(g===generation)error.value='检索失败，请稍后重试。'}finally{if(g===generation)busy.value=false}}
async function read(item:any,offset=0){const g=++generation;busy.value=true;source.value=null;error.value='';try{const r:any=await get(endpoint({action:'read',library:library.value,id:item.id,offset:String(offset)}));if(g===generation)detail.value={...r.data,id:item.id}}catch{if(g===generation)error.value='来源暂时无法读取。'}finally{if(g===generation)busy.value=false}}
function moreMembers(){read({id:detail.value.id},detail.value.offset+detail.value.members.length)}
async function inspect(id:string){const g=++generation;busy.value=true;error.value='';try{const r:any=await get(endpoint({action:'source',library:library.value,id}));if(g===generation)source.value=r.data}catch{if(g===generation)error.value='原件暂时无法核对，请稍后重试。'}finally{if(g===generation)busy.value=false}}
watch(()=>[props.sessionId,props.active,props.fixedLibrary],()=>{library.value=props.fixedLibrary||'knowledge';refresh()},{immediate:true})
</script>
<style scoped>
.libraries{padding:16px;border-bottom:1px solid #ddd;font-size:13px;line-height:1.6}header,.member{display:flex;justify-content:space-between;align-items:center;gap:10px}.summary{display:grid;gap:4px;margin:12px 0}.verified{color:#267248}.hint{color:#777}form{display:grid;gap:8px}label{display:grid;gap:4px}input,select{box-sizing:border-box;width:100%;padding:7px;border:1px solid #ccc;border-radius:6px;background:transparent;color:inherit}button{cursor:pointer;padding:4px 10px;border:1px solid #cbd5d1;border-radius:6px;background:transparent;color:inherit}button:disabled{opacity:.5;cursor:wait}ul{padding:0;list-style:none}li{border-top:1px solid #eee;padding:12px 0;overflow-wrap:anywhere}.detail{background:rgba(100,130,120,.06);padding:12px;border-radius:8px;overflow-wrap:anywhere}.detail p{white-space:pre-wrap}.source{border-top:1px solid #ccc;margin-top:12px;padding-top:8px}.member{padding:8px 0}.member button{flex-shrink:0}
</style>
