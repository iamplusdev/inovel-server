<script setup lang="ts">
/**
 * 接入分组壳：书源管理与 API / 书源 JSON 合并为一个一级入口。
 * 子路由保留原命名（book-sources / api-docs），外部跳转不受影响。
 */
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import GroupTabs from "@/components/GroupTabs.vue";

const route = useRoute();
const router = useRouter();

const tabs = [
  { name: "book-sources", label: "书源管理" },
  { name: "api-docs", label: "API 与书源 JSON" },
];

const active = computed(() => String(route.name ?? "book-sources"));

function go(name: string) {
  if (name === route.name) return;
  void router.push({ name });
}
</script>

<template>
  <div class="page group-page">
    <header class="page-header">
      <div>
        <h1 class="page-title">接入</h1>
        <p class="page-subtitle">手机 Legado 书源导入与服务端 API 地址</p>
      </div>
    </header>

    <GroupTabs :items="tabs" :active="active" @select="go" />

    <router-view v-slot="{ Component }">
      <component :is="Component" embedded />
    </router-view>
  </div>
</template>
