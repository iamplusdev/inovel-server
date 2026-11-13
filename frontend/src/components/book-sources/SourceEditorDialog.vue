<script setup lang="ts">
/** 书源编辑器：表单 / 完整 JSON 双视图，保存前可结构校验 */
import { reactive, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  createBookSource,
  fetchBookSource,
  updateBookSource,
  validateBookSource,
  type BookSourceItem,
  type BookSourceValidation,
} from "@/api/admin";

const props = defineProps<{
  visible: boolean;
  /** null=新增；否则编辑该书源 */
  row: BookSourceItem | null;
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
  saved: [];
  validated: [result: BookSourceValidation, title: string];
}>();

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

function resetForCreate() {
  editingId.value = null;
  editorTab.value = "form";
  Object.assign(form, {
    name: "", source_url: "", group_name: "", source_type: 0, enabled: true,
    enabled_explore: false, weight: 0, remark: "", searchUrl: "",
    rawText: JSON.stringify(TEMPLATE_SOURCE, null, 2),
  });
}

async function loadForEdit(row: BookSourceItem) {
  try {
    const detail = await fetchBookSource(row.id);
    editingId.value = row.id;
    editorTab.value = "form";
    form.remark = row.remark;
    form.rawText = JSON.stringify(detail.raw ?? TEMPLATE_SOURCE, null, 2);
    fillFormFromRaw(detail.raw ?? {});
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "书源详情加载失败");
    emit("update:visible", false);
  }
}

watch(
  () => props.visible,
  (v) => {
    if (!v) return;
    if (props.row) void loadForEdit(props.row);
    else resetForCreate();
  },
);

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
    emit("update:visible", false);
    emit("saved");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "保存失败");
  } finally {
    saving.value = false;
  }
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
    emit("validated", res, "书源校验（未保存内容）");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "校验失败");
  }
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    :title="editingId ? '编辑书源' : '新增书源'"
    width="760px"
    append-to-body
    @update:model-value="emit('update:visible', $event)"
  >
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
</template>

<style scoped>
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

.toolbar-spacer {
  flex: 1;
}

@media (max-width: 760px) {
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
