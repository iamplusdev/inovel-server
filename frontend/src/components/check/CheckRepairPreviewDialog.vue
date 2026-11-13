<script setup lang="ts">
/** 体检 · 修复预览弹窗：只读预览后再确认执行 */
import { computed } from "vue";
import type { RepairPreviewItem } from "@/api/types";

const props = defineProps<{
  visible: boolean;
  title: string;
  loading: boolean;
  items: RepairPreviewItem[];
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
  confirm: [];
}>();

const actionable = computed(() => props.items.some((it) => !it.error));
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="title"
    width="min(640px, 92vw)"
    append-to-body
    @update:model-value="emit('update:visible', $event)"
  >
    <div v-loading="loading" class="repair-preview-body">
      <p class="muted repair-preview-hint">
        预览只读取书籍，不会修改；确认后才会执行修复。
      </p>
      <div v-if="items.length" class="repair-preview-list">
        <div v-for="it in items" :key="it.id" class="repair-preview-item">
          <div class="repair-preview-head">
            <span class="repair-preview-title">{{ it.title || `#${it.id}` }}</span>
            <span class="repair-preview-meta">
              <span>{{ it.chapter_count ?? "—" }} 章</span>
              <span v-if="it.new_chapter_count != null">→ {{ it.new_chapter_count }} 章</span>
              <span
                v-if="it.need_reparse"
                class="repair-source"
                :class="{ 'is-error': it.source_available === false }"
              >
                源文件{{ it.source_available ? "可读" : "不可用" }}
              </span>
            </span>
          </div>
          <div class="repair-preview-actions">
            <span
              v-for="(action, ai) in it.actions"
              :key="ai"
              class="tag-soft"
              :class="it.error ? 'is-danger' : it.will_change ? 'is-warning' : 'is-success'"
            >
              {{ action }}
            </span>
          </div>
          <p v-if="it.error" class="repair-preview-error">{{ it.error }}</p>
        </div>
      </div>
      <p v-else-if="!loading" class="muted">没有可预览的书籍。</p>
    </div>
    <template #footer>
      <div class="repair-preview-footer">
        <button type="button" class="ghost-btn" @click="emit('update:visible', false)">取消</button>
        <button
          type="button"
          class="primary-btn"
          :disabled="loading || !actionable"
          @click="emit('confirm')"
        >
          确认修复
        </button>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.repair-preview-body {
  min-height: 120px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.repair-preview-hint {
  margin: 0;
}

.repair-preview-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 50vh;
  overflow: auto;
}

.repair-preview-item {
  padding: 10px 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-2);
}

.repair-preview-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.repair-preview-title {
  font-weight: 600;
  font-size: var(--text-sm);
  word-break: break-word;
}

.repair-preview-meta {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  color: var(--color-text-3);
  font-size: var(--text-xs);
}

.repair-source.is-error {
  color: var(--color-danger);
}

.repair-preview-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.repair-preview-error {
  margin: 6px 0 0;
  color: var(--color-danger);
  font-size: var(--text-xs);
}

.repair-preview-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.ghost-btn {
  display: inline-flex;
  align-items: center;
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

.ghost-btn:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  color: var(--color-text);
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

.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.primary-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
