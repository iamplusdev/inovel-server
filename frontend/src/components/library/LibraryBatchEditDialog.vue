<script setup lang="ts">
/** 书库批量编辑：只提交勾选项 */
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { batchUpdateBooks } from "@/api/admin";
import type { BatchUpdateBody } from "@/api/admin";
import type { CategoryNode } from "@/api/types";

const props = defineProps<{
  visible: boolean;
  ids: number[];
  categoryTree: CategoryNode[];
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
  saved: [];
}>();

const editCategory = ref(false);
const editSource = ref("");
const editCategoryName = ref("");
const editTagsMode = ref<"append" | "remove" | "replace">("append");
const editTags = ref("");
const editStatus = ref("");
const saving = ref(false);

/** 当前所选书源下的分类选项（与编辑页共用统计里的分类树） */
const editCategoryOptions = computed(() => {
  const node = props.categoryTree.find((n) => n.key === editSource.value);
  return node?.categories || [];
});

// 切换书源后，分类不在新列表中则清空
watch(editSource, () => {
  if (!editCategoryOptions.value.includes(editCategoryName.value)) {
    editCategoryName.value = "";
  }
});

// 每次打开重置表单
watch(
  () => props.visible,
  (v) => {
    if (!v) return;
    editCategory.value = false;
    editSource.value = "";
    editCategoryName.value = "";
    editTagsMode.value = "append";
    editTags.value = "";
    editStatus.value = "";
  },
);

function close() {
  emit("update:visible", false);
}

async function submit() {
  const ids = props.ids;
  if (!ids.length || saving.value) return;
  if (editCategory.value && !editCategoryName.value) {
    ElMessage.warning("请选择分类");
    return;
  }
  const tags = editTags.value.trim();
  // 替换模式允许空串（清空标签）；追加/移除空串视为不修改
  const withTags = tags.length > 0 || editTagsMode.value === "replace";
  if (!editCategory.value && !withTags && !editStatus.value) {
    ElMessage.warning("没有需要修改的内容");
    return;
  }
  const body: BatchUpdateBody = { ids };
  if (editCategory.value) {
    body.category_source = editSource.value;
    body.category_name = editCategoryName.value;
  }
  if (withTags) {
    body.tags = tags;
    body.tags_mode = editTagsMode.value;
  }
  if (editStatus.value) body.status = editStatus.value;

  saving.value = true;
  try {
    const res = await batchUpdateBooks(body);
    ElMessage.success(`已更新 ${res.updated_count} 本`);
    close();
    emit("saved");
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : "批量更新失败");
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    title="批量编辑"
    width="460px"
    append-to-body
    @update:model-value="emit('update:visible', $event)"
  >
    <div class="batch-form">
      <div class="batch-form-row">
        <el-checkbox v-model="editCategory">修改分类</el-checkbox>
      </div>
      <div v-if="editCategory" class="batch-form-row">
        <span class="batch-form-label">书源</span>
        <el-select v-model="editSource" size="small" style="width: 110px">
          <el-option label="本地" value="" />
          <el-option v-for="src in ['起点', '番茄', '纵横']" :key="src" :label="src" :value="src" />
        </el-select>
        <span class="batch-form-label">分类</span>
        <el-select
          v-model="editCategoryName"
          size="small"
          placeholder="选择分类"
          style="width: 150px"
        >
          <el-option v-for="cat in editCategoryOptions" :key="cat" :label="cat" :value="cat" />
        </el-select>
      </div>
      <div class="batch-form-row">
        <span class="batch-form-label">标签</span>
        <el-select v-model="editTagsMode" size="small" style="width: 92px">
          <el-option label="追加" value="append" />
          <el-option label="移除" value="remove" />
          <el-option label="替换" value="replace" />
        </el-select>
        <el-input
          v-model="editTags"
          size="small"
          placeholder="逗号分隔，留空不修改"
          style="flex: 1; min-width: 140px"
        />
      </div>
      <div class="batch-form-row">
        <span class="batch-form-label">状态</span>
        <el-select v-model="editStatus" size="small" style="width: 110px">
          <el-option label="不修改" value="" />
          <el-option label="连载" value="连载" />
          <el-option label="完结" value="完结" />
          <el-option label="未知" value="未知" />
        </el-select>
      </div>
      <p class="muted-xs">已选 {{ ids.length }} 本，只修改勾选的项。</p>
    </div>
    <template #footer>
      <el-button @click="close">取消</el-button>
      <el-button type="primary" :loading="saving" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.batch-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.batch-form-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.batch-form-label {
  min-width: 32px;
  font-size: var(--text-xs);
  color: var(--color-text-2);
}
</style>
