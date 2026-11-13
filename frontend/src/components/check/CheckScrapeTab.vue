<script setup lang="ts">
/** 体检 · 批量刮削页签：启动/停止、进度、结果筛选与导出 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { ElMessage } from "element-plus";
import {
  BATCH_STREAM_URL,
  cancelBatchScrape,
  fetchBatchStatus,
  startBatchScrape,
} from "@/api/progress";
import type { BatchStatus } from "@/api/types";
import { SCRAPE_MODE_OPTS } from "@/api/scrape";
import { openSse, pollStatus, type SseHandle } from "@/utils/sse";

const props = defineProps<{
  /** 与体检/导入互斥时被占用的任务名，非空则禁止启动 */
  blocked: string;
  refreshTasks: () => Promise<void>;
}>();

const emit = defineEmits<{
  finished: [];
}>();

const batch = ref<BatchStatus | null>(null);
/** all=按起点→番茄→纵横顺序，命中即停 */
const source = ref("all");
const onlyMissing = ref(true);
const minScore = ref(0.7);
/** 刮削取数方式：api（默认）/ auto（API 优先）/ chrome（备用） */
const scrapeMode = ref("api");
const starting = ref(false);
let handle: SseHandle | null = null;

const batchPercent = computed(() => {
  const st = batch.value;
  if (!st) return 0;
  const total = Number(st.total) || 0;
  const done = Number(st.done) || 0;
  return total ? Math.min(100, Math.floor((done * 100) / total)) : 0;
});

const batchLog = computed(() => {
  const st = batch.value;
  if (!st) return "尚未运行。";
  const lines: string[] = [];
  if (st.running) {
    lines.push(
      `刮削中 ${st.done || 0} / ${st.total || "?"} · 写入 ${st.matched || 0} · 跳过 ${
        st.skipped || 0
      } · 失败 ${st.failed || 0}${st.cancel_requested ? "（已请求停止…）" : ""}`,
    );
  } else {
    lines.push(
      `已完成 ${st.done || 0} / ${st.total || 0} · 写入 ${st.matched || 0} · 跳过 ${
        st.skipped || 0
      } · 失败 ${st.failed || 0}`,
    );
    if (st.last_error) lines.push("最近错误: " + st.last_error);
  }
  const log = st.log || [];
  if (log.length) {
    lines.push("");
    log.forEach((x) => lines.push(`${x.time}  ${x.message}`));
  }
  return lines.join("\n");
});

/** 批量刮削结构化结果：状态筛选 + CSV 导出 */
const batchActionFilter = ref("all");
const batchActionLabel: Record<string, string> = {
  matched: "写入",
  recovered: "兜底恢复",
  skipped: "跳过",
  failed: "失败",
};
const batchResults = computed(() => batch.value?.results || []);
const batchActionOptions = computed(() => {
  const counts = new Map<string, number>();
  for (const r of batchResults.value) {
    counts.set(r.action, (counts.get(r.action) || 0) + 1);
  }
  const known = Object.keys(batchActionLabel);
  const extras = [...counts.keys()].filter((k) => !known.includes(k)).sort();
  return [
    { action: "all", label: "全部", count: batchResults.value.length },
    ...known.map((action) => ({
      action,
      label: batchActionLabel[action],
      count: counts.get(action) || 0,
    })),
    ...extras.map((action) => ({
      action,
      label: action,
      count: counts.get(action) || 0,
    })),
  ];
});
const filteredBatchResults = computed(() =>
  batchActionFilter.value === "all"
    ? batchResults.value
    : batchResults.value.filter((r) => r.action === batchActionFilter.value),
);

function batchActionClass(action: string): string {
  if (action === "matched" || action === "recovered") return "is-success";
  if (action === "skipped") return "is-warning";
  return "is-danger";
}

function exportBatchReport() {
  const rows = filteredBatchResults.value;
  if (!rows.length) return;
  const quote = (v: unknown) => {
    const text = String(v ?? "");
    // CSV 公式注入防护：Excel 会把 = + - @ 开头的内容当公式
    const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
    return `"${safe.replace(/"/g, '""')}"`;
  };
  const header = ["书名", "结果", "书源", "匹配书名", "匹配作者", "分数", "说明", "时间"];
  const lines = [header.map(quote).join(",")];
  for (const r of rows) {
    lines.push(
      [
        r.title,
        batchActionLabel[r.action] || r.action,
        r.source || "",
        r.hit_title || "",
        r.hit_author || "",
        r.score != null ? r.score.toFixed(2) : "",
        r.message || "",
        r.time || "",
      ]
        .map(quote)
        .join(","),
    );
  }
  const blob = new Blob(["\uFEFF" + lines.join("\r\n")], {
    type: "text/csv;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `scrape-report-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-")}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function stopWatch() {
  handle?.close();
  handle = null;
}

function watchBatch() {
  stopWatch();
  const onMessage = (data: unknown) => {
    batch.value = data as BatchStatus;
    if (!(data as BatchStatus).running) {
      stopWatch();
      ElMessage.success("批量刮削完成");
      emit("finished");
    }
  };
  handle = openSse(BATCH_STREAM_URL, onMessage, {
    shouldStop: (d) => !(d as BatchStatus).running,
    onFallback: () => {
      handle = pollStatus(fetchBatchStatus, onMessage, {
        shouldStop: (d) => !(d as BatchStatus).running,
      });
    },
  });
}

async function onStartBatch() {
  await props.refreshTasks();
  if (props.blocked) {
    ElMessage.warning(`有「${props.blocked}」任务进行中，请先等待完成`);
    return;
  }
  starting.value = true;
  batchActionFilter.value = "all";
  try {
    const res = await startBatchScrape({
      source: source.value,
      only_missing: onlyMissing.value,
      min_score: minScore.value,
      mode: scrapeMode.value,
    });
    batch.value = res.status;
    ElMessage.success(res.started ? "一键刮削已开始" : "批处理已在运行");
    watchBatch();
    void props.refreshTasks();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "启动失败");
  } finally {
    starting.value = false;
  }
}

async function onCancelBatch() {
  try {
    const res = await cancelBatchScrape();
    batch.value = res.status;
    ElMessage[res.ok ? "success" : "warning"](res.ok ? "已请求停止" : "当前没有进行中的任务");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "停止失败");
  }
}

async function onRefreshBatch() {
  try {
    batch.value = await fetchBatchStatus();
    if (batch.value?.running) watchBatch();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "刷新失败");
  }
}

onMounted(() => {
  void onRefreshBatch();
});

onUnmounted(stopWatch);
</script>

<template>
  <div class="tab-panel">
    <p class="muted">
      按所选刮削源对书库批量识别：以<strong>书名+作者</strong>相似度取最近匹配后写入。
      选「全部」时按<strong>起点 → 番茄 → 纵横</strong>顺序，达到阈值即写入并停止，不再试后续书源。<br />
      刮削方式<strong>默认 API 直连</strong>；「自动」为 API 优先、失败再回退 Chrome；「Chrome」强制走浏览器（需环境支持）。
    </p>
    <div class="controls">
      <span class="label">刮削源</span>
      <select v-model="source" class="field-input">
        <option value="all">全部（顺序匹配）</option>
        <option value="qidian">起点</option>
        <option value="fanqie">番茄</option>
        <option value="zongheng">纵横</option>
      </select>
      <span class="label">刮削方式</span>
      <select v-model="scrapeMode" class="field-input" title="刮削取数方式">
        <option v-for="opt in SCRAPE_MODE_OPTS" :key="opt.value" :value="opt.value" :title="opt.hint">
          {{ opt.label }}
        </option>
      </select>
      <label class="check-label">
        <input v-model="onlyMissing" type="checkbox" />
        仅未刮削
      </label>
      <span class="label">匹配阈值</span>
      <input
        v-model.number="minScore"
        type="number"
        min="0.3"
        max="1"
        step="0.05"
        class="field-input narrow"
      />
      <button
        type="button"
        class="primary-btn"
        :disabled="starting || !!blocked"
        @click="onStartBatch"
      >
        {{ starting ? "启动中…" : blocked ? `${blocked}进行中` : "开始一键刮削" }}
      </button>
      <button type="button" class="ghost-btn danger" @click="onCancelBatch">停止</button>
      <button type="button" class="ghost-btn" @click="onRefreshBatch">刷新进度</button>
    </div>
    <p v-if="blocked" class="muted check-hint">
      当前有「{{ blocked }}」任务进行中，刮削暂不可用（导入 / 体检 / 刮削互斥）。
    </p>

    <div v-if="batch" class="batch-status">
      <div class="status-line">
        <span
          class="status-dot"
          :class="batch.running ? 'is-info' : batch.failed ? 'is-danger' : 'is-success'"
        >
          {{ batch.running ? "运行中" : "已结束" }}
        </span>
        <span class="muted">
          {{ batch.done || 0 }} / {{ batch.total || 0 }} · 写入 {{ batch.matched || 0 }}
          · 跳过 {{ batch.skipped || 0 }} · 失败 {{ batch.failed || 0 }}
        </span>
        <span class="progress-label">{{ batchPercent }}%</span>
      </div>
      <div class="progress-track">
        <div
          class="progress-bar"
          :class="{ 'is-success': !batch.running && batchPercent >= 100 }"
          :style="{ width: batchPercent + '%' }"
        />
      </div>
    </div>

    <div v-if="batchResults.length" class="batch-report">
      <div class="batch-report-head">
        <h3 class="batch-report-title">
          刮削结果
          <span class="muted-xs">{{ batchResults.length }} 条</span>
        </h3>
        <button type="button" class="ghost-btn sm" @click="exportBatchReport">
          导出 CSV
        </button>
      </div>
      <div class="filter-chips" role="group" aria-label="刮削结果筛选">
        <button
          v-for="opt in batchActionOptions"
          :key="opt.action"
          type="button"
          class="filter-chip"
          :class="{ 'is-active': batchActionFilter === opt.action }"
          :aria-pressed="batchActionFilter === opt.action"
          @click="batchActionFilter = opt.action"
        >
          {{ opt.label }}
          <span class="filter-count">{{ opt.count }}</span>
        </button>
      </div>
      <div class="batch-table-wrap">
        <table class="batch-table">
          <thead>
            <tr>
              <th>书名</th>
              <th>结果</th>
              <th>书源</th>
              <th>匹配结果</th>
              <th>分数</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in filteredBatchResults" :key="`${r.book_id}-${r.time}-${i}`">
              <td class="batch-title" :title="r.title">{{ r.title || "—" }}</td>
              <td>
                <span class="tag-soft" :class="batchActionClass(r.action)">
                  {{ batchActionLabel[r.action] || r.action }}
                </span>
              </td>
              <td :title="r.source_id || ''">{{ r.source || "—" }}</td>
              <td class="batch-hit" :title="`${r.hit_title || ''} ${r.hit_author || ''}`">
                {{ r.hit_title || "—" }}
                <template v-if="r.hit_author"> · {{ r.hit_author }}</template>
              </td>
              <td class="num">{{ r.score != null ? r.score.toFixed(2) : "—" }}</td>
              <td class="batch-msg" :title="r.message || ''">{{ r.message || "—" }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="!filteredBatchResults.length" class="muted batch-empty">
          当前筛选下没有结果。
        </p>
      </div>
    </div>

    <pre class="log-panel">{{ batchLog }}</pre>
  </div>
</template>

<style scoped>
.tab-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

@media (max-width: 576px) {
  .controls {
    flex-direction: column;
    align-items: stretch;
  }

  .controls .field-input,
  .controls .primary-btn,
  .controls .ghost-btn {
    width: 100%;
  }
}

.label {
  font-size: var(--text-xs);
  color: var(--color-text-3);
}

.field-input {
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  color: var(--color-text);
  font-size: var(--text-sm);
  font-family: inherit;
}

.field-input.narrow {
  width: 88px;
}

.check-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  color: var(--color-text-2);
  cursor: pointer;
}

.check-label input {
  accent-color: var(--color-accent);
}

.check-hint {
  margin: 0;
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

.ghost-btn.danger {
  color: var(--color-danger);
  border-color: var(--color-danger);
  background: var(--color-danger-soft);
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

.batch-status {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 8px;
}

.status-line {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.batch-report {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 12px 0;
}

.batch-report-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.batch-report-title {
  margin: 0;
  font-size: var(--text-sm);
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

.batch-table-wrap {
  overflow: auto;
  max-height: 360px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.batch-table {
  width: 100%;
  min-width: 640px;
  border-collapse: collapse;
  font-size: var(--text-xs);
}

.batch-table th,
.batch-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--color-border);
  text-align: left;
  vertical-align: top;
}

.batch-table th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--color-surface-2);
  color: var(--color-text-3);
  font-weight: 600;
  white-space: nowrap;
}

.batch-table tbody tr:last-child td {
  border-bottom: none;
}

.batch-title {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.batch-hit {
  max-width: 200px;
  color: var(--color-text-2);
}

.batch-msg {
  max-width: 260px;
  color: var(--color-text-3);
  word-break: break-word;
}

.batch-table .num {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.batch-empty {
  margin: 0;
  padding: 16px;
  text-align: center;
}
</style>
