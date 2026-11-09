<script setup lang="ts">
/**
 * 书源管理：适配 Legado 书源（原始 JSON 完整保留），支持新增/修改/删除、
 * 启用禁用/分组/权重、批量操作、导入导出、结构校验 + 站点连通性探测。
 * 与手机互通：顶部订阅链接可直接填进 Legado「网络导入」。
 */
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useLibraryStore } from "@/stores/library";
import {
  batchBookSources,
  createBookSource,
  deleteBookSource,
  exportBookSourcesUrl,
  fetchBookSource,
  fetchBookSourceSettings,
  fetchBookSources,
  importBookSources,
  patchBookSource,
  resetBookSourceToken,
  updateBookSource,
  validateBookSource,
  validateStoredBookSource,
  type BookSourceImportResult,
  type BookSourceItem,
  type BookSourceSettings,
  type BookSourceValidation,
} from "@/api/admin";
import AppIcon from "@/components/AppIcon.vue";

const lib = useLibraryStore();

// —— 列表 ——
const items = ref<BookSourceItem[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const q = ref("");
const group = ref("");
const enabledFilter = ref<"all" | "on" | "off">("all");
const groups = ref<string[]>([]);
const loading = ref(false);
const selectedIds = ref<number[]>([]);

async function load() {
  loading.value = true;
  try {
    const data = await fetchBookSources({
      q: q.value.trim() || undefined,
      group: group.value || undefined,
      enabled: enabledFilter.value === "all" ? undefined : enabledFilter.value === "on",
      page: page.value,
      page_size: pageSize.value,
    });
    items.value = data.items;
    total.value = data.total;
    groups.value = data.groups;
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "书源列表加载失败");
  } finally {
    loading.value = false;
  }
}

let searchTimer: ReturnType<typeof setTimeout> | undefined;
function onSearchInput() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    page.value = 1;
    void load();
  }, 300);
}

function onFilterChange() {
  page.value = 1;
  void load();
}

function onSelectionChange(rows: BookSourceItem[]) {
  selectedIds.value = rows.map((r) => r.id);
}

// —— 手机网络导入订阅 ——
const sourceSettings = ref<BookSourceSettings | null>(null);
const publicBase = computed(() =>
  (lib.stats?.public_base_url || sourceSettings.value?.public_base_url || location.origin).replace(/\/$/, ""),
);
const subscribeUrl = computed(() => `${publicBase.value}${sourceSettings.value?.subscribe_path || ""}`);
const legadoDeepLink = computed(() => `legado://import/bookSource?src=${encodeURIComponent(subscribeUrl.value)}`);
const baseIsLocal = computed(() => /127\.0\.0\.1|localhost|0\.0\.0\.0/i.test(publicBase.value));

async function loadSettings() {
  try {
    sourceSettings.value = await fetchBookSourceSettings();
  } catch {
    /* 订阅设置失败不影响书源管理主体功能 */
  }
}

async function copyText(text: string, label: string) {
  try {
    await navigator.clipboard.writeText(text);
    ElMessage.success(`${label}已复制`);
  } catch {
    ElMessage.warning(`复制失败，请手动复制：${text}`);
  }
}

async function resetToken() {
  try {
    await ElMessageBox.confirm("重置后旧订阅链接立即失效，手机端需要重新导入链接。继续？", "重置订阅令牌", {
      type: "warning",
      confirmButtonText: "重置",
      cancelButtonText: "取消",
    });
  } catch {
    return;
  }
  try {
    sourceSettings.value = await resetBookSourceToken();
    ElMessage.success("订阅令牌已重置");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "重置失败");
  }
}

onMounted(() => {
  void load();
  void loadSettings();
  void lib.loadStats().catch(() => undefined);
});

// —— 新增 / 编辑 ——
const editorOpen = ref(false);
const editingId = ref<number | null>(null);
const editorTab = ref<"form" | "json">("form");
const saving = ref(false);
const probeOnValidate = ref(true);
const form = reactive({
  name: "",
  source_url: "",
  group_name: "",
  source_type: 0,
  enabled: true,
  enabled_explore: false,
  weight: 0,
  remark: "",
  searchUrl: "",
  rawText: "",
});

const TEMPLATE_SOURCE = {
  bookSourceName: "",
  bookSourceUrl: "",
  bookSourceGroup: "",
  bookSourceType: 0,
  enabled: true,
  enabledExplore: false,
  weight: 0,
  searchUrl: "",
  header: "",
  ruleSearch: { bookList: "", name: "", bookUrl: "" },
  ruleBookInfo: { name: "", tocUrl: "" },
  ruleToc: { chapterList: "", chapterName: "", chapterUrl: "" },
  ruleContent: { content: "" },
};

function parseRaw(): Record<string, unknown> | null {
  try {
    const data = JSON.parse(form.rawText) as unknown;
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("根节点需为 JSON 对象");
    return data as Record<string, unknown>;
  } catch (e) {
    ElMessage.error(`书源 JSON 格式错误：${e instanceof Error ? e.message : "无法解析"}`);
    return null;
  }
}

function fillFormFromRaw(raw: Record<string, unknown>) {
  form.name = String(raw.bookSourceName ?? "");
  form.source_url = String(raw.bookSourceUrl ?? "");
  form.group_name = String(raw.bookSourceGroup ?? "");
  form.source_type = Number(raw.bookSourceType ?? 0);
  form.enabled = raw.enabled !== false && raw.enabled !== "false";
  form.enabled_explore = raw.enabledExplore === true || raw.enabledExplore === "true";
  form.weight = Number(raw.weight ?? 0);
  form.searchUrl = String(raw.searchUrl ?? "");
}

function formToRaw(raw: Record<string, unknown>): Record<string, unknown> {
  return {
    ...raw,
    bookSourceName: form.name.trim(),
    bookSourceUrl: form.source_url.trim(),
    bookSourceGroup: form.group_name.trim(),
    bookSourceType: Number(form.source_type),
    enabled: form.enabled,
    enabledExplore: form.enabled_explore,
    weight: Number(form.weight) || 0,
    searchUrl: form.searchUrl.trim(),
  };
}

function currentSource(): Record<string, unknown> | null {
  const raw = parseRaw();
  if (!raw) return null;
  return editorTab.value === "form" ? formToRaw(raw) : raw;
}

function switchEditorTab(tab: "form" | "json") {
  if (tab === editorTab.value) return;
  const raw = parseRaw();
  if (!raw) return;
  if (tab === "json") {
    form.rawText = JSON.stringify(formToRaw(raw), null, 2);
  } else {
    fillFormFromRaw(raw);
  }
  editorTab.value = tab;
}

function openCreate() {
  editingId.value = null;
  editorTab.value = "form";
  Object.assign(form, {
    name: "", source_url: "", group_name: "", source_type: 0, enabled: true,
    enabled_explore: false, weight: 0, remark: "", searchUrl: "",
    rawText: JSON.stringify(TEMPLATE_SOURCE, null, 2),
  });
  editorOpen.value = true;
}

async function openEdit(row: BookSourceItem) {
  try {
    const detail = await fetchBookSource(row.id);
    editingId.value = row.id;
    editorTab.value = "form";
    form.remark = row.remark;
    form.rawText = JSON.stringify(detail.raw ?? TEMPLATE_SOURCE, null, 2);
    fillFormFromRaw(detail.raw ?? {});
    editorOpen.value = true;
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "书源详情加载失败");
  }
}

async function saveEditor() {
  const src = currentSource();
  if (!src) return;
  if (!String(src.bookSourceName ?? "").trim() || !String(src.bookSourceUrl ?? "").trim()) {
    ElMessage.warning("书源名称和书源地址不能为空");
    return;
  }
  saving.value = true;
  try {
    if (editingId.value !== null) {
      await updateBookSource(editingId.value, { source: src, remark: form.remark });
      ElMessage.success("书源已保存");
    } else {
      const res = await createBookSource({ source: src, remark: form.remark });
      if (res.created === 0 && res.skipped > 0) {
        ElMessage.warning("该 bookSourceUrl 已存在，可在导入时勾选「覆盖」");
      } else {
        ElMessage.success("书源已新增");
      }
    }
    editorOpen.value = false;
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    saving.value = false;
  }
}

// —— 校验 ——
const validationOpen = ref(false);
const validationTitle = ref("书源校验");
const validation = ref<BookSourceValidation | null>(null);
const validationItems = ref<BookSourceValidation[]>([]);

function showValidation(result: BookSourceValidation, title: string) {
  validation.value = result;
  validationItems.value = [];
  validationTitle.value = title;
  validationOpen.value = true;
}

async function validateEditor() {
  const src = currentSource();
  if (!src) return;
  try {
    const res = await validateBookSource({
      source: src,
      probe: probeOnValidate.value,
      exclude_id: editingId.value ?? undefined,
    });
    if ("items" in res) return;
    showValidation(res, "书源校验（未保存内容）");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "校验失败");
  }
}

async function validateRow(row: BookSourceItem) {
  try {
    const res = await validateStoredBookSource(row.id, true);
    showValidation(res, `校验结果 · ${row.name}`);
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "校验失败");
  }
}

async function validateSelected() {
  if (!selectedIds.value.length) {
    ElMessage.warning("请先勾选要校验的书源");
    return;
  }
  try {
    const res = await validateBookSource({ ids: selectedIds.value, probe: true });
    if ("items" in res) {
      validation.value = null;
      validationItems.value = res.items;
      validationTitle.value = `批量校验 · ${res.items.length} 个书源`;
      validationOpen.value = true;
      await load();
    }
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "批量校验失败");
  }
}

// —— 导入 / 导出 ——
const importOpen = ref(false);
const importText = ref("");
const importUrl = ref("");
const importOverwrite = ref(false);
const importResult = ref<BookSourceImportResult | null>(null);
const importing = ref(false);

function openImport() {
  importText.value = "";
  importUrl.value = "";
  importResult.value = null;
  importOpen.value = true;
}

function onFilePicked(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    importText.value = String(reader.result ?? "");
    ElMessage.success(`已读取 ${file.name}`);
  };
  reader.readAsText(file, "utf-8");
  input.value = "";
}

async function doImport() {
  if (!importText.value.trim() && !importUrl.value.trim()) {
    ElMessage.warning("请粘贴书源 JSON、选择文件或填写网络导入地址");
    return;
  }
  importing.value = true;
  try {
    importResult.value = await importBookSources({
      text: importText.value.trim() || undefined,
      url: importUrl.value.trim() || undefined,
      overwrite: importOverwrite.value,
    });
    ElMessage.success("导入完成");
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "导入失败");
  } finally {
    importing.value = false;
  }
}

async function doExport(ids?: number[]) {
  try {
    const url = exportBookSourcesUrl({
      ids: ids?.length ? ids : undefined,
      group: !ids?.length && group.value ? group.value : undefined,
    });
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `bookSources_${new Date().toISOString().slice(0, 10).replace(/-/g, "")}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    ElMessage.success("书源已导出");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "导出失败");
  }
}

// —— 批量与删除 ——
async function doBatch(action: "enable" | "disable" | "delete" | "group") {
  if (!selectedIds.value.length) {
    ElMessage.warning("请先勾选书源");
    return;
  }
  let groupName = "";
  if (action === "delete") {
    try {
      await ElMessageBox.confirm(`确定删除选中的 ${selectedIds.value.length} 个书源？`, "删除书源", {
        type: "warning",
        confirmButtonText: "删除",
        cancelButtonText: "取消",
      });
    } catch {
      return;
    }
  } else if (action === "group") {
    try {
      const { value } = await ElMessageBox.prompt("输入新的分组名称（留空可清除分组）", "修改分组", {
        inputValue: group.value,
        confirmButtonText: "确定",
        cancelButtonText: "取消",
      });
      groupName = String(value ?? "").trim();
    } catch {
      return;
    }
  }
  try {
    const res = await batchBookSources({ ids: selectedIds.value, action, group: groupName });
    ElMessage.success(`已处理 ${res.affected} 个书源`);
    selectedIds.value = [];
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "批量操作失败");
  }
}

async function removeRow(row: BookSourceItem) {
  try {
    await ElMessageBox.confirm(`确定删除书源「${row.name}」？`, "删除书源", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消",
    });
  } catch {
    return;
  }
  try {
    await deleteBookSource(row.id);
    ElMessage.success("已删除");
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "删除失败");
  }
}

async function toggleRow(row: BookSourceItem, value: unknown) {
  try {
    await patchBookSource(row.id, { enabled: Boolean(value) });
    await load();
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "状态更新失败");
  }
}

const typeLabel = (t: number) => ["文本", "音频", "图片", "文件"][t] ?? `类型 ${t}`;
/** el-table 插槽 row 是 DefaultRow，统一收敛到书源类型 */
const asItem = (row: unknown): BookSourceItem => row as BookSourceItem;
</script>

<template>
  <div class="page sources-page">
    <header class="page-header">
      <h1 class="page-title">书源管理</h1>
    </header>

    <!-- 手机 Legado 网络导入 -->
    <section class="page-card">
      <div class="section-head">
        <AppIcon name="api" :size="16" />
        <h2 class="section-title">手机网络导入</h2>
      </div>
      <p class="muted">
        在 Legado「我的 → 书源管理 → ⋮ → 网络导入」粘贴下面的链接，即可导入此处所有<strong>已启用</strong>的书源（标准 JSON 数组）。
      </p>
      <div class="link-row">
        <el-input :model-value="subscribeUrl" readonly size="default" spellcheck="false" />
        <el-button @click="copyText(subscribeUrl, '订阅链接')">复制链接</el-button>
        <el-button @click="copyText(legadoDeepLink, 'legado 深链')">复制深链</el-button>
        <el-button @click="resetToken">重置令牌</el-button>
      </div>
      <p class="hint">
        链接内含独立订阅令牌（不暴露读接口密钥），请勿公开分享；重置后旧链接立即失效。
        本库对外书源仍在「API / 书源」页单独提供，不包含在此订阅中。
      </p>
      <p v-if="baseIsLocal" class="hint warn-text">
        当前对外地址是 {{ publicBase }}（本机地址），手机无法访问。请在服务器环境变量 PUBLIC_BASE_URL 配置公网域名后重启服务。
      </p>
    </section>

    <!-- 列表 -->
    <section class="page-card">
      <div class="section-head">
        <AppIcon name="list" :size="16" />
        <h2 class="section-title">书源列表</h2>
        <span class="muted-xs">共 {{ total }} 个</span>
      </div>

      <div class="toolbar">
        <el-input v-model="q" placeholder="搜索名称 / 地址 / 分组" clearable style="width: 240px" @input="onSearchInput" />
        <el-select v-model="group" placeholder="全部分组" clearable style="width: 140px" @change="onFilterChange">
          <el-option v-for="g in groups" :key="g" :label="g" :value="g" />
        </el-select>
        <el-select v-model="enabledFilter" style="width: 120px" @change="onFilterChange">
          <el-option label="全部状态" value="all" />
          <el-option label="已启用" value="on" />
          <el-option label="已禁用" value="off" />
        </el-select>
        <div class="toolbar-spacer" />
        <el-button type="primary" @click="openCreate">新增</el-button>
        <el-button @click="openImport">导入</el-button>
        <el-button @click="doExport()">导出</el-button>
      </div>

      <div class="toolbar secondary">
        <span class="muted-xs">已选 {{ selectedIds.length }} 项</span>
        <el-button size="small" :disabled="!selectedIds.length" @click="doBatch('enable')">启用</el-button>
        <el-button size="small" :disabled="!selectedIds.length" @click="doBatch('disable')">禁用</el-button>
        <el-button size="small" :disabled="!selectedIds.length" @click="doBatch('group')">改分组</el-button>
        <el-button size="small" :disabled="!selectedIds.length" @click="validateSelected">校验</el-button>
        <el-button size="small" :disabled="!selectedIds.length" @click="doExport(selectedIds)">导出选中</el-button>
        <el-button size="small" type="danger" plain :disabled="!selectedIds.length" @click="doBatch('delete')">删除</el-button>
      </div>

      <el-table
        :data="items"
        v-loading="loading"
        row-key="id"
        size="small"
        stripe
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="42" />
        <el-table-column label="书源名称" min-width="220">
          <template #default="{ row }">
            <div class="src-name">{{ row.name }}</div>
            <div class="muted-xs ellipsis">{{ row.source_url }}</div>
            <div v-if="row.remark" class="muted-xs">备注：{{ row.remark }}</div>
          </template>
        </el-table-column>
        <el-table-column label="分组" width="110">
          <template #default="{ row }">{{ row.group_name || "—" }}</template>
        </el-table-column>
        <el-table-column label="类型" width="76">
          <template #default="{ row }">{{ typeLabel(row.source_type) }}</template>
        </el-table-column>
        <el-table-column label="启用" width="76">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v) => toggleRow(asItem(row), v)" />
          </template>
        </el-table-column>
        <el-table-column label="权重" width="70" prop="weight" />
        <el-table-column label="校验" width="96">
          <template #default="{ row }">
            <span v-if="!row.last_checked_at" class="muted-xs">未校验</span>
            <span v-else :class="row.last_check_ok ? 'ok-text' : 'warn-text'">
              {{ row.last_check_ok ? "通过" : "有问题" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" @click="openEdit(asItem(row))">编辑</el-button>
            <el-button link size="small" @click="validateRow(asItem(row))">校验</el-button>
            <el-button link size="small" @click="doExport([asItem(row).id])">导出</el-button>
            <el-button link size="small" type="danger" @click="removeRow(asItem(row))">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          layout="total, prev, pager, next"
          @current-change="load"
        />
      </div>
      <p class="hint">
        书源规则（JSONPath / XPath / CSS / JS）由 Legado App 解释执行，服务端只做结构校验与站点连通性探测，
        不会试跑搜索/目录/正文。未知字段会原样保留，导入手机不会丢配置。
      </p>
    </section>

    <!-- 新增 / 编辑 -->
    <el-dialog v-model="editorOpen" :title="editingId ? '编辑书源' : '新增书源'" width="760px" append-to-body>
      <div class="editor-tabs">
        <button type="button" :class="{ 'is-active': editorTab === 'form' }" @click="switchEditorTab('form')">表单编辑</button>
        <button type="button" :class="{ 'is-active': editorTab === 'json' }" @click="switchEditorTab('json')">完整 JSON</button>
      </div>

      <div v-if="editorTab === 'form'" class="form-grid">
        <label class="field"><span>书源名称 *</span><el-input v-model="form.name" placeholder="例：某某小说网" /></label>
        <label class="field"><span>书源地址 *</span><el-input v-model="form.source_url" placeholder="https://example.com" /></label>
        <label class="field"><span>搜索地址</span><el-input v-model="form.searchUrl" :placeholder="'https://example.com/search?q={{key}}'" /></label>
        <label class="field"><span>分组</span><el-input v-model="form.group_name" placeholder="例：精选" /></label>
        <label class="field"><span>类型</span>
          <el-select v-model="form.source_type" style="width: 100%">
            <el-option :value="0" label="文本" /><el-option :value="1" label="音频" />
            <el-option :value="2" label="图片" /><el-option :value="3" label="文件" />
          </el-select>
        </label>
        <label class="field"><span>权重</span><el-input v-model.number="form.weight" type="number" /></label>
        <label class="field"><span>备注（仅本地）</span><el-input v-model="form.remark" /></label>
        <div class="field switches">
          <el-checkbox v-model="form.enabled">启用书源</el-checkbox>
          <el-checkbox v-model="form.enabled_explore">启用发现</el-checkbox>
        </div>
        <p class="hint span-2">
          规则块（ruleSearch / ruleBookInfo / ruleToc / ruleContent）请在「完整 JSON」中编辑，或导入现成书源后修改。
          切换标签时两边内容会自动同步。
        </p>
      </div>
      <div v-else>
        <el-input v-model="form.rawText" type="textarea" :rows="16" spellcheck="false" class="json-editor" />
        <p class="hint">根节点需为单个书源对象；导入多个书源请用「导入」功能。</p>
      </div>

      <div class="dialog-actions">
        <el-checkbox v-model="probeOnValidate">校验时探测站点连通性</el-checkbox>
        <div class="toolbar-spacer" />
        <el-button @click="validateEditor">校验</el-button>
        <el-button type="primary" :loading="saving" @click="saveEditor">保存</el-button>
      </div>
    </el-dialog>

    <!-- 导入 -->
    <el-dialog v-model="importOpen" title="导入 Legado 书源" width="640px" append-to-body>
      <p class="muted">支持粘贴 JSON（数组 / 单对象 / 多个对象拼接）、选择本地文件，或填写书源订阅 URL。</p>
      <el-input v-model="importText" type="textarea" :rows="9" placeholder="粘贴书源 JSON…" spellcheck="false" class="json-editor" />
      <div class="import-row">
        <input type="file" accept=".json,.txt,application/json,text/plain" @change="onFilePicked" />
        <el-input v-model="importUrl" placeholder="或填写网络书源 URL" clearable />
      </div>
      <div class="import-row">
        <el-checkbox v-model="importOverwrite">覆盖同名（bookSourceUrl 相同）书源</el-checkbox>
      </div>
      <p class="hint">网络导入地址需为公网 http/https URL（出于安全限制不拉取内网地址）；拉取上限 2 MB。</p>
      <div v-if="importResult" class="import-result">
        <span>解析 {{ importResult.total }} 个</span>
        <span class="ok-text">新增 {{ importResult.created }}</span>
        <span>更新 {{ importResult.updated }}</span>
        <span>跳过 {{ importResult.skipped }}</span>
        <span v-if="importResult.errors.length" class="err-text">失败 {{ importResult.errors.length }}</span>
        <ul v-if="importResult.errors.length"><li v-for="(msg, i) in importResult.errors" :key="i">{{ msg }}</li></ul>
      </div>
      <template #footer>
        <el-button @click="importOpen = false">关闭</el-button>
        <el-button type="primary" :loading="importing" @click="doImport">开始导入</el-button>
      </template>
    </el-dialog>

    <!-- 校验结果 -->
    <el-dialog v-model="validationOpen" :title="validationTitle" width="620px" append-to-body>
      <template v-if="validation">
        <div class="validation-summary">
          <span :class="validation.ok ? 'ok-text' : 'err-text'">
            {{ validation.ok ? "结构校验通过" : `发现 ${validation.errors.length} 个错误` }}
          </span>
          <span v-if="validation.warnings.length" class="warn-text">· {{ validation.warnings.length }} 条提示</span>
          <span v-if="validation.probe" class="muted-xs">
            · 连通性：{{ validation.probe.skipped || (validation.probe.ok ? `HTTP ${validation.probe.status} / ${validation.probe.ms} ms` : validation.probe.error) }}
          </span>
        </div>
        <ul v-if="validation.errors.length" class="validation-list">
          <li v-for="(msg, i) in validation.errors" :key="`e${i}`" class="err-text">{{ msg }}</li>
        </ul>
        <ul v-if="validation.warnings.length" class="validation-list">
          <li v-for="(msg, i) in validation.warnings" :key="`w${i}`" class="warn-text">{{ msg }}</li>
        </ul>
        <div class="check-grid">
          <div v-for="(c, i) in validation.checks" :key="i" class="check-item" :class="c.level">
            <span class="check-dot" />
            <span class="check-label">{{ c.label }}</span>
            <span class="muted-xs ellipsis">{{ c.detail }}</span>
          </div>
        </div>
      </template>
      <template v-else>
        <div class="validation-summary">
          通过 {{ validationItems.filter((v) => v.ok).length }} / {{ validationItems.length }}
        </div>
        <div class="check-grid">
          <div
            v-for="v in validationItems"
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
      <template #footer><el-button @click="validationOpen = false">关闭</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.sources-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.section-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.section-head .muted-xs {
  margin-left: auto;
}
.link-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
  flex-wrap: wrap;
}
.link-row .el-input {
  flex: 1 1 320px;
  min-width: 220px;
}
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.toolbar.secondary {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
}
.toolbar-spacer {
  flex: 1;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
.src-name {
  font-weight: 600;
}
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 380px;
}
.ok-text {
  color: var(--el-color-success);
}
.warn-text {
  color: var(--el-color-warning);
}
.err-text {
  color: var(--el-color-danger);
}
.editor-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--el-border-color-light);
  margin-bottom: 14px;
}
.editor-tabs button {
  border: 0;
  background: transparent;
  padding: 8px 16px;
  cursor: pointer;
  color: var(--el-text-color-regular);
  border-bottom: 2px solid transparent;
}
.editor-tabs button.is-active {
  color: var(--el-color-primary);
  border-bottom-color: var(--el-color-primary);
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px 16px;
}
.form-grid .field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 13px;
}
.form-grid .field > span {
  color: var(--el-text-color-secondary);
}
.form-grid .span-2 {
  grid-column: 1 / -1;
}
.switches {
  flex-direction: row !important;
  align-items: center;
  gap: 16px;
}
.json-editor :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}
.dialog-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
}
.import-row {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-top: 10px;
  flex-wrap: wrap;
}
.import-row .el-input {
  flex: 1 1 260px;
}
.import-result {
  margin-top: 12px;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 13px;
}
.import-result ul {
  flex: 1 1 100%;
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12px;
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
  .form-grid,
  .check-grid {
    grid-template-columns: 1fr;
  }
}
</style>
