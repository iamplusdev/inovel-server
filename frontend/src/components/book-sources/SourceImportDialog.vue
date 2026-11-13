<script setup lang="ts">
/** 书源导入：粘贴 JSON / 选择文件 / 网络 URL */
import { ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  importBookSources,
  type BookSourceImportResult,
} from "@/api/admin";

const props = defineProps<{ visible: boolean }>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
  imported: [];
}>();

const importText = ref("");
const importUrl = ref("");
const importOverwrite = ref(false);
const importResult = ref<BookSourceImportResult | null>(null);
const importing = ref(false);

watch(
  () => props.visible,
  (v) => {
    if (!v) return;
    importText.value = "";
    importUrl.value = "";
    importResult.value = null;
  },
);

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
    emit("imported");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "导入失败");
  } finally {
    importing.value = false;
  }
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="导入 Legado 书源"
    width="640px"
    append-to-body
    @update:model-value="emit('update:visible', $event)"
  >
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
      <el-button @click="emit('update:visible', false)">关闭</el-button>
      <el-button type="primary" :loading="importing" @click="doImport">开始导入</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.json-editor :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
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

.ok-text {
  color: var(--el-color-success);
}

.err-text {
  color: var(--el-color-danger);
}
</style>
