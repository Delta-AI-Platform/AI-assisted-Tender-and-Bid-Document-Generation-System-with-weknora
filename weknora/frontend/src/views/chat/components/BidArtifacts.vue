<template>
  <section v-if="items.length || error" class="bid-files" aria-label="标书最终产物">
    <div class="bid-heading"><strong>标书最终产物</strong><button @click="refresh" :disabled="loading">刷新</button></div>
    <p>仅展示当前最终版本。骨架、片段、检查记录和历史版本保存在内部工作区。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <ul><li v-for="item in items" :key="item.id">
      <span>{{ item.name }}</span>
      <button @click="download(item)" :disabled="busy === item.id">{{ busy === item.id ? '下载中…' : '下载' }}</button>
    </li></ul>
  </section>
</template>
<script setup lang="ts">
import { ref, watch } from 'vue'
import { get, getDown } from '@/utils/request'
const props = defineProps<{ sessionId: string; active?: boolean; collecting?: boolean }>()
const emit = defineEmits<{ (e: 'count', count: number): void }>()
type Item = { id: string; name: string; sha256: string }
const items = ref<Item[]>([]), error = ref(''), loading = ref(false), busy = ref('')
let generation = 0
const endpoint = (sid: string) => `/api/v1/sessions/${encodeURIComponent(sid)}/bid-artifacts`
async function refresh() {
  const turn = ++generation, sid = props.sessionId
  items.value = []; error.value = ''; loading.value = false; emit('count', 0)
  if (!sid || props.active === false) return
  loading.value = true
  try {
    const result = await get(endpoint(sid)) as { data?: Item[] }
    if (turn === generation) { items.value = [...(result.data || [])].reverse(); emit('count', items.value.length) }
  } catch (e: any) {
    if (turn === generation && e?.status !== 404) error.value = '文件列表暂时无法读取，请稍后刷新。'
  } finally { if (turn === generation) loading.value = false }
}
async function download(item: Item) {
  const sid = props.sessionId
  busy.value = item.id; error.value = ''
  try {
    const blob = await getDown(`${endpoint(sid)}/${encodeURIComponent(item.id)}/download`)
    const link = document.createElement('a'), url = URL.createObjectURL(blob)
    link.href = url; link.download = item.name; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch { error.value = '下载失败：请确认登录状态及文件版本后重试。' }
  finally { busy.value = '' }
}
watch(() => [props.sessionId, props.active, props.collecting], refresh, { immediate: true })
</script>
<style scoped>
.bid-files { padding: 16px; border-bottom: 1px solid #ddd; font-size: 13px; }
.bid-heading, li { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
p { color: #777; } ul { padding: 0; list-style: none; } li { padding: 6px 0; overflow-wrap: anywhere; }
button { cursor: pointer; flex-shrink: 0; }
</style>
