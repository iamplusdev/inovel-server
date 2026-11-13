<script setup lang="ts">
/** 书源校验结果：单个结果详情 / 批量结果概览 */
import type { BookSourceValidation } from "@/api/admin";

defineProps<{
  visible: boolean;
  title: string;
  /** 单个校验结果；null 时展示批量 items */
  result: BookSourceValidation | null;
  items: BookSourceValidation[];
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
}>();
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="title"
    width="620px"
    append-to-body
    @update:model-value="emit('update:visible', $event)"
  >
    <template v-if="result">
      <div class="validation-summary">
        <span :class="result.ok ? 'ok-text' : 'err-text'">
          {{ result.ok ? "结构校验通过" : `发现 ${result.errors.length} 个错误` }}
        </span>
        <span v-if="result.warnings.length" class="warn-text">· {{ result.warnings.length }} 条提示</span>
        <span v-if="result.probe" class="muted-xs">
          · 连通性：{{ result.probe.skipped || (result.probe.ok ? `HTTP ${result.probe.status} / ${result.probe.ms} ms` : result.probe.error) }}
        </span>
      </div>
      <ul v-if="result.errors.length" class="validation-list">
        <li v-for="(msg, i) in result.errors" :key="`e${i}`" class="err-text">{{ msg }}</li>
      </ul>
      <ul v-if="result.warnings.length" class="validation-list">
        <li v-for="(msg, i) in result.warnings" :key="`w${i}`" class="warn-text">{{ msg }}</li>
      </ul>
      <div class="check-grid">
        <div v-for="(c, i) in result.checks" :key="i" class="check-item" :class="c.level">
          <span class="check-dot" />
          <span class="check-label">{{ c.label }}</span>
          <span class="muted-xs ellipsis">{{ c.detail }}</span>
        </div>
      </div>
    </template>
    <template v-else>
      <div class="validation-summary">
        通过 {{ items.filter((v) => v.ok).length }} / {{ items.length }}
      </div>
      <div class="check-grid">
        <div
          v-for="v in items"
          :key="v.id"
          class="check-item"
          :class="v.ok ? 'ok' : 'error'"
        >
          <span class="check-dot" />
          <span class="check-label">{{ v.name }}</span>
          <span class="muted-xs">{{ v.ok ? "通过" : `${v.errors.length} 个错误` }}</span>
          <span v-if="v.warnings.length" class="warn-text">· {{ v.warnings.length }} 条提示</span>
        </div>
      </div>
    </template>
    <template #footer>
      <el-button @click="emit('update:visible', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.ok-text {
  color: var(--el-color-success);
}

.warn-text {
  color: var(--el-color-warning);
}

.err-text {
  color: var(--el-color-danger);
}

.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.validation-summary {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
  font-weight: 600;
}

.validation-list {
  margin: 6px 0 12px;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.7;
}

.check-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 12px;
}

.check-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  min-width: 0;
}

.check-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: none;
  background: var(--el-color-success);
}

.check-item.warning .check-dot {
  background: var(--el-color-warning);
}

.check-item.error .check-dot {
  background: var(--el-color-danger);
}

.check-label {
  flex: none;
  font-weight: 600;
}

@media (max-width: 760px) {
  .check-grid {
    grid-template-columns: 1fr;
  }
}
</style>
