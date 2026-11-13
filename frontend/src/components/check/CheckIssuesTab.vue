<script setup lang="ts">
/** 体检 · 异常书籍页签 */
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";
import BaseEmpty from "@/components/BaseEmpty.vue";
import type { LibraryReport } from "@/api/types";

const props = defineProps<{
  report: LibraryReport | null;
  scanned: boolean;
}>();

const emit = defineEmits<{
  repair: [bookId: number];
}>();

const router = useRouter();

const kindLabel: Record<string, string> = {
  chapter_parse: "未分章",
  encoding: "乱码",
  control_chars: "控制符",
  empty_chapters: "空章节",
  source_missing: "缺源文件",
};

/** 异常类型筛选：只影响列表展示，"修复全部"仍处理所有异常书 */
const issueKindFilter = ref("all");
const issueKindOptions = computed(() => {
  const counts = new Map<string, number>();
  for (const it of props.report?.issues || []) {
    counts.set(it.kind, (counts.get(it.kind) || 0) + 1);
  }
  return [
    { kind: "all", label: "全部", count: props.report?.issues?.length || 0 },
    ...Object.entries(kindLabel).map(([kind, label]) => ({
      kind,
      label,
      count: counts.get(kind) || 0,
    })),
  ];
});
const filteredIssues = computed(() => {
  const issues = props.report?.issues || [];
  return issueKindFilter.value === "all"
    ? issues
    : issues.filter((it) => it.kind === issueKindFilter.value);
});

// 修复后当前类型已无异常时，自动回到"全部"，避免停留在空列表
watch(
  () => props.report?.issues,
  (issues) => {
    if (issueKindFilter.value === "all") return;
    if (!(issues || []).some((it) => it.kind === issueKindFilter.value)) {
      issueKindFilter.value = "all";
    }
  },
);
</script>

<template>
  <div class="tab-panel">
    <p class="muted">
      修复：清理控制字符；若源 TXT 仍可读且未分章/乱码，则重新解析章节。
      勾选「深度体检」可查乱码/控制符（需读正文，较慢）。
    </p>
    <div
      v-if="scanned && report?.issues?.length"
      class="filter-chips"
      role="group"
      aria-label="异常类型筛选"
    >
      <button
        v-for="opt in issueKindOptions"
        :key="opt.kind"
        type="button"
        class="filter-chip"
        :class="{ 'is-active': issueKindFilter === opt.kind }"
        :aria-pressed="issueKindFilter === opt.kind"
        @click="issueKindFilter = opt.kind"
      >
        {{ opt.label }}
        <span class="filter-count">{{ opt.count }}</span>
      </button>
    </div>
    <p v-if="scanned && issueKindFilter !== 'all'" class="muted-xs filter-hint">
      筛选只影响列表展示，「修复全部」仍处理所有异常书。
    </p>
    <BaseEmpty
      v-if="!scanned"
      icon="check"
      title="尚未扫描"
      description="点右上角「开始扫描」检查异常书。"
    />
    <BaseEmpty
      v-else-if="!report?.issues?.length"
      icon="check"
      title="没有发现异常"
      description="书库健康。"
    />
    <BaseEmpty
      v-else-if="!filteredIssues.length"
      icon="check"
      title="当前筛选下没有异常"
    />
    <div v-for="it in filteredIssues" :key="it.book_id + it.kind" class="check-item">
      <div class="body">
        <div class="title">
          {{ it.title }}
          <span class="tag-soft is-warning">{{ kindLabel[it.kind] || it.kind }}</span>
        </div>
        <div class="muted">{{ it.message }} · ID {{ it.book_id }}</div>
      </div>
      <div class="ops">
        <button type="button" class="ghost-btn sm" @click="emit('repair', it.book_id)">修复</button>
        <button
          type="button"
          class="link-btn"
          @click="router.push({ name: 'book-detail', params: { id: String(it.book_id) } })"
        >
          编辑
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tab-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.filter-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: var(--text-xs);
  font-family: inherit;
  cursor: pointer;
}

.filter-chip:hover {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}

.filter-chip.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-weight: 600;
}

.filter-count {
  min-width: 16px;
  padding: 0 4px;
  border-radius: var(--radius-full);
  background: var(--color-surface-2);
  color: var(--color-text-3);
  font-variant-numeric: tabular-nums;
  text-align: center;
}

.filter-chip.is-active .filter-count {
  background: var(--color-surface);
  color: var(--color-accent);
}

.filter-hint {
  margin: 0;
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

.ops {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
  align-items: center;
}

.ghost-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-2);
  font-size: var(--text-sm);
  font-family: inherit;
  cursor: pointer;
}

.ghost-btn.sm {
  height: 32px;
}

.ghost-btn:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}

.link-btn {
  border: none;
  background: none;
  color: var(--color-accent);
  font-size: var(--text-sm);
  cursor: pointer;
  font-family: inherit;
  padding: 0 4px;
}
</style>
