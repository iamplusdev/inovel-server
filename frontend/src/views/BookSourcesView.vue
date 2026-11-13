<script setup lang="ts">
/**
 * 书源管理：适配 Legado 书源（原始 JSON 完整保留），支持新增/修改/删除、
 * 启用禁用/分组/权重、批量操作、导入导出、结构校验 + 站点连通性探测。
 * 与手机互通：顶部订阅链接可直接填进 Legado「网络导入」。
 */
import { computed, onMounted, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useLibraryStore } from "@/stores/library";
import {
  batchBookSources,
  deleteBookSource,
  exportBookSourcesUrl,
  fetchBookSourceSettings,
  fetchBookSources,
  patchBookSource,
  resetBookSourceToken,
  validateBookSource,
  validateStoredBookSource,
  type BookSourceItem,
  type BookSourceSettings,
  type BookSourceValidation,
} from "@/api/admin";
import AppIcon from "@/components/AppIcon.vue";
import SourceEditorDialog from "@/components/book-sources/SourceEditorDialog.vue";
import SourceImportDialog from "@/components/book-sources/SourceImportDialog.vue";
import SourceValidationDialog from "@/components/book-sources/SourceValidationDialog.vue";

defineProps<{ embedded?: boolean }>();

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
const editorRow = ref<BookSourceItem | null>(null);

function openCreate() {
  editorRow.value = null;
  editorOpen.value = true;
}

function openEdit(row: BookSourceItem) {
  editorRow.value = row;
  editorOpen.value = true;
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

function openImport() {
  importOpen.value = true;
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
    <header v-if="!embedded" class="page-header">
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
        本库对外书源仍在「接入 → API 与书源 JSON」页签单独提供，不包含在此订阅中。
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

    <SourceEditorDialog
      v-model:visible="editorOpen"
      :row="editorRow"
      @saved="load"
      @validated="showValidation"
    />

    <SourceImportDialog v-model:visible="importOpen" @imported="load" />

    <SourceValidationDialog
      v-model:visible="validationOpen"
      :title="validationTitle"
      :result="validation"
      :items="validationItems"
    />
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
</style>
