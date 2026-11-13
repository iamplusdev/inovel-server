<script setup lang="ts">
/** WebDAV 进度同步：详情页诊断 + 手动拉取/推送。 */
import { ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  fetchBook,
  fetchSyncInfo,
  pullBookProgress,
  pushBookProgressNow,
} from "@/api/admin";
import type { SyncInfo } from "@/api/admin";
import type { BookDetail } from "@/api/types";

const props = defineProps<{
  visible: boolean;
  bookId: number;
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
  pulled: [book: BookDetail];
}>();

const syncLoading = ref(false);
const syncInfo = ref<SyncInfo | null>(null);
const syncPulling = ref(false);
const syncPushing = ref(false);

async function loadSyncInfo() {
  if (!props.bookId) return;
  syncLoading.value = true;
  try {
    syncInfo.value = await fetchSyncInfo(props.bookId);
  } catch (e) {
    syncInfo.value = null;
    ElMessage.error(e instanceof Error ? e.message : "读取同步状态失败");
  } finally {
    syncLoading.value = false;
  }
}

watch(
  () => props.visible,
  (open) => {
    if (open) void loadSyncInfo();
  },
);

async function pullSync() {
  if (!props.bookId || syncPulling.value) return;
  syncPulling.value = true;
  try {
    const r = await pullBookProgress(props.bookId);
    if (r.updated) {
      ElMessage.success(`已拉取远端进度：第 ${(r.chapter_index ?? 0) + 1} 章`);
      try {
        // 只刷新书籍元信息，不覆盖表单里尚未保存的编辑
        emit("pulled", await fetchBook(props.bookId));
      } catch {
        /* ignore */
      }
    } else if (!r.enabled) {
      ElMessage.warning("WebDAV 同步未启用");
    } else if (r.error) {
      ElMessage.error(r.error);
    } else if (r.found) {
      ElMessage.info(r.reason ? `未更新：${r.reason}` : "远端进度未更新");
    } else {
      ElMessage.info(`未找到远程文件：${r.filename || ""}`);
    }
    await loadSyncInfo();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "拉取失败");
  } finally {
    syncPulling.value = false;
  }
}

async function pushSync() {
  if (!props.bookId || syncPushing.value) return;
  syncPushing.value = true;
  try {
    const r = await pushBookProgressNow(props.bookId);
    if (r.pushed) {
      ElMessage.success("已推送本地进度到 WebDAV");
    } else if (r.skipped === "remote_newer") {
      ElMessage.warning("远端进度更新，已跳过本次推送");
    } else {
      ElMessage.error(r.error || "推送失败（本地可能还没有阅读进度）");
    }
    await loadSyncInfo();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "推送失败");
  } finally {
    syncPushing.value = false;
  }
}

/** 远程文件名解码后展示；复制时仍使用原始编码名 */
function displayFilename(name?: string): string {
  if (!name) return "—";
  try {
    return decodeURIComponent(name);
  } catch {
    return name;
  }
}

async function copyFilename() {
  const name = syncInfo.value?.filename;
  if (!name) return;
  try {
    await navigator.clipboard.writeText(name);
    ElMessage.success("原始文件名已复制");
  } catch {
    ElMessage.warning("复制失败");
  }
}

function formatSyncTime(ms?: number): string {
  if (!ms) return "—";
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

function localProgressText(info: SyncInfo | null): string {
  if (!info) return "—";
  const idx = info.local.read_chapter_index;
  const base = idx >= 0 ? `第 ${idx + 1} 章 · ${info.local.read_percent}%` : "未读";
  return info.local.read_at ? `${base} · ${info.local.read_at}` : base;
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="WebDAV 进度同步"
    width="560px"
    append-to-body
    @update:model-value="emit('update:visible', $event)"
  >
    <div v-loading="syncLoading" class="sync-panel">
      <div class="sync-row">
        <span class="sync-label">本地进度</span>
        <span class="sync-value">{{ localProgressText(syncInfo) }}</span>
      </div>
      <div class="sync-row">
        <span class="sync-label">远程文件名</span>
        <span class="sync-value mono" :title="syncInfo?.filename || ''">
          {{ displayFilename(syncInfo?.filename) }}
        </span>
        <button
          v-if="syncInfo?.filename"
          type="button"
          class="link-btn sync-copy"
          @click="copyFilename"
        >
          复制
        </button>
      </div>
      <div class="sync-row">
        <span class="sync-label">远端状态</span>
        <span class="sync-value">
          <template v-if="!syncInfo?.configured">未启用 WebDAV</template>
          <template v-else-if="!syncInfo?.remote_exists">
            文件不存在（Legado 尚未上传该书）
          </template>
          <template v-else-if="syncInfo?.remote">
            第 {{ syncInfo.remote.durChapterIndex + 1 }} 章 ·
            {{ syncInfo.remote.durChapterTitle || "—" }} ·
            {{ formatSyncTime(syncInfo.remote.durChapterTime) }}
          </template>
          <template v-else>无法解析</template>
        </span>
      </div>
      <div class="sync-row">
        <span class="sync-label">匹配章节</span>
        <span class="sync-value">
          {{
            syncInfo?.matched
              ? `第 ${syncInfo.matched.index + 1} 章 · ${syncInfo.matched.title}`
              : "—"
          }}
        </span>
      </div>
      <p v-if="syncInfo?.error" class="sync-error">{{ syncInfo.error }}</p>
      <p class="muted-xs">
        拉取按 durChapterTime 最新者优先，章节标题优先匹配；推送使用本地章节序号。
      </p>
      <div class="sync-actions">
        <button type="button" class="ghost-btn" :disabled="syncLoading" @click="loadSyncInfo">
          刷新
        </button>
        <button type="button" class="primary-btn" :disabled="syncPulling" @click="pullSync">
          {{ syncPulling ? "拉取中…" : "拉取远端" }}
        </button>
        <button type="button" class="ghost-btn" :disabled="syncPushing" @click="pushSync">
          {{ syncPushing ? "推送中…" : "推送本地" }}
        </button>
      </div>
    </div>
  </el-dialog>
</template>

<style scoped>
.sync-panel {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 120px;
}

.sync-row {
  display: flex;
  gap: 12px;
  font-size: var(--text-sm);
}

.sync-label {
  width: 84px;
  flex-shrink: 0;
  color: var(--color-text-3);
}

.sync-value {
  color: var(--color-text);
  word-break: break-all;
}

.sync-value.mono {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: var(--text-xs);
}

.sync-copy {
  flex-shrink: 0;
  margin-left: auto;
}

.sync-error {
  margin: 0;
  color: var(--color-danger);
  font-size: var(--text-xs);
}

.sync-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 4px;
}

.link-btn {
  border: none;
  background: none;
  color: var(--color-accent);
  font-size: var(--text-xs);
  cursor: pointer;
  padding: 0;
  font-family: inherit;
}

.ghost-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
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

.ghost-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.primary-btn {
  height: 32px;
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
