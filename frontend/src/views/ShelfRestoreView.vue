<script setup lang="ts">
/**
 * 书架对照 / WebDAV 备份恢复。
 *
 * 从 Legado 的 WebDAV 备份（backup*.zip 或散放的 bookshelf.json /
 * bookSource.json）恢复书源与书架，并把两个书架拆成三部分：
 * 双端共有 / 仅服务端 / 仅手机在线。
 *
 * 在线书没有服务端正文，只进独立书架表，不污染本地书库；Windows 与网页
 * 书库都能看到它们，但点开只展示信息。
 */
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  clearShelf,
  fetchShelfBackups,
  fetchShelfCompare,
  fetchShelfStatus,
  previewShelfBackup,
  restoreShelfBackup,
  type ShelfBackupFile,
  type ShelfInput,
  type ShelfRestorePayload,
} from "@/api/shelf";
import type { ShelfCompare, ShelfPreview, ShelfRestoreResult } from "@/api/types";
import AppIcon from "@/components/AppIcon.vue";

defineProps<{ embedded?: boolean }>();

const backups = ref<ShelfBackupFile[]>([]);
const backupError = ref("");
const configReady = ref(false);
const selectedFiles = ref<string[]>([]);
const localFiles = ref<Array<{ name: string; content: string }>>([]);
const loadingBackups = ref(false);
const previewing = ref(false);
const restoring = ref(false);
const preview = ref<ShelfPreview | null>(null);
const result = ref<ShelfRestoreResult | null>(null);
const compare = ref<ShelfCompare | null>(null);
const status = ref<Record<string, unknown>>({});
const activeGroup = ref<"both" | "server" | "legado">("both");

const options = ref({
  restore_sources: true,
  overwrite_sources: false,
  restore_books: true,
  replace_shelf: true,
});

const groupMeta = [
  { key: "both", label: "双端共有", hint: "两个书架都有 · 服务端有正文，可直接阅读" },
  { key: "server", label: "仅服务端", hint: "本地书库独有 · 手机书架里没有" },
  { key: "legado", label: "仅手机在线", hint: "只在 Legado 书架 · 服务端没有正文" },
] as const;

const shownCompare = computed<ShelfCompare | null>(
  () => preview.value?.compare || result.value?.compare || compare.value,
);

function fmtSize(n: number) {
  if (!n) return "—";
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

async function loadBackups() {
  loadingBackups.value = true;
  backupError.value = "";
  try {
    const data = await fetchShelfBackups();
    configReady.value = data.configured;
    backups.value = data.items || [];
    backupError.value = data.error || "";
  } catch (e) {
    backupError.value = e instanceof Error ? e.message : "备份列表加载失败";
  } finally {
    loadingBackups.value = false;
  }
}

async function loadCompare() {
  try {
    compare.value = await fetchShelfCompare(200);
  } catch {
    compare.value = null;
  }
}

async function loadStatus() {
  try {
    const data = await fetchShelfStatus();
    status.value = data.state || {};
  } catch {
    status.value = {};
  }
}

function buildInput(): ShelfInput {
  return {
    files: selectedFiles.value.length ? selectedFiles.value : undefined,
    contents: localFiles.value.length ? localFiles.value : undefined,
  };
}

function hasInput(): boolean {
  return selectedFiles.value.length > 0 || localFiles.value.length > 0;
}

async function onPreview() {
  if (!hasInput()) {
    ElMessage.warning("请先选择一个 WebDAV 备份文件，或上传本地备份");
    return;
  }
  previewing.value = true;
  result.value = null;
  try {
    preview.value = await previewShelfBackup(buildInput());
    ElMessage.success("解析完成，下面是恢复后的三部分预览");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "备份解析失败");
  } finally {
    previewing.value = false;
  }
}

async function onRestore() {
  if (!hasInput()) {
    ElMessage.warning("请先选择备份文件");
    return;
  }
  if (!options.value.restore_sources && !options.value.restore_books) {
    ElMessage.warning("书源和书架都没勾选，没有可恢复的内容");
    return;
  }
  try {
    await ElMessageBox.confirm(
      options.value.replace_shelf && options.value.restore_books
        ? "恢复书架会替换当前已恢复的 Legado 书架（本地书库与正文不受影响），确认继续？"
        : "确认按当前选项恢复？",
      "恢复备份",
      { type: "warning", confirmButtonText: "开始恢复", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  restoring.value = true;
  try {
    const payload: ShelfRestorePayload = { ...buildInput(), ...options.value };
    result.value = await restoreShelfBackup(payload);
    preview.value = null;
    ElMessage.success("恢复完成");
    await Promise.all([loadCompare(), loadStatus()]);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "恢复失败");
  } finally {
    restoring.value = false;
  }
}

async function onUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  for (const file of files) {
    if (file.size > 64 * 1024 * 1024) {
      ElMessage.warning(`${file.name} 超过 64 MB，已跳过`);
      continue;
    }
    const buffer = await file.arrayBuffer();
    let binary = "";
    const bytes = new Uint8Array(buffer);
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
    }
    localFiles.value.push({ name: file.name, content: btoa(binary) });
  }
  input.value = "";
  result.value = null;
  preview.value = null;
}

function removeLocal(index: number) {
  localFiles.value.splice(index, 1);
}

async function onClear() {
  try {
    await ElMessageBox.confirm(
      "将清空已恢复的 Legado 书架（本地书库、正文与书源都不受影响），确认？",
      "清空书架",
      { type: "warning", confirmButtonText: "清空", cancelButtonText: "取消" },
    );
  } catch {
    return;
  }
  try {
    const res = await clearShelf();
    ElMessage.success(`已移除 ${res.deleted} 条书架记录`);
    preview.value = null;
    result.value = null;
    await Promise.all([loadCompare(), loadStatus()]);
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "清空失败");
  }
}

const lastRestored = computed(() => String(status.value.restored_at || ""));
const lastBackup = computed(() => String(status.value.backup_name || ""));

function groupItems(group: "both" | "server" | "legado") {
  const data = shownCompare.value;
  if (!data) return [];
  return data[group] as Array<Record<string, unknown>>;
}

function groupCount(group: "both" | "server" | "legado") {
  return shownCompare.value?.counts?.[group] ?? 0;
}

function itemName(item: Record<string, unknown>) {
  return String(item.name || item.shelf_name || "—");
}

function itemSub(item: Record<string, unknown>, group: string) {
  if (group === "legado") {
    return [item.origin_name, item.latest_chapter].filter(Boolean).join(" · ") || "在线书";
  }
  if (group === "both") {
    return `服务端 #${item.id} · 手机：${item.shelf_name || item.name}`;
  }
  return `服务端 #${item.id}`;
}

onMounted(() => {
  void Promise.all([loadBackups(), loadCompare(), loadStatus()]);
});
</script>

<template>
  <div class="page shelf-page">
    <header v-if="!embedded" class="page-header">
      <h1 class="page-title">书架对照 / 备份恢复</h1>
      <p class="page-subtitle">
        从 Legado 的 WebDAV 备份恢复书源与书架，并拆成「双端共有 / 仅服务端 / 仅手机在线」三部分。
      </p>
    </header>

    <!-- 选择备份 -->
    <section class="page-card">
      <div class="section-head">
        <AppIcon name="refresh" :size="16" />
        <h2 class="section-title">1 · 选择备份文件</h2>
        <button type="button" class="ghost-btn head-btn" :disabled="loadingBackups" @click="loadBackups">
          {{ loadingBackups ? "扫描中…" : "重新扫描 WebDAV" }}
        </button>
      </div>
      <p class="hint">
        扫描 WebDAV 配置目录下的 <code>backup*.zip</code>，以及散放的
        <code>bookshelf.json</code> / <code>bookSource.json</code>。可多选（新版备份包 + 单独的书源文件）。
      </p>
      <p v-if="!configReady && backupError" class="hint warn-text">
        {{ backupError }}（也可直接使用下方的本地上传）
      </p>

      <div v-if="backups.length" v-loading="loadingBackups" class="backup-list">
        <label v-for="f in backups" :key="f.path" class="backup-item" :class="{ 'is-on': selectedFiles.includes(f.path) }">
          <input v-model="selectedFiles" type="checkbox" :value="f.path" />
          <span class="backup-kind" :class="`is-${f.kind}`">{{ f.kind.toUpperCase() }}</span>
          <span class="backup-name" :title="f.path">{{ f.name }}</span>
          <span class="muted-xs">{{ fmtSize(f.size) }}</span>
          <span class="muted-xs backup-date">{{ f.modified || "" }}</span>
        </label>
      </div>
      <div v-else-if="!loadingBackups" class="empty-line">
        {{ backupError || "没有找到备份文件（可在手机 Legado 里手动备份，或上传本地备份）" }}
      </div>

      <div class="upload-row">
        <label class="ghost-btn upload-btn">
          上传本地备份
          <input type="file" accept=".zip,.json,.txt" multiple hidden @change="onUpload" />
        </label>
        <span class="muted-xs">支持 backup*.zip / bookshelf.json / bookSource.json，单个不超过 64 MB</span>
      </div>
      <div v-if="localFiles.length" class="local-list">
        <span v-for="(f, i) in localFiles" :key="`${f.name}-${i}`" class="file-tag">
          {{ f.name }}
          <button type="button" aria-label="移除" @click="removeLocal(i)">×</button>
        </span>
      </div>
    </section>

    <!-- 预览与选项 -->
    <section class="page-card">
      <div class="section-head">
        <AppIcon name="check" :size="16" />
        <h2 class="section-title">2 · 预览与恢复选项</h2>
      </div>
      <div class="option-grid">
        <label><input v-model="options.restore_sources" type="checkbox" /> 恢复书源到书源管理</label>
        <label><input v-model="options.overwrite_sources" type="checkbox" /> 覆盖同名书源</label>
        <label><input v-model="options.restore_books" type="checkbox" /> 恢复书架</label>
        <label><input v-model="options.replace_shelf" type="checkbox" /> 全量替换旧书架</label>
      </div>
      <p class="hint">
        备份是手机书架的全量快照，默认「全量替换」；取消勾选则只做增量合并。恢复书架不会改动本地正文书库。
      </p>
      <div class="action-row">
        <button type="button" class="ghost-btn" :disabled="previewing || !hasInput()" @click="onPreview">
          {{ previewing ? "解析中…" : "解析并预览" }}
        </button>
        <button type="button" class="primary-btn" :disabled="restoring || !hasInput()" @click="onRestore">
          {{ restoring ? "恢复中…" : "确认恢复" }}
        </button>
        <button type="button" class="ghost-btn danger" @click="onClear">清空已恢复书架</button>
      </div>

      <div v-if="preview || result" class="result-box">
        <template v-if="preview">
          <h3>预览结果 · {{ preview.backup_name }}</h3>
          <p class="muted-xs">
            解析到 {{ preview.parsed.books }} 本书、{{ preview.parsed.sources }} 个书源；
            包内 {{ preview.files.length }} 个数据文件。
          </p>
        </template>
        <template v-else-if="result">
          <h3>恢复完成 · {{ result.backup_name }}</h3>
          <p class="muted-xs">
            书源：新增 {{ result.sources.created }} / 更新 {{ result.sources.updated }} / 跳过
            {{ result.sources.skipped }}；书架：新增 {{ result.shelf.created }} / 更新
            {{ result.shelf.updated }}。
          </p>
        </template>
        <ul v-if="(preview?.warnings || result?.warnings)?.length" class="warn-list">
          <li v-for="(w, i) in (preview?.warnings || result?.warnings || [])" :key="i">{{ w }}</li>
        </ul>
      </div>
    </section>

    <!-- 三部分对照 -->
    <section class="page-card">
      <div class="section-head">
        <AppIcon name="library" :size="16" />
        <h2 class="section-title">3 · 两个书架的三部分</h2>
        <span v-if="shownCompare" class="muted-xs">
          服务端 {{ shownCompare.counts.server_total }} 本 · 手机书架
          {{ shownCompare.counts.shelf_total }} 本
        </span>
      </div>
      <template v-if="shownCompare">
        <div class="group-tabs">
          <button
            v-for="g in groupMeta"
            :key="g.key"
            type="button"
            class="group-tab"
            :class="{ 'is-active': activeGroup === g.key }"
            @click="activeGroup = g.key"
          >
            {{ g.label }} <strong>{{ groupCount(g.key) }}</strong>
          </button>
        </div>
        <p class="hint">{{ groupMeta.find((g) => g.key === activeGroup)?.hint }}</p>
        <div class="group-list">
          <div v-for="(item, i) in groupItems(activeGroup)" :key="i" class="group-item">
            <span class="group-item-name">{{ itemName(item) }}</span>
            <span class="muted-xs">{{ String(item.author || item.shelf_author || "") }}</span>
            <span class="muted-xs group-item-sub">{{ itemSub(item, activeGroup) }}</span>
          </div>
          <div v-if="!groupItems(activeGroup).length" class="empty-line">这一类暂时没有书</div>
        </div>
        <p v-if="shownCompare.truncated" class="hint warn-text">
          仅显示前 {{ shownCompare.limit }} 条，完整列表可在书库用「来源」筛选查看。
        </p>
      </template>
      <div v-else class="empty-line">尚未恢复过书架，先从上方选择一个备份文件。</div>

      <div v-if="lastRestored" class="status-line">
        上次恢复：{{ lastRestored }} · {{ lastBackup }}
      </div>
    </section>
  </div>
</template>

<style scoped>
.shelf-page {
  gap: 16px;
}

.page-subtitle {
  margin: 6px 0 0;
  color: var(--color-text-3);
  font-size: var(--text-sm);
}

.section-head {
  gap: 8px;
}

.head-btn {
  margin-left: auto;
}

.hint {
  margin: 6px 0 10px;
  font-size: var(--text-xs);
  color: var(--color-text-3);
  line-height: 1.6;
}

.hint code {
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--color-surface-2);
}

.warn-text {
  color: var(--color-warning, #c47d1a);
}

.backup-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 260px;
  overflow-y: auto;
  margin-bottom: 12px;
}

.backup-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 10px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: var(--text-sm);
}

.backup-item.is-on {
  border-color: var(--color-accent);
  background: var(--color-accent-ring, rgba(76, 111, 255, 0.07));
}

.backup-kind {
  flex-shrink: 0;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10px;
  color: #fff;
  background: #6a8bff;
}

.backup-kind.is-json {
  background: #4a9e6f;
}

.backup-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.backup-date {
  max-width: 190px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.upload-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.upload-btn {
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}

.local-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.file-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
}

.file-tag button {
  border: none;
  background: transparent;
  color: var(--color-text-3);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
}

.option-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
  gap: 8px 18px;
  margin: 10px 0 4px;
  font-size: var(--text-sm);
}

.option-grid label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}

.action-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.result-box {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
}

.result-box h3 {
  margin: 0 0 6px;
  font-size: var(--text-sm);
}

.warn-list {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: var(--text-xs);
  color: var(--color-warning, #c47d1a);
}

.group-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.group-tab {
  padding: 7px 14px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  background: var(--color-surface-2);
  color: var(--color-text-2);
  font-size: var(--text-sm);
  cursor: pointer;
  font-family: inherit;
}

.group-tab strong {
  margin-left: 4px;
  color: var(--color-text);
}

.group-tab.is-active {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: var(--color-accent-ring, rgba(76, 111, 255, 0.08));
}

.group-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 320px;
  overflow-y: auto;
}

.group-item {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border);
  font-size: var(--text-sm);
}

.group-item-name {
  font-weight: 600;
  min-width: 120px;
}

.group-item-sub {
  margin-left: auto;
  text-align: right;
}

.empty-line {
  padding: 18px 0;
  color: var(--color-text-3);
  font-size: var(--text-sm);
  text-align: center;
}

.status-line {
  margin-top: 12px;
  font-size: var(--text-xs);
  color: var(--color-text-3);
}
</style>
