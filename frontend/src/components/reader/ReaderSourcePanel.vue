<script setup lang="ts">
/** 阅读器「切换书源」面板 */
import type { OnlineSourceCandidate } from "@/api/online";

defineProps<{
  currentSourceName: string;
  items: OnlineSourceCandidate[];
  loading: boolean;
  error: string;
  hint: string;
  switchingId: number;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "refresh"): void;
  (e: "select", item: OnlineSourceCandidate): void;
}>();

function fmtWords(n: number): string {
  const value = Number(n) || 0;
  if (value >= 10000) return `${(value / 10000).toFixed(1).replace(/\.0$/, "")} 万字`;
  return value > 0 ? `${value} 字` : "";
}
</script>

<template>
  <aside class="source-panel" aria-label="切换书源">
    <div class="source-head">
      <strong class="source-title">切换书源</strong>
      <button
        type="button"
        class="head-btn"
        :disabled="loading || !!switchingId"
        @click="emit('refresh')"
      >
        {{ loading ? "搜索中" : "重新搜索" }}
      </button>
      <button type="button" class="head-btn close" title="关闭" @click="emit('close')">×</button>
    </div>

    <div class="source-meta muted-xs">
      <span>当前书源：{{ currentSourceName || "未知" }}</span>
      <span v-if="loading">正在搜索其他书源…</span>
      <span v-else-if="error" class="is-error">{{ error }}</span>
      <span v-else-if="hint">{{ hint }}</span>
      <span v-else-if="items.length">共 {{ items.length }} 个可用书源</span>
    </div>

    <div class="source-list">
      <div v-for="item in items" :key="`${item.source_id}-${item.book_url}`" class="source-row">
        <div class="source-info">
          <div class="source-name-line">
            <span class="source-name">{{ item.source_name || `书源 #${item.source_id}` }}</span>
            <span v-if="item.current" class="source-tag">当前</span>
          </div>
          <span v-if="item.last_chapter" class="source-last">最新：{{ item.last_chapter }}</span>
          <span v-if="fmtWords(item.word_count)" class="source-last">
            {{ fmtWords(item.word_count) }}
          </span>
        </div>
        <button
          type="button"
          class="switch-btn"
          :disabled="item.current || !!switchingId"
          @click="emit('select', item)"
        >
          {{ item.current ? "使用中" : switchingId === item.source_id ? "切换中…" : "切换" }}
        </button>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.source-panel {
  position: absolute;
  top: 12px;
  right: 12px;
  width: min(420px, calc(100vw - 24px));
  max-height: calc(100% - 24px);
  display: flex;
  flex-direction: column;
  background: var(--reader-panel-bg, var(--color-surface));
  color: var(--reader-fg, var(--color-text));
  border: 1px solid var(--reader-border, var(--color-border));
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  z-index: 40;
  overflow: hidden;
}

.source-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border-bottom: 1px solid var(--reader-border, var(--color-border));
}

.source-title {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
}

.head-btn {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--reader-border, var(--color-border));
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  color: var(--color-text-2);
  font-family: inherit;
  font-size: var(--text-xs);
  cursor: pointer;
}

.head-btn:hover:not(:disabled) {
  color: var(--color-accent);
  border-color: var(--color-accent);
}

.head-btn:disabled {
  opacity: 0.55;
  cursor: default;
}

.head-btn.close {
  width: 28px;
  padding: 0;
  font-size: 16px;
  line-height: 1;
  background: transparent;
  border-color: transparent;
}

.source-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--reader-border, var(--color-border));
  color: var(--reader-muted, var(--color-text-3));
}

.source-meta .is-error {
  color: var(--color-danger);
}

.source-list {
  overflow: auto;
  padding: 6px;
}

.source-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
}

.source-row:hover {
  background: var(--color-surface-2);
}

.source-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.source-name-line {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.source-name {
  font-size: var(--text-sm);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.source-tag {
  flex-shrink: 0;
  padding: 0 6px;
  border-radius: var(--radius-full);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: var(--text-xs);
}

.source-last {
  color: var(--reader-muted, var(--color-text-3));
  font-size: var(--text-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switch-btn {
  flex-shrink: 0;
  height: 30px;
  padding: 0 12px;
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-accent);
  font-family: inherit;
  font-size: var(--text-xs);
  cursor: pointer;
}

.switch-btn:hover:not(:disabled) {
  background: var(--color-accent-soft);
}

.switch-btn:disabled {
  opacity: 0.55;
  cursor: default;
  border-color: var(--reader-border, var(--color-border));
  color: var(--reader-muted, var(--color-text-3));
}
</style>
