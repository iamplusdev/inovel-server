<script setup lang="ts">
/** 体检 · 重复书籍页签 */
import BaseEmpty from "@/components/BaseEmpty.vue";
import type { LibraryReport } from "@/api/types";

type DuplicateGroup = NonNullable<LibraryReport["duplicates"]>[number];

defineProps<{
  report: LibraryReport | null;
  scanned: boolean;
}>();

const emit = defineEmits<{
  merge: [group: DuplicateGroup];
}>();

function fmtWords(n?: number) {
  if (n === undefined || n === null) return "—";
  if (n >= 100000000) return (n / 100000000).toFixed(1) + " 亿";
  if (n >= 10000) return (n / 10000).toFixed(1) + " 万";
  return String(n);
}
</script>

<template>
  <div class="tab-panel">
    <p class="muted">
      合并规则：保留章节/字数最多的一本，删除其余重复项（章节与多余封面一并删，源 TXT 不动）。
    </p>
    <BaseEmpty
      v-if="!scanned"
      icon="check"
      title="尚未扫描"
      description="点右上角「开始扫描」检查重复书。"
    />
    <BaseEmpty
      v-else-if="!report?.duplicates?.length"
      icon="check"
      title="没有发现重复书"
      description="书库中没有重复项。"
    />
    <div v-for="(g, gi) in report?.duplicates || []" :key="gi" class="check-item">
      <div class="body">
        <div class="title">
          {{ g.title }}<template v-if="g.author"> · {{ g.author }}</template>
        </div>
        <div v-for="b in g.books" :key="b.id" class="muted row">
          <span class="tag-soft" :class="b.id === g.keep_id ? 'is-success' : ''">
            {{ b.id === g.keep_id ? "保留" : "待删" }}
          </span>
          <span>
            ID {{ b.id }} · {{ b.chapter_count ?? 0 }} 章 · {{ fmtWords(b.word_count) }}
            · <span class="mono">{{ b.source_path || "" }}</span>
          </span>
        </div>
      </div>
      <button type="button" class="primary-btn sm" @click="emit('merge', g)">一键合并</button>
    </div>
  </div>
</template>

<style scoped>
.tab-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.check-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.body {
  flex: 1;
  min-width: 0;
}

.title {
  font-weight: 600;
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  flex-wrap: wrap;
}

.primary-btn {
  display: inline-flex;
  align-items: center;
  height: 36px;
  padding: 0 16px;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--color-accent);
  color: #fff;
  font-size: var(--text-sm);
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}

.primary-btn.sm {
  height: 32px;
  padding: 0 12px;
}

.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
</style>
