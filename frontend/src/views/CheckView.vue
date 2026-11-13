<script setup lang="ts">
/**
 * 书库体检：重复 / 异常 / 刮削 三页签。
 * 进入页面不自动扫描；点「开始/重新扫描」才执行（快速/深度可选），结果缓存在会话内。
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import { fetchTasksStatus } from "@/api/progress";
import {
  SCAN_STREAM_URL,
  cancelScan,
  fetchScanStatus,
  mergeDuplicates,
  previewRepair,
  relocateBooks,
  repairAll,
  startScan,
} from "@/api/library";
import type {
  LibraryReport,
  RepairPreviewItem,
  ScanStatus,
  TasksStatus,
} from "@/api/types";
import { rebuildThumbs } from "@/api/admin";
import { openSse, pollStatus, type SseHandle } from "@/utils/sse";
import { useLibraryStore } from "@/stores/library";
import { useCheckStore } from "@/stores/check";
import AppIcon from "@/components/AppIcon.vue";
import CheckDuplicatesTab from "@/components/check/CheckDuplicatesTab.vue";
import CheckIssuesTab from "@/components/check/CheckIssuesTab.vue";
import CheckScrapeTab from "@/components/check/CheckScrapeTab.vue";
import CheckRepairPreviewDialog from "@/components/check/CheckRepairPreviewDialog.vue";

defineProps<{ embedded?: boolean }>();

const route = useRoute();
const lib = useLibraryStore();

const activeTab = ref("dup");
// 支持 ?tab=dup|issue|scrape（书库「批量刮削」跳转用）
watch(
  () => route.query.tab,
  (t) => {
    if (t === "dup" || t === "issue" || t === "scrape") activeTab.value = t;
  },
  { immediate: true },
);
// 报告缓存在 Pinia（内存态，避免 sessionStorage 5MB 上限导致大报告静默丢失）
const check = useCheckStore();
const report = computed(() => check.report);
const scanned = computed(() => check.scanned);
/** 深度体检：读正文查乱码/控制符，大库较慢（改为后台任务，带进度） */
const deepScan = ref(false);
/** 后台体检任务状态 */
const scan = ref<ScanStatus | null>(null);
let scanHandle: SseHandle | null = null;

// 互斥任务：导入/体检/刮削同一时刻只允许一个在跑
const tasks = ref<TasksStatus | null>(null);
let tasksTimer: ReturnType<typeof setInterval> | null = null;
const scanBlocked = computed(() => {
  const t = tasks.value;
  if (!t) return "";
  if (t.import) return "导入";
  if (t.scrape) return "刮削";
  return "";
});
const scrapeBlocked = computed(() => {
  const t = tasks.value;
  if (!t) return "";
  if (t.import) return "导入";
  if (t.scan) return "体检";
  return "";
});

async function refreshTasks() {
  try {
    tasks.value = await fetchTasksStatus();
  } catch {
    /* ignore */
  }
}

const scanPercent = computed(() => {
  const st = scan.value;
  if (!st) return 0;
  return Math.min(100, Math.max(0, Number(st.percent) || 0));
});

const acting = ref(false);
const repairPreviewOpen = ref(false);
const repairPreviewLoading = ref(false);
const repairPreviewTitle = ref("修复预览");
const repairPreviewItems = ref<RepairPreviewItem[]>([]);
let repairPreviewIds: number[] = [];
let repairPreviewMode = "auto";
let repairPreviewSeq = 0;

const summary = computed(() => {
  if (!scanned.value) return "未扫描";
  const r = report.value;
  if (!r) return "未检查";
  return `重复 ${r.duplicate_groups || 0} 组 · 异常 ${r.issue_count || 0}${r.deep ? " · 深度" : ""}`;
});

/** 启动后台体检（深度/快速），完成后从状态里取回报告 */
async function loadReport() {
  await refreshTasks();
  if (scanBlocked.value) {
    ElMessage.warning(`有「${scanBlocked.value}」任务进行中，请先等待完成`);
    return;
  }
  try {
    const res = await startScan(deepScan.value);
    scan.value = res.status;
    ElMessage.success(res.started ? "开始体检…" : "体检已在进行中");
    watchScan();
    void refreshTasks();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "启动体检失败");
  }
}

function stopScanWatch() {
  scanHandle?.close();
  scanHandle = null;
}

function onScanMessage(data: unknown) {
  const st = data as ScanStatus;
  scan.value = st;
  if (!st.running) {
    stopScanWatch();
    if (st.error) {
      ElMessage.error(`体检失败：${st.error}`);
    } else {
      if (st.report) check.setReport(st.report);
      ElMessage.success("体检完成");
    }
  }
}

function watchScan() {
  stopScanWatch();
  scanHandle = openSse(SCAN_STREAM_URL, onScanMessage, {
    shouldStop: (d) => !(d as ScanStatus).running,
    onFallback: () => {
      scanHandle = pollStatus(fetchScanStatus, onScanMessage, {
        shouldStop: (d) => !(d as ScanStatus).running,
      });
    },
  });
}

async function onCancelScan() {
  try {
    const res = await cancelScan();
    scan.value = res.status;
    ElMessage[res.ok ? "success" : "warning"](res.ok ? "已请求停止体检" : "当前没有进行中的体检");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "停止失败");
  }
}

async function refreshAll() {
  // 修复/合并后刷新书库并按当前深度档位重扫一次
  await Promise.all([lib.loadBooks(), lib.loadStats()]);
  await loadReport();
}

async function onMerge(g: NonNullable<LibraryReport["duplicates"]>[number]) {
  const del = (g.books || []).filter((b) => b.id !== g.keep_id).map((b) => b.id);
  try {
    await ElMessageBox.confirm(
      `合并《${g.title}》\n保留 ID ${g.keep_id}，删除 ${del.join(", ")}？`,
      "合并重复书",
      { type: "warning", confirmButtonText: "合并", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  acting.value = true;
  try {
    const res = await mergeDuplicates(g.keep_id, del);
    ElMessage.success(`已合并，删除 ${(res.deleted || []).length} 本`);
    await refreshAll();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "合并失败");
  } finally {
    acting.value = false;
  }
}

/** 只读预览后再执行修复，避免误重解析 */
async function openRepairPreview(ids: number[], title: string, mode = "auto") {
  if (!ids.length) {
    ElMessage.warning("没有需要修复的问题书");
    return;
  }
  const seq = ++repairPreviewSeq;
  repairPreviewIds = ids;
  repairPreviewMode = mode;
  repairPreviewTitle.value = title;
  repairPreviewItems.value = [];
  repairPreviewOpen.value = true;
  repairPreviewLoading.value = true;
  try {
    const res = await previewRepair(mode, ids);
    if (seq !== repairPreviewSeq) return;
    repairPreviewItems.value = res.items || [];
  } catch (e) {
    if (seq === repairPreviewSeq) {
      repairPreviewOpen.value = false;
      ElMessage.error(e instanceof Error ? e.message : "修复预览失败");
    }
  } finally {
    if (seq === repairPreviewSeq) repairPreviewLoading.value = false;
  }
}

async function executeRepair(ids: number[], mode: string) {
  acting.value = true;
  try {
    const res = await repairAll(mode, ids);
    ElMessage.success(`已修复 ${res.count || 0} 本`);
    await refreshAll();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "修复失败");
  } finally {
    acting.value = false;
  }
}

async function confirmRepairPreview() {
  const ids = [...repairPreviewIds];
  const mode = repairPreviewMode;
  repairPreviewOpen.value = false;
  await executeRepair(ids, mode);
}

async function onRepairOne(bookId: number) {
  const issue = report.value?.issues.find((it) => it.book_id === bookId);
  await openRepairPreview([bookId], `修复预览 · ${issue?.title || `#${bookId}`}`);
}

async function onRepairAll() {
  // 只修当前报告里已列出的问题书；后端要求显式 ids，避免隐式全库深扫
  const ids = [...new Set((report.value?.issues || []).map((i) => i.book_id))];
  if (!ids.length) {
    ElMessage.warning("没有需要修复的问题书");
    return;
  }
  await openRepairPreview(ids, `批量修复预览（${ids.length} 本）`);
}

async function onRebuildThumbs() {
  const limit = 200;
  try {
    await ElMessageBox.confirm(
      `为最多 ${limit} 本已有封面的书生成/更新缩略图？\n幂等执行，已是最新的会跳过；可重复点击继续处理。`,
      "重建缩略图",
      { type: "info", confirmButtonText: "开始", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  acting.value = true;
  try {
    const res = await rebuildThumbs({ limit });
    if (!res.available) {
      ElMessage.warning("未安装 Pillow，无法生成缩略图");
    } else if (!res.total) {
      ElMessage.info("没有需要处理的封面");
    } else if (res.failed) {
      ElMessage.warning(
        `缩略图：生成 ${res.generated} · 跳过 ${res.skipped} · 失败 ${res.failed}`,
      );
    } else {
      ElMessage.success(`缩略图完成：生成 ${res.generated} · 跳过 ${res.skipped}`);
    }
    await lib.loadBooks();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "重建缩略图失败");
  } finally {
    acting.value = false;
  }
}

async function onRelocate() {
  try {
    await ElMessageBox.confirm(
      "按书籍分类归位源 TXT？\n将把「未分类」等目录中的文件移到对应分类文件夹（只移动位置，不改内容）。",
      "按分类归位",
      { type: "warning", confirmButtonText: "归位", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  acting.value = true;
  try {
    const res = await relocateBooks();
    ElMessage.success(`归位完成：移动 ${res.moved || 0} 本 · 失败 ${res.failed || 0} 本`);
    await refreshAll();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "归位失败");
  } finally {
    acting.value = false;
  }
}

onMounted(async () => {
  // 报告缓存在 Pinia（内存态）；如后台体检在跑则恢复进度，否则沿用上次报告
  try {
    const st = await fetchScanStatus();
    scan.value = st;
    if (st.running) watchScan();
    else if (st.report) check.setReport(st.report);
  } catch {
    /* ignore */
  }
  await refreshTasks();
  tasksTimer = setInterval(refreshTasks, 3000);
});
onUnmounted(() => {
  stopScanWatch();
  if (tasksTimer) clearInterval(tasksTimer);
});
</script>

<template>
  <div v-loading="acting" class="page check-page">
    <header class="page-header" :class="{ 'is-embedded': embedded }">
      <div class="title-row">
        <h1 v-if="!embedded" class="page-title">书库体检</h1>
        <span class="count-pill">{{ summary }}</span>
      </div>
      <div class="page-actions">
        <label class="check-label" title="读正文检查乱码/控制字符，书多时较慢">
          <input v-model="deepScan" type="checkbox" :disabled="!!scan?.running" />
          深度体检
        </label>
        <button
          type="button"
          class="ghost-btn"
          :disabled="!!scan?.running || !!scanBlocked"
          @click="loadReport"
        >
          <AppIcon name="refresh" :size="14" />
          {{
            scan?.running
              ? `扫描中 ${scanPercent}%`
              : scanBlocked
                ? `${scanBlocked}进行中`
                : scanned
                  ? "重新扫描"
                  : "开始扫描"
          }}
        </button>
        <button v-if="scan?.running" type="button" class="ghost-btn danger" @click="onCancelScan">
          停止扫描
        </button>
        <button type="button" class="ghost-btn" @click="onRepairAll">修复全部问题</button>
        <button
          type="button"
          class="ghost-btn"
          title="按书籍分类把本地 TXT 移到对应分类文件夹"
          @click="onRelocate"
        >
          按分类归位
        </button>
        <button
          type="button"
          class="ghost-btn"
          title="为已有封面批量生成/更新缩略图（每次最多 200 本）"
          :disabled="acting"
          @click="onRebuildThumbs"
        >
          <AppIcon name="refresh" :size="14" />
          重建缩略图
        </button>
      </div>
      <p v-if="scanBlocked" class="muted check-hint">
        当前有「{{ scanBlocked }}」任务进行中，体检暂不可用（导入 / 体检 / 刮削互斥）。
      </p>
      <div v-if="scan?.running" class="scan-progress">
        <div class="progress-track">
          <div class="progress-bar" :style="{ width: scanPercent + '%' }" />
        </div>
        <span class="progress-label">{{ scanPercent }}%</span>
      </div>
      <p v-else-if="scan?.error" class="muted check-hint is-error">体检失败：{{ scan.error }}</p>
    </header>

    <section class="page-card tabs-card">
      <div class="tab-nav" role="tablist">
        <button
          type="button"
          class="tab-btn"
          :class="{ 'is-active': activeTab === 'dup' }"
          @click="activeTab = 'dup'"
        >
          重复书籍
        </button>
        <button
          type="button"
          class="tab-btn"
          :class="{ 'is-active': activeTab === 'issue' }"
          @click="activeTab = 'issue'"
        >
          异常书籍
        </button>
        <button
          type="button"
          class="tab-btn"
          :class="{ 'is-active': activeTab === 'scrape' }"
          @click="activeTab = 'scrape'"
        >
          刮削书籍
        </button>
      </div>

      <CheckDuplicatesTab
        v-show="activeTab === 'dup'"
        :report="report"
        :scanned="scanned"
        @merge="onMerge"
      />
      <CheckIssuesTab
        v-show="activeTab === 'issue'"
        :report="report"
        :scanned="scanned"
        @repair="onRepairOne"
      />
      <CheckScrapeTab
        v-show="activeTab === 'scrape'"
        :blocked="scrapeBlocked"
        :refresh-tasks="refreshTasks"
        @finished="void refreshAll()"
      />
    </section>

    <CheckRepairPreviewDialog
      v-model:visible="repairPreviewOpen"
      :title="repairPreviewTitle"
      :loading="repairPreviewLoading"
      :items="repairPreviewItems"
      @confirm="confirmRepairPreview"
    />
  </div>
</template>

<style scoped>
.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

/* 嵌入“维护”分组时标题由页签承担，操作按钮靠右 */
.page-header.is-embedded .page-actions {
  margin-left: auto;
}

.count-pill {
  padding: 2px 10px;
  border-radius: var(--radius-full);
  background: var(--color-surface-2);
  color: var(--color-text-2);
  font-size: var(--text-xs);
  border: 1px solid var(--color-border);
}

.check-hint {
  margin: 0;
}

.check-hint.is-error {
  color: var(--color-danger);
}

.scan-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  margin-top: 4px;
}

.scan-progress .progress-track {
  flex: 1;
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

.ghost-btn:hover:not(:disabled) {
  border-color: var(--color-border-strong);
  color: var(--color-text);
}

.ghost-btn.danger {
  color: var(--color-danger);
  border-color: var(--color-danger);
  background: var(--color-danger-soft);
}

.tabs-card {
  padding: var(--space-3) var(--space-4) var(--space-4);
}

.tab-nav {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: var(--space-4);
}

.tab-btn {
  border: none;
  background: none;
  padding: 10px 14px;
  font-size: var(--text-sm);
  font-weight: 500;
  font-family: inherit;
  color: var(--color-text-2);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
}

.tab-btn:hover {
  color: var(--color-text);
}

.tab-btn.is-active {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
  font-weight: 600;
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

.progress-track {
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--color-surface-3);
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  background: var(--color-accent);
  border-radius: inherit;
  transition: width 0.25s ease;
}

</style>
